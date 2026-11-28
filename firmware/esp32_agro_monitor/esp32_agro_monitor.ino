/*
 * IoT Micro-Climate Monitoring System with Data Logging & Precision Agriculture
 * ESP32 + Cloud Firestore (green-life-1436d / greenlife-db via Firebase_ESP_Client)
 */

#include <WiFi.h>
#include <Preferences.h>
#include <Wire.h>
#include <time.h>
#include <math.h>
#include <DHT.h>
#include <Adafruit_BMP280.h>
#include <Firebase_ESP_Client.h>
#include "addons/TokenHelper.h"

// ---------------- USER CONFIG ----------------
#define WIFI_SSID             "YOUR_WIFI_SSID"
#define WIFI_PASSWORD         "YOUR_WIFI_PASSWORD"

#define API_KEY               "YOUR_FIREBASE_WEB_API_KEY"
#define FIREBASE_PROJECT_ID   "green-life-1436d"
#define FIRESTORE_DATABASE_ID "greenlife-db"
#define DEVICE_EMAIL          "esp32@greenlife.local"
#define DEVICE_PASSWORD       "YOUR_DEVICE_PASSWORD"

// ---------------- PIN MAPPING ----------------
#define PIN_DHT         4
#define DHT_TYPE        DHT11
#define PIN_SOIL        34   // Soil Moisture AO (ADC1)
#define PIN_UV          35   // HW-837 (GUVA-S12SD) SIO (ADC1)
#define PIN_TRIG        5
#define PIN_ECHO        18   // Use 1k + 2k voltage divider (5V -> 3.3V)
#define PIN_RELAY       26
#define PIN_BUZZER      25

// Set false if your relay module triggers HIGH instead of LOW
const bool RELAY_ACTIVE_LOW = true;

// Sensor calibration constants (measure in dry air vs submerged water)
const int SOIL_DRY_RAW = 3400;
const int SOIL_WET_RAW = 1400;
const float TANK_EMPTY_CM = 25.0;
const float TANK_FULL_CM  = 4.0;

// ---------------- OBJECTS ----------------
DHT dht(PIN_DHT, DHT_TYPE);
Adafruit_BMP280 bmp;
Preferences prefs;

FirebaseData fbdo;
FirebaseAuth auth;
FirebaseConfig fbConfig;

// ---------------- STATE ----------------
struct Limits {
  int soilMin = 35;
  int soilMax = 70;
  float tempMax = 38.0;
  int tankMinPercent = 15;
  int logIntervalMin = 10;
} limits;

String pumpMode = "auto";
bool manualOn = false;
bool pumpOn = false;

float lastTemp = 28.0, lastHum = 65.0, lastPres = 1010.0, lastLight = 0.0;
int lastSoil = 50, lastTank = 50;
int lastSoilRaw = 0, lastUvRaw = 0;
float lastTankCm = 0.0;
bool dhtOk = false, usOk = false;

unsigned long lastSensorMs = 0;
unsigned long lastLatestPushMs = 0;
unsigned long lastSyncDownMs = 0;
unsigned long lastHistoryMs = 0;
bool firstHistorySent = false;
bool fbReady = false;
bool bmpOk = false;

void setRelay(bool on) {
  pumpOn = on;
  int level = RELAY_ACTIVE_LOW ? (on ? LOW : HIGH) : (on ? HIGH : LOW);
  digitalWrite(PIN_RELAY, level);
}

void saveLimitsLocally() {
  prefs.begin("agro", false);
  prefs.putInt("soilMin", limits.soilMin);
  prefs.putInt("soilMax", limits.soilMax);
  prefs.putFloat("tempMax", limits.tempMax);
  prefs.putInt("tankMin", limits.tankMinPercent);
  prefs.putInt("logMin", limits.logIntervalMin);
  prefs.end();
}

void loadLimitsLocally() {
  prefs.begin("agro", true);
  limits.soilMin = prefs.getInt("soilMin", 35);
  limits.soilMax = prefs.getInt("soilMax", 70);
  limits.tempMax = prefs.getFloat("tempMax", 38.0);
  limits.tankMinPercent = prefs.getInt("tankMin", 15);
  limits.logIntervalMin = prefs.getInt("logMin", 10);
  prefs.end();
}

int readSoilPercent() {
  lastSoilRaw = analogRead(PIN_SOIL);
  int pct = map(lastSoilRaw, SOIL_DRY_RAW, SOIL_WET_RAW, 0, 100);
  return constrain(pct, 0, 100);
}

int readTankPercent() {
  digitalWrite(PIN_TRIG, LOW);
  delayMicroseconds(2);
  digitalWrite(PIN_TRIG, HIGH);
  delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);

  long duration = pulseIn(PIN_ECHO, HIGH, 30000);
  if (duration == 0) {
    usOk = false;
    return lastTank;
  }
  usOk = true;
  lastTankCm = (duration * 0.0343f) / 2.0f;
  int pct = (int)(((TANK_EMPTY_CM - lastTankCm) / (TANK_EMPTY_CM - TANK_FULL_CM)) * 100.0f);
  return constrain(pct, 0, 100);
}

uint64_t getEpochMs() {
  time_t now = time(nullptr);
  if (now < 1700000000) {
    unsigned long ms = millis();
    return (uint64_t)(ms > 0 ? ms : 1);
  }
  return (uint64_t)now * 1000ULL;
}

bool readFirestoreNumber(FirebaseJson &json, const String &field, double &outVal) {
  FirebaseJsonData d;
  if (json.get(d, "fields/" + field + "/doubleValue") && d.success) {
    outVal = d.doubleValue;
    return true;
  }
  if (json.get(d, "fields/" + field + "/integerValue") && d.success) {
    outVal = d.stringValue.length() > 0 ? d.stringValue.toDouble() : (double)d.intValue;
    return true;
  }
  return false;
}

void readSensorsAndAct() {
  float t = dht.readTemperature();
  float h = dht.readHumidity();
  dhtOk = (!isnan(t) && !isnan(h));
  if (dhtOk && t >= -40.0f && t <= 100.0f) lastTemp = t;
  if (dhtOk && h >= 0.0f && h <= 100.0f) lastHum = h;

  if (bmpOk) {
    float p = bmp.readPressure() / 100.0f;
    if (!isnan(p) && p >= 300.0f && p <= 1200.0f) lastPres = p;
  }

  lastUvRaw = analogRead(PIN_UV);
  lastLight = (lastUvRaw / 4095.0f) * 3300.0f; // HW-837 analog output in mV

  lastSoil = readSoilPercent();
  lastTank = readTankPercent();

  bool dryTank = (lastTank < limits.tankMinPercent);
  bool highTemp = (lastTemp > limits.tempMax);

  if (dryTank) {
    setRelay(false); // Dry-run lock overrides both auto & manual
  } else if (pumpMode == "manual") {
    setRelay(manualOn);
  } else {
    if (lastSoil < limits.soilMin) setRelay(true);
    else if (lastSoil >= limits.soilMax) setRelay(false);
  }

  digitalWrite(PIN_BUZZER, (dryTank || highTemp) ? HIGH : LOW);
}

void printSerialReport() {
  Serial.println("---------- SYSTEM STATUS ----------");
  Serial.printf("WiFi     : %s", WiFi.status() == WL_CONNECTED ? "CONNECTED" : "DISCONNECTED");
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf(" (IP: %s | RSSI: %d dBm)", WiFi.localIP().toString().c_str(), WiFi.RSSI());
  }
  Serial.printf(" | Firebase: %s\n", (fbReady && Firebase.ready()) ? "READY" : "NOT READY");
  Serial.printf("DHT11    : [%s] Temp: %.1f C | Humidity: %.0f %%\n",
                dhtOk ? "OK" : "NOT CONNECTED", lastTemp, lastHum);
  Serial.printf("BMP280   : [%s] Pressure: %.1f hPa\n",
                bmpOk ? "OK" : "NOT CONNECTED", lastPres);
  Serial.printf("HW-837   : [ADC] UV: %.0f mV (raw: %d)\n", lastLight, lastUvRaw);
  Serial.printf("Soil     : [%s] Moisture: %d %% (raw: %d)\n",
                (lastSoilRaw > 0 && lastSoilRaw < 4095) ? "OK" : "CHECK WIRING", lastSoil, lastSoilRaw);
  Serial.printf("HC-SR04  : [%s] Tank: %d %% (dist: %.1f cm)\n",
                usOk ? "OK" : "NO ECHO", lastTank, lastTankCm);
  Serial.printf("Pump     : %s (Mode: %s)\n", pumpOn ? "ON" : "OFF", pumpMode.c_str());
}

void syncFromFirestore() {
  if (!Firebase.ready()) return;

  if (Firebase.Firestore.getDocument(&fbdo, FIREBASE_PROJECT_ID, FIRESTORE_DATABASE_ID, "config/limits")) {
    FirebaseJson json;
    json.setJsonData(fbdo.payload());
    double val = 0;
    bool changed = false;
    if (readFirestoreNumber(json, "soilMin", val) && (int)val != limits.soilMin) {
      limits.soilMin = (int)val;
      changed = true;
    }
    if (readFirestoreNumber(json, "soilMax", val) && (int)val != limits.soilMax) {
      limits.soilMax = (int)val;
      changed = true;
    }
    if (readFirestoreNumber(json, "tempMax", val) && fabsf((float)val - limits.tempMax) > 0.01f) {
      limits.tempMax = (float)val;
      changed = true;
    }
    if (readFirestoreNumber(json, "tankMinPercent", val) && (int)val != limits.tankMinPercent) {
      limits.tankMinPercent = (int)val;
      changed = true;
    }
    if (readFirestoreNumber(json, "logIntervalMin", val) && (int)val >= 1 && (int)val != limits.logIntervalMin) {
      limits.logIntervalMin = (int)val;
      changed = true;
    }
    if (changed) saveLimitsLocally();
  }

  if (Firebase.Firestore.getDocument(&fbdo, FIREBASE_PROJECT_ID, FIRESTORE_DATABASE_ID, "control/pump")) {
    FirebaseJson json;
    json.setJsonData(fbdo.payload());
    FirebaseJsonData d;
    if (json.get(d, "fields/mode/stringValue") && d.success) pumpMode = d.stringValue;
    if (json.get(d, "fields/manualOn/booleanValue") && d.success) manualOn = d.boolValue;
  }
}

void pushLatestToFirestore() {
  if (!Firebase.ready()) return;
  FirebaseJson content;
  content.set("fields/temp/doubleValue", lastTemp);
  content.set("fields/humidity/doubleValue", lastHum);
  content.set("fields/pressure/doubleValue", lastPres);
  content.set("fields/light/doubleValue", lastLight);
  content.set("fields/soilMoisture/integerValue", String(lastSoil));
  content.set("fields/tankPercent/integerValue", String(lastTank));
  content.set("fields/pumpOn/booleanValue", pumpOn);
  content.set("fields/updatedAt/integerValue", String((unsigned long long)getEpochMs()));

  if (!Firebase.Firestore.patchDocument(
        &fbdo,
        FIREBASE_PROJECT_ID,
        FIRESTORE_DATABASE_ID,
        "telemetry/latest",
        content.raw(),
        "temp,humidity,pressure,light,soilMoisture,tankPercent,pumpOn,updatedAt")) {
    Serial.printf("[Firestore] telemetry/latest error: %s\n", fbdo.errorReason().c_str());
  }
}

void pushHistoryToFirestore() {
  if (!Firebase.ready()) return;
  FirebaseJson content;
  content.set("fields/temp/doubleValue", lastTemp);
  content.set("fields/humidity/doubleValue", lastHum);
  content.set("fields/pressure/doubleValue", lastPres);
  content.set("fields/light/doubleValue", lastLight);
  content.set("fields/soilMoisture/integerValue", String(lastSoil));
  content.set("fields/tankPercent/integerValue", String(lastTank));
  content.set("fields/ts/integerValue", String((unsigned long long)getEpochMs()));

  if (!Firebase.Firestore.createDocument(
        &fbdo,
        FIREBASE_PROJECT_ID,
        FIRESTORE_DATABASE_ID,
        "history",
        content.raw())) {
    Serial.printf("[Firestore] history error: %s\n", fbdo.errorReason().c_str());
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_TRIG, OUTPUT);
  pinMode(PIN_ECHO, INPUT);
  pinMode(PIN_RELAY, OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);
  setRelay(false);

  loadLimitsLocally();

  Wire.begin(21, 22);
  dht.begin();
  bmpOk = bmp.begin(0x76) || bmp.begin(0x77);
  Serial.println("\n=== BOOT HARDWARE CHECK ===");
  Serial.printf("BMP280 : %s\n", bmpOk ? "CONNECTED" : "NOT FOUND (0x76/0x77)");

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("WiFi   : Connecting");
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) {
    delay(300);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("WiFi   : CONNECTED, IP = ");
    Serial.println(WiFi.localIP());
    configTime(0, 0, "pool.ntp.org", "time.nist.gov");
    fbConfig.api_key = API_KEY;
    auth.user.email = DEVICE_EMAIL;
    auth.user.password = DEVICE_PASSWORD;
    fbConfig.token_status_callback = tokenStatusCallback;
    Firebase.begin(&fbConfig, &auth);
    Firebase.reconnectWiFi(true);
    fbReady = true;
  } else {
    Serial.println("WiFi   : FAILED / OFFLINE (running local mode)");
  }
}

void loop() {
  unsigned long now = millis();

  if (now - lastSensorMs >= 2000) {
    lastSensorMs = now;
    readSensorsAndAct();
  }

  if (now - lastLatestPushMs >= 5000) {
    lastLatestPushMs = now;
    printSerialReport();
    if (fbReady && WiFi.status() == WL_CONNECTED) {
      pushLatestToFirestore();
    }
  }

  if (fbReady && WiFi.status() == WL_CONNECTED) {
    if (now - lastSyncDownMs >= 3000) {
      lastSyncDownMs = now;
      syncFromFirestore();
    }
    unsigned long intervalMs = (unsigned long)limits.logIntervalMin * 60000UL;
    if ((!firstHistorySent && time(nullptr) >= 1700000000) || (now - lastHistoryMs >= intervalMs)) {
      firstHistorySent = true;
      lastHistoryMs = now;
      pushHistoryToFirestore();
    }
  }
}
