import { readFileSync } from "node:fs";
import { setTimeout as sleep } from "node:timers/promises";
import sharp from "sharp";
import { ApiError, GoogleGenAI } from "@google/genai";
import { env, requireEnv } from "./config.mjs";

let client;
const gemini = () => (client ??= new GoogleGenAI({ apiKey: env.GEMINI_API_KEY }));

const EMBED_MAX_EDGE = 512; // plenty for garment similarity; keeps requests small and cheap
const BACKOFF_MS = [5_000, 10_000, 20_000, 30_000, 30_000]; // ~95s total, enough to outlast a 1-minute rate window

function isRetryable(err) {
  const status = err instanceof ApiError ? err.status : undefined;
  return status === 429 || (status !== undefined && status >= 500);
}

/** One embedding for one piece of content (a Gemini Content with parts), retrying on rate limits. */
async function embed(parts) {
  for (let attempt = 0; ; attempt++) {
    try {
      const res = await gemini().models.embedContent({
        model: env.EMBED_MODEL,
        contents: { role: "user", parts },
        config: { outputDimensionality: env.EMBED_DIMS },
      });
      return res.embeddings[0].values;
    } catch (err) {
      if (!isRetryable(err) || attempt >= BACKOFF_MS.length) throw err;
      const wait = BACKOFF_MS[attempt];
      console.warn(`Gemini embed ${err.status}; retrying in ${Math.round(wait / 1000)}s (attempt ${attempt + 2}/${BACKOFF_MS.length + 1})`);
      await sleep(wait);
    }
  }
}

/** Shrink to fit 512x512 (keeps aspect ratio, never enlarges) and re-encode as JPEG. */
export async function shrinkForEmbedding(buffer) {
  return sharp(buffer)
    .rotate()
    .resize({ width: EMBED_MAX_EDGE, height: EMBED_MAX_EDGE, fit: "inside", withoutEnlargement: true })
    .flatten({ background: "#ffffff" })
    .jpeg({ quality: 85 })
    .toBuffer();
}

/** Embed an image buffer (any format sharp reads), sent to Gemini as a 512px JPEG. */
export async function embedImage(buffer) {
  const small = await shrinkForEmbedding(buffer);
  return embed([{ inlineData: { mimeType: "image/jpeg", data: small.toString("base64") } }]);
}

/** Embed text in the same vector space as images. gemini-embedding-2 takes the task as a text prefix, not task_type. */
export function embedText(text) {
  return embed([{ text: `task: sentence similarity | query: ${text}` }]);
}

/** Detect the real image type from the file's first bytes (extensions and reported content types can lie). */
export function sniffMime(buffer) {
  const b = buffer;
  if (b.length >= 3 && b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return "image/jpeg";
  if (b.length >= 8 && b.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) return "image/png";
  if (b.length >= 6 && /^GIF8[79]a$/.test(b.toString("latin1", 0, 6))) return "image/gif";
  if (b.length >= 12 && b.toString("latin1", 0, 4) === "RIFF" && b.toString("latin1", 8, 12) === "WEBP") return "image/webp";
  if (b.length >= 12 && b.toString("latin1", 4, 8) === "ftyp" && /^(avif|avis)$/.test(b.toString("latin1", 8, 12))) return "image/avif";
  if (b.length >= 12 && b.toString("latin1", 4, 8) === "ftyp" && /^(heic|heix|hevc|heim|heis|mif1|msf1)$/.test(b.toString("latin1", 8, 12))) return "image/heic";
  return null;
}

/** Best guess at an image file's type: its contents first, extension as a fallback. */
export function mimeFromPath(p, buffer) {
  const sniffed = buffer && sniffMime(buffer);
  if (sniffed) return sniffed;
  const ext = p.toLowerCase().split(".").pop();
  return { jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", webp: "image/webp", gif: "image/gif" }[ext];
}

// CLI: node src/embed.mjs photo.jpg
if (import.meta.url === `file://${process.argv[1]}`) {
  requireEnv("GEMINI_API_KEY");
  const file = process.argv[2];
  const buf = readFileSync(file);
  const v = await embedImage(buf, mimeFromPath(file, buf));
  console.log(`${v.length}-dim vector, first values: ${v.slice(0, 5).map((x) => x.toFixed(4)).join(", ")}`);
}
