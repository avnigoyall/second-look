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

  // Fiber composition ("60% cotton, 40% polyester") for the Full analysis. The backend parses it; this only
  // finds the text: JSON-LD material, then text under a Composition / Material / Fabric heading, then any
  // short page text with "N% fiber". Visible text only, capped.
  const MAX_MATERIALS = 500;
  const PCT_FIBER = /\b\d{1,3}(?:[.,]\d+)?\s*%\s*(?:organic |recycled |pima |supima )?(cotton|polyester|nylon|polyamide|elastane|spandex|lycra|viscose|rayon|lyocell|tencel|modal|acrylic|acetate|wool|merino|cashmere|alpaca|mohair|silk|linen|hemp|leather|polyurethane)\b/i;
  const HEADING = /^(composition|materials?|fabric( (&|and) care)?|material (&|and) care|content|fibre content|fiber content)\b/i;

  // Text of an element with a space between its pieces: textContent would glue "Care" and "Shell:" into
  // "CareShell:", hiding the label. innerText is empty for a closed <details>, so walk the text nodes.
  function spacedText(node) {
    if (!node) return "";
    const parts = [];
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) parts.push(walker.currentNode.nodeValue);
    return parts.join(" ").replace(/\s+/g, " ").trim();
  }

  function readMaterials(nodes) {
    const product = nodes.find((n) => hasType(n, "Product") || hasType(n, "ProductGroup"));
    const fromLd = [].concat(product?.material || []).map((m) => (typeof m === "string" ? m : m?.name)).filter(Boolean).join(", ");
    if (fromLd && PCT_FIBER.test(fromLd)) return clean(fromLd, MAX_MATERIALS);

    // A heading ("Composition") followed by its text block.
    for (const h of document.querySelectorAll("h2, h3, h4, h5, dt, button, summary, strong, span, p")) {
      const label = h.textContent.replace(/\s+/g, " ").trim();
      if (label.length > 40 || !HEADING.test(label)) continue;
      const scope = h.closest("details, dl, section, li, div") || h.parentElement;
      const text = spacedText(scope);
      if (PCT_FIBER.test(text) && text.length < 2000) return clean(text, MAX_MATERIALS);
    }
    // Any short element that reads like a fiber line.
    for (const el of document.querySelectorAll("li, p, span, div, td, dd")) {
      if (el.children.length > 3) continue;
      const text = spacedText(el);
      if (text.length < 200 && PCT_FIBER.test(text)) return clean(text, MAX_MATERIALS);
    }
    return fromLd ? clean(fromLd, MAX_MATERIALS) : null; // "Cotton" without a percent still helps
  }

  function readDetails() {
    let nodes = [];
    try { nodes = jsonLdNodes(); } catch { /* keep going with DOM fallbacks */ }
    const safe = (fn) => { try { return fn(nodes); } catch { return null; } };
    return { description: safe(readDescription), breadcrumb: safe(readBreadcrumb), materials: safe(readMaterials) };
  }

  global.SecondLookDetails = { readDetails };
})(globalThis);
