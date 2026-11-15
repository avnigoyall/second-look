# Social Radar evaluation

Status: measured.

Generated at: 2026-11-15T01:49:55.351633Z.

Scope: real_only. Synthetic posts excluded: 0. Embedding: vultr qwen3-embedding-4b, 1024 dims, index social_posts_vultr_vector.

| Measurement | real_only |
| --- | --- |
| Corpus posts | 33006 |
| Embedded posts | 31599 |
| Prepared Claude queries | 30 |
| Evaluated held-out queries | 30 |
| Successful lookups | 30 |
| Lookup errors | 0 |
| Recall@5 | 1.0000 |
| Recall@10 | 1.0000 |
| MRR@30 | 1.0000 |
| Lookup p50 (ms) | 1425.4066 |
| Lookup p95 (ms) | 2699.8658 |
| Weighted cluster purity by term | 0.4103 |
| Assigned post share | 1.0000 |

Staples and micro-trends do not separate reliably in this sample.

```json
{
  "groups": {
    "micro": {
      "terms": 0,
      "posts": 0,
      "median_decay_ratio": null,
      "median_breadth": null,
      "momentum_counts": {
        "rising": 0,
        "peaking": 0,
        "fading": 0,
        "steady": 0,
        "unknown": 0
      }
    },
    "staple": {
      "terms": 0,
      "posts": 0,
      "median_decay_ratio": null,
      "median_breadth": null,
      "momentum_counts": {
        "rising": 0,
        "peaking": 0,
        "fading": 0,
        "steady": 0,
        "unknown": 0
      }
    }
  },
  "confusion": {
    "micro_predicted_micro": 0,
    "micro_predicted_staple": 0,
    "staple_predicted_micro": 0,
    "staple_predicted_staple": 0
  },
  "balanced_accuracy": null,
  "eligible_terms": 0,
  "excluded_for_query_coverage": 19,
  "sufficient_terms": false,
  "separates": false,
  "interpretation": "Staples and micro-trends do not separate reliably in this sample.",
  "limits": "Only terms with at least 100 posts, enough observed weeks and exhausted term/corpus query windows are scored. Unvisited or truncated searches are not absence. Labels are noisy; a micro-trend can still be rising. A fading rule is not a micro-trend identity classifier."
}
```

Method and limits:

- Originals stay indexed as retrieval targets. Claude generates differently worded queries once; queries are held out from threshold calibration. This tests instance retrieval, not unseen-trend prediction.
- Prepare independently with `.venv/bin/python scripts/eval_social.py --prepare-only`. Run `.venv/bin/python scripts/eval_social.py` after embedding the targets to evaluate the cached queries.
- Seed 42 selects up to 30 term-balanced text posts. Preparation can run before embeddings exist; prepared queries are not a completed retrieval benchmark. Evaluation requires the targets to be embedded. Thirty queries are a small sample. Provider failures count as misses. MRR is truncated at the 30 retrieved results.
- Latency includes successful uncached embedding plus Atlas search. It excludes Claude paraphrase time and /analyze verdict time. Timeout failures do not count as successful fast lookups.
- Only a 12-month window is observed. first_seen_in_window is not the trend origin.
- Counts are samples from a search index and may be capped, never platform totals. Quoted terms, uneven coverage, language, spam, missing accounts and search ranking bias the sample.
- Cluster purity measures majority canonical search term, not a real-world community ground truth. Sparse observed interactions and inferred similarity edges limit interpretation.
- Staple and micro annotations are evaluation-only and noisy. Any threshold calibration is in-sample term comparison, not held-out classification accuracy. Report overlap plainly.
- No author handles or raw post text appear in this report. Queries and source URIs remain in ignored local files.

## Where coverage or counts are too low

- Published momentum is unknown for 42 of 42 terms. 0 terms have complete query coverage; the strict policy withholds momentum, decay and confidence above low when any week is unvisited, truncated or saturated.
- More than 25 percent of weeks hit the 20-post first-page cap for 15 terms (baggy jeans, butter yellow, cargo pants, cherry red, chinos, graphic tee, high heels, leg warmers, leopard print, low rise jeans, platform heels, quarter zip, sambas, skinny jeans, white t-shirt). Their weekly counts are lower bounds and cannot show growth.
- Fewer than 100 real posts (confidence low by rule) for 7 terms: balloon jeans, mesh flats, micro bag, mid rise jeans, plain black tee, slim sneakers, soft suiting.
- The eight-week preview is read-only and low confidence. Across press terms it reads: steady, unknown. With sampled lower-bound counts it cannot confirm or refute press opinion. Too few recent posts to compare: slim sneakers, soft suiting, balloon jeans, dad sneakers.

## Press versus graph comparison

Ten user-supplied press annotations are compared with real_only sample metrics. This is a sanity check against press opinion, not an independent accuracy score. Published momentum: 0 agree, 0 disagree, 10 not comparable (unknown). Recent eight-week preview (read-only, not published): 0 agree, 6 disagree, 4 not comparable. Fewer than 100 real sample posts, so confidence is low by rule: slim sneakers, soft suiting, balloon jeans.

| Term | press_status | Sample posts (real_only) | Breadth | Concentration | Published momentum | Confidence | Agreement | Preview momentum (8 wk) | Preview posts | Preview confidence | Preview agreement | Saturated week share | Coverage note | Press source/date |
| --- | --- | ---: | ---: | ---: | --- | --- | --- | --- | ---: | --- | --- | ---: | --- | --- |
| quarter zip | rising | 1601 | 18 | 0.478 | unknown | low | not_comparable | steady | 680 | low | disagree | 0.396 | Unvisited, truncated or unknown term/corpus searches are not observed absence. Decay is unavailable; momentum is unknown and confidence stays low. 21/53 observation weekly windows, including partial boundary weeks, saturated the 20-post first page. Those counts remain labeled lower bounds even after confirmed exhaustion. Saturation exceeds 25 percent, so confidence is capped at medium. | unmeasured |
| straight leg jeans | rising | 433 | 17 | 0.370 | unknown | low | not_comparable | steady | 84 | low | disagree | 0.000 | Unvisited, truncated or unknown term/corpus searches are not observed absence. Decay is unavailable; momentum is unknown and confidence stays low. | unmeasured |
| poplin shirt | rising | 111 | 6 | 0.523 | unknown | low | not_comparable | steady | 26 | low | disagree | 0.000 | Unvisited, truncated or unknown term/corpus searches are not observed absence. Decay is unavailable; momentum is unknown and confidence stays low. | unmeasured |
| slim sneakers | rising | 27 | 3 | 0.519 | unknown | low | not_comparable | unknown | 1 | low | not_comparable | 0.000 | Unvisited, truncated or unknown term/corpus searches are not observed absence. Decay is unavailable; momentum is unknown and confidence stays low. | unmeasured |
| soft suiting | rising | 22 | 2 | 0.500 | unknown | low | not_comparable | unknown | 2 | low | not_comparable | 0.000 | Unvisited, truncated or unknown term/corpus searches are not observed absence. Decay is unavailable; momentum is unknown and confidence stays low. | unmeasured |
| balloon jeans | fading | 13 | 2 | 0.308 | unknown | low | not_comparable | unknown | 2 | low | not_comparable | 0.000 | Unvisited, truncated or unknown term/corpus searches are not observed absence. Decay is unavailable; momentum is unknown and confidence stays low. | unmeasured |
| skinny jeans | fading | 1027 | 17 | 0.600 | unknown | low | not_comparable | steady | 178 | low | disagree | 0.396 | Unvisited, truncated or unknown term/corpus searches are not observed absence. Decay is unavailable; momentum is unknown and confidence stays low. 21/53 observation weekly windows, including partial boundary weeks, saturated the 20-post first page. Those counts remain labeled lower bounds even after confirmed exhaustion. Saturation exceeds 25 percent, so confidence is capped at medium. | unmeasured |
| dad sneakers | fading | 117 | 7 | 0.598 | unknown | low | not_comparable | unknown | 15 | low | not_comparable | 0.000 | Unvisited, truncated or unknown term/corpus searches are not observed absence. Decay is unavailable; momentum is unknown and confidence stays low. | unmeasured |
| mini bag | fading | 497 | 17 | 0.515 | unknown | low | not_comparable | steady | 95 | low | disagree | 0.000 | Unvisited, truncated or unknown term/corpus searches are not observed absence. Decay is unavailable; momentum is unknown and confidence stays low. | unmeasured |
| high heels | fading | 981 | 16 | 0.551 | unknown | low | not_comparable | steady | 163 | low | disagree | 0.264 | Unvisited, truncated or unknown term/corpus searches are not observed absence. Decay is unavailable; momentum is unknown and confidence stays low. 14/53 observation weekly windows, including partial boundary weeks, saturated the 20-post first page. Those counts remain labeled lower bounds even after confirmed exhaustion. Saturation exceeds 25 percent, so confidence is capped at medium. | unmeasured |
