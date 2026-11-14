// Run: node extension/tests/price.test.js
const assert = require("node:assert");
require("../src/content/reader/price.js");
require("../src/content/reader/priceWindows.js");
const { parsePrice, parseCurrency } = globalThis.SecondLookPrice;

const cases = [
  ["$29.90", 29.9], ["29.90", 29.9], [29.9, 29.9], ["$1,299.99", 1299.99], ["1.299,99 €", 1299.99],
  ["€39,90", 39.9], ["1,299", 1299], ["1.299", 1299], ["$20 - $30", 20], ["$29.90–$34.90", 29.9],
  ["Was $49.90 Now $39.90", 39.9], ["USD 49", 49], ["£ 12.5", 12.5], ["1 299,00 kr", 1299],
  ["Free", null], ["", null], [null, null], [-5, null], ["0", 0],
];
for (const [input, want] of cases) assert.strictEqual(parsePrice(input), want, `parsePrice(${JSON.stringify(input)})`);
assert.strictEqual(parseCurrency("$29.90"), "USD");
assert.strictEqual(parseCurrency("39,90 €"), "EUR");
assert.strictEqual(parseCurrency("GBP 12"), "GBP");
assert.strictEqual(parseCurrency("29.90"), null);

// ---------- buildPriceWindows (Claude price tier) ----------
const { buildPriceWindows, RADIUS, MAX_TOTAL, MAX_WINDOWS } = globalThis.SecondLookPriceWindows;
let windowChecks = 0;
const check = (cond, msg) => { assert.ok(cond, msg); windowChecks++; };
const filler = (n) => "lorem ipsum dolor sit amet ".repeat(Math.ceil(n / 27)).slice(0, n);

// A window around every amount (symbols before and codes after), far apart so they stay separate.
{
  const text = `${filler(500)} Price $39.90 ${filler(500)} now 29,90 € ${filler(500)} only £12.50 ${filler(500)} cost 49.00 USD ${filler(500)}`;
  const w = buildPriceWindows(text, "");
  check(w.length === 4, `4 separate windows, got ${w.length}`);
  check(w[0].includes("$39.90") && w[1].includes("29,90 €") && w[2].includes("£12.50") && w[3].includes("49.00 USD"),
    "each amount has its own window, in page order");
  check(w.every((x) => x.length <= 2 * RADIUS + 15), "each window is about RADIUS on each side");
  check(!w.some((x) => x.includes(filler(300))), "far filler text is left out");
}
// Amounts closer than 2 * RADIUS merge into one window.
{
  const text = `${filler(400)} Was $59.90 now $39.90 ${filler(50)} Member price $29.90 ${filler(400)}`;
  const w = buildPriceWindows(text, "");
  check(w.length === 1 && /\$59\.90.*\$39\.90.*\$29\.90/.test(w[0]), "overlapping windows merge");
}
// The add-to-cart window comes first; a window already inside a kept one is dropped.
{
  const cart = "Linen Shirt Price $39.90 Size S M L Add to cart";
  const w = buildPriceWindows(`${filler(300)} ${cart} ${filler(300)}`, cart);
  check(w[0] === cart, "cart window first");
  check(w.length === 2 && w[1].includes("$39.90") && w[1].length > cart.length, "a larger amount window is still kept");
  check(buildPriceWindows("Pay $10 now", "Pay $10 now").length === 1, "identical window deduped");
  check(buildPriceWindows("Pay $10 now", "Big box: Pay $10 now, Add to cart").length === 1, "window inside the cart window dropped");
}
// Cart text with no amounts still gives a window; no amounts and no cart text gives none.
{
  check(buildPriceWindows("no prices here", "Add to bag").length === 1, "cart window without amounts");
  check(buildPriceWindows("no prices here", "").length === 0, "nothing to send");
  check(buildPriceWindows("", null).length === 0, "empty input");
}
// Caps: total characters and window count; windows are never cut mid-amount.
{
  const many = Array.from({ length: 200 }, (_, i) => `${filler(400)} item ${i} $${i + 1}.99`).join(" ");
  const w = buildPriceWindows(many, "");
  const total = w.reduce((n, x) => n + x.length, 0);
  check(total <= MAX_TOTAL, `total ${total} <= ${MAX_TOTAL}`);
  check(w.length <= MAX_WINDOWS, `at most ${MAX_WINDOWS} windows`);
  check(w.every((x) => /\$\d+\.99/.test(x)), "every window holds a whole amount");
  const tight = Array.from({ length: 60 }, (_, i) => `${filler(300)} $${i}.50`).join(" ");
  check(buildPriceWindows(tight, "").length === MAX_WINDOWS, "window count cap reached first when windows are small");
  const big = buildPriceWindows(`${filler(3000)} $5.00`, `${filler(1494)} $123.45 more text`);
  check(big[0].length <= 1500 && !big[0].includes("$1"), "long cart text is cut at a space, never mid-amount");
}
// Whitespace collapses, so windows match the text the page shows.
{
  const w = buildPriceWindows("Sale\n\n   price   $19.99\tnow", "");
  check(w[0] === "Sale price $19.99 now", JSON.stringify(w[0]));
}

console.log(`price tests passed (${cases.length + 4} checks), price window tests passed (${windowChecks} checks)`);
