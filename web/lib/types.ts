export interface SensorReading {
  temp: number;
  humidity: number;
  pressure: number;
  light: number;
  soilMoisture: number;
  tankPercent: number;
  pumpOn: boolean;
  updatedAt: number;
}

export interface HistoryPoint {
  id: string;
  temp: number;
  humidity: number;
  pressure: number;
  light: number;
  soilMoisture: number;
  tankPercent: number;
  ts: number;
}

export interface SystemLimits {
  soilMin: number;
  soilMax: number;
  tempMax: number;
  tankMinPercent: number;
  logIntervalMin: number;
}

export interface PumpControl {
  mode: "auto" | "manual";
  manualOn: boolean;
}

export const DEFAULT_LIMITS: SystemLimits = {
  soilMin: 35,
  soilMax: 70,
  tempMax: 38,
  tankMinPercent: 15,
  logIntervalMin: 10,
};

export const DEFAULT_CONTROL: PumpControl = {
  mode: "auto",
  manualOn: false,
};
