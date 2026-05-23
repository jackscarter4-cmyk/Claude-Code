import Anthropic from "@anthropic-ai/sdk";
import { promises as fs } from "node:fs";
import path from "node:path";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

let cachedPrompt: string | null = null;
async function loadSystemPrompt(): Promise<string> {
  if (cachedPrompt) return cachedPrompt;
  cachedPrompt = await fs.readFile(
    path.join(process.cwd(), "app/lib/golf-swing-coach-prompt.md"),
    "utf-8",
  );
  return cachedPrompt;
}

export async function POST(request: Request) {
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return Response.json(
      {
        error:
          "ANTHROPIC_API_KEY is not set. Create swing-coach/.env.local with: ANTHROPIC_API_KEY=sk-ant-...",
      },
      { status: 500 },
    );
  }

  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return Response.json({ error: "Body must be JSON." }, { status: 400 });
  }

  let systemPrompt: string;
  try {
    systemPrompt = await loadSystemPrompt();
  } catch (err) {
    return Response.json(
      {
        error: `Could not read system prompt: ${err instanceof Error ? err.message : String(err)}`,
      },
      { status: 500 },
    );
  }

  const client = new Anthropic({ apiKey });

  try {
    const message = await client.messages.create({
      model: "claude-opus-4-7",
      max_tokens: 2000,
      system: [
        {
          type: "text",
          text: systemPrompt,
          cache_control: { type: "ephemeral" },
        },
      ],
      messages: [
        {
          role: "user",
          content: JSON.stringify(payload, null, 2),
        },
      ],
    });

    const text = message.content
      .filter((c): c is Anthropic.TextBlock => c.type === "text")
      .map((c) => c.text)
      .join("\n");

    return Response.json({
      text,
      usage: message.usage,
      stop_reason: message.stop_reason,
      model: message.model,
    });
  } catch (err) {
    console.error("Anthropic API error:", err);
    const status =
      err instanceof Anthropic.APIError ? (err.status ?? 500) : 500;
    return Response.json(
      { error: err instanceof Error ? err.message : String(err) },
      { status },
    );
  }
}
