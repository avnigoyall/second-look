"""Build the Social Radar atlas: one JSON file that the /graph-ui explorer renders.

Inputs: social_posts, social_communities, social_trends, the Claude name cache in
social_graph_meta, config/social_terms.json, and the full edge list written by
`social_graph.py --edges-out`. Output: .social_runs/social_atlas.json plus per-author details in
.social_runs/social_atlas_authors.json (both git-ignored).

Privacy: authors appear only as hash prefixes. Snippets pass sanitize_snippet (no handles,
no links, at most 160 characters) and link to DID-based post URLs. Counts are sampled
search-index results, never platform totals.

Run after the graph: .venv/bin/python scripts/social_atlas.py
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.services.social import post_url, sanitize_snippet, terms_for, utc  # noqa: E402

KINDS = ["reply", "quote", "mention", "similarity"]
EXAMPLES_PER_STYLE = 4
EXAMPLES_PER_COMMUNITY = 3
POSTS_PER_AUTHOR = 3
MIN_STYLE_COMMUNITY_POSTS = 3


def iso(value):
    return utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def best_examples(posts, limit, style_index=None, extra=None):
    """Most-liked posts with a usable public URL and nonempty sanitized text."""
    rows = []
    for post in sorted(posts, key=lambda p: (-(p.get("like_count") or 0), p.get("uri", ""))):
        url = post_url(post.get("uri", ""))
        snippet = sanitize_snippet(post.get("text"))
        if url and len(snippet) >= 20:
            row = {"url": url, "snippet": snippet, "created_at": iso(post["created_at"]), "likes": post.get("like_count") or 0}
            if style_index is not None:
                row["styles"] = [style_index[t] for t in terms_for(post) if t in style_index]
            if extra is not None:
                row.update(extra(post))
            rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def build(posts, communities, trends, claude_named, terms_config, edges, window):
    labels = {r["term"]: r for r in terms_config.get("terms", [])}
    trend_by_term = {t["term"]: t for t in trends}
    style_names = sorted(set(trend_by_term) | set(labels))
    style_index = {term: i for i, term in enumerate(style_names)}

    membership = {c["author_hash"]: str(c["community_id"]) for c in communities}
    community_names = {}
    for c in communities:
        community_names.setdefault(str(c["community_id"]), c.get("name") or "Unclassified discussion")
    sizes = Counter(membership.values())
    community_ids = sorted(community_names, key=lambda cid: (-sizes[cid], cid))
    community_index = {cid: i for i, cid in enumerate(community_ids)}

    # Authors: everyone in a community (includes reply/mention targets without posts).
    author_hashes = sorted(membership, key=lambda h: (community_index[membership[h]], h))
    author_index = {h: i for i, h in enumerate(author_hashes)}

    search_posts = [p for p in posts if p.get("source") != "thread"]
    posts_by_author = defaultdict(list)
    for p in posts:
        if p.get("author_hash") in author_index:
            posts_by_author[p["author_hash"]].append(p)

    # Style aggregates (search posts only, matching social_trends).
    style_posts = defaultdict(list)
    for p in search_posts:
        for term in terms_for(p):
            if term in style_index:
                style_posts[term].append(p)

    styles = []
    style_community = Counter()
    style_style_posts = Counter()
    for p in search_posts:
        matched = sorted({t for t in terms_for(p) if t in style_index})
        for i, a in enumerate(matched):
            for b in matched[i + 1:]:
                style_style_posts[(a, b)] += 1
    style_authors = {term: {p.get("author_hash") for p in rows if p.get("author_hash")} for term, rows in style_posts.items()}

    for term in style_names:
        rows = sorted(style_posts.get(term, []), key=lambda p: utc(p["created_at"]))
        trend = trend_by_term.get(term, {})
        config = labels.get(term, {})
        per_community = Counter(membership[p["author_hash"]] for p in rows if p.get("author_hash") in membership)
        for cid, n in per_community.items():
            style_community[(term, cid)] = n
        weekly = trend.get("weekly_counts") or []
        times = [utc(p["created_at"]) for p in rows]
        styles.append({
            "id": style_index[term], "term": term,
            "label": config.get("label", "unknown"), "press_status": config.get("press_status"),
            "posts": len(rows), "authors": len(style_authors.get(term, ())),
            "first_post": iso(times[0]) if times else None,
            "fifth_post": iso(times[4]) if len(times) >= 5 else None,
            "last_post": iso(times[-1]) if times else None,
            "weeks_with_posts": sum(1 for w in weekly if w.get("count", 0) > 0), "weeks_in_window": len(weekly),
            "weekly": [[w["week"], w.get("count", 0), w.get("corpus_count", 0), bool(w.get("saturated"))] for w in weekly],
            "breadth": trend.get("breadth"), "concentration": trend.get("concentration"),
            "weeks_to_breadth_3": trend.get("weeks_to_breadth_3"),
            "saturated_week_share": trend.get("saturated_week_share"),
            "momentum": trend.get("momentum", "unknown"), "confidence": trend.get("confidence", "low"),
            "likes": sum(p.get("like_count") or 0 for p in rows),
            "top_communities": [[community_index[cid], n] for cid, n in per_community.most_common(8)],
            "examples": best_examples(rows, EXAMPLES_PER_STYLE),
        })

    # Edges between authors, remapped to indexes, plus community-level aggregates.
    author_edges = {kind: [] for kind in KINDS}
    community_links = defaultdict(Counter)
    internal = defaultdict(Counter)
    degree = Counter()
    for a, b, kind in edges:
        if a not in author_index or b not in author_index or kind not in author_edges:
            continue
        ia, ib = author_index[a], author_index[b]
        author_edges[kind].extend((ia, ib))
        degree[ia] += 1
        degree[ib] += 1
        ca, cb = membership[a], membership[b]
        if ca == cb:
            internal[ca][kind] += 1
        else:
            key = tuple(sorted((community_index[ca], community_index[cb])))
            community_links[key][kind] += 1

    community_rows = []
    community_posts = defaultdict(list)
    for p in search_posts:
        cid = membership.get(p.get("author_hash"))
        if cid is not None:
            community_posts[cid].append(p)
    for cid in community_ids:
        rows = community_posts.get(cid, [])
        style_counts = Counter(t for p in rows for t in terms_for(p) if t in style_index)
        community_rows.append({
            "id": community_index[cid], "name": community_names[cid], "named_by": "claude" if cid in claude_named else "terms",
            "authors": sizes[cid], "posts": len(rows), "likes": sum(p.get("like_count") or 0 for p in rows),
            "top_styles": [[style_index[t], n] for t, n in style_counts.most_common(8)],
            "internal_edges": dict(internal.get(cid, {})),
            "examples": best_examples(rows, EXAMPLES_PER_COMMUNITY),
        })

    authors = {
        # Parallel arrays keep 25k authors compact: hash prefix, community, posts, degree, top style.
        "hash": [h[:10] for h in author_hashes],
        "community": [community_index[membership[h]] for h in author_hashes],
        "posts": [len(posts_by_author.get(h, ())) for h in author_hashes],
        "degree": [degree[i] for i in range(len(author_hashes))],
        "style": [],
    }
    author_detail = {}
    for i, h in enumerate(author_hashes):
        rows = posts_by_author.get(h, [])
        counts = Counter(t for p in rows for t in terms_for(p) if t in style_index)
        authors["style"].append(style_index[counts.most_common(1)[0][0]] if counts else -1)
        if rows:
            author_detail[h[:10]] = {
                "styles": [[style_index[t], n] for t, n in counts.most_common(5)],
                "likes": sum(p.get("like_count") or 0 for p in rows),
                "posts": best_examples(rows, POSTS_PER_AUTHOR, style_index),
            }

    connections = build_connections(search_posts, posts, membership, style_index, community_index, author_index)

    total_kinds = {kind: len(author_edges[kind]) // 2 for kind in KINDS}
    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "window": window, "posts": len(posts), "search_posts": len(search_posts),
        "authors": len(author_hashes), "posting_authors": sum(1 for h in author_hashes if posts_by_author.get(h)),
        "communities": len(community_ids), "claude_named_communities": sum(1 for c in community_rows if c["named_by"] == "claude"),
        "styles": len(styles), "edges": total_kinds, "total_edges": sum(total_kinds.values()),
        "note": ("Bluesky search-index sample. Counts are sampled, not platform totals. Similarity edges are inferred "
                 "from Vultr qwen3-embedding-4b post vectors; reply, quote and mention edges are observed. "
                 "Authors appear only as hash prefixes."),
    }
    return {
        "meta": meta, "styles": styles, "communities": community_rows,
        "style_community": [[style_index[t], community_index[c], n] for (t, c), n in style_community.items()
                            if n >= MIN_STYLE_COMMUNITY_POSTS],
        "style_style": [[style_index[a], style_index[b], n] for (a, b), n in style_style_posts.items()],
        "community_links": [[a, b, dict(k)] for (a, b), k in sorted(community_links.items())],
        "authors": authors, "author_edges": author_edges, "author_detail": author_detail,
        "connections": connections,
    }


ORDER = {"s": 0, "c": 1, "a": 2}


def pair_key(x, y):
    """Stable key for two nodes such as ("s", 3) and ("c", 12): styles, then communities, then authors."""
    a, b = sorted([x, y], key=lambda v: (ORDER[v[0]], v[1]))
    return f"{a[0]}{a[1]}|{b[0]}{b[1]}"


def build_connections(search_posts, all_posts, membership, style_index, community_index, author_index):
    """What links two nodes, with real example posts. Served one pair at a time."""
    out = {}
    by_style_pair, by_style_community = defaultdict(list), defaultdict(list)
    authors_by_style = defaultdict(set)
    for p in search_posts:
        matched = sorted({t for t in terms_for(p) if t in style_index}, key=lambda t: style_index[t])
        for t in matched:
            if p.get("author_hash"):
                authors_by_style[t].add(p["author_hash"])
        for i, a in enumerate(matched):
            for b in matched[i + 1:]:
                by_style_pair[(a, b)].append(p)
        cid = membership.get(p.get("author_hash"))
        if cid is not None:
            for t in matched:
                by_style_community[(t, cid)].append(p)
    for (a, b), rows in by_style_pair.items():
        out[pair_key(("s", style_index[a]), ("s", style_index[b]))] = {
            "shared_posts": len(rows), "shared_authors": len(authors_by_style[a] & authors_by_style[b]),
            "examples": best_examples(rows, 3)}
    # Style pairs with authors in common but no post tagged with both.
    terms = sorted(authors_by_style, key=lambda t: style_index[t])
    for i, a in enumerate(terms):
        for b in terms[i + 1:]:
            key = pair_key(("s", style_index[a]), ("s", style_index[b]))
            if key not in out:
                shared = len(authors_by_style[a] & authors_by_style[b])
                if shared:
                    out[key] = {"shared_posts": 0, "shared_authors": shared, "examples": []}
    for (t, cid), rows in by_style_community.items():
        out[pair_key(("s", style_index[t]), ("c", community_index[cid]))] = {"posts": len(rows), "examples": best_examples(rows, 3)}

    # Observed interactions, kept with the post that created them.
    def targets(p):
        if p.get("reply_parent_author_hash"):
            yield "reply", p["reply_parent_author_hash"]
        if p.get("quote_author_hash"):
            yield "quote", p["quote_author_hash"]
        for m in dict.fromkeys(p.get("mention_author_hashes") or []):
            yield "mention", m
    author_pairs, community_pairs = defaultdict(list), defaultdict(list)
    for p in all_posts:
        source = p.get("author_hash")
        if source not in author_index:
            continue
        for kind, target in targets(p):
            if target == source or target not in author_index:
                continue
            author_pairs[pair_key(("a", author_index[source]), ("a", author_index[target]))].append((kind, source, p))
            cs, ct = membership.get(source), membership.get(target)
            if cs is not None and ct is not None and cs != ct:
                community_pairs[pair_key(("c", community_index[cs]), ("c", community_index[ct]))].append((kind, source, p))
    for key, rows in list(author_pairs.items()) + list(community_pairs.items()):
        kinds = Counter(kind for kind, _, _ in rows)
        posts = [p for _, _, p in rows]
        meta = {p["uri"]: (kind, author_index[source]) for kind, source, p in rows}
        out[key] = {"kinds": dict(kinds), "examples": best_examples(
            posts, 4, extra=lambda post: {"kind": meta[post["uri"]][0], "from_author": meta[post["uri"]][1]})}
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--edges", type=Path, default=ROOT / ".social_runs" / "social_graph_edges.json")
    parser.add_argument("--output", type=Path, default=ROOT / ".social_runs" / "social_atlas.json")
    parser.add_argument("--authors-output", type=Path, default=ROOT / ".social_runs" / "social_atlas_authors.json",
                        help="Per-author details, served one author at a time")
    parser.add_argument("--connections-output", type=Path, default=ROOT / ".social_runs" / "social_atlas_connections.json",
                        help="What links two nodes, served one pair at a time")
    args = parser.parse_args(argv)
    if not args.edges.exists():
        parser.error("Edge list missing. Run: scripts/social_graph.py --name-limit 0 --edges-out " + str(args.edges))
    from backend.db import db
    edges = json.loads(args.edges.read_text())["edges"]
    posts = list(db["social_posts"].find({}, {"_id": 0, "uri": 1, "author_hash": 1, "created_at": 1, "term": 1,
                                             "terms": 1, "text": 1, "like_count": 1, "source": 1,
                                             "reply_parent_author_hash": 1, "quote_author_hash": 1,
                                             "mention_author_hashes": 1}))
    communities = list(db["social_communities"].find({}, {"_id": 0, "author_hash": 1, "community_id": 1, "name": 1}))
    trends = list(db["social_trends"].find({}, {"_id": 0}))
    claude_named = {d["community_id"] for d in db["social_graph_meta"].find({"source": "claude"}, {"community_id": 1})}
    terms_config = json.loads((ROOT / "config" / "social_terms.json").read_text())
    windows = [(w["weekly_counts"][0]["week"], w["weekly_counts"][-1]["week"]) for w in trends if w.get("weekly_counts")]
    window = {"first_week": min(a for a, _ in windows), "last_week": max(b for _, b in windows)} if windows else None
    atlas = build(posts, communities, trends, claude_named, terms_config, edges, window)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    details = atlas.pop("author_detail")
    connections = atlas.pop("connections")
    args.output.write_text(json.dumps(atlas, separators=(",", ":"), ensure_ascii=True))
    args.authors_output.write_text(json.dumps(details, separators=(",", ":"), ensure_ascii=True))
    args.connections_output.write_text(json.dumps(connections, separators=(",", ":"), ensure_ascii=True))
    print(json.dumps({**atlas["meta"], "bytes": args.output.stat().st_size,
                      "author_detail_bytes": args.authors_output.stat().st_size,
                      "connections": len(connections), "connection_bytes": args.connections_output.stat().st_size}))


if __name__ == "__main__":
    main()
