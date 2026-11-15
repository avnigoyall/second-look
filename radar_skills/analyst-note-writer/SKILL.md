---
name: analyst-note-writer
description: Wording rules for the reasons field of a style decision or a disagreement hypothesis. Use whenever you write text that will be stored or shown.
---

# Analyst note writer

The reasons you write are shown to people next to the label. Keep them short,
factual and traceable.

## Rules

- 2 to 5 reasons. One sentence each, under 25 words.
- Each reason ends with the evidence_ids it relies on, in square brackets, for
  example `[ev:get_style_stats:leg-warmers:01:3f2a9c0d1e]`. Copy ids exactly
  as tools returned them.
- Describe results only as Bluesky sample chatter: "in the Bluesky sample",
  "Bluesky posts in the sample". Never claim sales, market share, or what
  "everyone" wears.
- Use numbers only when a tool returned them. Never compute your own lifespan,
  rate or projection.
- Name thresholds by their config key from `/config/social_micro_trend.json`,
  not by value, unless a tool returned the value.
- State uncertainty plainly: "untested", "small sample", "may be seasonal".
- Plain words. No hype words ("viral", "exploding", "iconic"). No em dashes.
- Do not quote author handles or long post text.

## Template

- Shape: "Posts in the Bluesky sample rose over N weeks and fell to a fraction
  of peak by the last observed week. [ids]"
- Spread: "Activity came from many unconnected authors, not one cluster. [ids]"
- Precision: "k of n sampled posts were about the garment. [ids]"
- Caveat: "Only one season observed, so seasonality is untested. [ids]"
