"""Flet 1.x web dashboard wired to FastAPI + Firebase Auth + Firestore (greenlife-db)."""

from __future__ import annotations

import asyncio
import os
import random
from datetime import datetime

import flet as ft
import flet.canvas as cv
import httpx

API_BASE_URL = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

DARK_TEXT = "#0f172a"
MUTED_TEXT = "#475569"
BORDER_COLOR = "#cbd5e1"
CARD_BG = "#ffffff"
PAGE_BG = "#f8fafc"
SURFACE_BG = "#f1f5f9"


def _stat_card(
    icon: str,
    icon_color: str,
    label: str,
    value_ctrl: ft.Text,
) -> ft.Container:
    return ft.Container(
        col={"xs": 6, "sm": 4, "lg": 2},
        padding=14,
        border_radius=12,
        bgcolor=CARD_BG,
        border=ft.Border.all(1, "#e2e8f0"),
        content=ft.Column(
            spacing=8,
            controls=[
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    controls=[
                        ft.Text(label, size=12, color=MUTED_TEXT),
                        ft.Icon(icon, size=18, color=icon_color),
                    ],
                ),
                value_ctrl,
            ],
        ),
    )


def _limit_input(label: str, default: str) -> ft.TextField:
    return ft.TextField(
        label=label,
        value=default,
        width=130,
        dense=True,
        color=DARK_TEXT,
        bgcolor=CARD_BG,
        border_color=BORDER_COLOR,
        focused_border_color="#059669",
        label_style=ft.TextStyle(color=MUTED_TEXT, size=12),
    )


def _chart_legend() -> ft.Row:
    def _dot(color: str, text: str) -> ft.Row:
        return ft.Row(
            spacing=4,
            controls=[
                ft.Container(width=10, height=10, bgcolor=color, border_radius=5),
                ft.Text(text, size=12, color=MUTED_TEXT),
            ],
        )

    return ft.Row(
        spacing=16,
        alignment=ft.MainAxisAlignment.CENTER,
        controls=[
            _dot("#059669", "Soil (%)"),
            _dot("#e11d48", "Temp (C)"),
            _dot("#0284c7", "Humidity (%)"),
        ],
    )


def _line_chart(history: list[dict]) -> ft.Control:
    points = history[-24:]
    w, h = 760, 200
    pad_l, pad_r, pad_t, pad_b = 36, 16, 14, 28
    plot_w = w - pad_l - pad_r
    plot_h = h - pad_t - pad_b

    shapes: list[cv.Shape] = []
    grid_paint = ft.Paint(color="#e2e8f0", stroke_width=1)
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = pad_t + plot_h * (1 - frac)
        shapes.append(cv.Line(pad_l, y, w - pad_r, y, paint=grid_paint))
        shapes.append(
            cv.Text(
                4,
                y - 7,
                f"{int(frac * 100)}",
                style=ft.TextStyle(size=10, color=MUTED_TEXT),
                max_width=30,
            )
        )

    n = len(points)
    step_x = plot_w / max(n - 1, 1)

    series = [
        ("soilMoisture", "#059669"),
        ("temp", "#e11d48"),
        ("humidity", "#0284c7"),
    ]
    for key, color in series:
        line_paint = ft.Paint(color=color, stroke_width=2.5)
        dot_paint = ft.Paint(color=color)
        coords: list[tuple[float, float]] = []
        for idx, pt in enumerate(points):
            val = max(0.0, min(100.0, float(pt.get(key, 0))))
            x = pad_l + (plot_w / 2 if n == 1 else idx * step_x)
            y = pad_t + plot_h * (1 - val / 100.0)
            coords.append((x, y))
        for i in range(len(coords) - 1):
            shapes.append(cv.Line(coords[i][0], coords[i][1], coords[i + 1][0], coords[i + 1][1], paint=line_paint))
        for cx, cy in coords:
            shapes.append(cv.Circle(cx, cy, 3, paint=dot_paint))

    label_step = max(1, n // 6)
    for idx in range(0, n, label_step):
        x = pad_l + (plot_w / 2 if n == 1 else idx * step_x)
        ts_str = datetime.fromtimestamp(points[idx]["ts"] / 1000).strftime("%H:%M")
        shapes.append(
            cv.Text(
                max(pad_l, x - 14),
                h - 18,
                ts_str,
                style=ft.TextStyle(size=10, color=MUTED_TEXT),
                max_width=45,
            )
        )

    return ft.Column(
        spacing=8,
        controls=[
            ft.Container(
                padding=8,
                border_radius=10,
                bgcolor=SURFACE_BG,
                content=ft.Row(
                    scroll=ft.ScrollMode.AUTO,
                    controls=[cv.Canvas(shapes=shapes, width=w, height=h)],
                ),
            ),
            _chart_legend(),
        ],
    )


def _bar_chart(history: list[dict]) -> ft.Control:
    points = history[-16:]
    groups: list[ft.Control] = []
    for pt in points:
        ts_str = datetime.fromtimestamp(pt["ts"] / 1000).strftime("%H:%M")

        def _bar(val: float, color: str, tip: str) -> ft.Container:
            clamped = max(0.0, min(100.0, val))
            return ft.Container(
                width=10,
                height=max(4, int((clamped / 100.0) * 140)),
                bgcolor=color,
                border_radius=3,
                tooltip=f"{ts_str} {tip}: {val:.1f}",
            )

        groups.append(
            ft.Column(
                spacing=4,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.END,
                controls=[
                    ft.Row(
                        spacing=2,
                        vertical_alignment=ft.CrossAxisAlignment.END,
                        controls=[
                            _bar(float(pt.get("soilMoisture", 0)), "#059669", "Soil %"),
                            _bar(float(pt.get("temp", 0)), "#e11d48", "Temp C"),
                            _bar(float(pt.get("humidity", 0)), "#0284c7", "Humidity %"),
                        ],
                    ),
                    ft.Text(ts_str, size=9, color=MUTED_TEXT),
                ],
            )
        )

    return ft.Column(
        spacing=8,
        controls=[
            ft.Container(
                height=190,
                padding=12,
                border_radius=10,
                bgcolor=SURFACE_BG,
                content=ft.Row(
                    spacing=12,
                    scroll=ft.ScrollMode.AUTO,
                    vertical_alignment=ft.CrossAxisAlignment.END,
                    controls=groups,
                ),
            ),
            _chart_legend(),
        ],
    )


def _build_chart(history: list[dict], chart_type: str) -> ft.Control:
    if not history:
        return ft.Container(
            height=180,
            alignment=ft.Alignment(0, 0),
            bgcolor=SURFACE_BG,
            border_radius=10,
            content=ft.Text("No historical data available for the chart.", color=MUTED_TEXT),
        )
    return _line_chart(history) if chart_type == "line" else _bar_chart(history)


async def main(page: ft.Page) -> None:
    page.title = "Green Life — IoT Micro-Climate & Precision Agriculture"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.bgcolor = PAGE_BG
    page.padding = 20
    page.scroll = ft.ScrollMode.AUTO

    session: dict = {"token": None, "email": None, "is_signup": False}
    state_cache: dict = {}
    chart_type = "line"
    running = True

    def on_disconnect(_e) -> None:
        nonlocal running
        running = False

    page.on_disconnect = on_disconnect

    # ---------- Auth UI Controls ----------
    auth_title = ft.Text("Green Life Monitor Sign In", size=20, weight=ft.FontWeight.BOLD, color=DARK_TEXT)
    auth_sub = ft.Text(
        "Sign in with your Firebase Email/Password account.",
        size=13,
        color=MUTED_TEXT,
    )
    tf_email = ft.TextField(
        label="Email",
        width=340,
        keyboard_type=ft.KeyboardType.EMAIL,
        color=DARK_TEXT,
        bgcolor=CARD_BG,
        border_color=BORDER_COLOR,
        focused_border_color="#059669",
        label_style=ft.TextStyle(color=MUTED_TEXT),
    )
    tf_password = ft.TextField(
        label="Password (min 6 chars)",
        width=340,
        password=True,
        can_reveal_password=True,
        color=DARK_TEXT,
        bgcolor=CARD_BG,
        border_color=BORDER_COLOR,
        focused_border_color="#059669",
        label_style=ft.TextStyle(color=MUTED_TEXT),
    )
    auth_error = ft.Text("", size=12, color="#dc2626")
    auth_submit_btn = ft.Button(content="Sign In", width=340, bgcolor="#059669", color="#ffffff")
    auth_switch_btn = ft.OutlinedButton(
        content="Need a new account? Create Account",
        width=340,
        style=ft.ButtonStyle(color="#047857"),
    )

    # ---------- Dashboard UI Controls ----------
    subtitle_txt = ft.Text("Waiting for ESP32 / sensor data...", size=12, color=MUTED_TEXT)
    pump_badge = ft.Container(
        padding=ft.Padding.symmetric(horizontal=12, vertical=6),
        border_radius=20,
        bgcolor="#f1f5f9",
        content=ft.Text("Pump: OFF (AUTO)", size=12, weight=ft.FontWeight.BOLD, color=DARK_TEXT),
    )
    alert_banner = ft.Container(
        visible=False,
        padding=12,
        border_radius=10,
        bgcolor="#fef3c7",
        border=ft.Border.all(1, "#fcd34d"),
        content=ft.Text("", color="#78350f", size=13),
    )

    val_temp = ft.Text("--", size=20, weight=ft.FontWeight.BOLD, color=DARK_TEXT)
    val_hum = ft.Text("--", size=20, weight=ft.FontWeight.BOLD, color=DARK_TEXT)
    val_pres = ft.Text("--", size=20, weight=ft.FontWeight.BOLD, color=DARK_TEXT)
    val_light = ft.Text("--", size=20, weight=ft.FontWeight.BOLD, color=DARK_TEXT)
    val_soil = ft.Text("--", size=20, weight=ft.FontWeight.BOLD, color=DARK_TEXT)
    val_tank = ft.Text("--", size=20, weight=ft.FontWeight.BOLD, color=DARK_TEXT)

    mode_info = ft.Text("Auto Hysteresis (35% - 70%)", size=13, weight=ft.FontWeight.W_600, color=DARK_TEXT)
    decision_info = ft.Text("Status: waiting", size=12, color=MUTED_TEXT)
    auto_btn = ft.Button(content="Auto Mode", expand=True, bgcolor="#0f172a", color="#ffffff")
    manual_btn = ft.OutlinedButton(content="Manual Mode", expand=True, style=ft.ButtonStyle(color=DARK_TEXT))
    pump_toggle_btn = ft.Button(
        content="Turn ON",
        icon=ft.Icons.POWER_SETTINGS_NEW,
        disabled=True,
        bgcolor="#059669",
        color="#ffffff",
    )

    tf_soil_min = _limit_input("Soil Min (%)", "35")
    tf_soil_max = _limit_input("Soil Max (%)", "70")
    tf_temp_max = _limit_input("Temp Max (C)", "38")
    tf_tank_min = _limit_input("Tank Min (%)", "15")
    tf_log_min = _limit_input("Log Interval (m)", "10")
    limits_status = ft.Text("", size=12, color="#047857")

    line_tab_btn = ft.Button(content="Line", icon=ft.Icons.SHOW_CHART, bgcolor="#0f172a", color="#ffffff")
    bar_tab_btn = ft.OutlinedButton(content="Bar", icon=ft.Icons.BAR_CHART, style=ft.ButtonStyle(color=DARK_TEXT))
    chart_holder = ft.Container(content=_build_chart([], chart_type))
    export_btn = ft.OutlinedButton(
        content="Export CSV",
        icon=ft.Icons.DOWNLOAD,
        style=ft.ButtonStyle(color=DARK_TEXT),
    )
    ai_output = ft.Text(
        "Click 'Generate AI Insight' to analyze recorded sensor data.",
        size=13,
        color=DARK_TEXT,
    )
    ai_btn = ft.Button(
        content="Generate AI Insight",
        icon=ft.Icons.AUTO_AWESOME,
        bgcolor="#4f46e5",
        color="#ffffff",
    )

    limits_dirty = False

    def mark_limits_dirty(_e) -> None:
        nonlocal limits_dirty
        limits_dirty = True

    for tf in (tf_soil_min, tf_soil_max, tf_temp_max, tf_tank_min, tf_log_min):
        tf.on_change = mark_limits_dirty

    def auth_headers() -> dict[str, str]:
        return {"Authorization": f"Bearer {session['token']}"}

    def render_state(data: dict) -> None:
        state_cache.clear()
        state_cache.update(data)
        latest = data.get("latest")
        limits = data.get("limits", {})
        control = data.get("control", {"mode": "auto", "manualOn": False})
        decision = data.get("decision") or {}
        history = data.get("history", [])

        user_prefix = f"{session['email']} • " if session.get("email") else ""
        if latest:
            dt = datetime.fromtimestamp(latest["updatedAt"] / 1000).strftime("%Y-%m-%d %H:%M:%S")
            subtitle_txt.value = f"{user_prefix}Last updated: {dt}"
            val_temp.value = f"{latest['temp']:.1f} °C"
            val_hum.value = f"{latest['humidity']:.0f} %"
            val_pres.value = f"{latest['pressure']:.0f} hPa"
            val_light.value = f"{latest['light']:.0f} lx"
            val_soil.value = f"{latest['soilMoisture']:.0f} %"
            val_tank.value = f"{latest['tankPercent']:.0f} %"
        else:
            subtitle_txt.value = f"{user_prefix}Waiting for ESP32 / sensor data..."

        pump_on = bool(latest and latest["pumpOn"])
        mode = str(control.get("mode", "auto"))
        pump_badge.bgcolor = "#d1fae5" if pump_on else "#f1f5f9"
        pump_badge.content = ft.Text(
            f"Pump: {'ON' if pump_on else 'OFF'} ({mode.upper()})",
            size=12,
            weight=ft.FontWeight.BOLD,
            color="#065f46" if pump_on else DARK_TEXT,
        )

        if mode == "auto":
            auto_btn.bgcolor = "#0f172a"
            auto_btn.color = "#ffffff"
            manual_btn.style = ft.ButtonStyle(color=DARK_TEXT)
        else:
            auto_btn.bgcolor = "#e2e8f0"
            auto_btn.color = DARK_TEXT
            manual_btn.style = ft.ButtonStyle(color="#ffffff", bgcolor="#0f172a")

        dry_tank = bool(latest and latest["tankPercent"] < limits.get("tankMinPercent", 15))
        high_temp = bool(latest and latest["temp"] > limits.get("tempMax", 38))
        alerts = []
        if dry_tank:
            alerts.append("Low water tank! Pump is locked for dry-run protection.")
        if high_temp:
            alerts.append(f"Temperature exceeded limit ({limits.get('tempMax')} °C)!")
        alert_banner.visible = bool(alerts)
        alert_banner.content.value = " ".join(alerts)

        if mode == "auto":
            mode_info.value = f"Auto Hysteresis ({limits.get('soilMin')}% – {limits.get('soilMax')}%)"
        else:
            mode_info.value = "Manual Pump Switch"
        decision_info.value = f"Status: {decision.get('reason', 'waiting')}"

        manual_on = bool(control.get("manualOn"))
        pump_toggle_btn.disabled = mode == "auto" or dry_tank
        pump_toggle_btn.content = "Turn OFF" if manual_on else "Turn ON"
        pump_toggle_btn.bgcolor = "#e11d48" if manual_on else "#059669"

        if not limits_dirty and limits:
            tf_soil_min.value = str(limits["soilMin"])
            tf_soil_max.value = str(limits["soilMax"])
            tf_temp_max.value = str(limits["tempMax"])
            tf_tank_min.value = str(limits["tankMinPercent"])
            tf_log_min.value = str(limits["logIntervalMin"])

        export_btn.url = f"{API_BASE_URL}/api/export.csv?token={session['token']}"
        export_btn.disabled = len(history) == 0
        chart_holder.content = _build_chart(history, chart_type)
        page.update()

    async def fetch_state() -> None:
        if not session["token"]:
            return
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(f"{API_BASE_URL}/api/state", headers=auth_headers())
            if resp.status_code == 401:
                session["token"] = None
                auth_error.value = "Session expired. Please sign in again."
                show_view()
                return
            if resp.status_code == 200:
                render_state(resp.json())
        except Exception as exc:
            subtitle_txt.value = f"Backend unreachable ({API_BASE_URL}): {exc}"
            page.update()

    async def handle_auth(_e) -> None:
        auth_error.value = ""
        auth_submit_btn.disabled = True
        page.update()
        endpoint = "/api/auth/signup" if session["is_signup"] else "/api/auth/login"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{API_BASE_URL}{endpoint}",
                    json={"email": tf_email.value.strip(), "password": tf_password.value},
                )
            if resp.status_code == 200:
                data = resp.json()
                session["token"] = data["idToken"]
                session["email"] = data["email"]
                tf_password.value = ""
                show_view()
                await fetch_state()
            else:
                auth_error.value = resp.json().get("detail", "Authentication failed.")
        except Exception as exc:
            auth_error.value = f"Cannot connect to FastAPI ({API_BASE_URL}): {exc}"
        finally:
            auth_submit_btn.disabled = False
            page.update()

    def toggle_auth_mode(_e) -> None:
        session["is_signup"] = not session["is_signup"]
        auth_title.value = "Create Green Life Account" if session["is_signup"] else "Green Life Monitor Sign In"
        auth_submit_btn.content = "Create Account" if session["is_signup"] else "Sign In"
        auth_switch_btn.content = (
            "Already have an account? Sign In"
            if session["is_signup"]
            else "Need a new account? Create Account"
        )
        auth_error.value = ""
        page.update()

    def handle_logout(_e) -> None:
        session["token"] = None
        session["email"] = None
        show_view()

    async def set_control(mode: str, manual_on: bool) -> None:
        async with httpx.AsyncClient(timeout=8.0) as client:
            await client.put(
                f"{API_BASE_URL}/api/control",
                headers=auth_headers(),
                json={"mode": mode, "manualOn": manual_on},
            )
        await fetch_state()

    async def save_limits(_e) -> None:
        nonlocal limits_dirty
        try:
            payload = {
                "soilMin": float(tf_soil_min.value),
                "soilMax": float(tf_soil_max.value),
                "tempMax": float(tf_temp_max.value),
                "tankMinPercent": float(tf_tank_min.value),
                "logIntervalMin": int(float(tf_log_min.value)),
            }
        except ValueError:
            limits_status.value = "All fields must be valid numbers."
            limits_status.color = "#dc2626"
            page.update()
            return

        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.put(f"{API_BASE_URL}/api/limits", headers=auth_headers(), json=payload)
        if resp.status_code == 200:
            limits_dirty = False
            limits_status.value = "Limits saved successfully."
            limits_status.color = "#047857"
            await fetch_state()
        else:
            limits_status.value = resp.json().get("detail", "Validation failed.")
            limits_status.color = "#dc2626"
            page.update()

    async def simulate_reading(_e) -> None:
        sample = {
            "temp": round(random.uniform(26.0, 35.5), 1),
            "humidity": round(random.uniform(55.0, 82.0), 0),
            "pressure": round(random.uniform(1006.0, 1014.0), 1),
            "light": round(random.uniform(200.0, 850.0), 0),
            "soilMoisture": round(random.uniform(25.0, 78.0), 0),
            "tankPercent": round(random.uniform(12.0, 95.0), 0),
        }
        async with httpx.AsyncClient(timeout=8.0) as client:
            await client.post(f"{API_BASE_URL}/api/telemetry", headers=auth_headers(), json=sample)
        await fetch_state()

    async def run_predict(_e) -> None:
        ai_btn.disabled = True
        ai_output.value = "Analyzing..."
        page.update()
        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                resp = await client.post(f"{API_BASE_URL}/api/predict", headers=auth_headers())
            if resp.status_code == 200:
                ai_output.value = resp.json().get("insight", "No analysis available.")
            else:
                ai_output.value = f"Error: {resp.json().get('detail', resp.text)}"
        except Exception as exc:
            ai_output.value = f"Failed to reach AI endpoint: {exc}"
        finally:
            ai_btn.disabled = False
            page.update()

    def set_chart_type(kind: str):
        def _handler(_e) -> None:
            nonlocal chart_type
            chart_type = kind
            if kind == "line":
                line_tab_btn.bgcolor = "#0f172a"
                line_tab_btn.color = "#ffffff"
                bar_tab_btn.style = ft.ButtonStyle(color=DARK_TEXT)
            else:
                line_tab_btn.bgcolor = "#e2e8f0"
                line_tab_btn.color = DARK_TEXT
                bar_tab_btn.style = ft.ButtonStyle(color="#ffffff", bgcolor="#0f172a")
            chart_holder.content = _build_chart(state_cache.get("history", []), chart_type)
            page.update()

        return _handler

    auth_submit_btn.on_click = lambda _e: page.run_task(handle_auth, _e)
    auth_switch_btn.on_click = toggle_auth_mode
    auto_btn.on_click = lambda _e: page.run_task(set_control, "auto", bool(state_cache.get("control", {}).get("manualOn")))
    manual_btn.on_click = lambda _e: page.run_task(set_control, "manual", bool(state_cache.get("control", {}).get("manualOn")))
    pump_toggle_btn.on_click = lambda _e: page.run_task(set_control, "manual", not bool(state_cache.get("control", {}).get("manualOn")))
    line_tab_btn.on_click = set_chart_type("line")
    bar_tab_btn.on_click = set_chart_type("bar")
    ai_btn.on_click = lambda _e: page.run_task(run_predict, _e)

    auth_card = ft.Container(
        alignment=ft.Alignment(0, 0),
        padding=40,
        content=ft.Container(
            width=400,
            padding=24,
            border_radius=16,
            bgcolor=CARD_BG,
            border=ft.Border.all(1, "#e2e8f0"),
            content=ft.Column(
                spacing=14,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    auth_title,
                    auth_sub,
                    tf_email,
                    tf_password,
                    auth_error,
                    auth_submit_btn,
                    auth_switch_btn,
                ],
            ),
        ),
    )

    dashboard_view = ft.Column(
        spacing=16,
        controls=[
            ft.Container(
                padding=16,
                border_radius=14,
                bgcolor=CARD_BG,
                border=ft.Border.all(1, "#e2e8f0"),
                content=ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    wrap=True,
                    controls=[
                        ft.Column(
                            spacing=2,
                            controls=[
                                ft.Text(
                                    "Green Life — IoT Micro-Climate & Precision Agriculture",
                                    size=20,
                                    weight=ft.FontWeight.BOLD,
                                    color=DARK_TEXT,
                                ),
                                subtitle_txt,
                            ],
                        ),
                        ft.Row(
                            spacing=8,
                            wrap=True,
                            controls=[
                                pump_badge,
                                ft.OutlinedButton(
                                    content="Simulate Reading",
                                    style=ft.ButtonStyle(color=DARK_TEXT),
                                    on_click=lambda _e: page.run_task(simulate_reading, _e),
                                ),
                                ft.OutlinedButton(
                                    content="Refresh",
                                    icon=ft.Icons.REFRESH,
                                    style=ft.ButtonStyle(color=DARK_TEXT),
                                    on_click=lambda _e: page.run_task(fetch_state),
                                ),
                                ft.OutlinedButton(
                                    content="Logout",
                                    icon=ft.Icons.LOGOUT,
                                    style=ft.ButtonStyle(color=DARK_TEXT),
                                    on_click=handle_logout,
                                ),
                            ],
                        ),
                    ],
                ),
            ),
            alert_banner,
            ft.ResponsiveRow(
                spacing=10,
                run_spacing=10,
                controls=[
                    _stat_card(ft.Icons.THERMOSTAT, "#e11d48", "Temperature", val_temp),
                    _stat_card(ft.Icons.WATER_DROP, "#0284c7", "Humidity", val_hum),
                    _stat_card(ft.Icons.AIR, "#6366f1", "Pressure", val_pres),
                    _stat_card(ft.Icons.WB_SUNNY, "#d97706", "Light", val_light),
                    _stat_card(ft.Icons.GRASS, "#059669", "Soil Moisture", val_soil),
                    _stat_card(ft.Icons.WAVES, "#0891b2", "Water Tank", val_tank),
                ],
            ),
            ft.ResponsiveRow(
                spacing=16,
                run_spacing=16,
                controls=[
                    ft.Container(
                        col={"xs": 12, "md": 6},
                        padding=16,
                        border_radius=14,
                        bgcolor=CARD_BG,
                        border=ft.Border.all(1, "#e2e8f0"),
                        content=ft.Column(
                            spacing=12,
                            controls=[
                                ft.Text("Motor / Pump Control", size=16, weight=ft.FontWeight.W_600, color=DARK_TEXT),
                                ft.Row(spacing=8, controls=[auto_btn, manual_btn]),
                                ft.Container(
                                    padding=12,
                                    border_radius=10,
                                    bgcolor=SURFACE_BG,
                                    content=ft.Row(
                                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                        controls=[
                                            ft.Column(spacing=2, controls=[mode_info, decision_info]),
                                            pump_toggle_btn,
                                        ],
                                    ),
                                ),
                            ],
                        ),
                    ),
                    ft.Container(
                        col={"xs": 12, "md": 6},
                        padding=16,
                        border_radius=14,
                        bgcolor=CARD_BG,
                        border=ft.Border.all(1, "#e2e8f0"),
                        content=ft.Column(
                            spacing=12,
                            controls=[
                                ft.Text("Min / Max Threshold Limits", size=16, weight=ft.FontWeight.W_600, color=DARK_TEXT),
                                ft.Row(wrap=True, spacing=10, controls=[tf_soil_min, tf_soil_max, tf_temp_max, tf_tank_min, tf_log_min]),
                                ft.Row(
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                    controls=[
                                        limits_status,
                                        ft.Button(
                                            content="Save Limits",
                                            bgcolor="#059669",
                                            color="#ffffff",
                                            on_click=lambda _e: page.run_task(save_limits, _e),
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ),
                ],
            ),
            ft.Container(
                padding=16,
                border_radius=14,
                bgcolor=CARD_BG,
                border=ft.Border.all(1, "#e2e8f0"),
                content=ft.Column(
                    spacing=12,
                    controls=[
                        ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            wrap=True,
                            controls=[
                                ft.Text("Historical Sensor Trends", size=16, weight=ft.FontWeight.W_600, color=DARK_TEXT),
                                ft.Row(spacing=8, controls=[line_tab_btn, bar_tab_btn, export_btn]),
                            ],
                        ),
                        chart_holder,
                    ],
                ),
            ),
            ft.Container(
                padding=16,
                border_radius=14,
                bgcolor=CARD_BG,
                border=ft.Border.all(1, "#e2e8f0"),
                content=ft.Column(
                    spacing=10,
                    controls=[
                        ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            controls=[
                                ft.Text("Gemini Climate & Irrigation Insight", size=16, weight=ft.FontWeight.W_600, color=DARK_TEXT),
                                ai_btn,
                            ],
                        ),
                        ft.Container(
                            padding=12,
                            border_radius=10,
                            bgcolor=SURFACE_BG,
                            content=ai_output,
                        ),
                    ],
                ),
            ),
        ],
    )

    def show_view() -> None:
        is_authed = bool(session["token"])
        auth_card.visible = not is_authed
        dashboard_view.visible = is_authed
        page.update()

    page.add(auth_card, dashboard_view)
    show_view()

    async def poll_loop() -> None:
        while running:
            if session["token"]:
                await fetch_state()
            await asyncio.sleep(3)

    page.run_task(poll_loop)


if __name__ == "__main__":
    view = ft.AppView.FLET_APP if os.environ.get("FLET_VIEW") == "desktop" else ft.AppView.WEB_BROWSER
    ft.run(main, view=view, port=int(os.environ.get("FLET_PORT", "8550")))
