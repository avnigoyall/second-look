// Run: node extension/tests/price.test.js
const assert = require("node:assert");
require("../src/content/reader/price.js");
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
console.log(`price tests passed (${cases.length + 4} checks)`);
