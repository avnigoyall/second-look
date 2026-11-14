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

  function readJsonLd(doc = document) {
    const nodes = [];
    for (const script of doc.querySelectorAll('script[type="application/ld+json"]')) {
      const data = parseScript(script.textContent || "");
      if (data) collectNodes(data, nodes);
    }
    const products = nodes.filter((n) => hasType(n, "Product") || hasType(n, "ProductGroup"));
    if (!products.length) return null;

    // Prefer a product that has a name and offers.
    const product = products.find((p) => p.name && p.offers) || products.find((p) => p.name) || products[0];
    let { price, currency } = readOffers(product.offers);
    let image = firstImage(product.image);

    // ProductGroup (variants): take price and image from the first variant if missing.
    const variant = Array.isArray(product.hasVariant) ? product.hasVariant[0] : product.hasVariant;
    if (variant) {
      if (price == null) ({ price, currency } = readOffers(variant.offers));
      image = image || firstImage(variant.image);
    }

    return {
      name: typeof product.name === "string" ? product.name.trim() : null,
      image_url: absolute(image),
      price,
      currency: currency || null,
    };
  }

  global.SecondLookJsonLd = { readJsonLd };
})(globalThis);
