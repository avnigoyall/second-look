// DOM price tier: runs when JSON-LD and meta tags give a name but no price, before Gemini.
// Order: itemprop="price", then [data-price], then currency text inside the main product
// container (the element that holds both the product title and the add-to-cart button).
// Sale prices: struck-through or "was / original" prices are skipped, so the current price wins.
// Ranges: parsePrice returns the lower value.
(function (global) {
  "use strict";

  const { parsePrice, parseCurrency } = global.SecondLookPrice;
  const CURRENCY_RE = /([$€£¥₹]\s?\d[\d.,]*|\d[\d.,]*\s?(€|kr|USD|EUR|GBP|CAD))/;
  const OLD_PRICE_RE = /\b(was|original|compare|strike|old|list[-_ ]?price|before|crossed)\b/i; // not "regular": H&M uses it for the current non-member price
  const MAX_LEFTOVER_LETTERS = 15; // "Sale price $39.90" passes, "Free shipping over $50" does not

  function isVisible(el) {
    return el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden";
  }

  // Struck-through or labelled as an old price (the "was" half of a sale).
  function isOldPrice(el) {
    for (let node = el, i = 0; node && i < 3; node = node.parentElement, i++) {
      if (/^(S|DEL|STRIKE)$/.test(node.tagName)) return true;
      if (getComputedStyle(node).textDecorationLine.includes("line-through")) return true;
      const label = `${node.className && typeof node.className === "string" ? node.className : ""} ${node.getAttribute("data-qa") || ""} ${node.getAttribute("aria-label") || ""}`;
      if (OLD_PRICE_RE.test(label.replace(/[-_]+/g, " "))) return true;
    }
    return false;
  }

  function fromItemprop() {
    for (const el of document.querySelectorAll('[itemprop="price"]')) {
      const raw = el.getAttribute("content") || el.textContent;
      const price = parsePrice(raw);
      if (price != null) return { price, currency: parseCurrency(raw), via: 'itemprop="price"' };
    }
    return null;
  }

  function fromDataPrice(scope) {
    for (const el of scope.querySelectorAll("[data-price]")) {
      if (!isVisible(el) || isOldPrice(el)) continue;
      const attr = el.getAttribute("data-price");
      // Accept the attribute only when it reads like a price ("49.95", "$49.95"), not cents ("4995").
      const raw = /[.,]\d{2}\b|[$€£]/.test(attr) ? attr : el.textContent;
      const price = parsePrice(raw);
      if (price != null && price > 0) return { price, currency: parseCurrency(raw) || parseCurrency(el.textContent), via: "data-price" };
    }
    return null;
  }

  function findTitle(name) {
    const h1 = Array.from(document.querySelectorAll("h1")).find(isVisible);
    if (h1) return h1;
    if (!name) return null;
    const target = name.toLowerCase();
    return Array.from(document.querySelectorAll("h2, [class*='title'], [class*='name']"))
      .find((el) => isVisible(el) && (el.innerText || "").trim().toLowerCase() === target) || null;
  }

  function mainAddButton() {
    const m = global.SecondLookMatchers;
    return Array.from(document.querySelectorAll('button, a, [role="button"], input[type="submit"]'))
      .find((el) => isVisible(el) && m.isAddToCart(el) && !m.otherProductTile(el)) || null;
  }

  // Lowest common ancestor of the title and the add button. Falls back to a few levels above one.
  function productContainer(name) {
    const title = findTitle(name);
    const button = mainAddButton();
    if (title && button) {
      let node = title;
      while (node && !node.contains(button)) node = node.parentElement;
      if (node && node !== document.documentElement) return node;
    }
    let node = title || button;
    for (let i = 0; node && i < 4; i++) node = node.parentElement;
    return node || document.querySelector("main") || document.body;
  }

  // Deepest visible elements whose short text is mostly a price.
  // Labels near a price that mean it is not the regular current price.
  const NOT_CURRENT_RE = /member|\bwas\b|original/i;

  // The price's own text, the label just before it, and loose text in its parent.
  function nearbyText(el, candidate) {
    // The element just before counts as a label only if it is not a price itself
    // ("Member price $15.99" next to "$19.99" labels the member price, not the regular one).
    const prev = el.previousElementSibling;
    const prevRaw = prev ? prev.innerText || "" : "";
    const prevText = prevRaw.length <= 40 && !CURRENCY_RE.test(prevRaw) ? prevRaw : "";
    const parentOwn = el.parentElement
      ? Array.from(el.parentElement.childNodes).filter((n) => n.nodeType === Node.TEXT_NODE).map((n) => n.textContent).join(" ")
      : "";
    return `${candidate} ${prevText} ${parentOwn}`;
  }

  // The displayed current price: first currency amount in scope that is not struck through
  // and has no member / was / original label nearby.
  function fromText(scope) {
    const hits = [];
    for (const el of scope.querySelectorAll("*")) {
      if (el.closest("#secondlook-shadow-host")) continue;
      const text = (el.innerText || "").trim();
      if (!text || text.length > 40 || !CURRENCY_RE.test(text)) continue;
      let candidate = text;
      if (Array.from(el.children).some((c) => CURRENCY_RE.test(c.innerText || ""))) {
        // A child holds a price. Still check this element's own loose text: "$39.90 <s>$59.90</s>".
        candidate = Array.from(el.childNodes).filter((n) => n.nodeType === Node.TEXT_NODE)
          .map((n) => n.textContent).join(" ").trim();
        if (!CURRENCY_RE.test(candidate)) continue;
      }
      const letters = candidate.replace(/[\d\s.,$€£¥₹\-–]+/g, "").replace(/USD|EUR|GBP|CAD|kr/g, "");
      if (letters.length > MAX_LEFTOVER_LETTERS || !isVisible(el)) continue;
      const notCurrent = (candidate === text && isOldPrice(el)) || NOT_CURRENT_RE.test(nearbyText(el, candidate));
      hits.push({ el, text: candidate, old: notCurrent });
    }
    const current = hits.find((h) => !h.old);
    if (!current) return null;
    const price = parsePrice(current.text);
    return price == null ? null : { price, currency: parseCurrency(current.text), via: `text "${current.text}"` };
  }

  function readDomPrice(name) {
    try {
      const container = productContainer(name);
      return fromItemprop() || fromDataPrice(container) || fromText(container);
    } catch (err) {
      console.warn("[SecondLook] DOM price read failed", err);
      return null;
    }
  }

  // Only the visible text price in the main product container (no itemprop or data-price).
  // Used to double-check prices from JSON-LD and meta tags.
  function readDisplayedPrice(name) {
    try {
      return fromText(productContainer(name));
    } catch (err) {
      console.warn("[SecondLook] displayed price read failed", err);
      return null;
    }
  }

  global.SecondLookDomPrice = { readDomPrice, readDisplayedPrice, mainAddButton };
})(globalThis);
