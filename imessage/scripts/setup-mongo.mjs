// One-time setup: regular indexes + Atlas Vector Search indexes. Safe to re-run.
// Usage: node scripts/setup-mongo.mjs
import { env, requireEnv } from "../src/config.mjs";
import { closeDb, db, INDEX } from "../src/db.mjs";

requireEnv("MONGODB_URI");
const DIMS = env.EMBED_DIMS; // must match the embedding model output (src/config.mjs)

const d = await db();
const existing = new Set((await d.listCollections({}, { nameOnly: true }).toArray()).map((c) => c.name));
for (const name of ["items", "trends", "trend_examples"]) {
  if (!existing.has(name)) await d.createCollection(name);
}

await d.collection("items").createIndex({ photon_message_id: 1 }, { unique: true });
await d.collection("items").createIndex({ user_id: 1, status: 1, created_at: -1 });
await d.collection("trends").createIndex({ name: 1 }, { unique: true });
await d.collection("trend_examples").createIndex({ trend_id: 1 });
console.log(`Regular indexes ready in database "${env.MONGODB_DB}".`);

async function ensureVectorIndex(collection, name, fields) {
  const coll = d.collection(collection);
  const found = await coll.listSearchIndexes(name).toArray();
  if (found.length) {
    const current = found[0].latestDefinition?.fields?.find((f) => f.type === "vector")?.numDimensions;
    if (current === DIMS) {
      console.log(`Vector index ${name}: already exists (${found[0].status}, ${DIMS} dims)`);
    } else {
      await coll.updateSearchIndex(name, { fields });
      console.log(`Vector index ${name}: updated ${current} -> ${DIMS} dims (rebuilding, ~1 minute)`);
    }
    return;
  }
  await coll.createSearchIndex({ name, type: "vectorSearch", definition: { fields } });
  console.log(`Vector index ${name}: created (takes ~1 minute to become READY)`);
}

await ensureVectorIndex("items", INDEX.items, [
  { type: "vector", path: "image_embedding", numDimensions: DIMS, similarity: "cosine" },
  { type: "filter", path: "user_id" },
  { type: "filter", path: "status" },
]);
await ensureVectorIndex("trend_examples", INDEX.trendExamples, [
  { type: "vector", path: "embedding", numDimensions: DIMS, similarity: "cosine" },
]);

await closeDb();
