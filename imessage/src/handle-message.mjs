// Routes one received iMessage. All shopping logic lives in the backend (POST /imessage/*).
import { analyzePhoto, answerOwnership, linkPhone, NotLinkedError } from "./backend.mjs";
import { toJpeg } from "./image.mjs";
import { downloadAttachment, parseContent, reply } from "./photon.mjs";

const YES = /^(y|yes|yeah|yep|yup|i do|i own it|own it)\b/i;
const NO = /^(n|no|nope|nah|i don'?t)\b/i;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const LINK_PROMPT =
  "Hi! I'm Second Look. To check photos against your closet, reply with the email you use for Second Look.";
const HELP =
  "Send me a photo of something you're thinking about buying. I'll check it against your closet and current micro trends.";

const handled = new Set(); // the poller re-reads the newest message each cycle; handle each id once

/** Handle one received Photon message. */
export async function handleMessage(msg) {
  if (handled.has(msg.id)) return;
  handled.add(msg.id);

  const phone = msg.sentBy.handle;
  const spaceId = msg.space.id;
  const { text, attachmentIds } = parseContent(msg.content);

  if (attachmentIds.length) return handlePhoto({ msg, phone, spaceId, text, attachmentId: attachmentIds[0] });
  if (!text) return; // read receipts, typing, etc.

  if (EMAIL.test(text)) {
    const res = await linkPhone(phone, text);
    return reply(spaceId, res.linked
      ? `Linked to ${res.email}. Now send me a photo of something you're thinking about buying.`
      : "I couldn't find a Second Look account with that email. Sign up in the extension first, then send it again.");
  }

  if (YES.test(text) || NO.test(text)) {
    try {
      const res = await answerOwnership(phone, YES.test(text));
      return reply(spaceId, res.message);
    } catch (err) {
      if (err instanceof NotLinkedError) return reply(spaceId, LINK_PROMPT);
      throw err;
    }
  }
  return reply(spaceId, HELP);
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
    return reply(spaceId, res.message + ask);
  } catch (err) {
    if (err instanceof NotLinkedError) return reply(spaceId, LINK_PROMPT + " Then send the photo again.");
    throw err;
  }
}
