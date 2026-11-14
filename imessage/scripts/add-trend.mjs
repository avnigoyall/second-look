// Add (or update) a micro trend with example images.
// Usage: node scripts/add-trend.mjs "<name>" <emerging|peak|fading|staple> "<description>" img1.jpg [img2.jpg ...]
// Optional env: TREND_CATEGORY, TREND_STARTED (YYYY-MM-DD), TREND_PEAKED (YYYY-MM-DD)
import { readFileSync } from "node:fs";
import { basename } from "node:path";
import { requireEnv } from "../src/config.mjs";
import { addTrend, addTrendExample, closeDb, uploadPhoto } from "../src/db.mjs";
import { embedImage, mimeFromPath } from "../src/embed.mjs";
import { normalizeImage } from "../src/image.mjs";

requireEnv("GEMINI_API_KEY", "MONGODB_URI");

const [name, stage, description, ...images] = process.argv.slice(2);
const STAGES = ["emerging", "peak", "fading", "staple"];
if (!name || !STAGES.includes(stage) || !images.length) {
  console.error('Usage: node scripts/add-trend.mjs "<name>" <emerging|peak|fading|staple> "<description>" img1.jpg [img2.jpg ...]');
  process.exit(1);
}

const { id } = await addTrend({
  name,
  stage,
  description,
  category: process.env.TREND_CATEGORY ?? null,
  started_at: process.env.TREND_STARTED ?? null,
  peaked_at: process.env.TREND_PEAKED ?? null,
});

const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "-");
for (const file of images) {
  let buffer, mime;
  try {
    ({ buffer, mime } = await normalizeImage(readFileSync(file), mimeFromPath(file)));
  } catch (err) {
    console.warn(`skip ${file}: could not read image (${err.message})`);
    continue;
  }
  const embedding = await embedImage(buffer, mime);
  const photoId = await uploadPhoto(`trends/${slug}/${basename(file)}`, buffer, mime, { trend_id: id });
  await addTrendExample(id, photoId, embedding);
  console.log(`  + ${file}`);
}
console.log(`Trend "${name}" (${stage}) saved with ${images.length} example(s).`);
await closeDb();
