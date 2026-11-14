// readItem(): tries tier 1 (JSON-LD), tier 2 (meta tags), then tier 3 (Gemini via the service
// worker) and returns {name, image_url, price, currency, store, url, description, breadcrumb, source_tier, price_source},
// or null when no product name can be found. When a name is found but no price, the DOM price
// reader runs before Gemini. If the price is still missing after Gemini, the last tier asks Claude
// (POST /read-price) with text windows around every amount on the whole page and around the
// add-to-cart button. price_source is jsonld, meta, dom, gemini, claude, or null.
// When the name is known and only the price is missing, the Gemini and Claude tiers run at the same
// time and Gemini's price still wins, so the card waits for the slower of the two, not their sum.
// readItemWithDetails() also returns what each tier saw, for Diagnose.
(function (global) {
  "use strict";

  const LOG = "[SecondLook]";
  const MAX_TEXT = 8000;
  // The backend gives Claude 4 s. Past this the tier gives up and the card asks for the price.
  const CLAUDE_WAIT_MS = 5000;
  const CART_AREA_MAX = 1500; // the add-to-cart container stops growing past this many characters
  // When Gemini only fills the price and has not answered by now, a price from the Claude tier is used
  // (a busy Gemini retries for up to 6 s more). Gemini usually answers in about 1 s.
  const GEMINI_PRICE_WAIT_MS = 4000;

  function visibleText() {
    const main = document.querySelector("main, [role='main']");
    const source = main && main.innerText.trim().length > 200 ? main : document.body;
    return (source?.innerText || "").replace(/\s+/g, " ").trim().slice(0, MAX_TEXT);
  }

  // Text of the largest container around the main add-to-cart button that stays under CART_AREA_MAX.
  function cartAreaText() {
    const button = global.SecondLookDomPrice.mainAddButton();
    if (!button) return "";
    let text = "";
    for (let node = button.parentElement, i = 0; node && node !== document.body && i < 8; node = node.parentElement, i++) {
      const t = (node.innerText || "").replace(/\s+/g, " ").trim();
      if (t.length > CART_AREA_MAX) break;
      text = t;
    }
    return text;
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
    const started = Date.now();
    const res = await chrome.runtime.sendMessage({
      type: "READ_PAGE",
      payload: { url: location.href, text, image_url: imageUrl || null },
    });
    const ms = Date.now() - started;
    if (!res?.ok) return { ms, error: res?.error || "no response" };
    return { ms, result: res.data };
  }

  // Last price tier. Never throws and never waits longer than CLAUDE_WAIT_MS.
  async function askClaude(name) {
    const fullText = document.body?.innerText || "";
    const windows = global.SecondLookPriceWindows.buildPriceWindows(fullText, cartAreaText());
    const sent = { windows: windows.length, chars: windows.reduce((n, w) => n + w.length, 0) };
    if (!windows.length) return { ...sent, error: "no currency amounts on the page" };
    const started = Date.now();
    let timer;
    const timeout = new Promise((resolve) => { timer = setTimeout(() => resolve({ ok: false, error: "timed out" }), CLAUDE_WAIT_MS); });
    try {
      const res = await Promise.race([
        chrome.runtime.sendMessage({ type: "READ_PRICE", payload: { url: location.href, name, windows } }),
        timeout,
      ]);
      const ms = Date.now() - started;
      if (!res?.ok) return { ...sent, ms, error: res?.error || "no response" };
      return { ...sent, ms, result: res.data };
    } catch (err) {
      return { ...sent, ms: Date.now() - started, error: String(err?.message || err) };
    } finally {
      clearTimeout(timer);
    }
  }

  // Gemini's answer, unless it is still pending after waitMs and Claude found a price: then a stand-in error.
  async function geminiUnlessSlow(geminiP, claudeP, waitMs) {
    let timer;
    const late = new Promise((resolve) => { timer = setTimeout(() => resolve(null), waitMs); });
    const first = await Promise.race([geminiP, late]);
    clearTimeout(timer);
    if (first) return first;
    const claude = await claudeP;
    if (global.SecondLookPrice.parsePrice(claude.result?.price) != null) {
      return { ms: waitMs, error: `no answer within ${waitMs} ms, the Claude tier had the price` };
    }
    return geminiP;
  }

  async function readItemWithDetails({ useGemini = true } = {}) {
    const details = { url: location.href, tiers: {} };
    const started = Date.now();
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
      ...(global.SecondLookDetails?.readDetails() || {}), // description, breadcrumb
    };

    // Price check: when JSON-LD or meta gave a price, compare it with the price shown on the
    // page. Structured data can be stale or belong to another variant; the displayed price wins.
    if (item.price != null && (item.price_source === "jsonld" || item.price_source === "meta")) {
      const shown = global.SecondLookDomPrice.readDisplayedPrice(item.name);
      const check = { structured: item.price, structured_source: item.price_source, displayed: shown?.price ?? null, winner: item.price_source };
      if (shown && Math.abs(shown.price - item.price) > 0.005) {
        item.price = shown.price;
        item.currency = shown.currency || item.currency;
        item.price_source = "dom";
        check.winner = "displayed";
      }
      details.price_check = check;
      console.log(LOG, `price check: ${check.structured_source} ${check.structured} vs displayed ${check.displayed ?? "none"} -> using ${check.winner === "displayed" ? "displayed" : check.structured_source}`);
    }

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
    // Name known, price missing: start the Claude tier now, next to Gemini. Same inputs as running it after.
    const claudeEarly = item.name && item.price == null && useGemini ? askClaude(item.name) : null;
    if (needsGemini && useGemini) {
      const geminiP = askGemini(item.image_url || largestImage());
      const priceOnly = Boolean(claudeEarly && item.image_url);
      const gemini = priceOnly ? await geminiUnlessSlow(geminiP, claudeEarly, GEMINI_PRICE_WAIT_MS) : await geminiP;
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

    // Last tier: only when every tier above found no price. A failure here leaves the price null,
    // and the card asks the shopper for it.
    if (item.name && item.price == null && useGemini) {
      const claude = await (claudeEarly || askClaude(item.name));
      details.tiers.claude = claude;
      const price = global.SecondLookPrice.parsePrice(claude.result?.price);
      if (price != null) {
        item.price = price;
        item.currency = item.currency || global.SecondLookPrice.parseCurrency(claude.result.price_text) || null;
        item.price_source = "claude";
      }
    } else {
      // Ran next to Gemini, but Gemini found the price first.
      details.tiers.claude = claudeEarly ? { used: false, ran_in_parallel: true } : { used: false };
    }

    details.item = item.name ? item : null;
    details.price_source = item.price_source;
    console.log(LOG, "price source:", item.price_source || "none", item.price != null ? item.price : "");
    console.log(LOG, "readItem", details);
    const ms = (tier) => (details.tiers[tier]?.ms != null ? details.tiers[tier].ms : "-");
    console.log(LOG, `timing read total=${Date.now() - started} gemini_ms=${ms("gemini")} claude_ms=${ms("claude")}`);
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
