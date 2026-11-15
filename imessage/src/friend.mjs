// Friend-style wording for a reply (backend POST /photon/friend-reply). The backend's PHOTON_FRIEND_MODE is
// the only switch (scripts/demo_up.sh turns it on): when off, the backend answers `existing` at once.
// Never blocks or breaks the existing reply: any error or a 3.5 s timeout sends `existing` unchanged.
import { env } from "./config.mjs";

/** Returns the text to send: the friend reply when the layer answers, else `existing`. */
export async function friendReply({ phone, text, hasImage, verdict, verdictId, existing }) {
  if (!existing) return existing;
  try {
    const res = await fetch(`${env.SECOND_LOOK_API}/photon/friend-reply`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-agent-secret": env.IMESSAGE_AGENT_SECRET },
      body: JSON.stringify({
        sender_id: phone, text: text || null, has_image: Boolean(hasImage),
        verdict: verdict ? { verdict } : null, verdict_id: verdictId || null, existing_reply: existing,
      }),
      signal: AbortSignal.timeout(3_500),
    });
    if (!res.ok) return existing;
    const data = await res.json();
    return typeof data.reply === "string" && data.reply.trim() ? data.reply : existing;
  } catch {
    return existing;
  }
}
