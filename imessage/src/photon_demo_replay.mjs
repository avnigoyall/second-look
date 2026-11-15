// Demo-only scripted replies for the iMessage agent (config/photon_demo_replies.json).
// Off unless PHOTON_DEMO_REPLAY=1. PHOTON_DEMO_SEQUENCE=1 also enables step-only entries.
// Only the reply text is replaced: no backend call runs, so verdicts and Radar data are untouched.
// Never logs phone numbers or sender ids, only an 8-character hash of the sender id.
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const CONFIG = fileURLToPath(new URL("../../config/photon_demo_replies.json", import.meta.url));
const JITTER = 0.15;

const sha256 = (data) => createHash("sha256").update(data).digest("hex");
export const senderHash = (senderId) => sha256(String(senderId ?? "")).slice(0, 8);
const normalize = (text) => String(text ?? "").toLowerCase().replace(/\s+/g, " ").trim();
const fits = (entry, hasImage) => (entry.expects === "image" ? hasImage : !hasImage);

export function loadEntries(path = CONFIG) {
  return JSON.parse(readFileSync(path, "utf8"));
}

function matches(entry, { text, imageSha256 }) {
  const m = entry.match || {};
  if (m.text_contains != null && !normalize(text).includes(normalize(m.text_contains))) return false;
  if (m.image_sha256 != null && m.image_sha256 !== imageSha256) return false;
  return true;
}

/**
 * replay.decide({ senderId, text, hasImage, imageSha256 }) -> entry | null (null: use the live path).
 * replay.wait(entry) resolves after delay_ms plus up to 15% jitter.
 */
export function createReplay({ entries, replay = false, sequence = false, log = console.log,
  random = Math.random, setTimer = setTimeout } = {}) {
  const steps = new Map(); // sender hash -> current step
  const lastStep = Math.max(0, ...entries.map((e) => e.step));
  const matchEntries = entries.filter((e) => e.match && Object.keys(e.match).length);
  const stepEntries = entries.filter((e) => !e.match || !Object.keys(e.match).length);

  function decide({ senderId, text, hasImage, imageSha256 }) {
    if (!replay) return null;
    const who = senderHash(senderId);
    if (/\breset\b/i.test(text ?? "")) {
      steps.set(who, 1);
      log(`[demo replay] reset sequence for ${who}`);
      return null;
    }
    const step = steps.get(who) ?? 1;
    if (step > lastStep) return null; // sequence finished: live path until "reset"
    const message = { text, imageSha256 };
    let entry = matchEntries.find((e) => fits(e, hasImage) && matches(e, message));
    if (!entry && sequence) entry = stepEntries.find((e) => e.step === step && fits(e, hasImage));
    if (!entry) return null;
    steps.set(who, entry.step + 1);
    log(`[demo replay] fired ${entry.id}`);
    return entry;
  }

  function wait(entry) {
    const ms = Math.round(entry.delay_ms * (1 + random() * JITTER));
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
export async function demoReply({ senderId, text, hasImage, getImage }) {
  if (process.env.PHOTON_DEMO_REPLAY !== "1") return null;
  const replay = sharedReplay();
  let imageSha256 = null;
  if (hasImage) {
    try {
      imageSha256 = sha256((await getImage()).buffer);
      console.log(`[demo replay] image sha256 ${imageSha256} from ${senderHash(senderId)}`);
    } catch (err) {
      console.log(`[demo replay] image hash failed: ${err.message}`);
    }
  }
  const entry = replay.decide({ senderId, text, hasImage, imageSha256 });
  if (!entry) return null;
  await replay.wait(entry);
  return entry.reply;
}
