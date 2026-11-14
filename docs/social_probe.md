# Bluesky public endpoint probe

Checked: 2026-11-14T22:22:41.374550Z

Host: api.bsky.app, unauthenticated. Every actual API request, including HTTP retries, counts toward the 20-request probe budget. Offline connection failures are reported separately.
API requests used: 19. Offline connection failures: 5.

| Endpoint | Passed | Status |
| --- | --- | --- |
| app.bsky.feed.getPostThread | True | 200 |
| app.bsky.feed.getQuotes | True | 200 |
| app.bsky.feed.getRepostedBy | True | 200 |
| app.bsky.feed.searchPosts | True | 200 |
| app.bsky.graph.getFollows | True | 200 |

Cursor pagination: {"attempts": [{"cursor_error": "http_403", "first_page_count": 5, "phrase": "cargo pants", "since": "2025-11-15T00:00:00Z", "until": "2026-03-01T00:00:00Z"}], "passed": false}

Timestamp keyset pagination: {"new_on_second_page": 3, "passed": true, "status": "200"}
Four additional first-page tests for cargo pants, chinos, ballet flats, and jeans returned HTTP 200 with cursors; supplying their cursors returned HTTP 403. The historical-window test below reproduced the cursor failure. Offline sandbox failures never reached the API and are recorded separately.

Only endpoints marked passed may be used. Search pages are followed until the cursor is absent, unchanged, or repeats. A short page alone does not establish exhaustion.
Search results and hitsTotal are search index samples and may be capped. They are never platform totals.
Replies identify parent authors in their post URIs. Repost counts are not repost-account interaction edges. No community field is supplied by Bluesky.

The original seed file contains only a placeholder trend. The integration agent also inspected the live trends collection: all three entries were placeholders, not researched trends. The configured terms are a draft based on the requested fashion phrases; labels are evaluation annotations, never classifier inputs.

References: [Bluesky HTTP API](https://endpoints.bsky.app/), [official search implementation](https://github.com/bluesky-social/atproto/blob/main/packages/bsky/src/api/app/bsky/feed/searchPosts.ts).
Context7 was unavailable. The former docs.bsky.app API pages redirected to endpoints.bsky.app. Runtime HTTP probing establishes endpoint availability.
