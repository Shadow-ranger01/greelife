/*
 * IoT Micro-Climate Monitoring System with Data Logging & Precision Agriculture
 * ESP32 + Firebase Realtime Database (Direct HTTPS via Firebase_ESP_Client)
 */

#include <WiFi.h>
#include <Preferences.h>
#include <Wire.h>
#include <time.h>
#include <DHT.h>
#include <Adafruit_BMP280.h>
#include <BH1750.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Firebase_ESP_Client.h>
#include "addons/TokenHelper.h"
#include "addons/RTDBHelper.h"

// ---------------- USER CONFIG ----------------
#define WIFI_SSID       "YOUR_WIFI_SSID"
#define WIFI_PASSWORD   "YOUR_WIFI_PASSWORD"

#define API_KEY         "YOUR_FIREBASE_WEB_API_KEY"
#define DATABASE_URL    "YOUR_PROJECT-default-rtdb.firebaseio.com" // without https://
#define DEVICE_EMAIL    "esp32@yourdomain.com"
#define DEVICE_PASSWORD "YOUR_DEVICE_PASSWORD"

// ---------------- PIN MAPPING ----------------
#define PIN_DHT         4
#define DHT_TYPE        DHT11
#define PIN_SOIL        34   // ADC1 input
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
BH1750 lightMeter;
Adafruit_SSD1306 display(128, 64, &Wire, -1);
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

unsigned long lastSensorMs = 0;
unsigned long lastLatestPushMs = 0;
unsigned long lastSyncDownMs = 0;
unsigned long lastHistoryMs = 0;
bool fbReady = false;

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
  int raw = analogRead(PIN_SOIL);
  int pct = map(raw, SOIL_DRY_RAW, SOIL_WET_RAW, 0, 100);
  return constrain(pct, 0, 100);
}

int readTankPercent() {
  digitalWrite(PIN_TRIG, LOW);
  delayMicroseconds(2);
  digitalWrite(PIN_TRIG, HIGH);
  delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);

  long duration = pulseIn(PIN_ECHO, HIGH, 30000);
  if (duration == 0) return lastTank;
  float distCm = (duration * 0.0343f) / 2.0f;
  int pct = (int)(((TANK_EMPTY_CM - distCm) / (TANK_EMPTY_CM - TANK_FULL_CM)) * 100.0f);
  return constrain(pct, 0, 100);
}

uint64_t getEpochMs() {
  time_t now = time(nullptr);
  if (now < 1700000000) return (uint64_t)millis();
  return (uint64_t)now * 1000ULL;
}

void readSensorsAndAct() {
  float t = dht.readTemperature();
  float h = dht.readHumidity();
  if (!isnan(t)) lastTemp = t;
  if (!isnan(h)) lastHum = h;

  float p = bmp.readPressure() / 100.0f;
  if (!isnan(p) && p > 300.0f) lastPres = p;

  float lx = lightMeter.readLightLevel();
  if (lx >= 0.0f) lastLight = lx;

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

  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.printf("T:%.1fC H:%.0f%% P:%.0f\n", lastTemp, lastHum, lastPres);
  display.printf("Light: %.0f lx\n", lastLight);
  display.printf("Soil : %d%% (Min %d)\n", lastSoil, limits.soilMin);
  display.printf("Tank : %d%% %s\n", lastTank, dryTank ? "[LOW!]" : "");
  display.printf("Pump : %s (%s)\n", pumpOn ? "ON" : "OFF", pumpMode.c_str());
  display.display();
}

void syncFromFirebase() {
  if (!Firebase.ready()) return;

  if (Firebase.RTDB.getJSON(&fbdo, "/config/limits")) {
    FirebaseJson &json = fbdo.jsonObject();
    FirebaseJsonData d;
    bool changed = false;
    if (json.get(d, "soilMin") && d.intValue != limits.soilMin) { limits.soilMin = d.intValue; changed = true; }
    if (json.get(d, "soilMax") && d.intValue != limits.soilMax) { limits.soilMax = d.intValue; changed = true; }
    if (json.get(d, "tempMax")) { limits.tempMax = d.floatValue; changed = true; }
    if (json.get(d, "tankMinPercent")) { limits.tankMinPercent = d.intValue; changed = true; }
    if (json.get(d, "logIntervalMin") && d.intValue >= 1) { limits.logIntervalMin = d.intValue; }
    if (changed) saveLimitsLocally();
  }

  if (Firebase.RTDB.getJSON(&fbdo, "/control/pump")) {
    FirebaseJson &json = fbdo.jsonObject();
    FirebaseJsonData d;
    if (json.get(d, "mode")) pumpMode = d.stringValue;
    if (json.get(d, "manualOn")) manualOn = d.boolValue;
  }
}

void pushLatestToFirebase() {
  if (!Firebase.ready()) return;
  FirebaseJson json;
  json.set("temp", lastTemp);
  json.set("humidity", lastHum);
  json.set("pressure", lastPres);
  json.set("light", lastLight);
  json.set("soilMoisture", lastSoil);
  json.set("tankPercent", lastTank);
  json.set("pumpOn", pumpOn);
  json.set("updatedAt", (double)getEpochMs());
  Firebase.RTDB.setJSON(&fbdo, "/sensors/latest", &json);
}

void pushHistoryToFirebase() {
  if (!Firebase.ready()) return;
  FirebaseJson json;
  json.set("temp", lastTemp);
  json.set("humidity", lastHum);
  json.set("pressure", lastPres);
  json.set("light", lastLight);
  json.set("soilMoisture", lastSoil);
  json.set("tankPercent", lastTank);
  json.set("ts", (double)getEpochMs());
  Firebase.RTDB.pushJSON(&fbdo, "/sensors/history", &json);
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
  bmp.begin(0x76);
  lightMeter.begin();
  display.begin(SSD1306_SWITCHCAPVCC, 0x3C);

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting WiFi");
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) {
    delay(300);
    Serial.print(".");
  }

  if (WiFi.status() == WL_CONNECTED) {
    configTime(0, 0, "pool.ntp.org", "time.nist.gov");
    fbConfig.api_key = API_KEY;
    fbConfig.database_url = DATABASE_URL;
    auth.user.email = DEVICE_EMAIL;
    auth.user.password = DEVICE_PASSWORD;
    fbConfig.token_status_callback = tokenStatusCallback;
    Firebase.begin(&fbConfig, &auth);
    Firebase.reconnectWiFi(true);
    fbReady = true;
  }
}

void loop() {
  unsigned long now = millis();

  if (now - lastSensorMs >= 2000) {
    lastSensorMs = now;
    readSensorsAndAct();
  }

  if (fbReady && WiFi.status() == WL_CONNECTED) {
    if (now - lastSyncDownMs >= 3000) {
      lastSyncDownMs = now;
      syncFromFirebase();
    }
    if (now - lastLatestPushMs >= 5000) {
      lastLatestPushMs = now;
      pushLatestToFirebase();
    }
    unsigned long intervalMs = (unsigned long)limits.logIntervalMin * 60000UL;
    if (now - lastHistoryMs >= intervalMs) {
      lastHistoryMs = now;
      pushHistoryToFirebase();
    }
  }
}
