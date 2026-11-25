/**
 * Pure irrigation & export helpers shared by the UI and unit tests.
 */

export function computePumpDecision(
  soilMoisture,
  tankPercent,
  limits,
  control,
  currentlyOn = false
) {
  const dryTank = tankPercent < limits.tankMinPercent;
  if (dryTank) {
    return { pumpOn: false, dryTank: true, reason: "dry_tank_lock" };
  }

  if (control.mode === "manual") {
    return {
      pumpOn: Boolean(control.manualOn),
      dryTank: false,
      reason: control.manualOn ? "manual_on" : "manual_off",
    };
  }

  // Hysteresis prevents rapid relay chatter near a single threshold.
  if (soilMoisture < limits.soilMin) {
    return { pumpOn: true, dryTank: false, reason: "auto_below_min" };
  }
  if (soilMoisture >= limits.soilMax) {
    return { pumpOn: false, dryTank: false, reason: "auto_above_max" };
  }

  return {
    pumpOn: Boolean(currentlyOn),
    dryTank: false,
    reason: "auto_hysteresis_hold",
  };
}

export function validateLimits(limits) {
  const { soilMin, soilMax, tempMax, tankMinPercent, logIntervalMin } = limits;
  const nums = [soilMin, soilMax, tempMax, tankMinPercent, logIntervalMin];
  if (nums.some((n) => typeof n !== "number" || Number.isNaN(n))) {
    return "All limit fields must be valid numbers.";
  }
  if (soilMin < 0 || soilMax > 100 || soilMin >= soilMax) {
    return "Soil Min must be between 0 and 100 and strictly less than Soil Max.";
  }
  if (tankMinPercent < 0 || tankMinPercent > 95) {
    return "Tank Min (%) must be between 0 and 95.";
  }
  if (tempMax < 0 || tempMax > 70) {
    return "Max Temperature (°C) must be between 0 and 70.";
  }
  if (logIntervalMin < 1 || logIntervalMin > 1440) {
    return "Log Interval must be between 1 and 1440 minutes.";
  }
  return null;
}

export function toCsv(rows) {
  const headers = [
    "timestamp_iso",
    "temp_c",
    "humidity_pct",
    "pressure_hpa",
    "light_lux",
    "soil_moisture_pct",
    "tank_pct",
  ];
  const lines = [headers.join(",")];
  for (const r of rows) {
    const iso = new Date(r.ts).toISOString();
    lines.push(
      [
        iso,
        r.temp,
        r.humidity,
        r.pressure,
        r.light,
        r.soilMoisture,
        r.tankPercent,
      ].join(",")
    );
  }
  return lines.join("\n");
}
