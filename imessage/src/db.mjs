// MongoDB Atlas storage: items (closet + considered), trends, trend_examples, and photos in GridFS.
// Similarity search uses Atlas Vector Search; run `node scripts/setup-mongo.mjs` once to create the indexes.
import { GridFSBucket, MongoClient, ObjectId } from "mongodb";
import { env } from "./config.mjs";

export const INDEX = { items: "items_image_vector", trendExamples: "trend_examples_vector" };

let clientPromise;
/** Lazily connected so a missing env var surfaces through requireEnv, not a crash at import. */
export async function db() {
  clientPromise ??= new MongoClient(env.MONGODB_URI, { appName: "second-look" }).connect();
  return (await clientPromise).db(env.MONGODB_DB);
}

export async function closeDb() {
  if (clientPromise) await (await clientPromise).close();
}

// Atlas reports cosine scores as (1 + cos) / 2. Convert back to plain cosine.
const toCosine = (score) => 2 * score - 1;

export async function alreadyProcessed(photonMessageId) {
  return Boolean(await (await db()).collection("items").findOne({ photon_message_id: photonMessageId }, { projection: { _id: 1 } }));
}

/** Store a photo in GridFS (bucket "photos"). Returns the file id. */
export async function uploadPhoto(filename, buffer, mime, metadata = {}) {
  const bucket = new GridFSBucket(await db(), { bucketName: "photos" });
  return new Promise((resolve, reject) => {
    const stream = bucket.openUploadStream(filename, { metadata: { ...metadata, contentType: mime } });
    stream.on("error", reject).on("finish", () => resolve(stream.id));
    stream.end(buffer);
  });
}

/** Read a stored photo back from GridFS as a Buffer. */
export async function downloadPhoto(photoId) {
  const bucket = new GridFSBucket(await db(), { bucketName: "photos" });
  const chunks = [];
  for await (const chunk of bucket.openDownloadStream(photoId)) chunks.push(chunk);
  return Buffer.concat(chunks);
}

export async function saveItem(row) {
  const { insertedId } = await (await db()).collection("items").insertOne({ ...row, status: "pending", created_at: new Date() });
  return { id: insertedId };
}

/** Nearest owned closet items for one user. Same-category matches get a small boost. */
export async function matchCloset(embedding, userId, category, n = 5) {
  const rows = await (await db()).collection("items").aggregate([
    {
      $vectorSearch: {
        index: INDEX.items,
        path: "image_embedding",
        queryVector: embedding,
        numCandidates: 200,
        limit: n * 2,
        filter: { user_id: userId, status: "owned" },
      },
    },
    { $project: { attributes: 1, category: 1, created_at: 1, score: { $meta: "vectorSearchScore" } } },
  ]).toArray();
  return rows
    .map((r) => ({ id: r._id, attributes: r.attributes, category: r.category, created_at: r.created_at,
      similarity: toCosine(r.score) + (category && r.category === category ? 0.03 : 0) }))
    .sort((a, b) => b.similarity - a.similarity)
    .slice(0, n);
}

/** Nearest trends, scored by their best-matching example image. */
export async function matchTrends(embedding, n = 3) {
  const rows = await (await db()).collection("trend_examples").aggregate([
    { $vectorSearch: { index: INDEX.trendExamples, path: "embedding", queryVector: embedding, numCandidates: 200, limit: 50 } },
    { $group: { _id: "$trend_id", score: { $max: { $meta: "vectorSearchScore" } } } },
    { $sort: { score: -1 } },
    { $limit: n },
    { $lookup: { from: "trends", localField: "_id", foreignField: "_id", as: "trend" } },
    { $unwind: "$trend" },
  ]).toArray();
  return rows.map((r) => ({ ...r.trend, id: r._id, similarity: toCosine(r.score) }));
}

/** Most recent item awaiting a yes/no from this user. */
export async function latestPending(userId) {
  const item = await (await db()).collection("items").findOne(
    { user_id: userId, status: "pending" },
    { sort: { created_at: -1 }, projection: { attributes: 1 } },
  );
  return item && { id: item._id, attributes: item.attributes };
}

export async function setStatus(itemId, status) {
  await (await db()).collection("items").updateOne({ _id: new ObjectId(itemId) }, { $set: { status, updated_at: new Date() } });
}

export async function addTrend(trend) {
  const res = await (await db()).collection("trends").findOneAndUpdate(
    { name: trend.name },
    { $set: { ...trend, updated_at: new Date() }, $setOnInsert: { created_at: new Date() } },
    { upsert: true, returnDocument: "after" },
  );
  return { id: res._id };
}

export async function addTrendExample(trendId, photoId, embedding) {
  await (await db()).collection("trend_examples").insertOne({ trend_id: trendId, photo_id: photoId, embedding, created_at: new Date() });
}
