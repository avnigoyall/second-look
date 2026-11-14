// Text windows for the Claude price tier (POST /read-price). Pure functions, no DOM, so node can test them.
// buildPriceWindows(fullText, cartText) returns short windows of page text: first the text around the
// add-to-cart button, then RADIUS characters on each side of every currency amount, in page order.
// Overlapping windows merge, a window already inside another is dropped, and the total is capped.
// Windows are never cut mid-amount: amount windows end RADIUS characters past their last amount.
(function (global) {
  "use strict";

  const RADIUS = 120; // characters kept before and after each amount
  const CART_MAX = 1500; // the add-to-cart window
  const MAX_WINDOWS = 20;
  const MAX_TOTAL = 6000; // the backend accepts up to 24 windows and 8000 characters
  // "$12.34", "$ 12", "€39,90", "£12.50", "12.34 USD", "39,90 €", "1 299,00 EUR".
  const AMOUNT_RE = /[$€£]\s?\d(?:[\d.,]*\d)?|\d(?:[\d.,]*\d)?\s?(?:USD|EUR|GBP|CAD|€|£)(?![A-Za-z])/g;

  const clean = (text) => String(text || "").replace(/\s+/g, " ").trim();

  // [start, end) ranges around every amount, merged when they overlap or touch.
  function amountRanges(text, radius) {
    const ranges = [];
    for (const m of text.matchAll(AMOUNT_RE)) {
      const start = Math.max(0, m.index - radius);
      const end = Math.min(text.length, m.index + m[0].length + radius);
      const last = ranges[ranges.length - 1];
      if (last && start <= last[1]) last[1] = Math.max(last[1], end);
      else ranges.push([start, end]);
    }
    return ranges;
  }

  function buildPriceWindows(fullText, cartText, { radius = RADIUS, maxWindows = MAX_WINDOWS, maxTotal = MAX_TOTAL, cartMax = CART_MAX } = {}) {
    const text = clean(fullText);
    const candidates = [];
    let cart = clean(cartText);
    // Cut a long cart text at a space, so no amount is split in half ("$39" of "$39.90").
    if (cart.length > cartMax) cart = cart.slice(0, cart.lastIndexOf(" ", cartMax) > 0 ? cart.lastIndexOf(" ", cartMax) : cartMax);
    if (cart) candidates.push(cart);
    for (const [start, end] of amountRanges(text, radius)) candidates.push(text.slice(start, end).trim());

    const windows = [];
    let total = 0;
    for (const w of candidates) {
      if (windows.length >= maxWindows) break;
      if (!w || windows.some((kept) => kept.includes(w))) continue; // duplicate or already inside the cart window
      if (total + w.length > maxTotal) break; // never cut a window, a cut could split an amount
      windows.push(w);
      total += w.length;
    }
    return windows;
  }

  global.SecondLookPriceWindows = { buildPriceWindows, AMOUNT_RE, RADIUS, MAX_WINDOWS, MAX_TOTAL, CART_MAX };
})(globalThis);
