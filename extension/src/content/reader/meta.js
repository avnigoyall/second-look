// Tier 2: Open Graph, product meta tags, and schema.org microdata attributes.
(function (global) {
  "use strict";

  const { parsePrice, parseCurrency } = global.SecondLookPrice;

  function content(doc, selectors) {
    for (const sel of selectors) {
      const el = doc.querySelector(sel);
      const value = el?.getAttribute("content") || el?.getAttribute("href") || el?.getAttribute("src") ||
        (el && sel.startsWith("[itemprop") ? el.textContent : null);
      if (value && value.trim()) return value.trim();
    }
    return null;
  }

  function absolute(url) {
    try { return url ? new URL(url, location.href).href : null; } catch { return null; }
  }

  function storeName(doc = document) {
    return content(doc, ['meta[property="og:site_name"]', 'meta[name="application-name"]']) ||
      location.hostname.replace(/^www\./, "");
  }

  // "Linen Shirt | Zara United States" -> "Linen Shirt"
  function stripStoreSuffix(title, store) {
    const parts = title.split(/\s+[|\u2013\u2014-]\s+/);
    if (parts.length < 2) return title;
    const storeWord = (store || "").toLowerCase().split(/[\s.]/)[0];
    const last = parts[parts.length - 1].toLowerCase();
    if (storeWord && last.includes(storeWord)) parts.pop();
    return parts.join(" - ").trim();
  }

  function readMeta(doc = document) {
    const store = storeName(doc);
    const rawTitle = content(doc, [
      'meta[property="og:title"]', 'meta[name="twitter:title"]', '[itemprop="name"]',
    ]);
    const rawPrice = content(doc, [
      'meta[property="product:price:amount"]', 'meta[property="og:price:amount"]',
      'meta[itemprop="price"]', '[itemprop="price"]',
    ]);
    const currency = content(doc, [
      'meta[property="product:price:currency"]', 'meta[property="og:price:currency"]',
      'meta[itemprop="priceCurrency"]', '[itemprop="priceCurrency"]',
    ]) || parseCurrency(rawPrice);
    const image = content(doc, [
      'meta[property="og:image:secure_url"]', 'meta[property="og:image"]',
      'meta[name="twitter:image"]', 'link[rel="image_src"]', '[itemprop="image"]',
    ]);

    const result = {
      name: rawTitle ? stripStoreSuffix(rawTitle, store) : null,
      image_url: absolute(image),
      price: parsePrice(rawPrice),
      currency: currency || null,
    };
    return result.name || result.image_url || result.price != null ? result : null;
  }

  function canonicalUrl(doc = document) {
    return absolute(doc.querySelector('link[rel="canonical"]')?.getAttribute("href")) || location.href;
  }

  global.SecondLookMeta = { readMeta, storeName, canonicalUrl };
})(globalThis);
