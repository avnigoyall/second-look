import { anthropic, FALLBACK } from "./analyze.mjs";
import { env } from "./config.mjs";

const SYSTEM = `You are Second Look, a friendly, honest shopping second opinion that lives in iMessage.
You get JSON with:
- item: attributes of the piece the shopper is considering.
- closet_candidates: the pieces from the shopper's closet that look most visually similar, ranked by an image-embedding score (cosine; unrelated garments usually score around 0.5, the same kind of garment in a similar color around 0.65 or higher, near-identical photos higher still). The ranking is only a hint: decide yourself from the attributes.
- trend_candidates: micro trends from our curated library that look closest, same kind of score.

Write one short text message (under ~90 words, no markdown, no bullet symbols, at most one emoji) covering, in this order:
1. What the item is, its likely material (hedge if confidence is low) and a quick quality read.
2. Closet: if a candidate is essentially the same piece (same garment type, color and key details), say they basically already own this and name it. If one is similar but clearly different (same type and color family, different details), mention it as something similar they have. Otherwise say nothing about the closet.
3. Trend: only if the item clearly fits a candidate trend's description, name the trend and its stage. Emerging or peak micro trends tend to fade fast, so suggest thinking about whether they'd still wear it in a year; staples are safe. Otherwise say nothing about trends.
Never mention scores. Never invent closet items or trends that are not in the data. Do not ask "do you own this?"; that line is added after your message.`;

const brief = (a) => ({
  item: a.subcategory, category: a.category, colors: a.colors, pattern: a.pattern,
  material: a.material_guess, style_tags: a.style_tags, description: a.description,
});

/** Compose the iMessage reply text from analysis + nearest closet items and trends. */
export async function composeReply({ attributes, closet, trends }) {
  const data = {
    item: attributes,
    closet_candidates: closet.slice(0, 3).map((m) => ({ ...brief(m.attributes), score: +m.similarity.toFixed(2) })),
    trend_candidates: trends.slice(0, 3).map((t) => ({ name: t.name, stage: t.stage, description: t.description, score: +t.similarity.toFixed(2) })),
  };
  const response = await anthropic.beta.messages.create({
    ...FALLBACK,
    model: env.ANTHROPIC_MODEL,
    max_tokens: 4000,
    output_config: { effort: "low" },
    system: SYSTEM,
    messages: [{ role: "user", content: JSON.stringify(data, null, 2) }],
  });
  if (response.stop_reason === "refusal") throw new Error("Reply declined");
  const text = response.content.filter((b) => b.type === "text").map((b) => b.text).join("").trim();
  return `${text}\n\nDo you already own this? Reply yes or no.`;
}
