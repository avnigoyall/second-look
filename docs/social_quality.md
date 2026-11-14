# Social ingestion data quality

Generated: 2026-11-14T23:30:32.389659Z

Counts are sampled search index results, not Bluesky platform totals. The 12-month window and global 35000-post cap limit coverage.

```json
{
  "posts": 24081,
  "search_posts": 23081,
  "thread_posts": 1000,
  "authors": 15146,
  "reply_share": 0.3162659358000083,
  "completed_windows": 528,
  "total_windows": 1060,
  "search_requests": 2471,
  "probe_requests": 19,
  "thread_results": {
    "candidates": 200,
    "duplicate_posts": 301,
    "errors": 0,
    "fetched": 200,
    "new_posts": 1000,
    "per_thread_new_post_cap": 5,
    "requests": 200,
    "skipped_new_posts": 7596,
    "truncated_threads": 200
  },
  "stop_reason": "confirmation_finished",
  "graph_gate": "pass",
  "pagination": "keyset",
  "equal_timestamp_boundary_risk_windows": 531,
  "completed_windows_per_term": {
    "Levi's 501": 48,
    "baggy jeans": 12,
    "ballet flats": 12,
    "barrel jeans": 53,
    "bubble skirt": 53,
    "butter yellow": 18,
    "capri pants": 33,
    "cargo pants": 10,
    "cherry red": 11,
    "chinos": 7,
    "crochet top": 52,
    "graphic tee": 9,
    "leg warmers": 11,
    "leopard print": 10,
    "mesh flats": 53,
    "oxford shirt": 53,
    "plain black tee": 53,
    "quarter zip": 8,
    "sambas": 12,
    "white t-shirt": 10
  },
  "saturated_windows_per_term": {
    "Levi's 501": 9,
    "baggy jeans": 17,
    "ballet flats": 11,
    "barrel jeans": 1,
    "bubble skirt": 0,
    "butter yellow": 25,
    "capri pants": 6,
    "cargo pants": 19,
    "cherry red": 22,
    "chinos": 29,
    "crochet top": 1,
    "graphic tee": 24,
    "leg warmers": 28,
    "leopard print": 23,
    "mesh flats": 0,
    "oxford shirt": 1,
    "plain black tee": 0,
    "quarter zip": 21,
    "sambas": 20,
    "white t-shirt": 15
  },
  "saturation_unknown_windows": 0,
  "saturated_windows": 272,
  "first_page_zero_windows": 66,
  "first_page_short_nonempty_windows": 722,
  "first_page_metadata_sources": {
    "audit_request": 209,
    "saved_single_page": 851
  },
  "original_first_page_counts_unrecoverable_from_saved_state": 209,
  "global_cap": 35000,
  "remaining_capacity": 10919,
  "reserved_press_capacity": 10000,
  "nonpress_available_capacity": 919,
  "press_seed_terms": []
}
```

| Term | Total posts | Search matches | Thread additions | Empty search weeks | Completed windows | Saturated windows |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Levi's 501 | 786 | 781 | 5 | 0 | 48 | 9 |
| baggy jeans | 1494 | 1439 | 55 | 0 | 12 | 17 |
| ballet flats | 1110 | 1090 | 20 | 0 | 12 | 11 |
| barrel jeans | 492 | 472 | 20 | 0 | 53 | 1 |
| bubble skirt | 144 | 134 | 10 | 5 | 53 | 0 |
| butter yellow | 1076 | 1031 | 45 | 1 | 18 | 25 |
| capri pants | 787 | 762 | 25 | 1 | 33 | 6 |
| cargo pants | 1986 | 1891 | 95 | 0 | 10 | 19 |
| cherry red | 2018 | 1963 | 55 | 0 | 11 | 22 |
| chinos | 1991 | 1941 | 50 | 0 | 7 | 29 |
| crochet top | 398 | 388 | 10 | 1 | 52 | 1 |
| graphic tee | 2057 | 2017 | 40 | 0 | 9 | 24 |
| leg warmers | 2017 | 1847 | 170 | 0 | 11 | 28 |
| leopard print | 2164 | 2044 | 120 | 0 | 10 | 23 |
| mesh flats | 21 | 21 | 0 | 37 | 53 | 0 |
| oxford shirt | 500 | 455 | 45 | 0 | 53 | 1 |
| plain black tee | 56 | 56 | 0 | 22 | 53 | 0 |
| quarter zip | 1681 | 1601 | 80 | 0 | 8 | 21 |
| sambas | 1279 | 1259 | 20 | 0 | 12 | 20 |
| white t-shirt | 2167 | 2032 | 135 | 0 | 10 | 15 |

## Per-week coverage gaps

Only search matches determine coverage gaps. Inherited thread terms never fill a search gap. Empty weeks can reflect no indexed phrase matches, not necessarily no platform activity. Unvisited/incomplete windows must not be interpreted as zero activity.

## First-page audit provenance

851 first-page counts were recovered from saved single-page counters. 209 required fresh audit requests because the original first-page counts could not be recovered reliably. Fresh-audit saturation describes the response at audit time. It does not reconstruct missing original per-window flags or assign an external aggregate baseline to individual windows.

```json
{
  "Levi's 501": [],
  "baggy jeans": [],
  "ballet flats": [],
  "barrel jeans": [],
  "bubble skirt": [
    "2025-11-17",
    "2025-11-24",
    "2026-04-13",
    "2026-05-18",
    "2026-08-10"
  ],
  "butter yellow": [
    "2026-08-03"
  ],
  "capri pants": [
    "2025-11-10"
  ],
  "cargo pants": [],
  "cherry red": [],
  "chinos": [],
  "crochet top": [
    "2026-11-09"
  ],
  "graphic tee": [],
  "leg warmers": [],
  "leopard print": [],
  "mesh flats": [
    "2025-11-10",
    "2025-11-17",
    "2025-11-24",
    "2025-12-01",
    "2025-12-08",
    "2025-12-22",
    "2025-12-29",
    "2026-01-05",
    "2026-01-12",
    "2026-01-19",
    "2026-01-26",
    "2026-02-02",
    "2026-02-09",
    "2026-02-16",
    "2026-02-23",
    "2026-03-09",
    "2026-03-23",
    "2026-03-30",
    "2026-04-27",
    "2026-05-11",
    "2026-06-01",
    "2026-06-08",
    "2026-06-15",
    "2026-07-06",
    "2026-07-13",
    "2026-07-20",
    "2026-07-27",
    "2026-08-03",
    "2026-08-17",
    "2026-08-24",
    "2026-08-31",
    "2026-09-07",
    "2026-09-14",
    "2026-09-21",
    "2026-09-28",
    "2026-10-12",
    "2026-11-02"
  ],
  "oxford shirt": [],
  "plain black tee": [
    "2025-11-10",
    "2025-11-17",
    "2025-11-24",
    "2025-12-01",
    "2025-12-22",
    "2025-12-29",
    "2026-01-05",
    "2026-01-26",
    "2026-02-16",
    "2026-03-02",
    "2026-04-06",
    "2026-05-04",
    "2026-05-11",
    "2026-05-18",
    "2026-06-01",
    "2026-06-22",
    "2026-07-06",
    "2026-07-27",
    "2026-08-03",
    "2026-08-24",
    "2026-09-14",
    "2026-10-12"
  ],
  "quarter zip": [],
  "sambas": [],
  "white t-shirt": []
}
```

## Incomplete or unvisited windows

These windows have not been exhausted. A zero here is unknown coverage, not an observed zero.

```json
{
  "Levi's 501": [
    {
      "since": "2026-08-24T00:00:00Z",
      "until": "2026-08-31T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-17T00:00:00Z",
      "until": "2026-08-24T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-10T00:00:00Z",
      "until": "2026-08-17T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-03T00:00:00Z",
      "until": "2026-08-10T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-27T00:00:00Z",
      "until": "2026-08-03T00:00:00Z",
      "pages": 2
    }
  ],
  "baggy jeans": [
    {
      "since": "2026-08-24T00:00:00Z",
      "until": "2026-08-31T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-17T00:00:00Z",
      "until": "2026-08-24T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-10T00:00:00Z",
      "until": "2026-08-17T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-03T00:00:00Z",
      "until": "2026-08-10T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-27T00:00:00Z",
      "until": "2026-08-03T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-20T00:00:00Z",
      "until": "2026-07-27T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-13T00:00:00Z",
      "until": "2026-07-20T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-06T00:00:00Z",
      "until": "2026-07-13T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-29T00:00:00Z",
      "until": "2026-07-06T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-22T00:00:00Z",
      "until": "2026-06-29T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-15T00:00:00Z",
      "until": "2026-06-22T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-08T00:00:00Z",
      "until": "2026-06-15T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-01T00:00:00Z",
      "until": "2026-06-08T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-25T00:00:00Z",
      "until": "2026-06-01T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-18T00:00:00Z",
      "until": "2026-05-25T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-11T00:00:00Z",
      "until": "2026-05-18T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-04T00:00:00Z",
      "until": "2026-05-11T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-27T00:00:00Z",
      "until": "2026-05-04T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-20T00:00:00Z",
      "until": "2026-04-27T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-13T00:00:00Z",
      "until": "2026-04-20T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-06T00:00:00Z",
      "until": "2026-04-13T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-30T00:00:00Z",
      "until": "2026-04-06T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-23T00:00:00Z",
      "until": "2026-03-30T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-16T00:00:00Z",
      "until": "2026-03-23T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-09T00:00:00Z",
      "until": "2026-03-16T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-02T00:00:00Z",
      "until": "2026-03-09T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-23T00:00:00Z",
      "until": "2026-03-02T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-16T00:00:00Z",
      "until": "2026-02-23T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-09T00:00:00Z",
      "until": "2026-02-16T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-02T00:00:00Z",
      "until": "2026-02-09T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-26T00:00:00Z",
      "until": "2026-02-02T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-19T00:00:00Z",
      "until": "2026-01-26T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-12T00:00:00Z",
      "until": "2026-01-19T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-05T00:00:00Z",
      "until": "2026-01-12T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-29T00:00:00Z",
      "until": "2026-01-05T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-22T00:00:00Z",
      "until": "2025-12-29T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-15T00:00:00Z",
      "until": "2025-12-22T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-08T00:00:00Z",
      "until": "2025-12-15T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-01T00:00:00Z",
      "until": "2025-12-08T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-11-24T00:00:00Z",
      "until": "2025-12-01T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-11-17T00:00:00Z",
      "until": "2025-11-24T00:00:00Z",
      "pages": 2
    }
  ],
  "ballet flats": [
    {
      "since": "2026-08-24T00:00:00Z",
      "until": "2026-08-31T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-17T00:00:00Z",
      "until": "2026-08-24T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-10T00:00:00Z",
      "until": "2026-08-17T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-03T00:00:00Z",
      "until": "2026-08-10T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-27T00:00:00Z",
      "until": "2026-08-03T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-20T00:00:00Z",
      "until": "2026-07-27T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-13T00:00:00Z",
      "until": "2026-07-20T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-06T00:00:00Z",
      "until": "2026-07-13T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-29T00:00:00Z",
      "until": "2026-07-06T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-22T00:00:00Z",
      "until": "2026-06-29T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-15T00:00:00Z",
      "until": "2026-06-22T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-08T00:00:00Z",
      "until": "2026-06-15T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-01T00:00:00Z",
      "until": "2026-06-08T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-25T00:00:00Z",
      "until": "2026-06-01T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-18T00:00:00Z",
      "until": "2026-05-25T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-11T00:00:00Z",
      "until": "2026-05-18T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-04T00:00:00Z",
      "until": "2026-05-11T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-27T00:00:00Z",
      "until": "2026-05-04T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-20T00:00:00Z",
      "until": "2026-04-27T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-13T00:00:00Z",
      "until": "2026-04-20T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-06T00:00:00Z",
      "until": "2026-04-13T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-30T00:00:00Z",
      "until": "2026-04-06T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-23T00:00:00Z",
      "until": "2026-03-30T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-16T00:00:00Z",
      "until": "2026-03-23T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-09T00:00:00Z",
      "until": "2026-03-16T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-02T00:00:00Z",
      "until": "2026-03-09T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-23T00:00:00Z",
      "until": "2026-03-02T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-16T00:00:00Z",
      "until": "2026-02-23T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-09T00:00:00Z",
      "until": "2026-02-16T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-02T00:00:00Z",
      "until": "2026-02-09T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-26T00:00:00Z",
      "until": "2026-02-02T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-19T00:00:00Z",
      "until": "2026-01-26T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-12T00:00:00Z",
      "until": "2026-01-19T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-05T00:00:00Z",
      "until": "2026-01-12T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-29T00:00:00Z",
      "until": "2026-01-05T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-22T00:00:00Z",
      "until": "2025-12-29T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-15T00:00:00Z",
      "until": "2025-12-22T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-08T00:00:00Z",
      "until": "2025-12-15T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-01T00:00:00Z",
      "until": "2025-12-08T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-11-24T00:00:00Z",
      "until": "2025-12-01T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-11-17T00:00:00Z",
      "until": "2025-11-24T00:00:00Z",
      "pages": 2
    }
  ],
  "barrel jeans": [],
  "bubble skirt": [],
  "butter yellow": [
    {
      "since": "2026-08-24T00:00:00Z",
      "until": "2026-08-31T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-17T00:00:00Z",
      "until": "2026-08-24T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-10T00:00:00Z",
      "until": "2026-08-17T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-03T00:00:00Z",
      "until": "2026-08-10T00:00:00Z",
      "pages": 1
    },
    {
      "since": "2026-07-27T00:00:00Z",
      "until": "2026-08-03T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-20T00:00:00Z",
      "until": "2026-07-27T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-13T00:00:00Z",
      "until": "2026-07-20T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-06T00:00:00Z",
      "until": "2026-07-13T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-29T00:00:00Z",
      "until": "2026-07-06T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-22T00:00:00Z",
      "until": "2026-06-29T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-15T00:00:00Z",
      "until": "2026-06-22T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-08T00:00:00Z",
      "until": "2026-06-15T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-01T00:00:00Z",
      "until": "2026-06-08T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-25T00:00:00Z",
      "until": "2026-06-01T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-18T00:00:00Z",
      "until": "2026-05-25T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-11T00:00:00Z",
      "until": "2026-05-18T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-04T00:00:00Z",
      "until": "2026-05-11T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-27T00:00:00Z",
      "until": "2026-05-04T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-20T00:00:00Z",
      "until": "2026-04-27T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-13T00:00:00Z",
      "until": "2026-04-20T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-06T00:00:00Z",
      "until": "2026-04-13T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-30T00:00:00Z",
      "until": "2026-04-06T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-23T00:00:00Z",
      "until": "2026-03-30T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-16T00:00:00Z",
      "until": "2026-03-23T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-09T00:00:00Z",
      "until": "2026-03-16T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-02T00:00:00Z",
      "until": "2026-03-09T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-23T00:00:00Z",
      "until": "2026-03-02T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-16T00:00:00Z",
      "until": "2026-02-23T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-09T00:00:00Z",
      "until": "2026-02-16T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-02T00:00:00Z",
      "until": "2026-02-09T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-26T00:00:00Z",
      "until": "2026-02-02T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-12T00:00:00Z",
      "until": "2026-01-19T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-05T00:00:00Z",
      "until": "2026-01-12T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-08T00:00:00Z",
      "until": "2025-12-15T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-01T00:00:00Z",
      "until": "2025-12-08T00:00:00Z",
      "pages": 2
    }
  ],
  "capri pants": [
    {
      "since": "2026-08-24T00:00:00Z",
      "until": "2026-08-31T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-17T00:00:00Z",
      "until": "2026-08-24T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-10T00:00:00Z",
      "until": "2026-08-17T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-27T00:00:00Z",
      "until": "2026-08-03T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-20T00:00:00Z",
      "until": "2026-07-27T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-13T00:00:00Z",
      "until": "2026-07-20T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-06T00:00:00Z",
      "until": "2026-07-13T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-29T00:00:00Z",
      "until": "2026-07-06T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-15T00:00:00Z",
      "until": "2026-06-22T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-08T00:00:00Z",
      "until": "2026-06-15T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-01T00:00:00Z",
      "until": "2026-06-08T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-18T00:00:00Z",
      "until": "2026-05-25T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-11T00:00:00Z",
      "until": "2026-05-18T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-20T00:00:00Z",
      "until": "2026-04-27T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-13T00:00:00Z",
      "until": "2026-04-20T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-23T00:00:00Z",
      "until": "2026-03-30T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-16T00:00:00Z",
      "until": "2026-03-23T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-09T00:00:00Z",
      "until": "2026-03-16T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-26T00:00:00Z",
      "until": "2026-02-02T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-19T00:00:00Z",
      "until": "2026-01-26T00:00:00Z",
      "pages": 2
    }
  ],
  "cargo pants": [
    {
      "since": "2026-10-12T00:00:00Z",
      "until": "2026-10-19T00:00:00Z",
      "pages": 3
    },
    {
      "since": "2026-08-24T00:00:00Z",
      "until": "2026-08-31T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-17T00:00:00Z",
      "until": "2026-08-24T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-10T00:00:00Z",
      "until": "2026-08-17T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-03T00:00:00Z",
      "until": "2026-08-10T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-27T00:00:00Z",
      "until": "2026-08-03T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-20T00:00:00Z",
      "until": "2026-07-27T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-13T00:00:00Z",
      "until": "2026-07-20T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-06T00:00:00Z",
      "until": "2026-07-13T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-29T00:00:00Z",
      "until": "2026-07-06T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-22T00:00:00Z",
      "until": "2026-06-29T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-15T00:00:00Z",
      "until": "2026-06-22T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-08T00:00:00Z",
      "until": "2026-06-15T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-01T00:00:00Z",
      "until": "2026-06-08T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-25T00:00:00Z",
      "until": "2026-06-01T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-18T00:00:00Z",
      "until": "2026-05-25T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-11T00:00:00Z",
      "until": "2026-05-18T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-04T00:00:00Z",
      "until": "2026-05-11T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-27T00:00:00Z",
      "until": "2026-05-04T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-20T00:00:00Z",
      "until": "2026-04-27T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-13T00:00:00Z",
      "until": "2026-04-20T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-06T00:00:00Z",
      "until": "2026-04-13T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-30T00:00:00Z",
      "until": "2026-04-06T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-23T00:00:00Z",
      "until": "2026-03-30T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-16T00:00:00Z",
      "until": "2026-03-23T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-09T00:00:00Z",
      "until": "2026-03-16T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-02T00:00:00Z",
      "until": "2026-03-09T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-23T00:00:00Z",
      "until": "2026-03-02T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-16T00:00:00Z",
      "until": "2026-02-23T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-09T00:00:00Z",
      "until": "2026-02-16T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-02T00:00:00Z",
      "until": "2026-02-09T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-26T00:00:00Z",
      "until": "2026-02-02T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-19T00:00:00Z",
      "until": "2026-01-26T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-12T00:00:00Z",
      "until": "2026-01-19T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-05T00:00:00Z",
      "until": "2026-01-12T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-29T00:00:00Z",
      "until": "2026-01-05T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-22T00:00:00Z",
      "until": "2025-12-29T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-15T00:00:00Z",
      "until": "2025-12-22T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-08T00:00:00Z",
      "until": "2025-12-15T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-01T00:00:00Z",
      "until": "2025-12-08T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-11-24T00:00:00Z",
      "until": "2025-12-01T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-11-17T00:00:00Z",
      "until": "2025-11-24T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-11-14T22:25:01.051247Z",
      "until": "2025-11-17T00:00:00Z",
      "pages": 2
    }
  ],
  "cherry red": [
    {
      "since": "2026-09-28T00:00:00Z",
      "until": "2026-10-05T00:00:00Z",
      "pages": 3
    },
    {
      "since": "2026-08-24T00:00:00Z",
      "until": "2026-08-31T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-17T00:00:00Z",
      "until": "2026-08-24T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-10T00:00:00Z",
      "until": "2026-08-17T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-03T00:00:00Z",
      "until": "2026-08-10T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-27T00:00:00Z",
      "until": "2026-08-03T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-20T00:00:00Z",
      "until": "2026-07-27T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-13T00:00:00Z",
      "until": "2026-07-20T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-06T00:00:00Z",
      "until": "2026-07-13T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-29T00:00:00Z",
      "until": "2026-07-06T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-22T00:00:00Z",
      "until": "2026-06-29T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-15T00:00:00Z",
      "until": "2026-06-22T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-08T00:00:00Z",
      "until": "2026-06-15T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-01T00:00:00Z",
      "until": "2026-06-08T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-25T00:00:00Z",
      "until": "2026-06-01T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-18T00:00:00Z",
      "until": "2026-05-25T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-11T00:00:00Z",
      "until": "2026-05-18T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-04T00:00:00Z",
      "until": "2026-05-11T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-27T00:00:00Z",
      "until": "2026-05-04T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-20T00:00:00Z",
      "until": "2026-04-27T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-13T00:00:00Z",
      "until": "2026-04-20T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-06T00:00:00Z",
      "until": "2026-04-13T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-30T00:00:00Z",
      "until": "2026-04-06T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-23T00:00:00Z",
      "until": "2026-03-30T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-16T00:00:00Z",
      "until": "2026-03-23T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-09T00:00:00Z",
      "until": "2026-03-16T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-02T00:00:00Z",
      "until": "2026-03-09T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-23T00:00:00Z",
      "until": "2026-03-02T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-16T00:00:00Z",
      "until": "2026-02-23T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-09T00:00:00Z",
      "until": "2026-02-16T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-02T00:00:00Z",
      "until": "2026-02-09T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-26T00:00:00Z",
      "until": "2026-02-02T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-19T00:00:00Z",
      "until": "2026-01-26T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-12T00:00:00Z",
      "until": "2026-01-19T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-05T00:00:00Z",
      "until": "2026-01-12T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-29T00:00:00Z",
      "until": "2026-01-05T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-22T00:00:00Z",
      "until": "2025-12-29T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-15T00:00:00Z",
      "until": "2025-12-22T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-08T00:00:00Z",
      "until": "2025-12-15T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-01T00:00:00Z",
      "until": "2025-12-08T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-11-24T00:00:00Z",
      "until": "2025-12-01T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-11-17T00:00:00Z",
      "until": "2025-11-24T00:00:00Z",
      "pages": 2
    }
  ],
  "chinos": [
    {
      "since": "2026-11-09T00:00:00Z",
      "until": "2026-11-14T22:25:01.051247Z",
      "pages": 3
    },
    {
      "since": "2026-10-12T00:00:00Z",
      "until": "2026-10-19T00:00:00Z",
      "pages": 3
    },
    {
      "since": "2026-09-07T00:00:00Z",
      "until": "2026-09-14T00:00:00Z",
      "pages": 3
    },
    {
      "since": "2026-08-31T00:00:00Z",
      "until": "2026-09-07T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-24T00:00:00Z",
      "until": "2026-08-31T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-17T00:00:00Z",
      "until": "2026-08-24T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-10T00:00:00Z",
      "until": "2026-08-17T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-03T00:00:00Z",
      "until": "2026-08-10T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-27T00:00:00Z",
      "until": "2026-08-03T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-20T00:00:00Z",
      "until": "2026-07-27T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-13T00:00:00Z",
      "until": "2026-07-20T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-06T00:00:00Z",
      "until": "2026-07-13T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-29T00:00:00Z",
      "until": "2026-07-06T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-22T00:00:00Z",
      "until": "2026-06-29T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-15T00:00:00Z",
      "until": "2026-06-22T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-08T00:00:00Z",
      "until": "2026-06-15T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-01T00:00:00Z",
      "until": "2026-06-08T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-25T00:00:00Z",
      "until": "2026-06-01T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-18T00:00:00Z",
      "until": "2026-05-25T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-11T00:00:00Z",
      "until": "2026-05-18T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-04T00:00:00Z",
      "until": "2026-05-11T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-27T00:00:00Z",
      "until": "2026-05-04T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-20T00:00:00Z",
      "until": "2026-04-27T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-13T00:00:00Z",
      "until": "2026-04-20T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-06T00:00:00Z",
      "until": "2026-04-13T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-30T00:00:00Z",
      "until": "2026-04-06T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-23T00:00:00Z",
      "until": "2026-03-30T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-16T00:00:00Z",
      "until": "2026-03-23T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-09T00:00:00Z",
      "until": "2026-03-16T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-02T00:00:00Z",
      "until": "2026-03-09T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-23T00:00:00Z",
      "until": "2026-03-02T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-16T00:00:00Z",
      "until": "2026-02-23T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-09T00:00:00Z",
      "until": "2026-02-16T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-02T00:00:00Z",
      "until": "2026-02-09T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-26T00:00:00Z",
      "until": "2026-02-02T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-19T00:00:00Z",
      "until": "2026-01-26T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-12T00:00:00Z",
      "until": "2026-01-19T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-05T00:00:00Z",
      "until": "2026-01-12T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-29T00:00:00Z",
      "until": "2026-01-05T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-22T00:00:00Z",
      "until": "2025-12-29T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-15T00:00:00Z",
      "until": "2025-12-22T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-08T00:00:00Z",
      "until": "2025-12-15T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-01T00:00:00Z",
      "until": "2025-12-08T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-11-24T00:00:00Z",
      "until": "2025-12-01T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-11-17T00:00:00Z",
      "until": "2025-11-24T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-11-14T22:25:01.051247Z",
      "until": "2025-11-17T00:00:00Z",
      "pages": 2
    }
  ],
  "crochet top": [
    {
      "since": "2026-03-23T00:00:00Z",
      "until": "2026-03-30T00:00:00Z",
      "pages": 2
    }
  ],
  "graphic tee": [
    {
      "since": "2026-11-02T00:00:00Z",
      "until": "2026-11-09T00:00:00Z",
      "pages": 3
    },
    {
      "since": "2026-10-12T00:00:00Z",
      "until": "2026-10-19T00:00:00Z",
      "pages": 3
    },
    {
      "since": "2026-08-24T00:00:00Z",
      "until": "2026-08-31T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-17T00:00:00Z",
      "until": "2026-08-24T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-10T00:00:00Z",
      "until": "2026-08-17T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-03T00:00:00Z",
      "until": "2026-08-10T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-27T00:00:00Z",
      "until": "2026-08-03T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-20T00:00:00Z",
      "until": "2026-07-27T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-13T00:00:00Z",
      "until": "2026-07-20T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-06T00:00:00Z",
      "until": "2026-07-13T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-29T00:00:00Z",
      "until": "2026-07-06T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-22T00:00:00Z",
      "until": "2026-06-29T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-15T00:00:00Z",
      "until": "2026-06-22T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-08T00:00:00Z",
      "until": "2026-06-15T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-01T00:00:00Z",
      "until": "2026-06-08T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-25T00:00:00Z",
      "until": "2026-06-01T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-18T00:00:00Z",
      "until": "2026-05-25T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-11T00:00:00Z",
      "until": "2026-05-18T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-04T00:00:00Z",
      "until": "2026-05-11T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-27T00:00:00Z",
      "until": "2026-05-04T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-20T00:00:00Z",
      "until": "2026-04-27T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-13T00:00:00Z",
      "until": "2026-04-20T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-06T00:00:00Z",
      "until": "2026-04-13T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-30T00:00:00Z",
      "until": "2026-04-06T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-23T00:00:00Z",
      "until": "2026-03-30T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-16T00:00:00Z",
      "until": "2026-03-23T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-09T00:00:00Z",
      "until": "2026-03-16T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-02T00:00:00Z",
      "until": "2026-03-09T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-23T00:00:00Z",
      "until": "2026-03-02T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-16T00:00:00Z",
      "until": "2026-02-23T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-09T00:00:00Z",
      "until": "2026-02-16T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-02T00:00:00Z",
      "until": "2026-02-09T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-26T00:00:00Z",
      "until": "2026-02-02T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-19T00:00:00Z",
      "until": "2026-01-26T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-12T00:00:00Z",
      "until": "2026-01-19T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-05T00:00:00Z",
      "until": "2026-01-12T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-29T00:00:00Z",
      "until": "2026-01-05T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-22T00:00:00Z",
      "until": "2025-12-29T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-15T00:00:00Z",
      "until": "2025-12-22T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-08T00:00:00Z",
      "until": "2025-12-15T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-01T00:00:00Z",
      "until": "2025-12-08T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-11-24T00:00:00Z",
      "until": "2025-12-01T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-11-17T00:00:00Z",
      "until": "2025-11-24T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-11-14T22:25:01.051247Z",
      "until": "2025-11-17T00:00:00Z",
      "pages": 2
    }
  ],
  "leg warmers": [
    {
      "since": "2026-11-02T00:00:00Z",
      "until": "2026-11-09T00:00:00Z",
      "pages": 3
    },
    {
      "since": "2026-08-24T00:00:00Z",
      "until": "2026-08-31T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-17T00:00:00Z",
      "until": "2026-08-24T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-10T00:00:00Z",
      "until": "2026-08-17T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-03T00:00:00Z",
      "until": "2026-08-10T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-27T00:00:00Z",
      "until": "2026-08-03T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-20T00:00:00Z",
      "until": "2026-07-27T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-13T00:00:00Z",
      "until": "2026-07-20T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-06T00:00:00Z",
      "until": "2026-07-13T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-29T00:00:00Z",
      "until": "2026-07-06T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-22T00:00:00Z",
      "until": "2026-06-29T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-15T00:00:00Z",
      "until": "2026-06-22T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-08T00:00:00Z",
      "until": "2026-06-15T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-01T00:00:00Z",
      "until": "2026-06-08T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-25T00:00:00Z",
      "until": "2026-06-01T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-18T00:00:00Z",
      "until": "2026-05-25T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-11T00:00:00Z",
      "until": "2026-05-18T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-04T00:00:00Z",
      "until": "2026-05-11T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-27T00:00:00Z",
      "until": "2026-05-04T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-20T00:00:00Z",
      "until": "2026-04-27T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-13T00:00:00Z",
      "until": "2026-04-20T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-06T00:00:00Z",
      "until": "2026-04-13T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-30T00:00:00Z",
      "until": "2026-04-06T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-23T00:00:00Z",
      "until": "2026-03-30T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-16T00:00:00Z",
      "until": "2026-03-23T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-09T00:00:00Z",
      "until": "2026-03-16T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-02T00:00:00Z",
      "until": "2026-03-09T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-23T00:00:00Z",
      "until": "2026-03-02T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-16T00:00:00Z",
      "until": "2026-02-23T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-09T00:00:00Z",
      "until": "2026-02-16T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-02T00:00:00Z",
      "until": "2026-02-09T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-26T00:00:00Z",
      "until": "2026-02-02T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-19T00:00:00Z",
      "until": "2026-01-26T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-12T00:00:00Z",
      "until": "2026-01-19T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-05T00:00:00Z",
      "until": "2026-01-12T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-29T00:00:00Z",
      "until": "2026-01-05T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-22T00:00:00Z",
      "until": "2025-12-29T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-15T00:00:00Z",
      "until": "2025-12-22T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-08T00:00:00Z",
      "until": "2025-12-15T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-01T00:00:00Z",
      "until": "2025-12-08T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-11-24T00:00:00Z",
      "until": "2025-12-01T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-11-17T00:00:00Z",
      "until": "2025-11-24T00:00:00Z",
      "pages": 2
    }
  ],
  "leopard print": [
    {
      "since": "2026-08-31T00:00:00Z",
      "until": "2026-09-07T00:00:00Z",
      "pages": 3
    },
    {
      "since": "2026-08-24T00:00:00Z",
      "until": "2026-08-31T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-17T00:00:00Z",
      "until": "2026-08-24T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-10T00:00:00Z",
      "until": "2026-08-17T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-03T00:00:00Z",
      "until": "2026-08-10T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-27T00:00:00Z",
      "until": "2026-08-03T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-20T00:00:00Z",
      "until": "2026-07-27T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-13T00:00:00Z",
      "until": "2026-07-20T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-06T00:00:00Z",
      "until": "2026-07-13T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-29T00:00:00Z",
      "until": "2026-07-06T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-22T00:00:00Z",
      "until": "2026-06-29T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-15T00:00:00Z",
      "until": "2026-06-22T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-08T00:00:00Z",
      "until": "2026-06-15T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-01T00:00:00Z",
      "until": "2026-06-08T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-25T00:00:00Z",
      "until": "2026-06-01T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-18T00:00:00Z",
      "until": "2026-05-25T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-11T00:00:00Z",
      "until": "2026-05-18T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-04T00:00:00Z",
      "until": "2026-05-11T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-27T00:00:00Z",
      "until": "2026-05-04T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-20T00:00:00Z",
      "until": "2026-04-27T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-13T00:00:00Z",
      "until": "2026-04-20T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-06T00:00:00Z",
      "until": "2026-04-13T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-30T00:00:00Z",
      "until": "2026-04-06T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-23T00:00:00Z",
      "until": "2026-03-30T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-16T00:00:00Z",
      "until": "2026-03-23T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-09T00:00:00Z",
      "until": "2026-03-16T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-02T00:00:00Z",
      "until": "2026-03-09T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-23T00:00:00Z",
      "until": "2026-03-02T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-16T00:00:00Z",
      "until": "2026-02-23T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-09T00:00:00Z",
      "until": "2026-02-16T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-02T00:00:00Z",
      "until": "2026-02-09T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-26T00:00:00Z",
      "until": "2026-02-02T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-19T00:00:00Z",
      "until": "2026-01-26T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-12T00:00:00Z",
      "until": "2026-01-19T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-05T00:00:00Z",
      "until": "2026-01-12T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-29T00:00:00Z",
      "until": "2026-01-05T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-22T00:00:00Z",
      "until": "2025-12-29T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-15T00:00:00Z",
      "until": "2025-12-22T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-08T00:00:00Z",
      "until": "2025-12-15T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-01T00:00:00Z",
      "until": "2025-12-08T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-11-24T00:00:00Z",
      "until": "2025-12-01T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-11-17T00:00:00Z",
      "until": "2025-11-24T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-11-14T22:25:01.051247Z",
      "until": "2025-11-17T00:00:00Z",
      "pages": 2
    }
  ],
  "mesh flats": [],
  "oxford shirt": [],
  "plain black tee": [],
  "quarter zip": [
    {
      "since": "2026-11-09T00:00:00Z",
      "until": "2026-11-14T22:25:01.051247Z",
      "pages": 3
    },
    {
      "since": "2026-11-02T00:00:00Z",
      "until": "2026-11-09T00:00:00Z",
      "pages": 3
    },
    {
      "since": "2026-10-26T00:00:00Z",
      "until": "2026-11-02T00:00:00Z",
      "pages": 3
    },
    {
      "since": "2026-08-24T00:00:00Z",
      "until": "2026-08-31T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-17T00:00:00Z",
      "until": "2026-08-24T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-10T00:00:00Z",
      "until": "2026-08-17T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-03T00:00:00Z",
      "until": "2026-08-10T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-27T00:00:00Z",
      "until": "2026-08-03T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-20T00:00:00Z",
      "until": "2026-07-27T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-13T00:00:00Z",
      "until": "2026-07-20T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-06T00:00:00Z",
      "until": "2026-07-13T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-29T00:00:00Z",
      "until": "2026-07-06T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-22T00:00:00Z",
      "until": "2026-06-29T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-15T00:00:00Z",
      "until": "2026-06-22T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-08T00:00:00Z",
      "until": "2026-06-15T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-01T00:00:00Z",
      "until": "2026-06-08T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-25T00:00:00Z",
      "until": "2026-06-01T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-18T00:00:00Z",
      "until": "2026-05-25T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-11T00:00:00Z",
      "until": "2026-05-18T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-04T00:00:00Z",
      "until": "2026-05-11T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-27T00:00:00Z",
      "until": "2026-05-04T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-20T00:00:00Z",
      "until": "2026-04-27T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-13T00:00:00Z",
      "until": "2026-04-20T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-06T00:00:00Z",
      "until": "2026-04-13T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-30T00:00:00Z",
      "until": "2026-04-06T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-23T00:00:00Z",
      "until": "2026-03-30T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-16T00:00:00Z",
      "until": "2026-03-23T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-09T00:00:00Z",
      "until": "2026-03-16T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-02T00:00:00Z",
      "until": "2026-03-09T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-23T00:00:00Z",
      "until": "2026-03-02T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-16T00:00:00Z",
      "until": "2026-02-23T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-09T00:00:00Z",
      "until": "2026-02-16T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-02T00:00:00Z",
      "until": "2026-02-09T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-26T00:00:00Z",
      "until": "2026-02-02T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-19T00:00:00Z",
      "until": "2026-01-26T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-12T00:00:00Z",
      "until": "2026-01-19T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-05T00:00:00Z",
      "until": "2026-01-12T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-29T00:00:00Z",
      "until": "2026-01-05T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-22T00:00:00Z",
      "until": "2025-12-29T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-15T00:00:00Z",
      "until": "2025-12-22T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-08T00:00:00Z",
      "until": "2025-12-15T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-01T00:00:00Z",
      "until": "2025-12-08T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-11-24T00:00:00Z",
      "until": "2025-12-01T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-11-17T00:00:00Z",
      "until": "2025-11-24T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-11-14T22:25:01.051247Z",
      "until": "2025-11-17T00:00:00Z",
      "pages": 2
    }
  ],
  "sambas": [
    {
      "since": "2026-08-24T00:00:00Z",
      "until": "2026-08-31T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-17T00:00:00Z",
      "until": "2026-08-24T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-10T00:00:00Z",
      "until": "2026-08-17T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-03T00:00:00Z",
      "until": "2026-08-10T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-27T00:00:00Z",
      "until": "2026-08-03T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-20T00:00:00Z",
      "until": "2026-07-27T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-13T00:00:00Z",
      "until": "2026-07-20T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-06T00:00:00Z",
      "until": "2026-07-13T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-29T00:00:00Z",
      "until": "2026-07-06T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-22T00:00:00Z",
      "until": "2026-06-29T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-15T00:00:00Z",
      "until": "2026-06-22T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-08T00:00:00Z",
      "until": "2026-06-15T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-01T00:00:00Z",
      "until": "2026-06-08T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-25T00:00:00Z",
      "until": "2026-06-01T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-18T00:00:00Z",
      "until": "2026-05-25T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-11T00:00:00Z",
      "until": "2026-05-18T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-04T00:00:00Z",
      "until": "2026-05-11T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-27T00:00:00Z",
      "until": "2026-05-04T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-20T00:00:00Z",
      "until": "2026-04-27T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-13T00:00:00Z",
      "until": "2026-04-20T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-06T00:00:00Z",
      "until": "2026-04-13T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-30T00:00:00Z",
      "until": "2026-04-06T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-23T00:00:00Z",
      "until": "2026-03-30T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-16T00:00:00Z",
      "until": "2026-03-23T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-09T00:00:00Z",
      "until": "2026-03-16T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-02T00:00:00Z",
      "until": "2026-03-09T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-23T00:00:00Z",
      "until": "2026-03-02T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-16T00:00:00Z",
      "until": "2026-02-23T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-09T00:00:00Z",
      "until": "2026-02-16T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-02T00:00:00Z",
      "until": "2026-02-09T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-26T00:00:00Z",
      "until": "2026-02-02T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-19T00:00:00Z",
      "until": "2026-01-26T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-12T00:00:00Z",
      "until": "2026-01-19T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-05T00:00:00Z",
      "until": "2026-01-12T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-29T00:00:00Z",
      "until": "2026-01-05T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-22T00:00:00Z",
      "until": "2025-12-29T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-15T00:00:00Z",
      "until": "2025-12-22T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-08T00:00:00Z",
      "until": "2025-12-15T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-01T00:00:00Z",
      "until": "2025-12-08T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-11-24T00:00:00Z",
      "until": "2025-12-01T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-11-17T00:00:00Z",
      "until": "2025-11-24T00:00:00Z",
      "pages": 2
    }
  ],
  "white t-shirt": [
    {
      "since": "2026-10-19T00:00:00Z",
      "until": "2026-10-26T00:00:00Z",
      "pages": 3
    },
    {
      "since": "2026-08-24T00:00:00Z",
      "until": "2026-08-31T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-17T00:00:00Z",
      "until": "2026-08-24T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-10T00:00:00Z",
      "until": "2026-08-17T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-08-03T00:00:00Z",
      "until": "2026-08-10T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-27T00:00:00Z",
      "until": "2026-08-03T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-20T00:00:00Z",
      "until": "2026-07-27T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-13T00:00:00Z",
      "until": "2026-07-20T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-07-06T00:00:00Z",
      "until": "2026-07-13T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-29T00:00:00Z",
      "until": "2026-07-06T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-22T00:00:00Z",
      "until": "2026-06-29T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-15T00:00:00Z",
      "until": "2026-06-22T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-08T00:00:00Z",
      "until": "2026-06-15T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-06-01T00:00:00Z",
      "until": "2026-06-08T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-25T00:00:00Z",
      "until": "2026-06-01T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-18T00:00:00Z",
      "until": "2026-05-25T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-11T00:00:00Z",
      "until": "2026-05-18T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-05-04T00:00:00Z",
      "until": "2026-05-11T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-27T00:00:00Z",
      "until": "2026-05-04T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-20T00:00:00Z",
      "until": "2026-04-27T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-13T00:00:00Z",
      "until": "2026-04-20T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-04-06T00:00:00Z",
      "until": "2026-04-13T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-30T00:00:00Z",
      "until": "2026-04-06T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-23T00:00:00Z",
      "until": "2026-03-30T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-16T00:00:00Z",
      "until": "2026-03-23T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-09T00:00:00Z",
      "until": "2026-03-16T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-03-02T00:00:00Z",
      "until": "2026-03-09T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-23T00:00:00Z",
      "until": "2026-03-02T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-16T00:00:00Z",
      "until": "2026-02-23T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-09T00:00:00Z",
      "until": "2026-02-16T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-02-02T00:00:00Z",
      "until": "2026-02-09T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-26T00:00:00Z",
      "until": "2026-02-02T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-19T00:00:00Z",
      "until": "2026-01-26T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-12T00:00:00Z",
      "until": "2026-01-19T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2026-01-05T00:00:00Z",
      "until": "2026-01-12T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-29T00:00:00Z",
      "until": "2026-01-05T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-22T00:00:00Z",
      "until": "2025-12-29T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-15T00:00:00Z",
      "until": "2025-12-22T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-08T00:00:00Z",
      "until": "2025-12-15T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-12-01T00:00:00Z",
      "until": "2025-12-08T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-11-24T00:00:00Z",
      "until": "2025-12-01T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-11-17T00:00:00Z",
      "until": "2025-11-24T00:00:00Z",
      "pages": 2
    },
    {
      "since": "2025-11-14T22:25:01.051247Z",
      "until": "2025-11-17T00:00:00Z",
      "pages": 2
    }
  ]
}
```

## Limits

Quoted phrase search misses wording variants. Cherry red, sambas and other broad phrases may include non-fashion posts. Sample balance depends on index availability. Labels are a draft and not verified ground truth.
Thread enrichment adds real reply edges but can oversample active threads. A stable top-200 candidate list is fetched with at most five new posts retained per thread, preserving room under the global cap. Skipped posts and truncated threads are counted. Posts marked source=thread should be excluded from search trend counts. Posts matched by multiple terms remain deduplicated by URI with every matched term in terms.
Author identity metadata uses only hashes; original post text can contain public mentions. Runtime snippets must redact handles. STORE_TEXT defaults true; the wipe script can blank stored text. Hashes are pseudonyms, not a guarantee of anonymity.

## Contract deviations

Additive source=thread identifies enrichment posts so aggregate metrics can avoid sampling bias. Terms were drafted from the requested list because the seed file contains only a placeholder and the integration agent found only three placeholders in the live trends collection.
Cursor page requests returned HTTP 403 in the probe. Timestamp keyset fallback now uses until=oldest createdAt plus one microsecond and URI overlap deduplication. Legacy exclusive-boundary windows require actual re-verification; saturation alone never proves exhaustion. Empty responses or zero new term/window URIs on a non-full confirmation page establish sampled completion. Full repeated pages remain unresolved. Unseen posts with no insertion quota preserve their continuation position. First pages sample at most 20 posts per term/week; saturated windows are lower bounds, not platform totals.
The latest global cap is 35000 posts. Non-press collection reserves capacity for press_status seeds. Ten exact user-provided press_status seed rows are pending receipt. No press seed rows have been invented.
