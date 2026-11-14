# Social Trend Radar spec

Source: the user's Social Trend Radar task on November 14, 2026. The supplied schema, thresholds, ownership and execution gates are recorded in CONTRACT.md under Social Trend Radar shared contract. The task supplies the interview answers; no extra product choices are required before implementation.

Build an additive Bluesky signal for clothing purchase analysis, using public api.bsky.app search results from the last 12 months. Search index counts are samples and may be capped. First observation in the window is not the origin. Reply relationships will be sparse. Similarity edges must be distinguished from observed interactions.

Ingest about 15 fashion terms and five staples, with evaluation labels excluded from classifiers. Probe cursor pagination and optional endpoints first, at most 20 requests. Use weekly since/until queries, one request per second, 429 backoff, resumable cursors and deduplication by URI. Cap the corpus at 25000 posts. Enrich at most 200 threads within 400 requests. Hash authors using HMAC-SHA256 with a local secret salt. Allow text storage to be disabled and provide a text wipe command.

Embed separately with gemini-embedding-2 at 768 dimensions. Create an Atlas vector index filtered on term and created_at. Build author communities with weighted reply, mention and quote edges plus low-weight vector nearest-neighbor edges. Use seeded Louvain. Report actual interaction connectivity separately from similarity connectivity. Name communities using sanitized terms and snippets, without handles. Export at most 1500 nodes.

Compute normalized weekly counts and temporal and community statistics per term. Evaluate staple and micro separation honestly. Runtime retrieves 30 posts and requires a 0.4 vote share and a cosine threshold. Cache for ten minutes. Add optional context to /analyze and to Claude evidence without expanding allowed verdicts. Failures preserve existing behavior. Add one estimated Bluesky chatter line to the card, with no buttons.

Add authenticated POST /trend-ask with vector retrieval, optional term/date filters and Claude answers citing retrieved post URIs. Return sanitized short snippets and community names. No Ask UI.

Evaluate 30 paraphrased held-out posts with recall@5, recall@10 and MRR, cluster purity, staple/micro separation and lookup p50/p95. Document sample sizes and limitations. Unmeasured results must be marked unmeasured.

Optional UI runs last, for at most one hour: one self-contained dashboard/trends.html served at /trends-ui, a community graph and term curve with peak, and a dropdown only. Apply better-ui skill. Do not touch dashboard/index.html.

Stop ingest after at most 90 minutes and report quality. If fewer than 3000 posts, wait for a user response before live graph processing. Overall budget about four wall-clock hours. Work on feat/social-radar from current main, never push or commit on main. Only main agent edits CONTRACT.md. No Nessie requests, printed secrets or em dashes. Unit tests mock Bluesky. Report exact commands and all deviations.
