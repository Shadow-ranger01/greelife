# IoT Micro-Climate Monitoring System & Precision Agriculture

**Stack:** Next.js (App Router) + Firebase Realtime Database + Vercel + Gemini API + ESP32

## 1. Hardware Wiring (ESP32)

| Component | ESP32 Pin / Bus | Notes |
|---|---|---|
| BMP280, BH1750, SSD1306 OLED | `GPIO 21 (SDA)`, `GPIO 22 (SCL)` | Shared I²C (`3.3V`) |
| DHT11 | `GPIO 4` | Data pin (`3.3V`) |
| Soil Moisture Sensor | `GPIO 34` | Analog ADC1 input |
| HC-SR04 Ultrasonic | `Trig: GPIO 5`, `Echo: GPIO 18` | Use `1kΩ + 2kΩ` voltage divider on `Echo` (`5V -> 3.3V`) |
| 1-Channel 5V Relay + Pump | `GPIO 26` | Power relay & pump from external `5V` adapter, share `GND` with ESP32 |
| 3.3V Active Buzzer | `GPIO 25` | Alerts on low tank or high temp |

## 2. Firebase Setup
1. Create a Firebase project and enable **Realtime Database** + **Authentication (Email/Password)**.
2. Create two users in Authentication: one admin account (for web login) and one device account (for ESP32).
3. Apply [database.rules.json](./database.rules.json) to your Realtime Database Rules.

## 3. Web Dashboard (Local & Vercel)
```bash
cd web
cp .env.example .env.local   # Fill Firebase keys + GEMINI_API_KEY
npm install
npm test
npm run dev
```
Deploy `web/` to **Vercel** and add the exact same variables from `.env.example` in Vercel Project Settings → Environment Variables.
