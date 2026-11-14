// Routes one received iMessage. All shopping logic lives in the backend (POST /imessage/*).
import { analyzePhoto, answerOwnership, askQuestion, getDetails, linkPhone, NotLinkedError } from "./backend.mjs";
import { toJpeg } from "./image.mjs";
import { downloadAttachment, parseContent, reply } from "./photon.mjs";
import { routeMessage } from "./route.mjs";

const LINK_PROMPT =
  "Hi! I'm Second Look. To check photos against your closet and answer questions about your account, reply with the email you use for Second Look.";

const handled = new Set(); // the poller re-reads the newest message each cycle; handle each id once

/** Handle one received Photon message. */
export async function handleMessage(msg) {
  if (handled.has(msg.id)) return;
  handled.add(msg.id);

  const phone = msg.sentBy.handle;
  const spaceId = msg.space.id;
  const { text, attachmentIds } = parseContent(msg.content);

  const route = routeMessage(text, attachmentIds.length > 0);
  if (route.kind === "photo") return handlePhoto({ msg, phone, spaceId, text, attachmentId: attachmentIds[0] });
  if (route.kind === "ignore") return; // read receipts, typing, etc.

  if (route.kind === "link") {
    const res = await linkPhone(phone, text);
    return reply(spaceId, res.linked
      ? `Linked to ${res.email}. Now send me a photo of something you're thinking about buying.`
      : "I couldn't find a Second Look account with that email. Sign up in the extension first, then send it again.");
  }

  try {
    // "own": a short yes/no to "Do you already own this?". "ask": any other text (greetings and "help" too),
    // answered from this account's closet, savings, verdicts and recent messages.
    const res = route.kind === "own" ? await answerOwnership(phone, route.owns) : await askQuestion(phone, text);
    return reply(spaceId, res.message);
  } catch (err) {
    if (err instanceof NotLinkedError) return reply(spaceId, LINK_PROMPT);
    throw err;
  }
}

async function handlePhoto({ msg, phone, spaceId, text, attachmentId }) {
  const download = await downloadAttachment(attachmentId);
  let jpeg;
  try {
    const { buffer, sourceMime } = await toJpeg(download.buffer, download.mime);
    jpeg = buffer;
    console.log(`[${msg.id}] ${sourceMime} -> jpeg, ${(download.buffer.length / 1024) | 0} KB -> ${(jpeg.length / 1024) | 0} KB`);
  } catch (err) {
    console.error(`[${msg.id}] could not decode ${download.mime}:`, err.message);
    return reply(spaceId, "I couldn't open that file. Could you send it as a photo or screenshot instead?");
  }

  try {
    const res = await analyzePhoto(phone, jpeg, text);
    console.log(`[${msg.id}] verdict ${res.verdict} (${res.verdict_id})`);
    const ask = res.ask_ownership ? "\n\nDo you already own this? Reply yes or no." : "";
    await reply(spaceId, res.message);
    await sendDetails({ msg, phone, spaceId, verdictId: res.verdict_id });
    if (ask) await reply(spaceId, ask.trim()); // last, so a "yes" reply follows the question it answers
  } catch (err) {
    if (err instanceof NotLinkedError) return reply(spaceId, LINK_PROMPT + " Then send the photo again.");
    throw err;
  }
}

// Full analysis (materials, lifespan, cost per wear, outfits) as a second text. Never fails the photo:
// the verdict was already sent.
async function sendDetails({ msg, phone, spaceId, verdictId }) {
  if (!verdictId || verdictId === "unsaved") return;
  try {
    const { message } = await getDetails(phone, verdictId);
    if (message) await reply(spaceId, message);
  } catch (err) {
    console.log(`[${msg.id}] full analysis skipped: ${err.message}`);
  }
}
