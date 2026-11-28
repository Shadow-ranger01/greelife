"""Unit & API tests for the FastAPI + Firebase Auth/Firestore backend."""

import os
import unittest

import httpx
from fastapi.testclient import TestClient

import backend
from backend import app, compute_pump_decision, from_fs_doc, to_fs_fields


class FakeFirestoreTransport:
    def __init__(self) -> None:
        self.docs = {
            "config/limits": to_fs_fields(
                {"soilMin": 35, "soilMax": 70, "tempMax": 38, "tankMinPercent": 15, "logIntervalMin": 10}
            ),
            "control/pump": to_fs_fields({"mode": "auto", "manualOn": False}),
        }
        self.history: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "documents/telemetry/latest" in url:
            if request.method == "GET":
                doc = self.docs.get("telemetry/latest")
                return httpx.Response(200, json=doc) if doc else httpx.Response(404)
            if request.method == "PATCH":
                self.docs["telemetry/latest"] = request.read() and __import__("json").loads(request.read())
                return httpx.Response(200, json=self.docs["telemetry/latest"])

        if "documents/config/limits" in url:
            if request.method == "GET":
                return httpx.Response(200, json=self.docs["config/limits"])
            if request.method == "PATCH":
                self.docs["config/limits"] = __import__("json").loads(request.read())
                return httpx.Response(200, json=self.docs["config/limits"])

        if "documents/control/pump" in url:
            if request.method == "GET":
                return httpx.Response(200, json=self.docs["control/pump"])
            if request.method == "PATCH":
                self.docs["control/pump"] = __import__("json").loads(request.read())
                return httpx.Response(200, json=self.docs["control/pump"])

        if url.endswith("documents:runQuery"):
            return httpx.Response(
                200,
                json=[{"document": d} for d in self.history],
            )

        if url.endswith("documents/history") and request.method == "POST":
            body = __import__("json").loads(request.read())
            body["name"] = f"projects/green-life-1436d/databases/greenlife-db/documents/history/h{len(self.history)+1}"
            self.history.append(body)
            return httpx.Response(200, json=body)

        return httpx.Response(404)


class BackendTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["FIREBASE_API_KEY"] = "test-api-key"
        os.environ["FIREBASE_PROJECT_ID"] = "green-life-1436d"
        os.environ["FIREBASE_FIRESTORE_DATABASE_ID"] = "greenlife-db"
        os.environ.pop("GEMINI_API_KEY", None)

        self.fake_fs = FakeFirestoreTransport()
        self._orig_client = backend._http_client
        backend._http_client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(self.fake_fs.handler))
        self.client = TestClient(app)
        self.headers = {"Authorization": "Bearer fake-id-token"}

    def tearDown(self) -> None:
        backend._http_client = self._orig_client

    def test_firestore_value_roundtrip(self) -> None:
        raw = {"temp": 29.4, "soilMoisture": 42, "pumpOn": True, "mode": "auto"}
        encoded = to_fs_fields(raw)
        self.assertEqual(from_fs_doc(encoded), raw)

    def test_dry_run_lock_and_auto_hysteresis(self) -> None:
        limits = {"soilMin": 35, "soilMax": 70, "tankMinPercent": 15}
        locked = compute_pump_decision(20, 10, limits, {"mode": "manual", "manualOn": True}, True)
        self.assertFalse(locked["pumpOn"])
        self.assertEqual(locked["reason"], "dry_tank_lock")

        auto = {"mode": "auto", "manualOn": False}
        self.assertTrue(compute_pump_decision(30, 80, limits, auto, False)["pumpOn"])
        self.assertTrue(compute_pump_decision(50, 80, limits, auto, True)["pumpOn"])
        self.assertFalse(compute_pump_decision(50, 80, limits, auto, False)["pumpOn"])
        self.assertFalse(compute_pump_decision(70, 80, limits, auto, True)["pumpOn"])

    def test_auth_required_and_telemetry_flow(self) -> None:
        self.assertEqual(self.client.get("/api/state").status_code, 401)

        bad_limits = self.client.put(
            "/api/limits",
            headers=self.headers,
            json={"soilMin": 75, "soilMax": 70, "tempMax": 38, "tankMinPercent": 15, "logIntervalMin": 10},
        )
        self.assertEqual(bad_limits.status_code, 422)

        post = self.client.post(
            "/api/telemetry",
            headers=self.headers,
            json={
                "temp": 29.4,
                "humidity": 68,
                "pressure": 1009.2,
                "light": 420,
                "soilMoisture": 30,
                "tankPercent": 75,
                "ts": 1764060000000,
            },
        )
        self.assertEqual(post.status_code, 200)
        self.assertTrue(post.json()["decision"]["pumpOn"])

        state = self.client.get("/api/state", headers=self.headers).json()
        self.assertEqual(len(state["history"]), 1)
        self.assertEqual(state["latest"]["soilMoisture"], 30)

        csv_resp = self.client.get("/api/export.csv?token=fake-id-token")
        self.assertEqual(csv_resp.status_code, 200)
        self.assertIn("1764060000000,29.4,68.0,1009.2,420.0,30.0,75.0", csv_resp.text)

    def test_predict_requires_gemini_key(self) -> None:
        resp = self.client.post("/api/predict", headers=self.headers)
        self.assertEqual(resp.status_code, 500)


if __name__ == "__main__":
    unittest.main()
