---
name: precision-auditor
description: Check whether sampled Bluesky posts for a style are really about that fashion style. Use before trusting post counts, or when a spike looks suspicious.
---

# Precision auditor

Goal: estimate what share of a style's sampled posts are on-topic, so counts
can be trusted or discounted. This is a check on Bluesky sample chatter only.

## Steps

1. Call `sample_posts` for the style. Prefer a sample from the peak weeks and
   one from a quiet period if the tool allows it. One call is often enough.
2. Label each sampled post on-topic or off-topic:
   - On-topic: talks about wearing, buying, styling, seeing, or disliking the
     garment or look.
   - Off-topic: the term means something else (a song title, a product that is
     not clothing, a name), shop spam, bot reposts, or keyword stuffing.
3. Report on-topic count over sample size (for example "k of n sampled posts
   on-topic"). Do not extrapolate a precise rate from a small sample; give the
   counts.
4. Note overlap: posts that equally match another tracked style (for example
   other heels terms, balloon versus barrel pants).

## How to use the result

- If precision is low by the standard in `/config/social_micro_trend.json`
  (when defined there), recommend unknown or lower confidence.
- If off-topic posts cluster in the peak weeks, the peak may be an artifact.
  Say so.
- Cite the evidence_id from `sample_posts` in every statement.

Do not quote author handles. Paraphrase post text in a few words at most.
Never edit files.
