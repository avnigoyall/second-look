// Price parsing shared by every reader tier.
// Handles currency symbols and codes, thousands separators, decimal commas, and ranges
// (a range or "was / now" text returns the lowest number found).
(function (global) {
  "use strict";

  const SYMBOLS = { "$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR", "₩": "KRW" };

  function toNumber(token) {
    let t = token.replace(/[\s ]/g, "");
    const lastDot = t.lastIndexOf(".");
    const lastComma = t.lastIndexOf(",");
    if (lastDot >= 0 && lastComma >= 0) {
      // Both present: whichever comes last is the decimal separator.
      const decimal = lastDot > lastComma ? "." : ",";
      const thousands = decimal === "." ? "," : ".";
      t = t.split(thousands).join("").replace(decimal, ".");
    } else if (lastComma >= 0) {
      // "39,90" is a decimal comma. "1,299" is a thousands separator.
      t = /,\d{1,2}$/.test(t) ? t.replace(/\./g, "").replace(",", ".") : t.replace(/,/g, "");
    } else if (/^\d{1,3}(\.\d{3})+$/.test(t)) {
      // "1.299" reads as a thousands separator (common in EU prices).
      t = t.replace(/\./g, "");
    }
    const n = Number(t);
    return Number.isFinite(n) ? n : null;
  }

  function parsePrice(value) {
    if (value == null || value === "") return null;
    if (typeof value === "number") return Number.isFinite(value) && value >= 0 ? value : null;
    const text = String(value);
    const tokens = text.match(/\d[\d.,\s ]*\d|\d/g) || [];
    const numbers = tokens.map(toNumber).filter((n) => n != null && n >= 0);
    if (!numbers.length) return null;
    return Math.round(Math.min(...numbers) * 100) / 100;
  }

  function parseCurrency(value) {
    if (!value) return null;
    const text = String(value);
    const code = text.match(/\b[A-Z]{3}\b/);
    if (code) return code[0];
    for (const [symbol, iso] of Object.entries(SYMBOLS)) {
      if (text.includes(symbol)) return iso;
    }
    return null;
  }

  global.SecondLookPrice = { parsePrice, parseCurrency };
})(globalThis);
