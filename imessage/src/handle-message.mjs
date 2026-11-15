// Routes one received iMessage. All shopping logic lives in the backend (POST /imessage/*).
import { analyzePhoto, answerOwnership, askQuestion, getDetails, linkPhone, NotLinkedError } from "./backend.mjs";
import { friendReply } from "./friend.mjs";
import { toJpeg } from "./image.mjs";
import { downloadAttachment, parseContent, reply } from "./photon.mjs";
import { replySenderId, routeMessage } from "./route.mjs";
import { demoReply } from "./photon_demo_replay.mjs";

const LINK_PROMPT =
  "Hi! I'm Second Look. To check photos against your closet and answer questions about your account, reply with the email you use for Second Look.";

const handled = new Set(); // the poller re-reads the newest message each cycle; handle each id once

/** Reply to `msg` in its space, from the line that received it. */
export function replyTo(msg, text) {
  return reply(msg.space.id, text, replySenderId(msg));
}

/** Handle one received Photon message. */
export async function handleMessage(msg) {
  if (handled.has(msg.id)) return;
  handled.add(msg.id);

  const phone = msg.sentBy.handle;
  const send = (text) => replyTo(msg, text);
  const { text, attachmentIds } = parseContent(msg.content);
  const demo = await demoReply({ senderId: msg.sentBy?.id ?? phone, text, hasImage: attachmentIds.length > 0, getImage: () => downloadAttachment(attachmentIds[0]) }); if (demo) return send(demo);

  const route = routeMessage(text, attachmentIds.length > 0);
  if (route.kind === "photo") return handlePhoto({ msg, phone, send, text, attachmentId: attachmentIds[0] });
  if (route.kind === "ignore") return; // read receipts, typing, etc.

  if (route.kind === "link") {
    const res = await linkPhone(phone, text);
    return send(res.linked
      ? `Linked to ${res.email}. Now send me a photo of something you're thinking about buying.`
      : "I couldn't find a Second Look account with that email. Sign up in the extension first, then send it again.");
  }

  try {
    // "own": a short yes/no to "Do you already own this?". "ask": any other text (greetings and "help" too),
    // answered from this account's closet, savings, verdicts and recent messages.
    const res = route.kind === "own" ? await answerOwnership(phone, route.owns) : await askQuestion(phone, text);
    return send(res.message);
  } catch (err) {
    if (err instanceof NotLinkedError) return send(LINK_PROMPT);
    throw err;
  }
}

async function handlePhoto({ msg, phone, send, text, attachmentId }) {
  const download = await downloadAttachment(attachmentId);
  let jpeg;
  try {
    const { buffer, sourceMime } = await toJpeg(download.buffer, download.mime);
    jpeg = buffer;
    console.log(`[${msg.id}] ${sourceMime} -> jpeg, ${(download.buffer.length / 1024) | 0} KB -> ${(jpeg.length / 1024) | 0} KB`);
  } catch (err) {
    console.error(`[${msg.id}] could not decode ${download.mime}:`, err.message);
    return send("I couldn't open that file. Could you send it as a photo or screenshot instead?");
  }

  try {
    const res = await analyzePhoto(phone, jpeg, text);
    console.log(`[${msg.id}] verdict ${res.verdict} (${res.verdict_id})`);
    const ask = res.ask_ownership ? "\n\nDo you already own this? Reply yes or no." : "";
    await send(await friendReply({ phone, text, hasImage: true, verdict: res.verdict, verdictId: res.verdict_id, existing: res.message }));
    await sendDetails({ msg, phone, send, verdictId: res.verdict_id });
    if (ask) await send(ask.trim()); // last, so a "yes" reply follows the question it answers
  } catch (err) {
    if (err instanceof NotLinkedError) return send(LINK_PROMPT + " Then send the photo again.");
    throw err;
  }
}

// Full analysis (materials, lifespan, cost per wear, outfits) as a second text. Never fails the photo:
// the verdict was already sent.
async function sendDetails({ msg, phone, send, verdictId }) {
  if (!verdictId || verdictId === "unsaved") return;
  try {
    const { message } = await getDetails(phone, verdictId);
    if (message) await send(message); // from the line that received the photo, like every other reply
  } catch (err) {
    console.log(`[${msg.id}] full analysis skipped: ${err.message}`);
  }
}
