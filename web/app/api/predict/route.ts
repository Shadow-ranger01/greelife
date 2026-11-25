import { NextResponse } from "next/server";
import { GoogleGenAI } from "@google/genai";

async function verifyFirebaseToken(authHeader: string | null): Promise<boolean> {
  const firebaseKey = process.env.NEXT_PUBLIC_FIREBASE_API_KEY;
  if (!firebaseKey) return true;
  if (!authHeader?.startsWith("Bearer ")) return false;

  const idToken = authHeader.slice(7).trim();
  if (!idToken) return false;

  const res = await fetch(
    `https://identitytoolkit.googleapis.com/v1/accounts:lookup?key=${firebaseKey}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ idToken }),
    }
  );
  return res.ok;
}

export async function POST(request: Request) {
  const authorized = await verifyFirebaseToken(request.headers.get("authorization"));
  if (!authorized) {
    return NextResponse.json({ error: "Unauthorized." }, { status: 401 });
  }

  const apiKey = process.env.GEMINI_API_KEY;
  if (!apiKey) {
    return NextResponse.json(
      { error: "GEMINI_API_KEY is not configured on the server." },
      { status: 500 }
    );
  }

  let body: { history?: unknown[]; latest?: unknown; limits?: unknown };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON payload." }, { status: 400 });
  }

  const history = Array.isArray(body.history) ? body.history.slice(-30) : [];
  if (history.length === 0 && !body.latest) {
    return NextResponse.json(
      { error: "No sensor data available for analysis." },
      { status: 400 }
    );
  }

  const prompt = [
    "You are an agricultural micro-climate advisor for a smart farming system in Bangladesh.",
    "Analyze the sensor readings and configured thresholds below.",
    "Give a concise response in Bangla with 3 short bullet points:",
    "1) বর্তমান মাটি ও আবহাওয়ার প্রবণতা (Short-term micro-climate trend)",
    "2) সেচ ও ট্যাংকের অবস্থা (Irrigation & water tank status)",
    "3) পরবর্তী করণীয় পরামর্শ (Actionable advice for the farmer)",
    "",
    `Configured Limits: ${JSON.stringify(body.limits ?? {})}`,
    `Latest Reading: ${JSON.stringify(body.latest ?? {})}`,
    `Recent History (${history.length} points): ${JSON.stringify(history)}`,
  ].join("\n");

  try {
    const ai = new GoogleGenAI({ apiKey });
    const interaction = await ai.interactions.create({
      model: "gemini-4-hydrogen",
      input: prompt,
      store: false,
    });

    return NextResponse.json({
      insight: interaction.output_text ?? "কোনো বিশ্লেষণ পাওয়া যায়নি।",
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Gemini request failed.";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
