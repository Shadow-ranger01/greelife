"use client";

import { useEffect, useId, useMemo, useState, type FormEvent } from "react";
import {
  createUserWithEmailAndPassword,
  onAuthStateChanged,
  signInWithEmailAndPassword,
  signInWithPopup,
  signOut,
  type User,
} from "firebase/auth";
import {
  collection,
  doc,
  limit,
  onSnapshot,
  orderBy,
  query,
  setDoc,
} from "firebase/firestore";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  AlertTriangle,
  BarChart3,
  Droplets,
  Download,
  Eye,
  EyeOff,
  Gauge,
  LineChart as LineChartIcon,
  LogOut,
  Power,
  Sparkles,
  Sun,
  Thermometer,
  Waves,
  Wind,
} from "lucide-react";
import { auth, db, googleProvider, isFirebaseConfigured } from "@/lib/firebase";
import { computePumpDecision, toCsv, validateLimits } from "@/lib/irrigation.mjs";
import {
  DEFAULT_CONTROL,
  DEFAULT_LIMITS,
  type HistoryPoint,
  type PumpControl,
  type SensorReading,
  type SystemLimits,
} from "@/lib/types";

export default function DashboardPage() {
  const [user, setUser] = useState<User | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [isSignUp, setIsSignUp] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [authPending, setAuthPending] = useState(false);

  const [latest, setLatest] = useState<SensorReading | null>(null);
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [limits, setLimits] = useState<SystemLimits>(DEFAULT_LIMITS);
  const [limitDraft, setLimitDraft] = useState<SystemLimits>(DEFAULT_LIMITS);
  const [control, setControl] = useState<PumpControl>(DEFAULT_CONTROL);

  const [chartType, setChartType] = useState<"line" | "bar">("line");
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [aiInsight, setAiInsight] = useState<string | null>(null);
  const [aiLoading, setAiLoading] = useState(false);

  useEffect(() => {
    if (!auth) {
      setAuthReady(true);
      return;
    }
    return onAuthStateChanged(auth, (u) => {
      setUser(u);
      setAuthReady(true);
    });
  }, []);

  useEffect(() => {
    if (!db || !user) return;

    const unsubLatest = onSnapshot(doc(db, "telemetry", "latest"), (snap) => {
      if (snap.exists()) setLatest(snap.data() as SensorReading);
    });

    const historyQuery = query(
      collection(db, "history"),
      orderBy("ts", "desc"),
      limit(40)
    );
    const unsubHistory = onSnapshot(historyQuery, (snap) => {
      const rows = snap.docs
        .map((d) => ({ id: d.id, ...(d.data() as Omit<HistoryPoint, "id">) }))
        .sort((a, b) => a.ts - b.ts);
      setHistory(rows);
    });

    const unsubLimits = onSnapshot(doc(db, "config", "limits"), (snap) => {
      if (snap.exists()) {
        const merged = { ...DEFAULT_LIMITS, ...snap.data() } as SystemLimits;
        setLimits(merged);
        setLimitDraft(merged);
      }
    });

    const unsubControl = onSnapshot(doc(db, "control", "pump"), (snap) => {
      if (snap.exists()) {
        setControl({ ...DEFAULT_CONTROL, ...snap.data() } as PumpControl);
      }
    });

    return () => {
      unsubLatest();
      unsubHistory();
      unsubLimits();
      unsubControl();
    };
  }, [user]);

  const chartData = useMemo(
    () =>
      history.map((pt) => ({
        ...pt,
        timeLabel: new Date(pt.ts).toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
        }),
      })),
    [history]
  );

  const dryTank = latest ? latest.tankPercent < limits.tankMinPercent : false;
  const highTemp = latest ? latest.temp > limits.tempMax : false;
  const expectedDecision = latest
    ? computePumpDecision(
        latest.soilMoisture,
        latest.tankPercent,
        limits,
        control,
        latest.pumpOn
      )
    : null;

  async function handleEmailAuth(e: FormEvent) {
    e.preventDefault();
    if (!auth) return;
    setAuthError(null);
    setAuthPending(true);
    try {
      if (isSignUp) {
        await createUserWithEmailAndPassword(auth, email.trim(), password);
      } else {
        await signInWithEmailAndPassword(auth, email.trim(), password);
      }
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : "Authentication failed.");
    } finally {
      setAuthPending(false);
    }
  }

  async function handleGoogleSignIn() {
    if (!auth) return;
    setAuthError(null);
    setAuthPending(true);
    try {
      await signInWithPopup(auth, googleProvider);
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : "Google Sign-In failed.");
    } finally {
      setAuthPending(false);
    }
  }

  async function updateControl(next: PumpControl) {
    if (!db) return;
    setSaveMessage(null);
    await setDoc(doc(db, "control", "pump"), {
      mode: next.mode,
      manualOn: Boolean(next.manualOn),
    });
  }

  async function handleSaveLimits(e: FormEvent) {
    e.preventDefault();
    if (!db) return;
    const err = validateLimits(limitDraft);
    if (err) {
      setSaveMessage(err);
      return;
    }
    await setDoc(doc(db, "config", "limits"), {
      soilMin: limitDraft.soilMin,
      soilMax: limitDraft.soilMax,
      tempMax: limitDraft.tempMax,
      tankMinPercent: limitDraft.tankMinPercent,
      logIntervalMin: limitDraft.logIntervalMin,
    });
    setSaveMessage("Limits saved successfully.");
  }

  function handleExportCsv() {
    if (history.length === 0) return;
    const csv = toCsv(history);
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `microclimate-history-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function handlePredict() {
    setAiLoading(true);
    setAiInsight(null);
    try {
      const token = user ? await user.getIdToken() : "";
      const res = await fetch("/api/predict", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ history, latest, limits }),
      });
      const data = await res.json();
      setAiInsight(res.ok ? data.insight : `Error: ${data.error}`);
    } catch {
      setAiInsight("Failed to connect to the AI server.");
    } finally {
      setAiLoading(false);
    }
  }

  if (!isFirebaseConfigured) {
    return (
      <main className="mx-auto max-w-xl p-6 mt-12 rounded-xl border bg-white shadow-sm">
        <h1 className="text-xl font-bold text-slate-900">Firebase is not configured</h1>
        <p className="mt-2 text-sm text-slate-600">
          Copy <code>web/.env.example</code> to <code>web/.env.local</code> and add your Firebase and Gemini keys.
        </p>
      </main>
    );
  }

  if (!authReady) {
    return <main className="p-8 text-center text-slate-500">Loading...</main>;
  }

  if (!user) {
    return (
      <main className="mx-auto max-w-md p-6 mt-14 rounded-2xl border bg-white shadow-sm">
        <h1 className="text-xl font-bold text-slate-900">
          {isSignUp ? "Create Green Life Account" : "Green Life Monitor Sign In"}
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          Sign in to view live sensor data and control the motor.
        </p>

        <form onSubmit={handleEmailAuth} method="POST" className="mt-5 space-y-4">
          <div>
            <label htmlFor="email" className="block text-xs font-medium text-slate-700">
              Email <span aria-hidden="true">*</span>
            </label>
            <input
              id="email"
              name="email"
              type="email"
              autoComplete="username"
              spellCheck={false}
              autoCorrect="off"
              autoCapitalize="none"
              enterKeyHint="next"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mt-1 w-full min-h-[44px] rounded-lg border border-slate-300 px-3 py-2 text-base sm:text-sm"
            />
          </div>

          <div>
            <label htmlFor="password" className="block text-xs font-medium text-slate-700">
              Password <span aria-hidden="true">*</span>
            </label>
            <div className="relative mt-1">
              <input
                id="password"
                name="password"
                type={showPassword ? "text" : "password"}
                autoComplete={isSignUp ? "new-password" : "current-password"}
                enterKeyHint="done"
                minLength={6}
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full min-h-[44px] rounded-lg border border-slate-300 px-3 py-2 pr-10 text-base sm:text-sm"
              />
              <button
                type="button"
                aria-pressed={showPassword}
                aria-label={showPassword ? "Hide password" : "Show password"}
                onClick={() => setShowPassword((prev) => !prev)}
                className="absolute inset-y-0 right-0 flex items-center px-3 text-slate-500 hover:text-slate-700"
              >
                {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
          </div>

          {authError && (
            <p role="alert" aria-live="polite" className="text-sm text-red-600">
              {authError}
            </p>
          )}

          <button
            type="submit"
            disabled={authPending}
            className="w-full min-h-[44px] rounded-lg bg-emerald-600 py-2 text-sm font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
          >
            {isSignUp ? "Create Account" : "Sign In"}
          </button>
        </form>

        <div className="my-4 flex items-center gap-3 text-xs text-slate-400">
          <div className="h-px flex-1 bg-slate-200" />
          <span>or</span>
          <div className="h-px flex-1 bg-slate-200" />
        </div>

        <button
          type="button"
          disabled={authPending}
          onClick={handleGoogleSignIn}
          className="w-full min-h-[44px] rounded-lg border border-slate-300 bg-white py-2 text-sm font-medium text-slate-800 hover:bg-slate-50 disabled:opacity-50"
        >
          Sign in with Google
        </button>

        <button
          type="button"
          onClick={() => {
            setIsSignUp((prev) => !prev);
            setAuthError(null);
          }}
          className="mt-4 w-full text-center text-xs text-emerald-700 hover:underline"
        >
          {isSignUp
            ? "Already have an account? Sign In"
            : "Need a new account? Create Account"}
        </button>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-6xl p-4 sm:p-6 space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border bg-white p-4 shadow-sm">
        <div>
          <h1 className="text-xl font-bold text-slate-900">
            Green Life — IoT Micro-Climate & Precision Agriculture
          </h1>
          <p className="text-xs text-slate-500">
            {user.email ?? user.displayName} •{" "}
            {latest?.updatedAt
              ? `Last updated: ${new Date(latest.updatedAt).toLocaleString()}`
              : "Waiting for ESP32 data..."}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span
            className={`rounded-full px-3 py-1 text-xs font-semibold ${
              latest?.pumpOn
                ? "bg-emerald-100 text-emerald-800"
                : "bg-slate-100 text-slate-700"
            }`}
          >
            Pump: {latest?.pumpOn ? "ON" : "OFF"} ({control.mode.toUpperCase()})
          </span>
          <button
            type="button"
            onClick={() => auth && signOut(auth)}
            className="inline-flex items-center gap-1 rounded-lg border px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
          >
            <LogOut className="h-3.5 w-3.5" /> Logout
          </button>
        </div>
      </header>

      {(dryTank || highTemp) && (
        <div
          role="status"
          className="flex items-center gap-2 rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900"
        >
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span>
            {dryTank && "Low water tank! Pump is locked for dry-run protection. "}
            {highTemp && `Temperature exceeded limit (${limits.tempMax}°C)!`}
          </span>
        </div>
      )}

      <section className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <StatCard icon={<Thermometer className="h-4 w-4 text-rose-500" />} label="Temperature" value={latest ? `${latest.temp.toFixed(1)} °C` : "--"} />
        <StatCard icon={<Droplets className="h-4 w-4 text-sky-500" />} label="Humidity" value={latest ? `${latest.humidity.toFixed(0)} %` : "--"} />
        <StatCard icon={<Wind className="h-4 w-4 text-indigo-500" />} label="Pressure" value={latest ? `${latest.pressure.toFixed(0)} hPa` : "--"} />
        <StatCard icon={<Sun className="h-4 w-4 text-amber-500" />} label="Light" value={latest ? `${latest.light.toFixed(0)} lx` : "--"} />
        <StatCard icon={<Gauge className="h-4 w-4 text-emerald-600" />} label="Soil Moisture" value={latest ? `${latest.soilMoisture.toFixed(0)} %` : "--"} />
        <StatCard icon={<Waves className="h-4 w-4 text-cyan-600" />} label="Water Tank" value={latest ? `${latest.tankPercent.toFixed(0)} %` : "--"} />
      </section>

      <section className="grid gap-6 lg:grid-cols-2">
        <div className="rounded-2xl border bg-white p-5 shadow-sm space-y-4">
          <h2 className="font-semibold text-slate-900">Motor / Pump Control</h2>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => updateControl({ ...control, mode: "auto" })}
              className={`flex-1 rounded-lg py-2 text-sm font-medium border ${
                control.mode === "auto"
                  ? "bg-slate-900 text-white border-slate-900"
                  : "bg-white text-slate-700 hover:bg-slate-50"
              }`}
            >
              Auto Mode
            </button>
            <button
              type="button"
              onClick={() => updateControl({ ...control, mode: "manual" })}
              className={`flex-1 rounded-lg py-2 text-sm font-medium border ${
                control.mode === "manual"
                  ? "bg-slate-900 text-white border-slate-900"
                  : "bg-white text-slate-700 hover:bg-slate-50"
              }`}
            >
              Manual Mode
            </button>
          </div>

          <div className="flex items-center justify-between rounded-xl bg-slate-50 p-4">
            <div className="text-sm">
              <p className="font-medium text-slate-800">
                {control.mode === "auto"
                  ? `Auto Hysteresis (${limits.soilMin}% – ${limits.soilMax}%)`
                  : "Manual Pump Switch"}
              </p>
              <p className="text-xs text-slate-500">
                Status: {expectedDecision ? expectedDecision.reason : "waiting"}
              </p>
            </div>
            <button
              type="button"
              disabled={control.mode === "auto" || dryTank}
              onClick={() =>
                updateControl({ mode: "manual", manualOn: !control.manualOn })
              }
              className={`inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold text-white disabled:opacity-40 ${
                control.manualOn ? "bg-rose-600 hover:bg-rose-700" : "bg-emerald-600 hover:bg-emerald-700"
              }`}
            >
              <Power className="h-4 w-4" />
              {control.manualOn ? "Turn OFF" : "Turn ON"}
            </button>
          </div>
        </div>

        <form onSubmit={handleSaveLimits} className="rounded-2xl border bg-white p-5 shadow-sm space-y-4">
          <h2 className="font-semibold text-slate-900">Min / Max Threshold Limits</h2>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 text-sm">
            <LimitInput label="Soil Min (%)" value={limitDraft.soilMin} onChange={(v) => setLimitDraft({ ...limitDraft, soilMin: v })} />
            <LimitInput label="Soil Max (%)" value={limitDraft.soilMax} onChange={(v) => setLimitDraft({ ...limitDraft, soilMax: v })} />
            <LimitInput label="Temp Max (°C)" value={limitDraft.tempMax} onChange={(v) => setLimitDraft({ ...limitDraft, tempMax: v })} />
            <LimitInput label="Tank Min (%)" value={limitDraft.tankMinPercent} onChange={(v) => setLimitDraft({ ...limitDraft, tankMinPercent: v })} />
            <LimitInput label="Log Interval (m)" value={limitDraft.logIntervalMin} onChange={(v) => setLimitDraft({ ...limitDraft, logIntervalMin: v })} />
          </div>
          <div className="flex items-center justify-between">
            <span role="status" aria-live="polite" className="text-xs text-slate-600">
              {saveMessage ?? ""}
            </span>
            <button
              type="submit"
              className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-700"
            >
              Save Limits
            </button>
          </div>
        </form>
      </section>

      <section className="rounded-2xl border bg-white p-5 shadow-sm space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="font-semibold text-slate-900">Historical Sensor Trends</h2>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setChartType("line")}
              className={`inline-flex items-center gap-1 rounded-lg border px-3 py-1.5 text-xs font-medium ${
                chartType === "line" ? "bg-slate-900 text-white" : "text-slate-700"
              }`}
            >
              <LineChartIcon className="h-3.5 w-3.5" /> Line
            </button>
            <button
              type="button"
              onClick={() => setChartType("bar")}
              className={`inline-flex items-center gap-1 rounded-lg border px-3 py-1.5 text-xs font-medium ${
                chartType === "bar" ? "bg-slate-900 text-white" : "text-slate-700"
              }`}
            >
              <BarChart3 className="h-3.5 w-3.5" /> Bar
            </button>
            <button
              type="button"
              onClick={handleExportCsv}
              disabled={history.length === 0}
              className="inline-flex items-center gap-1 rounded-lg border px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-40"
            >
              <Download className="h-3.5 w-3.5" /> Export CSV
            </button>
          </div>
        </div>

        <div className="h-72 w-full">
          {chartData.length === 0 ? (
            <div className="flex h-full items-center justify-center text-sm text-slate-400">
              No historical data available for the chart
            </div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              {chartType === "line" ? (
                <LineChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="timeLabel" fontSize={12} />
                  <YAxis fontSize={12} />
                  <Tooltip />
                  <Legend />
                  <Line type="monotone" dataKey="soilMoisture" name="Soil (%)" stroke="#059669" strokeWidth={2} dot={false} />
                  <Line type="monotone" dataKey="temp" name="Temp (°C)" stroke="#e11d48" strokeWidth={2} dot={false} />
                  <Line type="monotone" dataKey="humidity" name="Humidity (%)" stroke="#0284c7" strokeWidth={2} dot={false} />
                </LineChart>
              ) : (
                <BarChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="timeLabel" fontSize={12} />
                  <YAxis fontSize={12} />
                  <Tooltip />
                  <Legend />
                  <Bar dataKey="soilMoisture" name="Soil (%)" fill="#059669" />
                  <Bar dataKey="temp" name="Temp (°C)" fill="#e11d48" />
                  <Bar dataKey="humidity" name="Humidity (%)" fill="#0284c7" />
                </BarChart>
              )}
            </ResponsiveContainer>
          )}
        </div>
      </section>

      <section className="rounded-2xl border bg-white p-5 shadow-sm space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold text-slate-900">Gemini Climate & Irrigation Insight</h2>
          <button
            type="button"
            onClick={handlePredict}
            disabled={aiLoading}
            className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-2 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            <Sparkles className="h-3.5 w-3.5" />
            {aiLoading ? "Analyzing..." : "Generate AI Insight"}
          </button>
        </div>
        <div className="whitespace-pre-wrap rounded-xl bg-slate-50 p-4 text-sm text-slate-700">
          {aiInsight ?? "Click the button above to analyze recorded sensor data."}
        </div>
      </section>
    </main>
  );
}

function StatCard({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-xl border bg-white p-3.5 shadow-sm">
      <div className="flex items-center justify-between text-xs text-slate-500">
        <span>{label}</span>
        {icon}
      </div>
      <p className="mt-2 text-lg font-bold text-slate-900">{value}</p>
    </div>
  );
}

function LimitInput({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (val: number) => void;
}) {
  const inputId = useId();
  return (
    <div>
      <label htmlFor={inputId} className="block text-xs text-slate-600">
        {label}
      </label>
      <input
        id={inputId}
        type="number"
        value={Number.isNaN(value) ? "" : value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm"
      />
    </div>
  );
}
