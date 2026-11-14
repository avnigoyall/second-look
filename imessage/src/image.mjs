// Converts any incoming photo (HEIC, AVIF, WebP, PNG, JPEG, GIF...) to a right-sized JPEG that Claude and Gemini accept.
import heicConvert from "heic-convert";
import sharp from "sharp";
import { sniffMime } from "./embed.mjs";

const MAX_EDGE = 1568; // Claude's recommended max; larger images are downscaled anyway and cost more tokens.

/** Crop to a fractional box ({x_min, y_min, x_max, y_max} in 0..1) with a little padding. Falls back to the full image if the box is unusable. */
export async function cropToBox(buffer, box, pad = 0.04) {
  const { width, height } = await sharp(buffer).metadata();
  const clamp = (v) => Math.min(1, Math.max(0, v));
  const x0 = clamp(Math.min(box.x_min, box.x_max) - pad), x1 = clamp(Math.max(box.x_min, box.x_max) + pad);
  const y0 = clamp(Math.min(box.y_min, box.y_max) - pad), y1 = clamp(Math.max(box.y_min, box.y_max) + pad);
  const w = Math.round((x1 - x0) * width), h = Math.round((y1 - y0) * height);
  if (w < 32 || h < 32) return buffer; // box too small or degenerate; use the whole image
  return sharp(buffer).extract({ left: Math.round(x0 * width), top: Math.round(y0 * height), width: w, height: h }).jpeg({ quality: 90 }).toBuffer();
}

/** Returns { buffer, mime: "image/jpeg", sourceMime }. Throws if the data is not a readable image. */
export async function normalizeImage(input, reportedMime) {
  const sourceMime = sniffMime(input) ?? reportedMime ?? "unknown";
  let buf = input;

  // sharp's bundled decoder handles AVIF but not iPhone HEIC (HEVC), so decode HEIC separately first.
  if (sourceMime === "image/heic") {
    buf = Buffer.from(await heicConvert({ buffer: input, format: "JPEG", quality: 0.92 }));
  }

  const out = await sharp(buf, { animated: false })
    .rotate() // respect EXIF orientation so sideways phone photos come out upright
    .resize({ width: MAX_EDGE, height: MAX_EDGE, fit: "inside", withoutEnlargement: true })
    .flatten({ background: "#ffffff" }) // transparent PNG/WebP backgrounds become white, not black
    .jpeg({ quality: 88, mozjpeg: true })
    .toBuffer();

  return { buffer: out, mime: "image/jpeg", sourceMime };
}
