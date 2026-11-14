// Client for the Second Look backend's iMessage endpoints (backend/routers/imessage.py).
import { env } from "./config.mjs";

export class NotLinkedError extends Error {}

async function post(path, body) {
  let res;
  try {
    res = await fetch(`${env.SECOND_LOOK_API}${path}`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-agent-secret": env.IMESSAGE_AGENT_SECRET },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(30_000), // the verdict engine's worst case is about attributes + 10 s
    });
  } catch (err) {
    throw new Error(`Second Look backend unreachable at ${env.SECOND_LOOK_API} (${err.name}). Is uvicorn running?`);
  }
  const data = await res.json().catch(() => ({}));
  if (res.status === 404 && data.detail === "phone_not_linked") throw new NotLinkedError();
  if (!res.ok) throw new Error(`backend ${path} -> ${res.status}: ${JSON.stringify(data).slice(0, 300)}`);
  return data;
}

/** Link a phone to the account with this email. Returns { linked, email }. */
export const linkPhone = (phone, email) => post("/imessage/link", { phone, email });

/** Run the verdict engine on a JPEG. Returns { verdict_id, verdict, message, ask_ownership }. */
export const analyzePhoto = (phone, jpeg, text) =>
  post("/imessage/analyze", { phone, image_base64: `data:image/jpeg;base64,${jpeg.toString("base64")}`, text: text || null });

/** Answer "do you already own this?" for the latest iMessage verdict. Returns { ok, message }. */
export const answerOwnership = (phone, owns) => post("/imessage/own", { phone, owns });

/** Answer any other text from this account's closet, savings, verdicts and recent messages. Returns { message }. */
export const askQuestion = (phone, text) => post("/imessage/ask", { phone, text });
