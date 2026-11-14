import { analyzeImage } from "./analyze.mjs";
import { alreadyProcessed, latestPending, matchCloset, matchTrends, saveItem, setStatus, uploadPhoto } from "./db.mjs";
import { embedImage, embedText } from "./embed.mjs";
import { cropToBox, normalizeImage } from "./image.mjs";
import { downloadAttachment, parseContent, reply } from "./photon.mjs";
import { composeReply } from "./respond.mjs";

const YES = /^(y|yes|yeah|yep|yup|i do|i own it|own it)\b/i;
const NO = /^(n|no|nope|nah|i don'?t)\b/i;
const handled = new Set(); // in-memory guard for text replies (photos are guarded by the DB unique key)

/** Handle one received Photon message. */
export async function handleMessage(msg) {
  if (handled.has(msg.id)) return;
  handled.add(msg.id);

  const userId = msg.sentBy.id;
  const spaceId = msg.space.id;
  const { text, attachmentIds } = parseContent(msg.content);

  if (attachmentIds.length) {
    if (await alreadyProcessed(msg.id)) return;
    return handlePhoto({ msg, userId, spaceId, text, attachmentId: attachmentIds[0] });
  }
  if (!text) return; // read receipts, typing, etc.

  const pending = await latestPending(userId);
  if (pending && YES.test(text)) {
    await setStatus(pending.id, "owned");
    return reply(spaceId, `Added the ${pending.attributes.subcategory} to your closet 👍 I'll flag lookalikes next time.`);
  }
  if (pending && NO.test(text)) {
    await setStatus(pending.id, "considered");
    return reply(spaceId, "Got it, not in your closet. Send another photo whenever you're deciding on something.");
  }
  return reply(spaceId, "Send me a photo of something you're thinking about buying, and I'll tell you what it's made of, how well it's made, if you already own something like it, and whether it's a passing micro trend.");
}

async function handlePhoto({ msg, userId, spaceId, text, attachmentId }) {
  const download = await downloadAttachment(attachmentId);
  let buffer, mime, sourceMime;
  try {
    ({ buffer, mime, sourceMime } = await normalizeImage(download.buffer, download.mime));
  } catch (err) {
    console.error(`[${msg.id}] could not decode ${download.mime}:`, err.message);
    return reply(spaceId, "I couldn't open that file. Could you send it as a photo or screenshot instead?");
  }

  console.log(`[${msg.id}] analyzing ${sourceMime} → jpeg (${(download.buffer.length / 1024).toFixed(0)} KB → ${(buffer.length / 1024).toFixed(0)} KB)`);
  const attributes = await analyzeImage(buffer, mime, text);
  if (!attributes.is_clothing_or_accessory) {
    return reply(spaceId, "That doesn't look like clothing or an accessory, which is all I can help with. Send me a photo of a piece you're considering!");
  }

  // Embed only the garment (screenshots carry phone UI, models and backgrounds that swamp similarity).
  const imageEmbedding = await embedImage(await cropToBox(buffer, attributes.item_box));
  const [textEmbedding, closet, trends] = await Promise.all([
    embedText(attributes.description),
    matchCloset(imageEmbedding, userId, attributes.category),
    matchTrends(imageEmbedding),
  ]);
  console.log(`[${msg.id}] ${attributes.subcategory}; closet top ${closet[0]?.similarity?.toFixed(3) ?? "-"}, trend top ${trends[0] ? `${trends[0].name} ${trends[0].similarity.toFixed(3)}` : "-"}`);

  const ext = mime.split("/")[1].replace("jpeg", "jpg");
  const photoId = await uploadPhoto(`${userId}/${msg.id}.${ext}`, buffer, mime, { user_id: userId, photon_message_id: msg.id });
  await saveItem({
    photon_message_id: msg.id,
    user_id: userId,
    space_id: spaceId,
    photo_id: photoId,
    attributes,
    category: attributes.category,
    quality_score: attributes.quality_score,
    image_embedding: imageEmbedding,
    text_embedding: textEmbedding,
  });

  await reply(spaceId, await composeReply({ attributes, closet, trends }));
}
