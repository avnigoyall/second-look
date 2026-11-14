// Product description and breadcrumb, when the page has them. Both help the backend label only the item
// being added (for example "Men > Pants" when the photo shows a full outfit). Light by design: JSON-LD first,
// then common markup. Returns {description, breadcrumb}, each a string or null.
(function (global) {
  "use strict";

  const MAX_DESCRIPTION = 2000;
  const MAX_BREADCRUMB = 300;
  const SEPARATOR = " > ";

  function clean(text, max) {
    const value = String(text || "").replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
    return value ? value.slice(0, max) : null;
  }

  function jsonLdNodes() {
    const nodes = [];
    const walk = (data, depth) => {
      if (!data || typeof data !== "object" || depth > 6) return;
      if (Array.isArray(data)) return data.forEach((d) => walk(d, depth + 1));
      if (data["@type"]) nodes.push(data);
      if (data["@graph"]) walk(data["@graph"], depth + 1);
    };
    for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
      try { walk(JSON.parse(script.textContent), 0); } catch { /* broken JSON-LD: skip it */ }
    }
    return nodes;
  }

  const hasType = (node, type) => [].concat(node["@type"]).includes(type);

  function readDescription(nodes) {
    const product = nodes.find((n) => (hasType(n, "Product") || hasType(n, "ProductGroup")) && n.description);
    if (product) return clean(product.description, MAX_DESCRIPTION);
    const meta = document.querySelector('meta[property="og:description"], meta[name="description"]');
    return clean(meta?.content, MAX_DESCRIPTION);
  }

  function readBreadcrumb(nodes) {
    const list = nodes.find((n) => hasType(n, "BreadcrumbList") && Array.isArray(n.itemListElement));
    if (list) {
      const names = list.itemListElement
        .slice()
        .sort((a, b) => (a.position || 0) - (b.position || 0))
        .map((el) => el.name || el.item?.name)
        .filter((name) => typeof name === "string" && name.trim());
      if (names.length) return clean(names.join(SEPARATOR), MAX_BREADCRUMB);
    }
    const nav = document.querySelector('nav[aria-label*="breadcrumb" i], [class*="breadcrumb" i]');
    if (!nav) return null;
    const parts = [...nav.querySelectorAll("li, a")]
      .map((el) => el.textContent.replace(/\s+/g, " ").trim())
      .filter((t, i, all) => t && t.length < 60 && all.indexOf(t) === i);
    return parts.length ? clean(parts.join(SEPARATOR), MAX_BREADCRUMB) : null;
  }

  function readDetails() {
    try {
      const nodes = jsonLdNodes();
      return { description: readDescription(nodes), breadcrumb: readBreadcrumb(nodes) };
    } catch {
      return { description: null, breadcrumb: null };
    }
  }

  global.SecondLookDetails = { readDetails };
})(globalThis);
