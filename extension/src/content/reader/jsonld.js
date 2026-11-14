// Tier 1: schema.org Product data from <script type="application/ld+json">.
(function (global) {
  "use strict";

  const { parsePrice, parseCurrency } = global.SecondLookPrice;

  function parseScript(text) {
    try {
      return JSON.parse(text);
    } catch {
      try {
        // Some stores ship raw newlines or tabs inside strings.
        return JSON.parse(text.replace(/[\u0000-\u001F]+/g, " "));
      } catch {
        return null;
      }
    }
  }

  // Flatten arrays, @graph containers, and nested objects into one list of typed nodes.
  function collectNodes(data, out = [], depth = 0) {
    if (!data || typeof data !== "object" || depth > 6) return out;
    if (Array.isArray(data)) {
      data.forEach((d) => collectNodes(d, out, depth + 1));
      return out;
    }
    if (data["@type"]) out.push(data);
    if (data["@graph"]) collectNodes(data["@graph"], out, depth + 1);
    if (data.mainEntity) collectNodes(data.mainEntity, out, depth + 1);
    return out;
  }

  function hasType(node, type) {
    const t = node["@type"];
    return Array.isArray(t) ? t.includes(type) : t === type;
  }

  function firstImage(image) {
    if (!image) return null;
    if (typeof image === "string") return image;
    if (Array.isArray(image)) return firstImage(image[0]);
    return image.url || image.contentUrl || image["@id"] || null;
  }

  function readOffers(offers) {
    if (!offers) return { price: null, currency: null };
    const list = Array.isArray(offers) ? offers : [offers];
    const prices = [];
    let currency = null;
    for (const o of list) {
      if (!o || typeof o !== "object") continue;
      const spec = Array.isArray(o.priceSpecification) ? o.priceSpecification[0] : o.priceSpecification;
      const raw = o.price ?? o.lowPrice ?? spec?.price ?? null;
      const p = parsePrice(raw);
      if (p != null) prices.push(p);
      currency = currency || o.priceCurrency || spec?.priceCurrency || parseCurrency(raw);
      if (o.offers) {
        const nested = readOffers(o.offers);
        if (nested.price != null) prices.push(nested.price);
        currency = currency || nested.currency;
      }
    }
    return { price: prices.length ? Math.min(...prices) : null, currency };
  }

  function absolute(url) {
    try { return url ? new URL(url, location.href).href : null; } catch { return null; }
  }

  // ---------- ProductGroup variant choice ----------

  // Product codes from the current URL: "productpage.1101074044.html" -> "1101074044",
  // plus the last path segment ("/p/linen-shirt-123" -> "linen-shirt-123").
  function pageCodes() {
    const path = location.pathname;
    const codes = [];
    const hm = path.match(/productpage\.(\d+)/i);
    if (hm) codes.push(hm[1]);
    const last = path.split("/").filter(Boolean).pop() || "";
    const segment = last.replace(/\.html?$/i, "");
    if (segment.length >= 4 && /\d/.test(segment)) codes.push(segment); // skip words like "product"
    return codes.map((c) => c.toLowerCase());
  }

  function variantUrls(variant) {
    const offers = Array.isArray(variant.offers) ? variant.offers : variant.offers ? [variant.offers] : [];
    return [variant.url, variant["@id"], variant.sku, variant.productID, ...offers.map((o) => o?.url)]
      .filter((v) => typeof v === "string" || typeof v === "number")
      .map((v) => String(v).toLowerCase());
  }

  // The words a page uses to name its color: URL path, h1, og:title.
  function pageColorText(doc) {
    const og = doc.querySelector('meta[property="og:title"]')?.getAttribute("content") || "";
    const h1 = doc.querySelector("h1")?.innerText || "";
    return ` ${decodeURIComponent(location.pathname)} ${h1} ${og} `.toLowerCase().replace(/[-_/.]+/g, " ");
  }

  function variantColor(variant) {
    const c = Array.isArray(variant.color) ? variant.color[0] : variant.color;
    return typeof c === "string" ? c.trim().toLowerCase() : "";
  }

  // Returns { variant, match: "code" | "color" } or { variant: null, match: "none" }.
  function chooseVariant(variants, doc) {
    const codes = pageCodes();
    const byCode = variants.find((v) => variantUrls(v).some((u) => codes.some((c) => u.includes(c))));
    if (byCode) return { variant: byCode, match: "code" };
    const text = pageColorText(doc);
    const byColor = variants
      .map((v) => ({ v, color: variantColor(v) }))
      .filter(({ color }) => color && text.includes(` ${color.replace(/[-_/]+/g, " ")} `))
      .sort((a, b) => b.color.length - a.color.length)[0]; // "navy blue" beats "blue"
    if (byColor) return { variant: byColor.v, match: "color" };
    return { variant: null, match: "none" };
  }

  function readJsonLd(doc = document) {
    const nodes = [];
    for (const script of doc.querySelectorAll('script[type="application/ld+json"]')) {
      const data = parseScript(script.textContent || "");
      if (data) collectNodes(data, nodes);
    }
    const products = nodes.filter((n) => hasType(n, "Product") || hasType(n, "ProductGroup"));
    if (!products.length) return null;

    // Prefer a product that has a name and offers, or a group with variants.
    const product = products.find((p) => p.name && (p.offers || p.hasVariant)) || products.find((p) => p.name) || products[0];
    const name = typeof product.name === "string" ? product.name.trim() : null;
    const variants = Array.isArray(product.hasVariant) ? product.hasVariant : product.hasVariant ? [product.hasVariant] : [];

    if (variants.length) {
      // A group of variants (H&M lists every color): pick the one this page shows.
      const { variant, match } = chooseVariant(variants, doc);
      if (!variant) {
        // No match: keep the group's name, leave price null so the DOM price tier runs,
        // and leave the image null so og:image (which follows the page) is used.
        return { name: name || null, image_url: null, price: null, currency: null, variant_match: "none", variants: variants.length };
      }
      const { price, currency } = readOffers(variant.offers);
      return {
        name: name || (typeof variant.name === "string" ? variant.name.trim() : null),
        image_url: absolute(firstImage(variant.image)), // null falls back to og:image
        price,
        currency: currency || null,
        variant_match: match,
        variant_color: variantColor(variant) || null,
        variants: variants.length,
      };
    }

    const { price, currency } = readOffers(product.offers);
    return {
      name,
      image_url: absolute(firstImage(product.image)),
      price,
      currency: currency || null,
    };
  }

  global.SecondLookJsonLd = { readJsonLd };
})(globalThis);
