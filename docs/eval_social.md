# Social Radar evaluation

Status: unmeasured.

Generated at: 2026-11-14T22:33:31.641972Z.

| Measurement | Result |
| --- | --- |
| Corpus posts | 7312 |
| Embedded posts | 0 |
| Held-out queries | unmeasured |
| Successful lookups | unmeasured |
| Lookup errors | unmeasured |
| Recall@5 | unmeasured |
| Recall@10 | unmeasured |
| MRR@30 | unmeasured |
| Lookup p50 (ms) | unmeasured |
| Lookup p95 (ms) | unmeasured |
| Weighted cluster purity by term | unmeasured |
| Assigned post share | 0.0000 |

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
- Seed 42 selects up to 30 term-balanced posts with text and embeddings. Thirty queries are a small sample. Provider failures count as misses. MRR is truncated at the 30 retrieved results.
- Latency includes successful uncached embedding plus Atlas search. It excludes Claude paraphrase time and /analyze verdict time. Timeout failures do not count as successful fast lookups.
- Only a 12-month window is observed. first_seen_in_window is not the trend origin.
- Counts are samples from a search index and may be capped, never platform totals. Quoted terms, uneven coverage, language, spam, missing accounts and search ranking bias the sample.
- Cluster purity measures majority canonical search term, not a real-world community ground truth. Sparse observed interactions and inferred similarity edges limit interpretation.
- Staple and micro annotations are evaluation-only and noisy. Any threshold calibration is in-sample term comparison, not held-out classification accuracy. Report overlap plainly.
- No author handles or raw post text appear in this report. Queries and source URIs remain in ignored local files.

Unverified: No embedded posts available. Retrieval and latency remain unmeasured.
