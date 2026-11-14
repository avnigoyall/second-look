// Converts any incoming photo (HEIC, AVIF, WebP, PNG, JPEG, GIF) to a JPEG the backend can read.
// iPhones send HEIC and shopping sites send AVIF; the backend's Pillow reads neither, so convert here.
import heicConvert from "heic-convert";
import sharp from "sharp";

const MAX_EDGE = 1568; // the backend resizes again (600 px crops); this just keeps uploads small

/** Detect the real image type from the file's first bytes (extensions and reported content types can lie). */
export function sniffMime(b) {
  if (b.length >= 3 && b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return "image/jpeg";
  if (b.length >= 8 && b.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) return "image/png";
  if (b.length >= 6 && /^GIF8[79]a$/.test(b.toString("latin1", 0, 6))) return "image/gif";
  if (b.length >= 12 && b.toString("latin1", 0, 4) === "RIFF" && b.toString("latin1", 8, 12) === "WEBP") return "image/webp";
  if (b.length >= 12 && b.toString("latin1", 4, 8) === "ftyp" && /^(avif|avis)$/.test(b.toString("latin1", 8, 12))) return "image/avif";
  if (b.length >= 12 && b.toString("latin1", 4, 8) === "ftyp" && /^(heic|heix|hevc|heim|heis|mif1|msf1)$/.test(b.toString("latin1", 8, 12))) return "image/heic";
  return null;
}

/** Returns { buffer (JPEG), sourceMime }. Throws if the data is not a readable image. */
export async function toJpeg(input, reportedMime) {
  const sourceMime = sniffMime(input) ?? reportedMime ?? "unknown";
  // sharp's bundled decoder handles AVIF but not iPhone HEIC (HEVC), so decode HEIC separately first.
  const buf = sourceMime === "image/heic" ? Buffer.from(await heicConvert({ buffer: input, format: "JPEG", quality: 0.92 })) : input;
  const buffer = await sharp(buf, { animated: false })
    .rotate() // respect EXIF orientation so sideways phone photos come out upright
    .resize({ width: MAX_EDGE, height: MAX_EDGE, fit: "inside", withoutEnlargement: true })
    .flatten({ background: "#ffffff" }) // transparent backgrounds become white, not black
    .jpeg({ quality: 88, mozjpeg: true })
    .toBuffer();
  return { buffer, sourceMime };
}
