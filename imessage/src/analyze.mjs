import { readFileSync } from "node:fs";
import Anthropic from "@anthropic-ai/sdk";
import { betaZodOutputFormat } from "@anthropic-ai/sdk/helpers/beta/zod";
import { z } from "zod";
import { env, requireEnv } from "./config.mjs";
import { mimeFromPath } from "./embed.mjs";

export const anthropic = new Anthropic();

// Server-side fallback: if a request is declined, the API retries it on a fallback model in the same call.
export const FALLBACK = { betas: ["server-side-fallback-2026-07-01"], fallbacks: "default" };

export const ItemAttributes = z.object({
  is_clothing_or_accessory: z.boolean().describe("False if the photo is not of a wearable item"),
  category: z.string().describe("One of: top, bottom, dress, outerwear, shoes, bag, accessory, jewelry, other"),
  subcategory: z.string().describe("Specific item type, e.g. 'cropped cardigan', 'barrel-leg jeans', 'ballet flat'"),
  colors: z.array(z.string()),
  pattern: z.string().describe("e.g. solid, striped, plaid, floral, graphic"),
  material_guess: z.string().describe("Most likely fabric/material, e.g. 'cotton jersey', 'polyester satin', 'genuine leather'"),
  material_confidence: z.enum(["low", "medium", "high"]).describe("high only if a care/fiber label is legible"),
  material_cues: z.array(z.string()).describe("Visual evidence for the material: sheen, drape, texture, weave vs knit, label text"),
  quality_signals: z.array(z.string()).describe("Observed construction cues, positive or negative: stitching, seams, hems, hardware, lining, pilling, fabric weight"),
  quality_score: z.number().int().describe("1 (poor) to 5 (excellent), based only on visible evidence"),
  style_tags: z.array(z.string()).describe("Aesthetic/style descriptors, e.g. 'coquette', 'quiet luxury', 'western', 'gorpcore'"),
  description: z.string().describe("One or two plain sentences describing the item itself (not the model, background or styling), for search and memory"),
  item_box: z
    .object({ x_min: z.number(), y_min: z.number(), x_max: z.number(), y_max: z.number() })
    .describe("Tight bounding box around the main item only, as fractions of image width and height (0 = left/top, 1 = right/bottom). Exclude phone UI, text, background and other garments."),
});

const SYSTEM = `You are the vision analyst for Second Look, a service that gives shoppers a second opinion before they buy clothing.
Given one photo, identify the main item and report its attributes.
- Material: reason from visible cues (sheen, drape, texture, knit vs woven, how it creases, visible labels). A photo rarely proves fiber content; say "low" confidence unless a label is legible.
- Quality: judge only what is visible: stitch density and straightness, seam finishing, pattern matching at seams, hem construction, hardware, lining, pilling or fuzzing, fabric weight and opacity. If the photo is too small or blurry to judge, say so in quality_signals and score 3.
- If the photo shows several items, describe the most prominent one. Photos are often screenshots of shopping sites or social apps; ignore the app interface and focus on the garment.
- If it is not clothing or an accessory, set is_clothing_or_accessory to false and fill the other fields as best you can.`;

/** Analyze one item photo with Claude. Returns ItemAttributes. */
export async function analyzeImage(buffer, mime, userText = "") {
  const response = await anthropic.beta.messages.parse({
    ...FALLBACK,
    model: env.ANTHROPIC_MODEL,
    max_tokens: 16000,
    output_config: { effort: "medium", format: betaZodOutputFormat(ItemAttributes) },
    system: SYSTEM,
    messages: [
      {
        role: "user",
        content: [
          { type: "image", source: { type: "base64", media_type: mime, data: buffer.toString("base64") } },
          { type: "text", text: userText ? `The shopper wrote: "${userText}"` : "Analyze this item." },
        ],
      },
    ],
  });
  if (response.stop_reason === "refusal") throw new Error(`Analysis declined: ${response.stop_details?.category ?? "unknown"}`);
  if (!response.parsed_output) throw new Error(`Analysis did not return valid attributes (stop_reason: ${response.stop_reason})`);
  return response.parsed_output;
}

// CLI: node src/analyze.mjs photo.jpg
if (import.meta.url === `file://${process.argv[1]}`) {
  const file = process.argv[2];
  const { normalizeImage } = await import("./image.mjs");
  const { buffer, mime, sourceMime } = await normalizeImage(readFileSync(file), mimeFromPath(file));
  console.log(`Detected ${sourceMime} → ${mime}`);
  console.log(JSON.stringify(await analyzeImage(buffer, mime), null, 2));
}
