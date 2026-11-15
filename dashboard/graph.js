/* Style threads: Social Radar atlas explorer (sigma.js v4 + graphology).
 * Data: GET /social-atlas (scripts/social_atlas.py). Authors are hash prefixes only.
 */
(() => {
  "use strict";

  const Graph = graphology.Graph || graphology;
  const FA2 = graphologyLibrary.layoutForceAtlas2;
  const { DEFAULT_STYLES } = Sigma;
  const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Garment families give every style a color, and communities inherit their dominant family.
  const FAMILIES = [
    { key: "denim", name: "Denim", color: "#7BA4FF" },
    { key: "shoes", name: "Shoes", color: "#FF8FB4" },
    { key: "tops", name: "Tops", color: "#F2D78A" },
    { key: "trousers", name: "Trousers and skirts", color: "#8CD4AA" },
    { key: "bags", name: "Bags and knits", color: "#C6A8FF" },
    { key: "prints", name: "Colors and prints", color: "#FF9F6E" },
  ];
  const FAMILY_OF = {
    "baggy jeans": "denim", "barrel jeans": "denim", "skinny jeans": "denim", "low rise jeans": "denim",
    "mid rise jeans": "denim", "high waisted jeans": "denim", "bootcut jeans": "denim", "flare jeans": "denim",
    "straight leg jeans": "denim", "balloon jeans": "denim", "Levi's 501": "denim",
    "ballet flats": "shoes", "mesh flats": "shoes", "sambas": "shoes", "chunky sneakers": "shoes",
    "dad sneakers": "shoes", "slim sneakers": "shoes", "high heels": "shoes", "kitten heels": "shoes",
    "block heels": "shoes", "stiletto heels": "shoes", "platform heels": "shoes", "slingback heels": "shoes",
    "graphic tee": "tops", "white t-shirt": "tops", "plain black tee": "tops", "oxford shirt": "tops",
    "poplin shirt": "tops", "crochet top": "tops", "quarter zip": "tops",
    "cargo pants": "trousers", "capri pants": "trousers", "chinos": "trousers", "balloon pants": "trousers",
    "bubble skirt": "trousers", "soft suiting": "trousers",
    "leg warmers": "bags", "mini bag": "bags", "micro bag": "bags",
    "butter yellow": "prints", "cherry red": "prints", "leopard print": "prints",
  };
  const KINDS = [
    { key: "reply", name: "Replies", color: "#ECE8F7", observed: true },
    { key: "quote", name: "Quotes", color: "#F2D78A", observed: true },
    { key: "mention", name: "Mentions", color: "#FF9F6E", observed: true },
    { key: "similarity", name: "Similar posts", color: "#7BA4FF", observed: false },
  ];
  const DIM = "rgba(156, 150, 184, 0.045)";

  const $ = (id) => document.getElementById(id);
  const fmt = new Intl.NumberFormat("en-US");
  const n = (v) => (v == null ? "n/a" : fmt.format(v));
  const day = (iso) => (iso ? new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }) : "n/a");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const familyColor = (key) => (FAMILIES.find((f) => f.key === key) || FAMILIES[0]).color;
  const styleFamily = (term) => FAMILY_OF[term] || "prints";
  const alpha = (hex, a) => {
    const v = parseInt(hex.slice(1), 16);
    return `rgba(${(v >> 16) & 255}, ${(v >> 8) & 255}, ${v & 255}, ${a})`;
  };

  const state = {
    atlas: null, view: "map", renderer: null, graph: null,
    hovered: null, selected: null, hiddenKinds: new Set(["similarity"]), family: null,
    mapPositions: null, authorDetail: new Map(), authorNeighbors: null,
  };

  // ---------- data ----------

  async function load() {
    $("status").textContent = "Loading the Bluesky sample";
    const response = await fetch("/social-atlas");
    if (!response.ok) {
      throw new Error(response.status === 404
        ? "The atlas has not been built. Run scripts/social_atlas.py, then reload."
        : `The atlas request failed with HTTP ${response.status}. Check that the backend is running.`);
    }
    const atlas = await response.json();
    atlas.styles.forEach((s) => { s.family = styleFamily(s.term); s.color = familyColor(s.family); });
    atlas.communities.forEach((c) => {
      const top = c.top_styles[0];
      c.family = top ? atlas.styles[top[0]].family : null;
      c.color = c.family ? familyColor(c.family) : "#9C96B8";
    });
    state.atlas = atlas;
    const m = atlas.meta;
    $("subtitle").textContent = `${n(m.styles)} clothing styles, ${n(m.communities)} communities and ${n(m.authors)} authors from ${n(m.posts)} sampled Bluesky posts, ${monthLabel(m.window.first_week)} to ${monthLabel(m.window.last_week)}.`;
    $("status").textContent = "";
  }
  const monthLabel = (week) => new Date(week + "T00:00:00Z").toLocaleDateString("en-US", { month: "long", year: "numeric", timeZone: "UTC" });

  // ---------- map view: styles + communities ----------

  function buildMapGraph() {
    const { styles, communities, style_community, community_links, style_style } = state.atlas;
    const g = new Graph({ type: "undirected", multi: false });
    const familyIndex = Object.fromEntries(FAMILIES.map((f, i) => [f.key, i]));
    // Seed positions on a ring by family so the settle animation is repeatable.
    styles.forEach((s, i) => {
      const angle = (familyIndex[s.family] / FAMILIES.length) * Math.PI * 2 + (i % 7) * 0.08;
      g.addNode(`s${s.id}`, {
        x: Math.cos(angle) * 100, y: Math.sin(angle) * 100,
        size: 5 + Math.sqrt(s.posts) * 0.3, color: s.color, label: s.term,
        kind: "style", ref: s.id, family: s.family,
      });
    });
    communities.forEach((c, i) => {
      const anchor = c.top_styles[0] ? g.getNodeAttributes(`s${c.top_styles[0][0]}`) : { x: 0, y: 0 };
      const angle = i * 2.399963;
      g.addNode(`c${c.id}`, {
        x: anchor.x + Math.cos(angle) * 12, y: anchor.y + Math.sin(angle) * 12,
        size: 1.3 + Math.sqrt(c.authors) * 0.14, color: alpha(c.color, 0.62), baseColor: c.color,
        label: c.name, kind: "community", ref: c.id, family: c.family,
      });
    });
    // A community attaches only to the styles that define it: up to three with at least a
    // 15 percent share of its style posts. Every link is still listed in the inspector.
    communities.forEach(({ id: c, top_styles: rows, authors: size }) => {
      const total = rows.reduce((sum, [, p]) => sum + p, 0);
      const presence = Math.min(1, 0.35 + Math.log10(1 + size) * 0.4);
      rows.forEach(([s, posts], rank) => {
        const share = posts / total;
        if (rank > 2 || (rank > 0 && share < 0.15)) return;
        g.mergeEdge(`s${s}`, `c${c}`, {
          weight: 1 + share * 6, kind: "membership", color: alpha(styles[s].color, (0.08 + share * 0.24) * presence),
          size: (0.3 + share * 1.3) * presence,
        });
      });
    });
    // Every other style-community pair: weight 0 so it never moves the layout, shown only on focus.
    style_community.forEach(([s, c, posts]) => {
      if (g.hasEdge(`s${s}`, `c${c}`)) return;
      g.addEdge(`s${s}`, `c${c}`, { weight: 0, kind: "membership-minor", color: alpha(styles[s].color, 0.35), size: 0.5 });
    });
    // Observed replies, quotes and mentions between communities; inferred similarity is left to the author view.
    community_links.forEach(([a, b, kinds]) => {
      const observed = (kinds.reply || 0) + (kinds.quote || 0) + (kinds.mention || 0);
      if (observed < 2) return;
      g.mergeEdge(`c${a}`, `c${b}`, {
        weight: 0.15 * Math.log2(1 + observed), kind: "community-observed",
        color: "rgba(236, 232, 247, 0.07)", size: 0.35,
      });
    });
    style_style.forEach(([a, b, shared]) => {
      if (shared < 8) return;
      g.mergeEdge(`s${a}`, `s${b}`, { weight: 0.5, kind: "co-mention", color: "rgba(236, 232, 247, 0.10)", size: 0.5 });
    });
    return g;
  }

  // ---------- author view: every author, clustered around its community ----------

  function buildAuthorGraph() {
    const { authors, author_edges, communities } = state.atlas;
    const g = new Graph({ type: "undirected", multi: true });
    const centers = clusterCenters();
    const members = new Map();
    authors.community.forEach((c, i) => { if (!members.has(c)) members.set(c, []); members.get(c).push(i); });
    members.forEach((list, cid) => {
      // Hubs sit at the center of their community; a golden-angle spiral fills the disc.
      list.sort((a, b) => authors.degree[b] - authors.degree[a] || a - b);
      const center = centers[cid];
      list.forEach((i, j) => {
        const r = center.r * Math.sqrt((j + 0.5) / list.length);
        const t = j * 2.399963;
        const style = authors.style[i];
        const color = style >= 0 ? state.atlas.styles[style].color : communities[cid].color;
        g.addNode(`a${i}`, {
          x: center.x + Math.cos(t) * r, y: center.y + Math.sin(t) * r,
          size: Math.min(6, 1.1 + Math.log2(1 + authors.degree[i]) * 0.42),
          color: authors.posts[i] ? color : alpha(color, 0.45),
          label: `Author ${authors.hash[i]}`, kind: "author", ref: i, community: cid,
          family: style >= 0 ? state.atlas.styles[style].family : communities[cid].family,
        });
      });
    });
    // Name the 30 largest discs with an anchor at their center; clicking one opens the community.
    [...members.keys()].sort((a, b) => members.get(b).length - members.get(a).length).slice(0, 30).forEach((cid, rank) => {
      const center = centers[cid];
      g.addNode(`c${cid}`, {
        x: center.x, y: center.y, size: 0.01, color: "rgba(0,0,0,0)",
        label: communities[cid].name, kind: "community", ref: cid, family: communities[cid].family,
        anchor: rank < 14 ? "major" : "minor",
      });
    });
    KINDS.forEach(({ key, color }) => {
      const flat = author_edges[key] || [];
      const edgeColor = key === "similarity" ? alpha(color, 0.05) : alpha(color, 0.3);
      for (let k = 0; k < flat.length; k += 2) {
        g.addEdgeWithKey(`${key}${k}`, `a${flat[k]}`, `a${flat[k + 1]}`, {
          kind: key, color: edgeColor, size: key === "similarity" ? 0.3 : 0.6, path: key === "similarity" ? "line" : "curved",
        });
      }
    });
    return g;
  }

  function clusterCenters() {
    // Communities keep their map position, scaled up and relaxed so author discs do not overlap.
    const { communities } = state.atlas;
    const pos = state.mapPositions;
    const SPACING = 3.2;
    const radii = communities.map((c) => SPACING * Math.sqrt(c.authors) + 2);
    // Scale map coordinates so the discs cover about 35 percent of the circle that bounds them.
    let extent = 1;
    communities.forEach((c) => { const p = pos[`c${c.id}`]; if (p) extent = Math.max(extent, Math.hypot(p.x, p.y)); });
    const area = radii.reduce((sum, r) => sum + r * r, 0);
    const scale = Math.sqrt(area / 0.35) / extent;
    const centers = communities.map((c, i) => {
      const p = pos[`c${c.id}`] || { x: 0, y: 0 };
      return { x: p.x * scale, y: p.y * scale, r: radii[i] };
    });
    // Sweep and prune: sort by left edge and compare only discs whose x ranges can touch.
    const order = centers.map((_, i) => i);
    for (let pass = 0; pass < 300; pass++) {
      let moved = false;
      order.sort((i, j) => (centers[i].x - centers[i].r) - (centers[j].x - centers[j].r));
      for (let oi = 0; oi < order.length; oi++) {
        const a = centers[order[oi]];
        for (let oj = oi + 1; oj < order.length; oj++) {
          const b = centers[order[oj]];
          if (b.x - b.r > a.x + a.r + 4) break;
          const dx = b.x - a.x, dy = b.y - a.y;
          const d = Math.hypot(dx, dy) || 0.01;
          const overlap = a.r + b.r + 4 - d;
          if (overlap > 0) {
            // The smaller disc moves more, so big communities keep their map position.
            const total = a.r + b.r, ux = dx / d, uy = dy / d;
            a.x -= ux * overlap * (b.r / total); a.y -= uy * overlap * (b.r / total);
            b.x += ux * overlap * (a.r / total); b.y += uy * overlap * (a.r / total);
            moved = true;
          }
        }
      }
      if (!moved) break;
    }
    state.clusterOverlaps = centers.reduce((count, a, i) => count + centers.slice(i + 1)
      .filter((b) => Math.hypot(b.x - a.x, b.y - a.y) < a.r + b.r).length, 0);
    return centers;
  }

  // ---------- renderer ----------

  function nodeVisible(attrs) {
    return !state.family || attrs.family === state.family;
  }

  function makeRenderer(g) {
    if (state.renderer) state.renderer.kill();
    const isAuthors = state.view === "authors";
    const renderer = new Sigma(g, $("stage"), {
      settings: {
        // Labels and glows are drawn by drawOverlay() for halo text and real collision culling.
        renderLabels: false,
        hideEdgesOnMove: isAuthors,
        zIndex: true, minCameraRatio: 0.02, maxCameraRatio: 4,
        // Sizes are screen pixels that grow gently on zoom, like Obsidian's graph view.
        itemSizesReference: "screen", zoomToSizeRatioFunction: (ratio) => Math.pow(ratio, 0.35),
        stagePadding: 70,
      },
      customGraphState: { focus: false },
      primitives: {
        depthLayers: ["edges", "nodes", "litEdges", "litNodes", "topNodes"],
        // Threads: gently curved edges; straight lines for the dense similarity layer.
        edges: { paths: [Sigma.rendering.pathLine(), Sigma.rendering.pathCurved({ curvature: 0.22, segments: 16 })] },
      },
      styles: {
        nodes: [
          DEFAULT_STYLES.nodes,
          { color: { attribute: "color" }, size: { attribute: "size" }, label: { attribute: "label" }, backdropVisibility: "hidden" },
          { when: (a) => !nodeVisible(a), then: { color: DIM } },
          { when: (_a, s, gs) => gs.focus && !s.lit, then: { color: DIM } },
          { whenState: "lit", then: { depth: "litNodes" } },
          { whenState: "isHovered", then: { depth: "topNodes" } },
          { whenState: "selected", then: { depth: "topNodes" } },
        ],
        edges: [
          DEFAULT_STYLES.edges,
          { color: { attribute: "color" }, size: { attribute: "size" }, path: { attribute: "path", defaultValue: "curved" },
            curvature: { attribute: "curvature", defaultValue: 0.24 } },
          { when: (a) => state.hiddenKinds.has(a.kind), then: { visibility: "hidden" } },
          { when: (a, s) => a.kind === "membership-minor" && !s.lit, then: { visibility: "hidden" } },
          { when: (_a, s, gs) => gs.focus && !s.lit, then: { visibility: "hidden" } },
          { whenState: "lit", then: { depth: "litEdges" } },
        ],
      },
    });
    renderer.on("afterRender", drawOverlay);
    renderer.on("enterNode", ({ node, event }) => { state.hovered = node; applyFocus(); showTooltip(node, event); });
    renderer.on("leaveNode", () => { state.hovered = null; applyFocus(); $("tooltip").hidden = true; });
    renderer.on("clickNode", ({ node }) => select(node, { animate: true, keepZoom: true }));
    renderer.on("clickStage", () => clearSelection());
    state.renderer = renderer;
    state.graph = g;
    // The constructor renders before state points at this graph; redraw the overlay for it now.
    drawOverlay();
    return renderer;
  }

  // ---------- overlay: glows under the WebGL canvas, haloed labels above it ----------

  const SERIF = '"Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif';
  const styleLabelSize = (a) => (a.size > 14 ? 14 : 13);
  const overlay = { glow: null, labels: null };

  function overlayCanvas(id, z) {
    let canvas = document.getElementById(id);
    if (!canvas) {
      canvas = document.createElement("canvas");
      canvas.id = id;
      canvas.className = "overlay";
      canvas.style.zIndex = z;
      document.body.insertBefore(canvas, $("stage").nextSibling);
    }
    const dpr = window.devicePixelRatio || 1;
    const w = window.innerWidth, h = window.innerHeight;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    return ctx;
  }

  function screenNode(node) {
    const { renderer } = state;
    const d = renderer.getNodeDisplayData(node);
    if (!d) return null;
    const p = renderer.framedGraphToViewport({ x: d.x, y: d.y });
    return { x: p.x, y: p.y, r: renderer.scaleSize(d.size), d };
  }

  function drawOverlay() {
    const { renderer, graph } = state;
    if (!renderer || !graph) return;
    const glow = overlayCanvas("glow-layer", 0);
    const labels = overlayCanvas("label-layer", 2);
    const W = window.innerWidth, H = window.innerHeight;
    const focus = renderer.getGraphState().focus;
    const ratio = renderer.getCamera().getState().ratio;
    const onScreen = (p) => p && p.x > -60 && p.x < W + 60 && p.y > -60 && p.y < H + 60;

    // Soft glows behind style nodes and anything lit, so the graph reads like light, not flat paper.
    glow.globalCompositeOperation = "lighter";
    graph.forEachNode((node, a) => {
      const st = renderer.getNodeState(node);
      const important = a.kind === "style" || st.lit || node === state.selected;
      if (!important || (focus && !st.lit) || !nodeVisible(a)) return;
      const p = screenNode(node);
      if (!onScreen(p)) return;
      const base = a.baseColor || a.color;
      const radius = p.r * (node === state.selected || node === state.hovered ? 5 : 3.4);
      const gradient = glow.createRadialGradient(p.x, p.y, p.r * 0.6, p.x, p.y, radius);
      gradient.addColorStop(0, alpha(base.startsWith("#") ? base : "#ECE8F7", node === state.selected ? 0.42 : 0.24));
      gradient.addColorStop(1, alpha(base.startsWith("#") ? base : "#ECE8F7", 0));
      glow.fillStyle = gradient;
      glow.beginPath(); glow.arc(p.x, p.y, radius, 0, Math.PI * 2); glow.fill();
    });

    // Labels in priority order. A label is skipped if its box would overlap one already drawn.
    const candidates = [];
    const add = (node, a, priority) => candidates.push({ node, a, priority });
    graph.forEachNode((node, a) => {
      const st = renderer.getNodeState(node);
      if (!nodeVisible(a) && node !== state.selected) return;
      if (node === state.selected) return add(node, a, 0);
      if (node === state.hovered) return add(node, a, 1);
      if (focus) {
        if (!st.lit) return;
        if (a.kind === "style") return add(node, a, 2);
        if (a.kind === "community") return add(node, a, 3 + 1 / (1 + (a.size || 0)));
        return add(node, a, 4 + 1 / (1 + (a.size || 0)));
      }
      if (a.kind === "style") return add(node, a, 2 + 1 / (1 + a.size));
      if (a.anchor) return add(node, a, a.anchor === "major" ? 3 : 5);
      if (a.kind === "community" && ratio < 0.45) return add(node, a, 6 + 1 / (1 + a.size));
    });
    candidates.sort((x, y) => x.priority - y.priority);
    const placed = [];
    // Style nodes reserve their own disc so no label is drawn over them.
    graph.forEachNode((node, a) => {
      if (a.kind !== "style" || (focus && !renderer.getNodeState(node).lit)) return;
      const p = screenNode(node);
      if (onScreen(p)) placed.push({ x: p.x - p.r, y: p.y - p.r, w: p.r * 2, h: p.r * 2 });
    });
    const overlaps = (box) => placed.some((b) => box.x < b.x + b.w && box.x + box.w > b.x && box.y < b.y + b.h && box.y + box.h > b.y);
    labels.textBaseline = "middle";
    labels.lineJoin = "round";
    for (const { node, a, priority } of candidates) {
      const p = screenNode(node);
      if (!onScreen(p)) continue;
      const strong = priority < 2 || a.kind === "style" || a.anchor === "major";
      const size = node === state.selected ? 15 : a.kind === "style" ? styleLabelSize(a) : a.anchor ? (a.anchor === "major" ? 12.5 : 12) : 11.5;
      labels.font = `${a.kind === "author" ? "500 " : ""}${size}px ${a.kind === "author" ? "-apple-system, system-ui, sans-serif" : SERIF}`;
      const text = a.label;
      const width = labels.measureText(text).width;
      // Candidate spots: right of the node first; styles may also sit left, above or below.
      const gap = p.r + 7;
      const spots = a.anchor ? [[p.x - width / 2, p.y]]
        : a.kind === "style" || priority < 2 ? [[p.x + gap, p.y], [p.x - gap - width, p.y]] : [[p.x + gap, p.y]];
      let chosen = null;
      for (const [x, y] of spots) {
        const pad = a.anchor ? 12 : 3;
        const box = { x: x - pad, y: y - size * 0.62 - (a.anchor ? 4 : 0), w: width + pad * 2, h: size * 1.24 + (a.anchor ? 8 : 0) };
        if (priority < 2 || !overlaps(box)) { chosen = { x, y, box }; break; }
      }
      if (!chosen) continue;
      placed.push(chosen.box);
      if (a.anchor) {
        // Over busy author discs a soft pill reads better than a text halo.
        const padX = 9, h = size + 10, x0 = chosen.x - padX, y0 = chosen.y - h / 2, w0 = width + padX * 2;
        labels.fillStyle = "rgba(19, 16, 36, 0.74)";
        labels.beginPath();
        labels.roundRect ? labels.roundRect(x0, y0, w0, h, h / 2) : labels.rect(x0, y0, w0, h);
        labels.fill();
        labels.fillStyle = a.anchor === "major" ? "#ECE8F7" : "rgba(236, 232, 247, 0.78)";
        labels.fillText(text, chosen.x, chosen.y);
        continue;
      }
      labels.lineWidth = 4.5;
      labels.strokeStyle = "rgba(19, 16, 36, 0.88)";
      labels.strokeText(text, chosen.x, chosen.y);
      labels.fillStyle = strong ? "#ECE8F7" : "rgba(236, 232, 247, 0.68)";
      labels.fillText(text, chosen.x, chosen.y);
    }

    // Rings mark the hovered and selected nodes.
    for (const node of [state.hovered, state.selected]) {
      if (!node || !graph.hasNode(node)) continue;
      const p = screenNode(node);
      if (!onScreen(p)) continue;
      labels.lineWidth = 1.5;
      labels.strokeStyle = "rgba(236, 232, 247, 0.85)";
      labels.beginPath(); labels.arc(p.x, p.y, p.r + 4, 0, Math.PI * 2); labels.stroke();
    }
  }

  function applyFocus() {
    const { renderer, graph } = state;
    if (!renderer) return;
    const anchor = state.hovered || state.selected;
    const lit = new Set();
    if (anchor && graph.hasNode(anchor)) {
      lit.add(anchor);
      graph.forEachEdge(anchor, (edge, attrs, source, target) => {
        if (state.hiddenKinds.has(attrs.kind)) return;
        lit.add(source); lit.add(target);
      });
    }
    graph.forEachNode((node) => renderer.setNodeState(node, { lit: lit.has(node), selected: node === state.selected }));
    graph.forEachEdge((edge, attrs, source, target) => {
      renderer.setEdgeState(edge, { lit: !!anchor && (source === anchor || target === anchor) });
    });
    renderer.setGraphState({ focus: !!anchor });
    renderer.refresh({ skipIndexation: true });
  }

  function showTooltip(node, event) {
    const a = state.graph.getNodeAttributes(node);
    const tip = $("tooltip");
    let line = "";
    if (a.kind === "style") {
      const s = state.atlas.styles[a.ref];
      line = `${n(s.posts)} posts from ${n(s.authors)} authors`;
    } else if (a.kind === "community") {
      const c = state.atlas.communities[a.ref];
      line = `Community of ${n(c.authors)} authors, ${n(c.posts)} posts`;
    } else {
      const A = state.atlas.authors;
      line = `${n(A.posts[a.ref])} sampled posts, ${n(A.degree[a.ref])} connections`;
    }
    tip.innerHTML = `<strong>${esc(a.label)}</strong><span>${esc(line)}</span>`;
    const x = event?.original?.clientX ?? event.x;
    const y = event?.original?.clientY ?? event.y;
    tip.style.left = `${Math.min(window.innerWidth - 280, x + 14)}px`;
    tip.style.top = `${y + 14}px`;
    tip.hidden = false;
  }

  // ---------- layout animation (the one orchestrated motion moment) ----------

  function settle(g, onDone) {
    const settings = {
      ...FA2.inferSettings(g), linLogMode: true, adjustSizes: false, gravity: 0.25,
      scalingRatio: 10, barnesHutOptimize: true, barnesHutTheta: 0.6, edgeWeightInfluence: 1, outboundAttractionDistribution: true,
    };
    const totalFrames = REDUCED_MOTION ? 0 : 60;
    if (!totalFrames) {
      FA2.assign(g, { iterations: 420, settings, getEdgeWeight: "weight" });
      onDone();
      return;
    }
    let frame = 0;
    const step = () => {
      FA2.assign(g, { iterations: 5, settings, getEdgeWeight: "weight" });
      frame += 1;
      if (frame < totalFrames) requestAnimationFrame(step);
      else onDone();
    };
    requestAnimationFrame(step);
  }

  // All 42 style labels stay visible, so nudge style nodes until their label boxes no longer overlap.
  function separateStyleLabels(g) {
    const styles = [];
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    g.forEachNode((node, a) => {
      minX = Math.min(minX, a.x); maxX = Math.max(maxX, a.x); minY = Math.min(minY, a.y); maxY = Math.max(maxY, a.y);
      if (a.kind === "style") styles.push({ node, a });
    });
    const container = $("stage");
    const unitsPerPx = Math.max((maxX - minX) / (container.clientWidth * 0.62), (maxY - minY) / (container.clientHeight * 0.82));
    const measure = document.createElement("canvas").getContext("2d");
    const boxes = styles.map(({ node, a }) => {
      // Same font as drawOverlay, plus the halo and a little air.
      measure.font = `${styleLabelSize(a)}px ${SERIF}`;
      const textWidth = measure.measureText(a.label).width;
      return { node, x: a.x, y: a.y, w: (a.size + 7 + textWidth + 14) * unitsPerPx, h: 24 * unitsPerPx, r: a.size * unitsPerPx };
    });
    for (let pass = 0; pass < 120; pass++) {
      let moved = false;
      for (let i = 0; i < boxes.length; i++) {
        for (let j = i + 1; j < boxes.length; j++) {
          const A = boxes[i], B = boxes[j];
          // Label boxes start at the node's left edge and extend right.
          const ax = A.x - A.r, bx = B.x - B.r;
          const overlapX = Math.min(ax + A.w, bx + B.w) - Math.max(ax, bx);
          const overlapY = Math.min(A.y + A.h / 2, B.y + B.h / 2) - Math.max(A.y - A.h / 2, B.y - B.h / 2);
          if (overlapX > 0 && overlapY > 0) {
            const dir = A.y <= B.y ? -1 : 1;
            const push = overlapY / 2 + unitsPerPx;
            A.y += dir * push; B.y -= dir * push;
            moved = true;
          }
        }
      }
      if (!moved) break;
    }
    boxes.forEach((b) => g.mergeNodeAttributes(b.node, { y: b.y }));
  }

  // Second pass using the renderer's real scale: measured text and node radii, separated in graph units.
  function separateOnScreen(g) {
    const { renderer } = state;
    const o = renderer.viewportToGraph({ x: 0, y: 0 }), e = renderer.viewportToGraph({ x: 100, y: 0 });
    const unitsPerPx = Math.hypot(e.x - o.x, e.y - o.y) / 100;
    const measure = document.createElement("canvas").getContext("2d");
    const items = [];
    g.forEachNode((node, a) => {
      if (a.kind !== "style") return;
      const r = renderer.scaleSize(a.size);
      measure.font = `${styleLabelSize(a)}px ${SERIF}`;
      const w = measure.measureText(a.label).width;
      // Clear zone: the node disc plus its label to the right, in graph units.
      items.push({ node, x: a.x, y: a.y, left: (r + 6) * unitsPerPx, right: (r + 7 + w + 8) * unitsPerPx,
        half: (Math.max(r, 11) + 3) * unitsPerPx });
    });
    let passes = 0;
    for (; passes < 800; passes++) {
      let moved = false;
      for (let i = 0; i < items.length; i++) {
        for (let j = i + 1; j < items.length; j++) {
          const A = items[i], B = items[j];
          const ox = Math.min(A.x + A.right, B.x + B.right) - Math.max(A.x - A.left, B.x - B.left);
          const oy = Math.min(A.y + A.half, B.y + B.half) - Math.max(A.y - A.half, B.y - B.half);
          if (ox > 0 && oy > 0) {
            // Separate along the axis that needs the smaller move.
            if (oy <= ox) {
              const dir = A.y <= B.y ? -1 : 1, d = oy / 2 + unitsPerPx * 0.5;
              A.y += dir * d; B.y -= dir * d;
            } else {
              const dir = A.x <= B.x ? -1 : 1, d = ox / 2 + unitsPerPx * 0.5;
              A.x += dir * d; B.x -= dir * d;
            }
            moved = true;
          }
        }
      }
      if (!moved) break;
    }
    state.labelPasses = passes;
    items.forEach((it) => g.mergeNodeAttributes(it.node, { x: it.x, y: it.y }));
    renderer.refresh();
  }

  // ---------- views ----------

  function showMap() {
    state.view = "map";
    setViewButtons();
    const g = buildMapGraph();
    if (state.mapPositions) {
      g.forEachNode((node) => g.mergeNodeAttributes(node, state.mapPositions[node]));
      makeRenderer(g);
      restoreSelection();
      return;
    }
    makeRenderer(g);
    settle(g, () => {
      // Repeatable extra pass without animation so positions converge before being stored.
      FA2.assign(g, { iterations: 150, settings: { ...FA2.inferSettings(g), linLogMode: true, gravity: 0.25, scalingRatio: 10, barnesHutOptimize: true, barnesHutTheta: 0.6, edgeWeightInfluence: 1, outboundAttractionDistribution: true }, getEdgeWeight: "weight" });
      separateStyleLabels(g);
      state.mapPositions = {};
      g.forEachNode((node, a) => { state.mapPositions[node] = { x: a.x, y: a.y }; });
      // Refine label spacing at the renderer's real scale, then store the final positions.
      separateOnScreen(g);
      g.forEachNode((node, a) => { state.mapPositions[node] = { x: a.x, y: a.y }; });
      if (state.pendingView === "authors") { state.pendingView = null; showAuthors(); return; }
      restoreSelection();
    });
  }

  function showAuthors() {
    if (!state.mapPositions) {
      // The author layout reuses the settled map; open it as soon as the map is ready.
      state.pendingView = "authors";
      $("status").textContent = "Settling the map first";
      return;
    }
    state.view = "authors";
    setViewButtons();
    $("status").textContent = "Placing 25,658 authors";
    requestAnimationFrame(() => {
      const g = buildAuthorGraph();
      makeRenderer(g);
      $("status").textContent = "";
      restoreSelection();
    });
  }

  function setViewButtons() {
    $("view-map").setAttribute("aria-selected", String(state.view === "map"));
    $("view-authors").setAttribute("aria-selected", String(state.view === "authors"));
    $("edge-kinds").hidden = state.view !== "authors";
  }

  // Selections survive a view switch when the same entity exists in the other view.
  function restoreSelection() {
    const sel = state.selection;
    if (!sel) return;
    const key = nodeKeyFor(sel);
    if (key && state.graph.hasNode(key)) select(key, { animate: true });
    else applyFocus();
  }

  function nodeKeyFor(sel) {
    if (sel.kind === "style") return state.view === "map" ? `s${sel.ref}` : null;
    if (sel.kind === "community") return `c${sel.ref}`;
    if (sel.kind === "author") return state.view === "authors" ? `a${sel.ref}` : null;
    return null;
  }

  // ---------- selection + inspector ----------

  function select(node, { animate, keepZoom = false }) {
    const a = state.graph.getNodeAttributes(node);
    state.selected = node;
    state.selection = { kind: a.kind, ref: a.ref };
    applyFocus();
    renderInspector(a);
    if (animate) focusCamera(node, keepZoom ? null : (a.kind === "author" ? neighborhoodRatio(node) : 0.45));
  }

  // Zoom so a node's visible neighbors fit on screen (authors often link across communities).
  function neighborhoodRatio(node) {
    const { renderer, graph } = state;
    const origin = renderer.getNodeDisplayData(node);
    let reach = 0;
    graph.forEachEdge(node, (_e, attrs, source, target) => {
      if (state.hiddenKinds.has(attrs.kind)) return;
      const other = renderer.getNodeDisplayData(source === node ? target : source);
      if (other) reach = Math.max(reach, Math.hypot(other.x - origin.x, other.y - origin.y));
    });
    // Framed-graph units span about 1 at ratio 1; leave room for the inspector and labels.
    return Math.min(1, Math.max(0.08, reach * 2.6));
  }

  // Center a node in the part of the canvas the inspector does not cover.
  function focusCamera(node, ratio) {
    const { renderer } = state;
    const camera = renderer.getCamera();
    const d = renderer.getNodeDisplayData(node);
    if (!d) return;
    const current = camera.getState();
    const target = { ...current, ratio: ratio ?? current.ratio };
    const panel = $("inspector");
    const covered = !panel.hidden && window.innerWidth > 760 ? panel.getBoundingClientRect().width + 16 : 0;
    const visible = { x: (window.innerWidth - covered) / 2, y: window.innerHeight / 2 };
    const center = { x: window.innerWidth / 2, y: window.innerHeight / 2 };
    // Offset in framed-graph units at the target zoom: viewport delta scales with ratio.
    const scale = target.ratio / current.ratio;
    const a = renderer.viewportToFramedGraph(center), b = renderer.viewportToFramedGraph(visible);
    target.x = d.x + (a.x - b.x) * scale;
    target.y = d.y + (a.y - b.y) * scale;
    camera.animate(target, { duration: REDUCED_MOTION ? 0 : 650 });
  }

  function selectEntity(kind, ref) {
    state.selection = { kind, ref };
    if (kind === "author" && state.view !== "authors") return showAuthors();
    if (kind === "style" && state.view !== "map") return showMap();
    if (kind === "community" && state.view === "authors" && !state.graph.hasNode(`c${ref}`)) return showMap();
    const key = nodeKeyFor(state.selection);
    if (key) select(key, { animate: true });
  }

  function clearSelection() {
    state.selected = null;
    state.selection = null;
    $("inspector").hidden = true;
    applyFocus();
  }

  function renderInspector(a) {
    const panel = $("detail");
    if (a.kind === "style") panel.innerHTML = styleDetail(state.atlas.styles[a.ref]);
    else if (a.kind === "community") panel.innerHTML = communityDetail(state.atlas.communities[a.ref]);
    else { panel.innerHTML = authorDetail(a.ref, null); loadAuthor(a.ref); }
    $("inspector").hidden = false;
    $("inspector").scrollTop = 0;
  }

  const kindLine = (label, color) => `<p class="detail-kind"><span class="swatch" style="background:${color}"></span>${esc(label)}</p>`;
  const fact = (label, value, small = "") => `<div><dt>${esc(label)}</dt><dd>${value}${small ? `<small>${esc(small)}</small>` : ""}</dd></div>`;

  function styleDetail(s) {
    const family = FAMILIES.find((f) => f.key === s.family);
    const daysTo5 = s.fifth_post ? Math.round((new Date(s.fifth_post) - new Date(s.first_post)) / 864e5) : null;
    const press = s.press_status ? `<span class="press" style="color:${s.press_status === "rising" ? "#8CD4AA" : "#FF9F6E"}">press says ${esc(s.press_status)}</span>` : "";
    const communities = s.top_communities.map(([cid, posts]) => {
      const c = state.atlas.communities[cid];
      return { label: c.name, value: posts, color: c.color, action: `community:${cid}` };
    });
    return `
      ${kindLine(`${family.name} style`, s.color)}
      <h2 class="detail-title">${esc(s.term)}</h2>
      <p class="detail-lede">Sample momentum: ${esc(s.momentum)}, ${esc(s.confidence)} confidence ${press}</p>
      <dl class="facts">
        ${fact("Posts", n(s.posts))}
        ${fact("Authors", n(s.authors))}
        ${fact("First post", day(s.first_post))}
        ${fact("Fifth post", day(s.fifth_post), daysTo5 != null ? `${daysTo5} days later` : "")}
        ${fact("Last post", day(s.last_post))}
        ${fact("Weeks with posts", `${n(s.weeks_with_posts)}`, `of ${s.weeks_in_window}`)}
        ${fact("Communities with 3+ posts", n(s.breadth))}
        ${fact("Largest community share", s.concentration == null ? "n/a" : `${Math.round(s.concentration * 100)}%`)}
      </dl>
      <h3 class="section-title">Share of sampled posts, by week</h3>
      ${sparkline(s.weekly, s.color)}
      <p class="spark-caption">Each bar is this style's share of everything sampled that week, because some weeks were collected more deeply than others. Hover a bar for the raw count.${s.weekly.some((w) => w[3]) ? " Faint bars hit the 20-post first-page cap, so they are lower bounds." : ""}</p>
      <h3 class="section-title">Communities talking about it</h3>
      ${bars(communities)}
      <h3 class="section-title">Most-liked posts</h3>
      ${posts(s.examples)}
      <p class="note">Bluesky search sample, ${esc(state.atlas.meta.window.first_week)} to ${esc(state.atlas.meta.window.last_week)}. Counts are sampled, not platform totals. A first post in the window is not the trend's origin.</p>`;
  }

  function communityDetail(c) {
    const styles = c.top_styles.map(([sid, posts]) => {
      const s = state.atlas.styles[sid];
      return { label: s.term, value: posts, color: s.color, action: `style:${sid}` };
    });
    const internal = c.internal_edges || {};
    const named = c.named_by === "claude" ? "Named by Claude from its top terms and posts" : "Labeled from its two most common styles";
    return `
      ${kindLine("Community", c.color)}
      <h2 class="detail-title">${esc(c.name)}</h2>
      <p class="detail-lede">${esc(named)}</p>
      <dl class="facts">
        ${fact("Authors", n(c.authors))}
        ${fact("Posts", n(c.posts))}
        ${fact("Replies inside", n(internal.reply || 0))}
        ${fact("Quotes and mentions", n((internal.quote || 0) + (internal.mention || 0)))}
        ${fact("Similar-post links inside", n(internal.similarity || 0))}
        ${fact("Likes on sampled posts", n(c.likes))}
      </dl>
      <h3 class="section-title">Styles it posts about</h3>
      ${bars(styles)}
      <h3 class="section-title">Most-liked posts</h3>
      ${posts(c.examples)}
      <p class="note">Communities come from Louvain clustering of observed replies, quotes and mentions plus inferred similar-post links. They are estimates, not groups Bluesky provides.</p>`;
  }

  function authorConnections(i) {
    if (!state.authorNeighbors) {
      const map = new Map();
      for (const { key } of KINDS) {
        const flat = state.atlas.author_edges[key] || [];
        for (let k = 0; k < flat.length; k += 2) {
          for (const [x, y] of [[flat[k], flat[k + 1]], [flat[k + 1], flat[k]]]) {
            if (!map.has(x)) map.set(x, []);
            map.get(x).push([y, key]);
          }
        }
      }
      state.authorNeighbors = map;
    }
    return state.authorNeighbors.get(i) || [];
  }

  function authorDetail(i, detail) {
    const A = state.atlas.authors;
    const c = state.atlas.communities[A.community[i]];
    const links = authorConnections(i);
    const byKind = Object.fromEntries(KINDS.map(({ key }) => [key, links.filter(([, k]) => k === key).length]));
    // One row per person, with their interaction kinds counted.
    const people = new Map();
    links.filter(([, k]) => k !== "similarity").forEach(([j, k]) => {
      if (!people.has(j)) people.set(j, {});
      people.get(j)[k] = (people.get(j)[k] || 0) + 1;
    });
    const plural = (count, one, many) => `${count} ${count === 1 ? one : many}`;
    const observed = [...people.entries()]
      .sort((x, y) => Object.values(y[1]).reduce((a, b) => a + b, 0) - Object.values(x[1]).reduce((a, b) => a + b, 0))
      .slice(0, 10)
      .map(([j, kinds]) => ({
        label: `Author ${A.hash[j]}`,
        value: [kinds.reply && plural(kinds.reply, "reply", "replies"), kinds.quote && plural(kinds.quote, "quote", "quotes"),
          kinds.mention && plural(kinds.mention, "mention", "mentions")].filter(Boolean).join(", "),
        color: KINDS.find((x) => x.key === Object.keys(kinds)[0]).color, action: `author:${j}`,
      }));
    const styles = detail ? detail.styles.map(([sid, count]) => {
      const s = state.atlas.styles[sid];
      return { label: s.term, value: count, color: s.color, action: `style:${sid}` };
    }) : [];
    return `
      ${kindLine("Author", c.color)}
      <h2 class="detail-title author">Author ${esc(A.hash[i])}</h2>
      <p class="detail-lede">Pseudonymous hash. In <button class="inline-link" data-action="community:${c.id}">${esc(c.name)}</button></p>
      <dl class="facts">
        ${fact("Sampled posts", n(A.posts[i]))}
        ${fact("Connections", n(links.length))}
        ${fact("Replies", n(byKind.reply))}
        ${fact("Quotes and mentions", n(byKind.quote + byKind.mention))}
        ${fact("Similar-post links", n(byKind.similarity))}
        ${fact("Likes on sampled posts", detail ? n(detail.likes) : "n/a")}
      </dl>
      ${styles.length ? `<h3 class="section-title">Styles they post about</h3>${bars(styles)}` : ""}
      ${observed.length ? `<h3 class="section-title">People they interacted with</h3>${bars(observed)}` : ""}
      <h3 class="section-title">Sampled posts</h3>
      ${detail ? posts(detail.posts) : `<p class="empty">${A.posts[i] ? "Loading posts" : "No sampled posts. This author appears because others replied to, quoted or mentioned them."}</p>`}`;
  }

  async function loadAuthor(i) {
    const A = state.atlas.authors;
    if (!A.posts[i]) return;
    const prefix = A.hash[i];
    let detail = state.authorDetail.get(prefix);
    if (!detail) {
      try {
        const response = await fetch(`/social-atlas/author/${prefix}`);
        detail = response.ok ? await response.json() : { styles: [], likes: null, posts: [] };
      } catch {
        detail = { styles: [], likes: null, posts: [] };
      }
      state.authorDetail.set(prefix, detail);
    }
    if (state.selection?.kind === "author" && state.selection.ref === i) $("detail").innerHTML = authorDetail(i, detail);
  }

  function sparkline(weekly, color) {
    const W = 336, H = 76, pad = 2;
    const shares = weekly.map(([, count, corpus]) => (corpus ? count / corpus : 0));
    const max = Math.max(1e-9, ...shares);
    const bw = (W - pad * 2) / weekly.length;
    const pct = (v) => `${(v * 100).toFixed(v >= 0.1 ? 0 : 1)}%`;
    const rects = weekly.map(([week, count, corpus, saturated], k) => {
      const h = Math.max(count ? 2 : 0, (shares[k] / max) * (H - 28));
      return `<rect x="${(pad + k * bw).toFixed(2)}" y="${(H - 12 - h).toFixed(2)}" width="${Math.max(1, bw - 1.2).toFixed(2)}" height="${h.toFixed(2)}" rx="1" fill="${color}" fill-opacity="${saturated ? 0.38 : 0.9}"><title>Week of ${esc(week)}: ${count} of ${corpus} sampled posts (${pct(shares[k])})${saturated ? ", capped sample" : ""}</title></rect>`;
    }).join("");
    const first = weekly[0]?.[0] || "", last = weekly[weekly.length - 1]?.[0] || "";
    return `<svg class="spark" viewBox="0 0 ${W} ${H}" role="img" aria-label="Weekly share of sampled posts, peak ${pct(max)}">${rects}
      <text x="${pad}" y="${H - 1}" fill="#9C96B8" font-size="10">${esc(monthShort(first))}</text>
      <text x="${W - pad}" y="${H - 1}" fill="#9C96B8" font-size="10" text-anchor="end">${esc(monthShort(last))}</text>
      <text x="${W - pad}" y="9" fill="#9C96B8" font-size="10" text-anchor="end">peak ${pct(max)} of a week's posts</text></svg>`;
  }
  const monthShort = (week) => (week ? new Date(week + "T00:00:00Z").toLocaleDateString("en-US", { month: "short", year: "2-digit", timeZone: "UTC" }) : "");

  function bars(rows) {
    if (!rows.length) return `<p class="empty">None in the sample.</p>`;
    const max = Math.max(...rows.map((r) => (typeof r.value === "number" ? r.value : 1)));
    return `<ul class="bars">${rows.map((r) => `
      <li><button data-action="${esc(r.action)}">
        <span class="name"><span class="swatch" style="background:${r.color};display:inline-block;margin-right:8px"></span>${esc(r.label)}</span>
        <span class="value">${esc(typeof r.value === "number" ? n(r.value) : r.value)}</span>
        ${typeof r.value === "number" ? `<span class="bar"><span style="width:${(r.value / max) * 100}%;background:${r.color}"></span></span>` : ""}
      </button></li>`).join("")}</ul>`;
  }

  function posts(rows) {
    if (!rows?.length) return `<p class="empty">No posts with readable text in the sample.</p>`;
    return `<ul class="posts">${rows.map((p) => `
      <li><p>${esc(p.snippet)}</p>
        <span class="meta"><span>${esc(day(p.created_at))}</span><span>${n(p.likes)} ${p.likes === 1 ? "like" : "likes"}</span>
        <a href="${esc(p.url)}" target="_blank" rel="noopener noreferrer">Open on Bluesky</a></span></li>`).join("")}</ul>`;
  }

  // ---------- search ----------

  function searchIndex() {
    const { styles, communities, authors } = state.atlas;
    return [
      ...styles.map((s) => ({ kind: "style", ref: s.id, label: s.term, color: s.color, type: "Style" })),
      ...communities.map((c) => ({ kind: "community", ref: c.id, label: c.name, color: c.color, type: `${n(c.authors)} ${c.authors === 1 ? "author" : "authors"}` })),
      { authors },
    ];
  }

  function runSearch(query) {
    const list = $("suggestions");
    const q = query.trim().toLowerCase();
    if (!q) { list.hidden = true; return; }
    const index = state.searchIndex || (state.searchIndex = searchIndex());
    const results = index.filter((r) => r.label && r.label.toLowerCase().includes(q))
      .sort((a, b) => (a.kind === "style" ? -1 : 1) - (b.kind === "style" ? -1 : 1) || a.label.length - b.label.length)
      .slice(0, 12);
    if (/^[0-9a-f]{3,10}$/.test(q)) {
      const A = state.atlas.authors;
      for (let i = 0; i < A.hash.length && results.length < 16; i++) {
        if (A.hash[i].startsWith(q)) results.push({ kind: "author", ref: i, label: `Author ${A.hash[i]}`, color: "#9C96B8", type: "Author" });
      }
    }
    list.innerHTML = results.length
      ? results.map((r, k) => `<li role="option" data-action="${r.kind}:${r.ref}" aria-selected="${k === 0}"><span class="swatch" style="background:${r.color}"></span>${esc(r.label)}<span class="kind">${esc(r.type)}</span></li>`).join("")
      : `<li class="empty">No style, community or author matches.</li>`;
    list.hidden = false;
  }

  // ---------- controls ----------

  function buildControls() {
    const kinds = $("edge-kinds");
    const counts = state.atlas.meta.edges;
    kinds.insertAdjacentHTML("beforeend", KINDS.map((k) => `
      <label><input type="checkbox" value="${k.key}" ${state.hiddenKinds.has(k.key) ? "" : "checked"}>
        <span class="line" style="background:${k.color}"></span>${esc(k.name)} <span class="count">${n(counts[k.key])}</span></label>`).join(""));
    kinds.addEventListener("change", (e) => {
      const key = e.target.value;
      if (e.target.checked) state.hiddenKinds.delete(key); else state.hiddenKinds.add(key);
      applyFocus();
    });
    $("families").innerHTML = FAMILIES.map((f) => `
      <li><button data-family="${f.key}" aria-pressed="false"><span class="swatch" style="background:${f.color}"></span>${esc(f.name)}</button></li>`).join("");
    $("families").addEventListener("click", (e) => {
      const button = e.target.closest("button[data-family]");
      if (!button) return;
      state.family = state.family === button.dataset.family ? null : button.dataset.family;
      document.querySelectorAll("#families button").forEach((b) => {
        b.setAttribute("aria-pressed", String(b.dataset.family === state.family));
        b.classList.toggle("dimmed", !!state.family && b.dataset.family !== state.family);
      });
      applyFocus();
    });
    $("view-map").addEventListener("click", () => state.view !== "map" && showMap());
    $("view-authors").addEventListener("click", () => state.view !== "authors" && showAuthors());
    $("reset").addEventListener("click", () => state.renderer?.getCamera().reset({ duration: REDUCED_MOTION ? 0 : 500 }));
    $("close").addEventListener("click", clearSelection);

    document.addEventListener("click", (e) => {
      const target = e.target.closest("[data-action]");
      if (!target) return;
      const [kind, ref] = target.dataset.action.split(":");
      $("suggestions").hidden = true;
      $("search").value = "";
      selectEntity(kind, Number(ref));
    });
    const search = $("search");
    search.addEventListener("input", () => runSearch(search.value));
    search.addEventListener("keydown", (e) => {
      const items = [...$("suggestions").querySelectorAll("li[data-action]")];
      const current = items.findIndex((li) => li.getAttribute("aria-selected") === "true");
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const next = (current + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
        items.forEach((li, k) => li.setAttribute("aria-selected", String(k === next)));
      } else if (e.key === "Enter" && items[current]) {
        items[current].click();
        search.blur();
      } else if (e.key === "Escape") {
        search.value = ""; $("suggestions").hidden = true; search.blur();
      }
    });
    document.addEventListener("keydown", (e) => {
      if (e.target === search) return;
      if (e.key === "/") { e.preventDefault(); search.focus(); }
      else if (e.key === "Escape") clearSelection();
      else if (e.key.toLowerCase() === "f") $("reset").click();
    });
    setViewButtons();
  }

  // Read-only hook for browser tests: screen position of a node by label.
  window.styleThreads = {
    point(label) {
      const { graph, renderer } = state;
      const node = graph.findNode((_k, a) => a.label === label);
      if (!node) return null;
      const d = renderer.getNodeDisplayData(node);
      return renderer.framedGraphToViewport({ x: d.x, y: d.y });
    },
    ready: () => !!state.mapPositions,
    labelPasses: () => state.labelPasses ?? null,
    overlaps: () => state.clusterOverlaps,
  };

  load().then(() => { buildControls(); showMap(); }).catch((error) => {
    $("status").textContent = error.message;
    $("subtitle").textContent = "The graph could not load.";
  });
})();
