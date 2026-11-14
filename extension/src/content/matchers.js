// Detects add-to-cart and remove controls. Shared by the click listeners and Diagnose.
(function (global) {
  "use strict";

  const ADD_RE = /add\s*(to\s*)?(shopping\s*)?(bag|cart|basket)/i;
  const REMOVE_RE = /(remove|delete|trash)/i;
  const CONTROL_SELECTOR = 'button, a, [role="button"], input[type="submit"], input[type="button"]';
  const MAX_TEXT = 80; // longer text means a container, not a control

  // All the strings that describe a control. Hyphens and underscores become spaces so
  // data-testid="add-to-cart" matches the same pattern as "Add to cart".
  function controlTexts(el, attrs) {
    const out = [];
    const text = (el.innerText || el.textContent || "").trim();
    if (text && text.length <= MAX_TEXT) out.push(text);
    for (const attr of attrs) {
      const v = el.getAttribute(attr);
      if (v) out.push(v);
    }
    return out.map((s) => s.replace(/[-_]+/g, " ").replace(/\s+/g, " ").trim()).filter(Boolean);
  }

  const ADD_ATTRS = ["aria-label", "title", "value", "data-testid"];
  const REMOVE_ATTRS = ["aria-label", "title"];

  function isAddToCart(el) {
    return controlTexts(el, ADD_ATTRS).some((t) => ADD_RE.test(t));
  }

  function isRemove(el) {
    return controlTexts(el, REMOVE_ATTRS).some((t) => REMOVE_RE.test(t));
  }

  // Walk the click's composed path (works through open shadow roots) to the first control
  // that passes the test. Clicks inside Second Look's own UI are ignored.
  function findControl(event, test) {
    const host = document.getElementById("secondlook-shadow-host");
    const path = event.composedPath();
    if (host && path.includes(host)) return null;
    for (const node of path) {
      if (!(node instanceof Element)) continue;
      if (node.matches(CONTROL_SELECTOR) && test(node)) return node;
    }
    return null;
  }

  function describe(el) {
    return controlTexts(el, ADD_ATTRS)[0] || el.tagName.toLowerCase();
  }

  function candidates(test) {
    return Array.from(document.querySelectorAll(CONTROL_SELECTOR)).filter(test).map(describe);
  }

  global.SecondLookMatchers = { ADD_RE, REMOVE_RE, isAddToCart, isRemove, findControl, describe, candidates };
})(globalThis);
