// readItem(): tries tier 1 (JSON-LD), tier 2 (meta tags), then tier 3 (Gemini via the service
// worker) and returns {name, image_url, price, currency, store, url, source_tier, price_source},
// or null when no product name can be found. When a name is found but no price, the DOM price
// reader runs before Gemini. price_source is jsonld, meta, dom, gemini, or null.
// readItemWithDetails() also returns what each tier saw, for Diagnose.
(function (global) {
  "use strict";

  const LOG = "[SecondLook]";
  const MAX_TEXT = 8000;

  function visibleText() {
    const main = document.querySelector("main, [role='main']");
    const source = main && main.innerText.trim().length > 200 ? main : document.body;
    return (source?.innerText || "").replace(/\s+/g, " ").trim().slice(0, MAX_TEXT);
  }

  // Largest visible image on the page, as a fallback image candidate for Gemini.
  function largestImage() {
    let best = null;
    let bestArea = 0;
    for (const img of document.images) {
      const rect = img.getBoundingClientRect();
      const area = rect.width * rect.height;
      if (area > bestArea && rect.width >= 150 && rect.height >= 150 && img.currentSrc) {
        best = img.currentSrc;
        bestArea = area;
      }
    }
    return best;
  }

  async function askGemini(imageUrl) {
    const text = visibleText();
    if (!text) return { error: "page has no visible text" };
    const res = await chrome.runtime.sendMessage({
      type: "READ_PAGE",
      payload: { url: location.href, text, image_url: imageUrl || null },
    });
    if (!res?.ok) return { error: res?.error || "no response" };
    return { result: res.data };
  }

  async function readItemWithDetails({ useGemini = true } = {}) {
    const details = { url: location.href, tiers: {} };
    let t1 = null;
    let t2 = null;
    try { t1 = global.SecondLookJsonLd.readJsonLd(); } catch (e) { details.tiers.jsonld = { error: String(e) }; }
    try { t2 = global.SecondLookMeta.readMeta(); } catch (e) { details.tiers.meta = { error: String(e) }; }
    details.tiers.jsonld = details.tiers.jsonld || t1 || { found: false };
    details.tiers.meta = details.tiers.meta || t2 || { found: false };

    const item = {
      name: t1?.name || t2?.name || null,
      image_url: t1?.image_url || t2?.image_url || null,
      price: t1?.price ?? t2?.price ?? null,
      currency: t1?.currency || t2?.currency || null,
      store: global.SecondLookMeta.storeName(),
      url: global.SecondLookMeta.canonicalUrl(),
      source_tier: t1?.name ? 1 : t2?.name ? 2 : null,
      price_source: t1?.price != null ? "jsonld" : t2?.price != null ? "meta" : null,
    };

    // DOM price: when tier 1 or 2 found a name but no price, read the price off the page
    // before spending a Gemini call. Without a name, Gemini runs anyway and fills the price.
    if (item.name && item.price == null) {
      const dom = global.SecondLookDomPrice.readDomPrice(item.name);
      details.tiers.dom = dom || { found: false };
      if (dom) {
        item.price = dom.price;
        item.currency = item.currency || dom.currency || null;
        item.price_source = "dom";
      }
    } else {
      details.tiers.dom = { used: false };
    }

    // Tier 3 runs when the name or image is missing, or only to fill a missing price.
    const needsGemini = !item.name || !item.image_url || item.price == null;
    if (needsGemini && useGemini) {
      const gemini = await askGemini(item.image_url || largestImage());
      details.tiers.gemini = gemini;
      const g = gemini.result;
      if (g) {
        if (!item.name && g.name) {
          item.name = g.name;
          item.source_tier = 3;
        }
        item.image_url = item.image_url || g.image_url || null;
        if (item.price == null && g.price != null) {
          item.price = global.SecondLookPrice.parsePrice(g.price);
          if (item.price != null) item.price_source = "gemini";
        }
      }
    } else {
      details.tiers.gemini = { used: false };
    }

    details.item = item.name ? item : null;
    details.price_source = item.price_source;
    console.log(LOG, "price source:", item.price_source || "none", item.price != null ? item.price : "");
    console.log(LOG, "readItem", details);
    return details;
  }

  async function readItem(options) {
    return (await readItemWithDetails(options)).item;
  }

  // For freshly opened tabs: wait until product data shows up (single-page apps render late).
  async function waitForProductData(timeoutMs = 4000) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      const t1 = global.SecondLookJsonLd.readJsonLd();
      if (t1?.name) return;
      const t2 = global.SecondLookMeta.readMeta();
      if (t2?.name && t2?.price != null) return;
      await new Promise((r) => setTimeout(r, 250));
    }
  }

  global.SecondLookReader = { readItem, readItemWithDetails, waitForProductData, visibleText };
})(globalThis);
