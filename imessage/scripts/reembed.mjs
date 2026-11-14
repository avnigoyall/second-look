// Re-embed stored data with the current embedding model, cropping each item to the garment.
// Usage: node scripts/reembed.mjs          only items never cropped (missing attributes.item_box)
//        node scripts/reembed.mjs --all    every item AND every trend example (use after changing EMBED_MODEL/EMBED_DIMS)
import { requireEnv } from "../src/config.mjs";
import { analyzeImage } from "../src/analyze.mjs";
import { closeDb, db, downloadPhoto } from "../src/db.mjs";
import { embedImage, embedText } from "../src/embed.mjs";
import { cropToBox, normalizeImage } from "../src/image.mjs";

requireEnv("GEMINI_API_KEY", "MONGODB_URI");

const all = process.argv.includes("--all");
const d = await db();
const items = d.collection("items");
const todo = await items.find(all ? {} : { "attributes.item_box": { $exists: false } }, { projection: { photo_id: 1, attributes: 1 } }).toArray();
console.log(`${todo.length} item(s) to re-embed`);

for (const it of todo) {
  const { buffer, mime } = await normalizeImage(await downloadPhoto(it.photo_id));
  const box = it.attributes.item_box ?? (await analyzeImage(buffer, mime)).item_box;
  const image_embedding = await embedImage(await cropToBox(buffer, box));
  const text_embedding = await embedText(it.attributes.description);
  await items.updateOne({ _id: it._id }, { $set: { image_embedding, text_embedding, "attributes.item_box": box, updated_at: new Date() } });
  console.log(`  ✓ ${it.attributes.subcategory}`);
}

if (all) {
  const examples = await d.collection("trend_examples").find({}, { projection: { photo_id: 1 } }).toArray();
  console.log(`${examples.length} trend example(s) to re-embed`);
  for (const ex of examples) {
    const { buffer } = await normalizeImage(await downloadPhoto(ex.photo_id));
    await d.collection("trend_examples").updateOne({ _id: ex._id }, { $set: { embedding: await embedImage(buffer) } });
  }
}
await closeDb();
