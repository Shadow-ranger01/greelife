"""Flet 1.x web dashboard wired to FastAPI + Firebase Auth + Firestore (greenlife-db)."""

from __future__ import annotations

import asyncio
import os
import random
from datetime import datetime

import flet as ft
import httpx

API_BASE_URL = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def _stat_card(icon: str, icon_color: str, label: str, value_ctrl: ft.Text) -> ft.Container:
    return ft.Container(
        expand=True,
        padding=14,
        border_radius=12,
        bgcolor=ft.Colors.WHITE,
        border=ft.Border.all(1, ft.Colors.BLUE_GREY_100),
        content=ft.Column(
            spacing=8,
            controls=[
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    controls=[
                        ft.Text(label, size=12, color=ft.Colors.BLUE_GREY_500),
                        ft.Icon(icon, size=18, color=icon_color),
                    ],
                ),
                value_ctrl,
            ],
        ),
    )


def _bar_chart(history: list[dict], metric: str) -> ft.Control:
    if not history:
        return ft.Container(
            height=180,
            alignment=ft.Alignment(0, 0),
            content=ft.Text("No historical data available yet.", color=ft.Colors.BLUE_GREY_400),
        )

    points = history[-20:]
    max_val = max(max(float(p[metric]) for p in points), 1.0)
    color_map = {
        "soilMoisture": ft.Colors.GREEN_600,
        "temp": ft.Colors.RED_500,
        "humidity": ft.Colors.LIGHT_BLUE_600,
    }
    bar_color = color_map.get(metric, ft.Colors.INDIGO_500)

    bars: list[ft.Control] = []
    for p in points:
        val = float(p[metric])
        height = max(6, int((val / max_val) * 130))
        ts_str = datetime.fromtimestamp(p["ts"] / 1000).strftime("%H:%M")
        bars.append(
            ft.Column(
                spacing=4,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.END,
                controls=[
                    ft.Text(f"{val:.0f}", size=10, color=ft.Colors.BLUE_GREY_600),
                    ft.Container(
                        width=18,
                        height=height,
                        bgcolor=bar_color,
                        border_radius=4,
                        tooltip=f"{ts_str}: {val}",
                    ),
                    ft.Text(ts_str, size=9, color=ft.Colors.BLUE_GREY_400),
                ],
            )
        )

    return ft.Container(
        height=190,
        padding=10,
        border_radius=10,
        bgcolor=ft.Colors.BLUE_GREY_50,
        content=ft.Row(
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
            vertical_alignment=ft.CrossAxisAlignment.END,
            controls=bars,
        ),
    )


async def main(page: ft.Page) -> None:
    page.title = "Green Life — IoT Micro-Climate & Precision Agriculture"
    page.bgcolor = ft.Colors.BLUE_GREY_50
    page.padding = 20
    page.scroll = ft.ScrollMode.AUTO

    session: dict = {"token": None, "email": None, "is_signup": False}
    state_cache: dict = {}
    selected_metric = "soilMoisture"
    running = True

    def on_disconnect(_e) -> None:
        nonlocal running
        running = False

    page.on_disconnect = on_disconnect

    # ---------- Auth UI Controls ----------
    auth_title = ft.Text("Green Life Monitor Sign In", size=20, weight=ft.FontWeight.BOLD)
    tf_email = ft.TextField(label="Email", width=340, keyboard_type=ft.KeyboardType.EMAIL)
    tf_password = ft.TextField(label="Password (min 6 chars)", width=340, password=True, can_reveal_password=True)
    auth_error = ft.Text("", size=12, color=ft.Colors.RED_700)
    auth_submit_btn = ft.Button(content="Sign In", width=340, bgcolor=ft.Colors.GREEN_700, color=ft.Colors.WHITE)
    auth_switch_btn = ft.OutlinedButton(content="Need a new account? Create Account", width=340)

    # ---------- Dashboard UI Controls ----------
    subtitle_txt = ft.Text("Waiting for ESP32 / sensor data...", size=12, color=ft.Colors.BLUE_GREY_500)
    pump_badge = ft.Container(
        padding=ft.Padding.symmetric(horizontal=12, vertical=6),
        border_radius=20,
        bgcolor=ft.Colors.BLUE_GREY_100,
        content=ft.Text("Pump: OFF (AUTO)", size=12, weight=ft.FontWeight.BOLD),
    )
    alert_banner = ft.Container(
        visible=False,
        padding=12,
        border_radius=10,
        bgcolor=ft.Colors.AMBER_100,
        border=ft.Border.all(1, ft.Colors.AMBER_400),
        content=ft.Text("", color=ft.Colors.AMBER_900, size=13),
    )

    val_temp = ft.Text("--", size=20, weight=ft.FontWeight.BOLD)
    val_hum = ft.Text("--", size=20, weight=ft.FontWeight.BOLD)
    val_pres = ft.Text("--", size=20, weight=ft.FontWeight.BOLD)
    val_light = ft.Text("--", size=20, weight=ft.FontWeight.BOLD)
    val_soil = ft.Text("--", size=20, weight=ft.FontWeight.BOLD)
    val_tank = ft.Text("--", size=20, weight=ft.FontWeight.BOLD)

    mode_info = ft.Text("Auto Hysteresis (35% - 70%)", size=13, weight=ft.FontWeight.W_600)
    decision_info = ft.Text("Status: waiting", size=12, color=ft.Colors.BLUE_GREY_500)
    auto_btn = ft.OutlinedButton(content="Auto Mode", expand=True)
    manual_btn = ft.OutlinedButton(content="Manual Mode", expand=True)
    pump_toggle_btn = ft.Button(content="Turn ON", icon=ft.Icons.POWER_SETTINGS_NEW, disabled=True)

    tf_soil_min = ft.TextField(label="Soil Min (%)", value="35", width=130, dense=True)
    tf_soil_max = ft.TextField(label="Soil Max (%)", value="70", width=130, dense=True)
    tf_temp_max = ft.TextField(label="Temp Max (C)", value="38", width=130, dense=True)
    tf_tank_min = ft.TextField(label="Tank Min (%)", value="15", width=130, dense=True)
    tf_log_min = ft.TextField(label="Log Interval (m)", value="10", width=130, dense=True)
    limits_status = ft.Text("", size=12, color=ft.Colors.GREEN_700)

    chart_holder = ft.Container(content=_bar_chart([], selected_metric))
    export_btn = ft.OutlinedButton(content="Export CSV", icon=ft.Icons.DOWNLOAD)
    ai_output = ft.Text("Click 'Generate AI Insight' to analyze recorded sensor history.", size=13)
    ai_btn = ft.Button(content="Generate AI Insight", icon=ft.Icons.AUTO_AWESOME)

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
            val_temp.value = f"{latest['temp']:.1f} C"
            val_hum.value = f"{latest['humidity']:.0f} %"
            val_pres.value = f"{latest['pressure']:.0f} hPa"
            val_light.value = f"{latest['light']:.0f} lx"
            val_soil.value = f"{latest['soilMoisture']:.0f} %"
            val_tank.value = f"{latest['tankPercent']:.0f} %"
        else:
            subtitle_txt.value = f"{user_prefix}Waiting for ESP32 / sensor data..."

        pump_on = bool(latest and latest["pumpOn"])
        mode_str = str(control.get("mode", "auto")).upper()
        pump_badge.bgcolor = ft.Colors.GREEN_100 if pump_on else ft.Colors.BLUE_GREY_100
        pump_badge.content = ft.Text(
            f"Pump: {'ON' if pump_on else 'OFF'} ({mode_str})",
            size=12,
            weight=ft.FontWeight.BOLD,
            color=ft.Colors.GREEN_900 if pump_on else ft.Colors.BLUE_GREY_800,
        )

        dry_tank = bool(latest and latest["tankPercent"] < limits.get("tankMinPercent", 15))
        high_temp = bool(latest and latest["temp"] > limits.get("tempMax", 38))
        alerts = []
        if dry_tank:
            alerts.append("Low water tank! Pump is locked for dry-run protection.")
        if high_temp:
            alerts.append(f"Temperature exceeded limit ({limits.get('tempMax')} C)!")
        alert_banner.visible = bool(alerts)
        alert_banner.content.value = " ".join(alerts)

        if control.get("mode") == "auto":
            mode_info.value = f"Auto Hysteresis ({limits.get('soilMin')}% - {limits.get('soilMax')}%)"
        else:
            mode_info.value = "Manual Pump Switch"
        decision_info.value = f"Status: {decision.get('reason', 'waiting')}"

        manual_on = bool(control.get("manualOn"))
        pump_toggle_btn.disabled = control.get("mode") == "auto" or dry_tank
        pump_toggle_btn.content = "Turn OFF" if manual_on else "Turn ON"
        pump_toggle_btn.bgcolor = ft.Colors.RED_600 if manual_on else ft.Colors.GREEN_700
        pump_toggle_btn.color = ft.Colors.WHITE

        if not limits_dirty and limits:
            tf_soil_min.value = str(limits["soilMin"])
            tf_soil_max.value = str(limits["soilMax"])
            tf_temp_max.value = str(limits["tempMax"])
            tf_tank_min.value = str(limits["tankMinPercent"])
            tf_log_min.value = str(limits["logIntervalMin"])

        export_btn.url = f"{API_BASE_URL}/api/export.csv?token={session['token']}"
        chart_holder.content = _bar_chart(history, selected_metric)
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
            limits_status.color = ft.Colors.RED_700
            page.update()
            return

        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.put(f"{API_BASE_URL}/api/limits", headers=auth_headers(), json=payload)
        if resp.status_code == 200:
            limits_dirty = False
            limits_status.value = "Limits saved to Firestore."
            limits_status.color = ft.Colors.GREEN_700
            await fetch_state()
        else:
            limits_status.value = resp.json().get("detail", "Validation failed.")
            limits_status.color = ft.Colors.RED_700
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

    def switch_metric(metric: str):
        def _handler(_e) -> None:
            nonlocal selected_metric
            selected_metric = metric
            chart_holder.content = _bar_chart(state_cache.get("history", []), selected_metric)
            page.update()
        return _handler

    auth_submit_btn.on_click = lambda _e: page.run_task(handle_auth, _e)
    auth_switch_btn.on_click = toggle_auth_mode
    auto_btn.on_click = lambda _e: page.run_task(set_control, "auto", bool(state_cache.get("control", {}).get("manualOn")))
    manual_btn.on_click = lambda _e: page.run_task(set_control, "manual", bool(state_cache.get("control", {}).get("manualOn")))
    pump_toggle_btn.on_click = lambda _e: page.run_task(set_control, "manual", not bool(state_cache.get("control", {}).get("manualOn")))
    ai_btn.on_click = lambda _e: page.run_task(run_predict, _e)

    auth_card = ft.Container(
        alignment=ft.Alignment(0, 0),
        padding=40,
        content=ft.Container(
            width=400,
            padding=24,
            border_radius=16,
            bgcolor=ft.Colors.WHITE,
            border=ft.Border.all(1, ft.Colors.BLUE_GREY_100),
            content=ft.Column(
                spacing=14,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    auth_title,
                    ft.Text("Sign in with your Firebase Email/Password account.", size=12, color=ft.Colors.BLUE_GREY_500),
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
                bgcolor=ft.Colors.WHITE,
                border=ft.Border.all(1, ft.Colors.BLUE_GREY_100),
                content=ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    wrap=True,
                    controls=[
                        ft.Column(
                            spacing=2,
                            controls=[
                                ft.Text("Green Life — IoT Micro-Climate & Precision Agriculture", size=20, weight=ft.FontWeight.BOLD),
                                subtitle_txt,
                            ],
                        ),
                        ft.Row(
                            spacing=8,
                            controls=[
                                pump_badge,
                                ft.OutlinedButton(content="Simulate Reading", on_click=lambda _e: page.run_task(simulate_reading, _e)),
                                ft.OutlinedButton(content="Refresh", icon=ft.Icons.REFRESH, on_click=lambda _e: page.run_task(fetch_state)),
                                ft.OutlinedButton(content="Logout", icon=ft.Icons.LOGOUT, on_click=handle_logout),
                            ],
                        ),
                    ],
                ),
            ),
            alert_banner,
            ft.Row(
                spacing=10,
                wrap=True,
                controls=[
                    _stat_card(ft.Icons.THERMOSTAT, ft.Colors.RED_500, "Temperature", val_temp),
                    _stat_card(ft.Icons.WATER_DROP, ft.Colors.LIGHT_BLUE_600, "Humidity", val_hum),
                    _stat_card(ft.Icons.AIR, ft.Colors.INDIGO_500, "Pressure", val_pres),
                    _stat_card(ft.Icons.WB_SUNNY, ft.Colors.AMBER_600, "Light", val_light),
                    _stat_card(ft.Icons.GRASS, ft.Colors.GREEN_600, "Soil Moisture", val_soil),
                    _stat_card(ft.Icons.WAVES, ft.Colors.CYAN_700, "Water Tank", val_tank),
                ],
            ),
            ft.Row(
                spacing=16,
                wrap=True,
                vertical_alignment=ft.CrossAxisAlignment.START,
                controls=[
                    ft.Container(
                        expand=1,
                        min_width=320,
                        padding=16,
                        border_radius=14,
                        bgcolor=ft.Colors.WHITE,
                        border=ft.Border.all(1, ft.Colors.BLUE_GREY_100),
                        content=ft.Column(
                            spacing=12,
                            controls=[
                                ft.Text("Motor / Pump Control", size=16, weight=ft.FontWeight.W_600),
                                ft.Row(spacing=8, controls=[auto_btn, manual_btn]),
                                ft.Container(
                                    padding=12,
                                    border_radius=10,
                                    bgcolor=ft.Colors.BLUE_GREY_50,
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
                        expand=1,
                        min_width=340,
                        padding=16,
                        border_radius=14,
                        bgcolor=ft.Colors.WHITE,
                        border=ft.Border.all(1, ft.Colors.BLUE_GREY_100),
                        content=ft.Column(
                            spacing=12,
                            controls=[
                                ft.Text("Min / Max Threshold Limits", size=16, weight=ft.FontWeight.W_600),
                                ft.Row(wrap=True, spacing=10, controls=[tf_soil_min, tf_soil_max, tf_temp_max, tf_tank_min, tf_log_min]),
                                ft.Row(
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                    controls=[
                                        limits_status,
                                        ft.Button(content="Save Limits", bgcolor=ft.Colors.GREEN_700, color=ft.Colors.WHITE, on_click=lambda _e: page.run_task(save_limits, _e)),
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
                bgcolor=ft.Colors.WHITE,
                border=ft.Border.all(1, ft.Colors.BLUE_GREY_100),
                content=ft.Column(
                    spacing=12,
                    controls=[
                        ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            wrap=True,
                            controls=[
                                ft.Text("Historical Sensor Trends", size=16, weight=ft.FontWeight.W_600),
                                ft.Row(
                                    spacing=8,
                                    controls=[
                                        ft.OutlinedButton(content="Soil %", on_click=switch_metric("soilMoisture")),
                                        ft.OutlinedButton(content="Temp C", on_click=switch_metric("temp")),
                                        ft.OutlinedButton(content="Humidity %", on_click=switch_metric("humidity")),
                                        export_btn,
                                    ],
                                ),
                            ],
                        ),
                        chart_holder,
                    ],
                ),
            ),
            ft.Container(
                padding=16,
                border_radius=14,
                bgcolor=ft.Colors.WHITE,
                border=ft.Border.all(1, ft.Colors.BLUE_GREY_100),
                content=ft.Column(
                    spacing=10,
                    controls=[
                        ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            controls=[
                                ft.Text("Gemini Climate & Irrigation Insight", size=16, weight=ft.FontWeight.W_600),
                                ai_btn,
                            ],
                        ),
                        ft.Container(
                            padding=12,
                            border_radius=10,
                            bgcolor=ft.Colors.BLUE_GREY_50,
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
