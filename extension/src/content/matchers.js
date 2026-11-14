// Detects add-to-cart and remove controls. Shared by the click listeners and Diagnose.
(function (global) {
  "use strict";

  // Visible text, aria-label, title, value (English plus Spanish, French, German).
  const ADD_TEXT_RES = [
    /add\s*(to\s*)?(shopping\s*)?(bag|cart|basket)/i,
    /(añadir|agregar)\s+a\s+(la\s+)?(bolsa|cesta|carrito)/i,
    /ajouter\s+au\s+panier/i,
    /in\s+den\s+warenkorb/i,
  ];
  // Machine attributes: data-qa-action, data-test, data-qa, data-testid, id, class tokens.
  const ADD_ATTR_RE = /add[-_ ]?to[-_ ]?(cart|bag|basket)/i;
  const ADD_ATTRS = ["data-qa-action", "data-test", "data-qa", "data-testid", "id"];
  // Wishlist and list buttons never count, even if another attribute says add-to-cart.
  const NOT_CART_RE = /wish[-_ ]?list|favou?rite|\bsave\b|\blista\b/i;
  const REMOVE_RE = /(remove|delete|trash)/i;
  const CONTROL_SELECTOR = 'button, a, [role="button"], input[type="submit"], input[type="button"]';
  const MAX_TEXT = 80; // longer text means a container, not a control

  function visibleText(el) {
    const text = (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
    return text.length <= MAX_TEXT ? text : "";
  }

  function humanTexts(el) {
    return [visibleText(el), el.getAttribute("aria-label"), el.getAttribute("title"), el.value]
      .filter((v) => typeof v === "string" && v.trim())
      .map((v) => v.replace(/[-_]+/g, " ").replace(/\s+/g, " ").trim());
  }

  function attrValues(el) {
    const values = ADD_ATTRS.map((a) => el.getAttribute(a)).filter(Boolean);
    return values.concat(Array.from(el.classList || []));
  }

  // Any data-* attribute that says add-to-cart (used to confirm a bare "ADD" button).
  function hasAddDataAttr(el) {
    return Array.from(el.attributes).some((a) => a.name.startsWith("data-") && ADD_ATTR_RE.test(a.value));
  }

  function isAddToCart(el) {
    const texts = humanTexts(el);
    const attrs = attrValues(el);
    if ([...texts, ...attrs].some((t) => NOT_CART_RE.test(t))) return false;
    if (texts.some((t) => ADD_TEXT_RES.some((re) => re.test(t)))) return true;
    // A bare "ADD" button (Zara) only counts when a data attribute confirms it.
    if (/^add$/i.test(visibleText(el))) return hasAddDataAttr(el);
    return attrs.some((v) => ADD_ATTR_RE.test(v));
  }

  function isRemove(el) {
    return humanTexts(el).some((t) => REMOVE_RE.test(t));
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

  // ---------- product tiles (recommendations, "you may also like") ----------

  function samePath(a, b) {
    try {
      const norm = (u) => {
        const url = new URL(u, location.href);
        return (url.origin + url.pathname).toLowerCase().replace(/\/+$/, "");
      };
      return norm(a) === norm(b);
    } catch {
      return false;
    }
  }

  function isCurrentPage(href) {
    const canonical = document.querySelector('link[rel="canonical"]')?.href;
    return samePath(href, location.href) || (canonical && samePath(href, canonical));
  }

  // The nearest small ancestor with a linked image (how product tiles show their photo).
  // A container that holds the page's h1 is the main product, never a tile.
  function tileFor(control) {
    let node = control.parentElement || control.getRootNode()?.host;
    for (let depth = 0; node && node !== document.body && depth < 8; depth++) {
      if ((node.innerText || "").length > 600 || node.querySelector("h1")) return null;
      if (node.querySelector("a[href] img, a[href] picture")) return node;
      node = node.parentElement || node.getRootNode()?.host || null;
    }
    return null;
  }

  // Returns the other product's URL when the control sits in a tile for a different product.
  function otherProductTile(control) {
    const tile = tileFor(control);
    if (!tile) return null;
    const links = Array.from(tile.querySelectorAll("a[href]"))
      .map((a) => a.href)
      .filter((h) => /^https?:/i.test(h));
    if (!links.length || links.some(isCurrentPage)) return null;
    return links[0];
  }

  function describe(el) {
    return humanTexts(el)[0] || attrValues(el)[0] || el.tagName.toLowerCase();
  }

  function candidates(test) {
    return Array.from(document.querySelectorAll(CONTROL_SELECTOR)).filter(test).map((el) => {
      const other = test === isAddToCart ? otherProductTile(el) : null;
      return other ? `${describe(el)} (tile for another product, ignored)` : describe(el);
    });
  }

  global.SecondLookMatchers = {
    ADD_TEXT_RES, ADD_ATTR_RE, NOT_CART_RE, REMOVE_RE,
    isAddToCart, isRemove, findControl, otherProductTile, describe, candidates,
  };
})(globalThis);
