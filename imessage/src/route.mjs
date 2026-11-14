// Decides what one received iMessage is. Pure (no I/O) so it can be tested without Photon or the backend.

const YES = /^(y|yes|yeah|yep|yup|i do|i own it|own it)\b/i;
const NO = /^(n|no|nope|nah|i don'?t)\b/i;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const ANSWER_MAX_WORDS = 6; // "yes" / "nope, I don't" answer the ownership question; longer texts are questions

/**
 * Returns one of:
 *   { kind: "photo" }            an attachment (the caption rides along)
 *   { kind: "ignore" }           no text (read receipts, typing, etc.)
 *   { kind: "link" }             an email: link this phone to that account
 *   { kind: "own", owns: bool }  a short yes/no: answers "Do you already own this?"
 *   { kind: "ask" }              anything else: a question for POST /imessage/ask
 */
/**
 * The Photon user id to reply from: the line that received this message (`receivedBy.id`). Every shopper may
 * have their own shared line, assigned by the backend, so replies cannot always come from PHOTON_AGENT_HANDLE.
 * Returns null when Photon left it out; the caller then falls back to the agent handle's user.
 */
export function replySenderId(msg) {
  const id = msg?.receivedBy?.id;
  return typeof id === "string" && id ? id : null;
}

export function routeMessage(text, hasAttachment) {
  if (hasAttachment) return { kind: "photo" };
  const t = (text ?? "").trim();
  if (!t) return { kind: "ignore" };
  if (EMAIL.test(t)) return { kind: "link" };
  const short = !t.includes("?") && t.split(/\s+/).length <= ANSWER_MAX_WORDS;
  if (short && YES.test(t)) return { kind: "own", owns: true };
  if (short && NO.test(t)) return { kind: "own", owns: false };
  return { kind: "ask" };
}
