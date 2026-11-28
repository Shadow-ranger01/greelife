"""FastAPI backend wired to Firebase Auth & Cloud Firestore (same config as web/)."""

from __future__ import annotations

import csv
import io
import json
import os
import time
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from google import genai
from pydantic import BaseModel, Field, model_validator

APP_DIR = Path(__file__).resolve().parent
WEB_ENV_PATH = APP_DIR.parent / "web" / ".env.local"
LOCAL_ENV_PATH = APP_DIR / ".env"


def _load_env_files() -> None:
    """Load python_app/.env first, falling back to web/.env.local keys."""
    for env_file in (LOCAL_ENV_PATH, WEB_ENV_PATH):
        if not env_file.exists():
            continue
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env_files()

DEFAULT_LIMITS = {
    "soilMin": 35,
    "soilMax": 70,
    "tempMax": 38,
    "tankMinPercent": 15,
    "logIntervalMin": 10,
}
DEFAULT_CONTROL = {"mode": "auto", "manualOn": False}


def _fb_cfg() -> tuple[str, str, str]:
    api_key = os.environ.get("FIREBASE_API_KEY") or os.environ.get("NEXT_PUBLIC_FIREBASE_API_KEY", "")
    project_id = os.environ.get("FIREBASE_PROJECT_ID") or os.environ.get("NEXT_PUBLIC_FIREBASE_PROJECT_ID", "")
    database_id = (
        os.environ.get("FIREBASE_FIRESTORE_DATABASE_ID")
        or os.environ.get("NEXT_PUBLIC_FIREBASE_FIRESTORE_DATABASE_ID")
        or "(default)"
    )
    if not api_key or not project_id:
        raise HTTPException(status_code=500, detail="Firebase config missing in python_app/.env.")
    return api_key, project_id, database_id


def _fs_base_url(project_id: str, database_id: str) -> str:
    return f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/{database_id}/documents"


def to_fs_fields(data: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, bool):
            fields[k] = {"booleanValue": v}
        elif isinstance(v, int):
            fields[k] = {"integerValue": str(v)}
        elif isinstance(v, float):
            fields[k] = {"doubleValue": v}
        elif isinstance(v, str):
            fields[k] = {"stringValue": v}
    return {"fields": fields}


def from_fs_doc(doc: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, val_obj in doc.get("fields", {}).items():
        if "booleanValue" in val_obj:
            out[k] = bool(val_obj["booleanValue"])
        elif "integerValue" in val_obj:
            out[k] = int(val_obj["integerValue"])
        elif "doubleValue" in val_obj:
            out[k] = float(val_obj["doubleValue"])
        elif "stringValue" in val_obj:
            out[k] = str(val_obj["stringValue"])
    return out


class AuthIn(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=6)


class TelemetryIn(BaseModel):
    temp: float = Field(ge=-40, le=100)
    humidity: float = Field(ge=0, le=100)
    pressure: float = Field(ge=300, le=1200)
    light: float = Field(ge=0, le=200000)
    soilMoisture: float = Field(ge=0, le=100)
    tankPercent: float = Field(ge=0, le=100)
    ts: int | None = Field(default=None, gt=0)
    forceLog: bool = True


class LimitsIn(BaseModel):
    soilMin: float = Field(ge=0, le=99)
    soilMax: float = Field(ge=1, le=100)
    tempMax: float = Field(ge=0, le=70)
    tankMinPercent: float = Field(ge=0, le=95)
    logIntervalMin: int = Field(ge=1, le=1440)

    @model_validator(mode="after")
    def check_soil_range(self) -> LimitsIn:
        if self.soilMin >= self.soilMax:
            raise ValueError("soilMin must be strictly less than soilMax")
        return self


class ControlIn(BaseModel):
    mode: Literal["auto", "manual"]
    manualOn: bool


def compute_pump_decision(
    soil_moisture: float,
    tank_percent: float,
    limits: dict[str, Any],
    control: dict[str, Any],
    currently_on: bool = False,
) -> dict[str, Any]:
    """Compute pump relay state with dry-run lock and auto soil-moisture hysteresis."""
    if tank_percent < limits["tankMinPercent"]:
        return {"pumpOn": False, "dryTank": True, "reason": "dry_tank_lock"}

    if control["mode"] == "manual":
        manual_on = bool(control["manualOn"])
        return {
            "pumpOn": manual_on,
            "dryTank": False,
            "reason": "manual_on" if manual_on else "manual_off",
        }

    # Hysteresis band prevents relay chatter near a single moisture threshold.
    if soil_moisture < limits["soilMin"]:
        return {"pumpOn": True, "dryTank": False, "reason": "auto_below_min"}
    if soil_moisture >= limits["soilMax"]:
        return {"pumpOn": False, "dryTank": False, "reason": "auto_above_max"}

    return {
        "pumpOn": bool(currently_on),
        "dryTank": False,
        "reason": "auto_hysteresis_hold",
    }


def _extract_token(authorization: str | None, query_token: str | None = None) -> str:
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        if token:
            return token
    if query_token and query_token.strip():
        return query_token.strip()
    raise HTTPException(status_code=401, detail="Missing Firebase auth token.")


def _raise_fs_error(resp: httpx.Response) -> None:
    if resp.status_code in (401, 403):
        raise HTTPException(status_code=401, detail="Firebase authentication expired or unauthorized.")
    try:
        msg = resp.json().get("error", {}).get("message", resp.text)
    except Exception:
        msg = resp.text
    raise HTTPException(status_code=502, detail=f"Firestore error: {msg}")


async def _fs_get_doc(client: httpx.AsyncClient, base: str, path: str, token: str) -> dict[str, Any] | None:
    resp = await client.get(f"{base}/{path}", headers={"Authorization": f"Bearer {token}"})
    if resp.status_code == 404:
        return None
    if not resp.is_success:
        _raise_fs_error(resp)
    return from_fs_doc(resp.json())


async def _fs_patch_doc(
    client: httpx.AsyncClient, base: str, path: str, data: dict[str, Any], token: str
) -> None:
    resp = await client.patch(
        f"{base}/{path}",
        headers={"Authorization": f"Bearer {token}"},
        json=to_fs_fields(data),
    )
    if not resp.is_success:
        _raise_fs_error(resp)


async def _fs_query_history(client: httpx.AsyncClient, base: str, token: str, max_rows: int = 40) -> list[dict[str, Any]]:
    resp = await client.post(
        f"{base}:runQuery",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "structuredQuery": {
                "from": [{"collectionId": "history"}],
                "orderBy": [{"field": {"fieldPath": "ts"}, "direction": "DESCENDING"}],
                "limit": max_rows,
            }
        },
    )
    if not resp.is_success:
        _raise_fs_error(resp)
    rows: list[dict[str, Any]] = []
    for item in resp.json():
        doc = item.get("document")
        if doc:
            row = from_fs_doc(doc)
            row["id"] = doc.get("name", "").rsplit("/", 1)[-1]
            rows.append(row)
    rows.sort(key=lambda r: r.get("ts", 0))
    return rows


async def _fetch_state(client: httpx.AsyncClient, token: str) -> dict[str, Any]:
    _, project_id, database_id = _fb_cfg()
    base = _fs_base_url(project_id, database_id)
    latest = await _fs_get_doc(client, base, "telemetry/latest", token)
    limits_doc = await _fs_get_doc(client, base, "config/limits", token)
    control_doc = await _fs_get_doc(client, base, "control/pump", token)
    history = await _fs_query_history(client, base, token, 40)

    limits = {**DEFAULT_LIMITS, **(limits_doc or {})}
    control = {**DEFAULT_CONTROL, **(control_doc or {})}
    decision = (
        compute_pump_decision(
            latest["soilMoisture"],
            latest["tankPercent"],
            limits,
            control,
            latest.get("pumpOn", False),
        )
        if latest
        else None
    )
    return {
        "latest": latest,
        "history": history,
        "limits": limits,
        "control": control,
        "decision": decision,
    }


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10.0)


app = FastAPI(title="Green Life Agro API (Firebase Auth + Firestore)")


@app.post("/api/auth/login")
async def auth_login(payload: AuthIn):
    api_key, _, _ = _fb_cfg()
    async with _http_client() as client:
        resp = await client.post(
            f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={api_key}",
            json={"email": payload.email.strip(), "password": payload.password, "returnSecureToken": True},
        )
    if not resp.is_success:
        detail = resp.json().get("error", {}).get("message", "Authentication failed.")
        raise HTTPException(status_code=401, detail=detail)
    data = resp.json()
    return {"idToken": data["idToken"], "email": data["email"], "localId": data["localId"]}


@app.post("/api/auth/signup")
async def auth_signup(payload: AuthIn):
    api_key, _, _ = _fb_cfg()
    async with _http_client() as client:
        resp = await client.post(
            f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={api_key}",
            json={"email": payload.email.strip(), "password": payload.password, "returnSecureToken": True},
        )
    if not resp.is_success:
        detail = resp.json().get("error", {}).get("message", "Sign up failed.")
        raise HTTPException(status_code=400, detail=detail)
    data = resp.json()
    return {"idToken": data["idToken"], "email": data["email"], "localId": data["localId"]}


@app.get("/api/state")
async def get_state(authorization: str | None = Header(default=None)):
    token = _extract_token(authorization)
    async with _http_client() as client:
        return await _fetch_state(client, token)


@app.post("/api/telemetry")
async def post_telemetry(payload: TelemetryIn, authorization: str | None = Header(default=None)):
    token = _extract_token(authorization)
    now_ms = payload.ts or int(time.time() * 1000)
    _, project_id, database_id = _fb_cfg()
    base = _fs_base_url(project_id, database_id)

    async with _http_client() as client:
        state = await _fetch_state(client, token)
        currently_on = bool(state["latest"] and state["latest"].get("pumpOn"))
        decision = compute_pump_decision(
            payload.soilMoisture,
            payload.tankPercent,
            state["limits"],
            state["control"],
            currently_on,
        )

        latest_doc = {
            "temp": payload.temp,
            "humidity": payload.humidity,
            "pressure": payload.pressure,
            "light": payload.light,
            "soilMoisture": payload.soilMoisture,
            "tankPercent": payload.tankPercent,
            "pumpOn": decision["pumpOn"],
            "updatedAt": now_ms,
        }
        await _fs_patch_doc(client, base, "telemetry/latest", latest_doc, token)

        last_ts = state["history"][-1]["ts"] if state["history"] else 0
        interval_ms = int(state["limits"]["logIntervalMin"]) * 60_000
        if payload.forceLog or (now_ms - last_ts) >= interval_ms:
            history_doc = {
                "temp": payload.temp,
                "humidity": payload.humidity,
                "pressure": payload.pressure,
                "light": payload.light,
                "soilMoisture": payload.soilMoisture,
                "tankPercent": payload.tankPercent,
                "ts": now_ms,
            }
            resp = await client.post(
                f"{base}/history",
                headers={"Authorization": f"Bearer {token}"},
                json=to_fs_fields(history_doc),
            )
            if not resp.is_success:
                _raise_fs_error(resp)

        return {"saved": True, "updatedAt": now_ms, "decision": decision}


@app.put("/api/limits")
async def put_limits(payload: LimitsIn, authorization: str | None = Header(default=None)):
    token = _extract_token(authorization)
    _, project_id, database_id = _fb_cfg()
    base = _fs_base_url(project_id, database_id)
    async with _http_client() as client:
        await _fs_patch_doc(client, base, "config/limits", payload.model_dump(), token)
    return {"saved": True, "limits": payload.model_dump()}


@app.put("/api/control")
async def put_control(payload: ControlIn, authorization: str | None = Header(default=None)):
    token = _extract_token(authorization)
    _, project_id, database_id = _fb_cfg()
    base = _fs_base_url(project_id, database_id)
    async with _http_client() as client:
        await _fs_patch_doc(client, base, "control/pump", payload.model_dump(), token)
    return {"saved": True, "control": payload.model_dump()}


@app.get("/api/export.csv")
async def export_csv(
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
):
    id_token = _extract_token(authorization, token)
    _, project_id, database_id = _fb_cfg()
    base = _fs_base_url(project_id, database_id)
    async with _http_client() as client:
        rows = await _fs_query_history(client, base, id_token, 200)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["ts_ms", "temp_c", "humidity_pct", "pressure_hpa", "light_lux", "soil_moisture_pct", "tank_pct"]
    )
    for r in rows:
        writer.writerow(
            [r["ts"], r["temp"], r["humidity"], r["pressure"], r["light"], r["soilMoisture"], r["tankPercent"]]
        )
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=microclimate-history.csv"},
    )


@app.post("/api/predict")
async def predict_climate(authorization: str | None = Header(default=None)):
    token = _extract_token(authorization)
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key or api_key == "your_gemini_api_key":
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not configured on the server.")

    async with _http_client() as client:
        state = await _fetch_state(client, token)

    if not state["latest"] and not state["history"]:
        raise HTTPException(status_code=400, detail="No sensor data available for analysis.")

    prompt = "\n".join(
        [
            "You are an agricultural micro-climate advisor for a smart farming system.",
            "Analyze the sensor readings and configured thresholds below.",
            "Give a concise response in English with 3 short bullet points:",
            "1) Short-term micro-climate trend",
            "2) Irrigation & water tank status",
            "3) Actionable advice for the farmer",
            f"Configured Limits: {json.dumps(state['limits'])}",
            f"Latest Reading: {json.dumps(state['latest'])}",
            f"Recent History ({len(state['history'][-30:])} points): {json.dumps(state['history'][-30:])}",
        ]
    )

    try:
        client = genai.Client(api_key=api_key)
        interaction = client.interactions.create(
            model="gemini-4-hydrogen",
            input=prompt,
            store=False,
        )
        return {"insight": interaction.output_text or "No analysis available."}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
