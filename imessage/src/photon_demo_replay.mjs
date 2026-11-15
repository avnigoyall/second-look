// Demo-only scripted replies for the iMessage agent (config/photon_demo_replies.json).
// Off unless PHOTON_DEMO_REPLAY=1. PHOTON_DEMO_SEQUENCE=1 also enables step-only entries.
// Only the reply text is replaced: the verdict engine never runs, so verdicts and Radar data are untouched.
// Never logs phone numbers or sender ids, only an 8-character hash of the sender id.
// match.image_color asks the backend (POST /imessage/demo-color, read-only Gemini attribute read) for the top's
// color_family, so a black top and a blue top can get different scripted replies.
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { env } from "./config.mjs";
import { toJpeg } from "./image.mjs";

const CONFIG = fileURLToPath(new URL("../../config/photon_demo_replies.json", import.meta.url));
const JITTER = 0.15;

const sha256 = (data) => createHash("sha256").update(data).digest("hex");
export const senderHash = (senderId) => sha256(String(senderId ?? "")).slice(0, 8);
const normalize = (text) => String(text ?? "").toLowerCase().replace(/\s+/g, " ").trim();
const fits = (entry, hasImage) => (entry.expects === "image" ? hasImage : !hasImage);

export function loadEntries(path = CONFIG) {
  return JSON.parse(readFileSync(path, "utf8"));
}

function matches(entry, { text, imageSha256, imageColor }) {
  const m = entry.match || {};
  if (m.text_contains != null && !normalize(text).includes(normalize(m.text_contains))) return false;
  if (m.image_sha256 != null && m.image_sha256 !== imageSha256) return false;
  if (m.image_color != null && m.image_color !== imageColor) return false;
  return true;
}

export const usesImageColor = (entries) => entries.some((e) => e.match?.image_color != null);

/**
 * replay.decide({ senderId, text, hasImage, imageSha256, imageColor }) -> entry | null (null: live path).
 * Match entries fire whenever they match, in any order. Step-only entries follow the sequence, which only moves
 * forward. replay.wait(entry, elapsedMs) resolves delay_ms plus up to 15% jitter after the message arrived.
 */
export function createReplay({ entries, replay = false, sequence = false, log = console.log,
  random = Math.random, setTimer = setTimeout } = {}) {
  const steps = new Map(); // sender hash -> current step
  const lastStep = Math.max(0, ...entries.map((e) => e.step));
  const matchEntries = entries.filter((e) => e.match && Object.keys(e.match).length);
  const stepEntries = entries.filter((e) => !e.match || !Object.keys(e.match).length);

  function decide({ senderId, text, hasImage, imageSha256, imageColor = null }) {
    if (!replay) return null;
    const who = senderHash(senderId);
    if (/\breset\b/i.test(text ?? "")) {
      steps.set(who, 1);
      log(`[demo replay] reset sequence for ${who}`);
      return null;
    }
    const step = steps.get(who) ?? 1;
    const message = { text, imageSha256, imageColor };
    let entry = matchEntries.find((e) => fits(e, hasImage) && matches(e, message));
    if (!entry && sequence && step <= lastStep) entry = stepEntries.find((e) => e.step === step && fits(e, hasImage));
    if (!entry) return null;
    steps.set(who, Math.max(step, entry.step + 1));
    log(`[demo replay] fired ${entry.id}`);
    return entry;
  }

  function wait(entry, elapsedMs = 0) {
    const ms = Math.max(0, Math.round(entry.delay_ms * (1 + random() * JITTER)) - elapsedMs);
    return new Promise((resolve) => setTimer(resolve, ms));
  }

  return { decide, wait };
}

let shared = null;
function sharedReplay() {
  shared ??= createReplay({
    entries: loadEntries(),
    replay: process.env.PHOTON_DEMO_REPLAY === "1",
    sequence: process.env.PHOTON_DEMO_SEQUENCE === "1",
  });
  return shared;
}

/**
 * The hook used by handle-message.mjs. Returns the scripted reply text (after its delay) or null.
 * getImage() is called only when replay is on and the message has an image.
 */
export async function demoReply({ senderId, text, hasImage, getImage, colorOf = topColor }) {
  if (process.env.PHOTON_DEMO_REPLAY !== "1") return null;
  const started = Date.now();
  const replay = sharedReplay();
  let imageSha256 = null;
  let imageColor = null;
  if (hasImage) {
    try {
      const image = await getImage();
      imageSha256 = sha256(image.buffer);
      if (usesImageColor(loadEntriesCached())) imageColor = await colorOf(image);
      console.log(`[demo replay] image sha256 ${imageSha256} color ${imageColor} from ${senderHash(senderId)}`);
    } catch (err) {
      console.log(`[demo replay] image check failed: ${err.message}`);
    }
  }
  const entry = replay.decide({ senderId, text, hasImage, imageSha256, imageColor });
  if (!entry) return null;
  await replay.wait(entry, Date.now() - started); // the color check counts toward the delay: no double wait
  return entry.reply;
}

let cachedEntries = null;
const loadEntriesCached = () => (cachedEntries ??= loadEntries());

/** color_family of the top in the photo, from the backend's read-only Gemini attribute read. null on failure. */
export async function topColor(image) {
  const { buffer } = await toJpeg(image.buffer, image.mime);
  const res = await fetch(`${env.SECOND_LOOK_API}/imessage/demo-color`, {
    method: "POST",
    headers: { "content-type": "application/json", "x-agent-secret": env.IMESSAGE_AGENT_SECRET },
    body: JSON.stringify({ image_base64: buffer.toString("base64") }),
    signal: AbortSignal.timeout(15_000),
  });
  if (!res.ok) throw new Error(`demo-color -> ${res.status}`);
  return (await res.json()).color_family ?? null;
}
