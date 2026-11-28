# Green Life — IoT Micro-Climate Monitoring & Precision Agriculture

**Stack:** Next.js (App Router) + Firebase Authentication (Email/Password & Google Sign-In) + Cloud Firestore (`greenlife-db`) + Vercel + Gemini API + ESP32

## 1. Hardware Wiring & Connection Diagram (ESP32)

```mermaid
flowchart LR
    subgraph PSU["External 5V Power Supply"]
        V5["+5V Rail"]
        GND["Common GND"]
    end

    subgraph I2C["I2C Bus (3.3V)"]
        BMP["BMP280 Pressure"]
        BH["BH1750 Light"]
        OLED["SSD1306 OLED 0.96"]
    end

    subgraph SENS["Digital & Analog Sensors"]
        DHT["DHT11 Temp & Humidity"]
        SOIL["Soil Moisture Sensor"]
        US["HC-SR04 Ultrasonic"]
    end

    ESP["ESP32 DevKitC"]

    subgraph OUT["Actuators & Alerts"]
        BUZ["3.3V Active Buzzer"]
        REL["5V Relay Module"]
        PUMP["5V Pump + 1N4007 Diode"]
    end

    V5 -->|"5V (VIN)"| ESP
    V5 -->|"VCC"| US
    V5 -->|"DC+ & COM"| REL
    GND -->|"GND"| ESP
    GND -->|"GND"| US
    GND -->|"DC-"| REL
    GND -->|"Pump -"| PUMP

    ESP -->|"3V3 & GND"| I2C
    ESP <-->|"GPIO 21 SDA / GPIO 22 SCL"| I2C

    ESP -->|"3V3 & GND"| DHT
    DHT -->|"DATA -> GPIO 4"| ESP

    ESP -->|"3V3 & GND"| SOIL
    SOIL -->|"SIG -> GPIO 34"| ESP

    ESP -->|"TRIG -> GPIO 5"| US
    US -->|"ECHO -> 1k/2k Divider -> GPIO 18"| ESP

    ESP -->|"GPIO 25 & GND"| BUZ
    ESP -->|"IN -> GPIO 26"| REL
    REL -->|"NO -> Pump +"| PUMP
```

> **Safety Notes:** Use a `1kΩ + 2kΩ` voltage divider on `HC-SR04 ECHO` (`5V -> 3.3V`), place a reverse-biased `1N4007` flyback diode across the pump terminals, and power the relay/pump from an external `5V` supply with common `GND`.

| Component | ESP32 Pin / Bus | Power & Notes |
|---|---|---|
| BMP280, BH1750, SSD1306 OLED | `GPIO 21 (SDA)`, `GPIO 22 (SCL)` | Shared I²C (`3.3V`, `GND`) |
| DHT11 | `GPIO 4` | `3.3V`, `GND` (`10kΩ` pull-up on `DATA` if bare sensor) |
| Soil Moisture Sensor | `GPIO 34` | Analog ADC1 input (`3.3V`, `GND`) |
| HC-SR04 Ultrasonic | `Trig: GPIO 5`, `Echo: GPIO 18` | `5V`, `GND`; use `1kΩ + 2kΩ` voltage divider on `Echo` |
| 1-Channel 5V Relay + Pump | `GPIO 26` | External `5V` + common `GND`; add `1N4007` flyback diode |
| 3.3V Active Buzzer | `GPIO 25` | `GPIO 25 (+)` and `GND (-)` |

## 2. Firebase Project (`green-life-1436d`)
- Web App registered: `greenlife-web` (`1:214170718127:web:3071937fe7026ea7e3695a`)
- Cloud Firestore database: `greenlife-db` (Enterprise Native, `asia-southeast1`)
- Rules & Indexes: [firestore.rules](./firestore.rules) & [firestore.indexes.json](./firestore.indexes.json)
- Auth providers: Email/Password & Google Sign-In (create one Email/Password user for ESP32, and sign in via Email/Password or Google on the web app).

## 3. Web Dashboard (Local & Vercel)
```bash
cd web
cp .env.example .env.local   # Add Firebase keys + GEMINI_API_KEY
npm install
npm test
npm run dev
```
Deploy `web/` to **Vercel**, add the same environment variables from `web/.env.example`, and add your Vercel domain under Firebase Console → Authentication → Settings → Authorized domains (needed for Google Sign-In).
