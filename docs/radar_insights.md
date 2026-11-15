# Radar insights: cap-robust composition metrics (Nov 15)

Reproduce every number: `python scripts/radar_insights.py` (tables) or `--json` (all values). Read-only on Atlas. No
model calls, no new data, no spend. All numbers describe a Bluesky keyword sample, not the market.

## Summary

Raw volume is unreliable here (first-page cap of 20, Sunday-heavy samples, deeper collection from 2026-08-31). So we
measured what kind of posts appear, on a cap-consistent sample: the newest 20 posts of every term-week, which is what
the first page returned all year. One metric stood out and survived the artifact checks: the share of posts that are
**commerce** (deal posts, listings, prices). It went the opposite way from our starting hypothesis. We expected resale
to rise as a style dies. Instead, deal posts rise on the styles the press calls rising, and stay flat or fall on the
styles the press calls fading. In the last quarter (Aug 10 to Nov 8, 2026) versus the three before, every rising term
gained more commerce share than every fading term (12 of 12 pairs, permutation p = 0.06, 3 rising vs 4 fading terms).
Most of these posts are new-product deal posts from Amazon-style affiliate accounts, not people selling their own
clothes. Hypothesis: on Bluesky, a style turning into a cheap deal-post product is a sign of mass-market (fast fashion)
adoption, and it shows up while the style is still "rising". How sure we are: low. n = 7 labeled terms, the press list
has no sources or dates, and a few bot accounts write most of the deal posts. Treat it as a lead, not a claim.

## Method

- Cap-consistent sample: newest 20 posts per (term, week). 23,648 of 33,006 posts. This removes the 2026-08-31 depth
  change by construction.
- Blocks: 4-week blocks (13 blocks, min n = 20 per block) and 13-week quarters (4 quarters, min n = 15). The trailing
  partial week is dropped.
- Trajectory: mean of the last 3 blocks minus mean of earlier blocks (quarters: last quarter minus earlier quarters).
- Validation label: press_status in config/social_terms.json (rising: straight leg jeans, quarter zip, poplin shirt,
  slim sneakers, soft suiting; fading: balloon jeans, skinny jeans, dad sneakers, mini bag, high heels). Only 5 of 10
  terms have enough posts for 4-week blocks, 7 of 10 for quarters. slim sneakers (26 posts in the year), soft suiting
  (22) and balloon jeans (9) never qualify.
- Separation: AUC = share of (fading, rising) pairs where the fading term's trajectory is higher. 0.5 = no separation,
  0 or 1 = perfect. p = exact two-sided permutation test over all splits.
- Artifact checks: (a) depth step = median over terms of (mean after the 2026-08-31 block minus mean before), divided by
  the across-term SD of the metric; (b) Spearman rho of the metric level against the term's saturation (share of weeks
  at the cap). Large values mean the metric may track collection, not chatter.
- Metric definitions and regexes live in one place: `METRICS` and the `*_RE` patterns in scripts/radar_insights.py.

## Metrics x validation

| Metric | AUC 4w (2r/3f) | AUC quarters (3r/4f) | p (quarters) | Depth step / SD | rho vs saturation | Verdict |
|---|---|---|---|---|---|---|
| commerce (deal, listing, price) | 0.00 | 0.00 | 0.06 | 0.54 | -0.38 | Best lead. Direction is opposite to the resale hypothesis |
| commerce_excess (minus other terms) | 0.00 | 0.00 | 0.06 | -0.23 | -0.36 | Same result after removing the platform-wide rise |
| deal (new-product deal posts) | 0.00 | 0.08 | 0.11 | 0.39 | -0.31 | Drives the commerce result |
| secondhand (depop, ebay, pre-owned ...) | 1.00 | 0.75 | 0.40 | -0.09 | 0.07 | Fading direction as hypothesised, but levels are 0 to 4 percent. Too rare to use |
| zero_engagement | 0.17 | 0.25 | 0.40 | 0.30 | -0.40 | Partly mechanical: deal posts get no likes |
| likes_median ("fatigue") | 0.67 | 0.54 | 1.00 | -0.40 | 0.34 | Fails. Median is 0 to 3 for every term, no signal |
| likes_mean | 0.50 | 0.67 | 0.63 | -0.03 | 0.29 | Fails. Driven by single viral posts |
| backlash (over, dead, ugly ...) | 0.33 | 0.33 | 0.63 | 0.01 | -0.03 | Fails. 0 to 9 percent, noisy |
| revival (is back, bring back ...) | 0.67 | 0.33 | 0.63 | -0.14 | 0.10 | Fails. Too rare |
| desire (want, need, where can I find ...) | 0.67 | 0.83 | 0.23 | -0.35 | -0.10 | Fails. Direction even favours fading terms |
| wearing (wore, OOTD, my new ...) | 0.50 | 0.58 | 0.86 | -0.02 | -0.12 | Fails |
| conversation (reply or quote share) | 0.50 | 0.58 | 0.86 | -0.29 | 0.53 | Fails, and tracks saturation |
| newcomer (authors new to the term) | 1.00 | 0.83 | 0.23 | -0.84 | 0.27 | Fails. Falls mechanically with block index, and has the largest depth step |
| top_author_share | 0.17 | 0.17 | 0.23 | -0.01 | -0.63 | Fails. Mostly a saturation proxy (rho -0.63) |
| communities_per20 (diffusion) | 0.33 | 0.42 | 0.86 | -0.20 | -0.16 | Fails. Community names are built from the terms themselves, so diffusion is partly circular |
| drift_from_first (meaning drift) | 0.33 | n/a | n/a | 0.08 | -0.58 | Fails. Centroid moves 0.00 to 0.02 cosine for every term; flat |
| dispersion | 0.83 | n/a | n/a | 0.15 | 0.80 | Fails. Near constant (0.11 to 0.12) and tracks saturation |
| velocity_pph (time to cap) | 0.00 (1r/1f) | n/a | n/a | -0.04 | 0.54 | Not testable. Only 2 press terms have saturated weeks |

The depth step is n/a for quarters: the 2026-08-31 change falls inside the last quarter.

## Top findings (ranked by robustness)

### 1. Deal posts flood styles the press calls rising (hypothesis)

Commerce share by quarter (Nov 10 / Feb 9 / May 11 / Aug 10 starts), cap-consistent sample:

| Term | Press | Commerce share by quarter | Excess over other terms, last quarter | Trajectory |
|---|---|---|---|---|
| quarter zip | rising | 6% / 12% / 10% / 31% (n = 168 / 236 / 247 / 259) | +22 points | +0.22 |
| poplin shirt | rising | - / 12% / 40% / 37% (n = 34 / 20 / 35) | +27 points | +0.11 |
| straight leg jeans | rising | 9% / 12% / 10% / 19% | +9 points | +0.09 |
| mini bag | fading | 21% / 12% / 25% / 24% (high but flat) | +15 points | +0.05 |
| skinny jeans | fading | 3% / 3% / 1% / 6% | -4 points | +0.04 |
| high heels | fading | 1% / 2% / 2% / 2% | -8 points | +0.01 |
| dad sneakers | fading | 12% / 5% / 11% / 8% | -2 points | -0.01 |

Platform baseline (all other terms pooled, Levi's 501 excluded): 5.2% / 5.6% / 6.9% / 9.9%. Deal posts rise everywhere
on Bluesky, so the table also shows the excess over that baseline. The ordering is the same.

Why we think it is not an artifact:
- Depth: the sample is the newest 20 posts per week in every week, so the 2026-08-31 change cannot enter. Direct check:
  in the 111 deep term-weeks (more than 40 posts, almost all from 2026-08-31 on), commerce share is 10.9% in the newest 20 posts and 10.4% in the older
  posts. For quarter zip it is 25.9% vs 26.3%. Composition does not depend on how deep we page.
- Sunday skew: commerce share is highest on weekdays (12.3% Monday) and lowest on Sunday (6.4%). Saturated terms are
  sampled mostly on Sunday evening, so saturation pushes commerce share down, not up. quarter zip became more saturated
  and its commerce share still rose.
- Season: quarter zip is a cold-weather item. Its Nov 2025 to Feb 2026 quarter (peak season) had 6% commerce. The Aug to
  Nov 2026 quarter had 31%. The same calendar season a year apart differs by 5x, so season does not explain it.
- Regex precision (read by hand): 33 of 40 random commerce matches across all terms were listings, deals or promos (the
  other 7 were articles or comments that mention a price). For quarter zip in the last quarter: 24 of 25 were
  new-product deal posts.

Why we are not sure:
- n = 7 labeled terms, p = 0.06. The press list has no sources or dates.
- Few accounts: in the last quarter, quarter zip commerce posts (80) came from 25 accounts, and the top 3 wrote 70%. A
  year earlier: 10 posts from 10 accounts. poplin shirt: 13 posts from 3 accounts. So "deal share" largely means "deal
  bots picked up this keyword". The growth in distinct deal accounts (10 to 25 for quarter zip) is the more
  conservative reading.
- Possible confound: the rising terms are plain product names (quarter zip, poplin shirt, straight leg jeans) that
  retailers list under. high heels is polysemous (art and adult tags), which keeps its commerce share low for another
  reason.
- The press "rising" label may itself come from retail activity. Then this metric would measure the same thing as the
  label, not predict it.

Unlabeled terms with the same pattern (last quarter excess, Levi's 501 aside): capri pants (5% to 25% commerce; +16
points), block heels (6% to 21%), high waisted jeans (3% to 14%), flare jeans (14% to 23%). Hypothesis only: these are
the styles where Bluesky shows mass-market product push in autumn 2026.

### 2. Engagement fatigue appears only through the deal posts

quarter zip zero-engagement share rose from 28% to 48% and median likes fell from 1 to 0 in the last quarter. But deal
posts get almost no likes, so this is mostly the same signal as finding 1. We found no term where people kept posting
but stopped engaging, after removing commerce. Fatigue as a separate metric did not survive.

### 3. Secondhand resale is not where styles go to die, on this sample

The original hypothesis (owners offload a dying style on resale apps) has the expected direction (AUC 0.75 in
quarters), but secondhand share is 0 to 4 percent for every press term. The one big secondhand signal is Levi's 501,
where one resale bot account writes most posts (70% commerce in the last quarter). We cannot measure the death of a
style this way.

## Limits

- n = 10 press labels, only 7 usable. No finding here is a claim.
- Bluesky sample chatter, keyword search. Not sales, not the market.
- like_count was read at ingest time. Posts from the last days have had less time to collect likes.
- Regexes are English-centric and were checked on 65 posts in total.
- Communities come from a graph whose names are built from the terms, so diffusion metrics are partly circular.
- Embedding drift and dispersion are nearly constant; the 1024-d embeddings separate terms, but not time blocks.

## Why no new data or Vultr calls

The coordinator allowed deeper collection and Vultr classification. We did not use them. The 3 press terms that never
qualify (soft suiting, slim sneakers, balloon jeans) have 9 to 26 posts in the whole year and are never capped, so
deeper pagination adds nothing for them. The top finding uses the cap-consistent sample, which deeper pages do not
change (checked above). A model classifier would only refine a regex whose precision we already measured (33 of 40, and
24 of 25 on the key term). Spend: 0 USD.
