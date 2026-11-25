import test from "node:test";
import assert from "node:assert/strict";
import { computePumpDecision, toCsv, validateLimits } from "../lib/irrigation.mjs";

const limits = {
  soilMin: 35,
  soilMax: 70,
  tempMax: 38,
  tankMinPercent: 15,
  logIntervalMin: 10,
};

test("dry-run lock blocks pump even in manual ON mode", () => {
  const res = computePumpDecision(20, 10, limits, { mode: "manual", manualOn: true }, true);
  assert.equal(res.pumpOn, false);
  assert.equal(res.dryTank, true);
  assert.equal(res.reason, "dry_tank_lock");
});

test("auto hysteresis turns ON below soilMin, holds between min/max, turns OFF at soilMax", () => {
  const auto = { mode: "auto", manualOn: false };
  assert.equal(computePumpDecision(30, 80, limits, auto, false).pumpOn, true);
  assert.equal(computePumpDecision(50, 80, limits, auto, true).pumpOn, true);
  assert.equal(computePumpDecision(50, 80, limits, auto, false).pumpOn, false);
  assert.equal(computePumpDecision(70, 80, limits, auto, true).pumpOn, false);
});

test("validateLimits rejects inverted soilMin/soilMax", () => {
  assert.ok(validateLimits({ ...limits, soilMin: 75, soilMax: 70 }));
  assert.equal(validateLimits(limits), null);
});

test("toCsv formats header and rows cleanly", () => {
  const csv = toCsv([
    {
      ts: 1764060000000,
      temp: 29.4,
      humidity: 68,
      pressure: 1009.2,
      light: 420,
      soilMoisture: 42,
      tankPercent: 75,
    },
  ]);
  const lines = csv.split("\n");
  assert.equal(lines.length, 2);
  assert.ok(lines[0].startsWith("timestamp_iso,temp_c,"));
  assert.ok(lines[1].endsWith(",29.4,68,1009.2,420,42,75"));
});
