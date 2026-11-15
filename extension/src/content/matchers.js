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

  // ---------- ready to add? (size chosen, in stock) ----------
  // The check only runs when the store would really add the item. Otherwise the click goes straight to
  // the store, which shows its own "select a size" or "sold out" message.

  const UNAVAILABLE_RE = /\b(sold\s*out|out of stock|unavailable|notify me|coming soon|agotado|épuisé|ausverkauft)\b/i;
  const NEEDS_CHOICE_RE = /\b(select|choose|pick)\s+(a\s+|an\s+|your\s+)?(size|colou?r|option|variant|style|fit|length)\b/i;
  const SIZE_RE = /\b(sizes?|talla|taille|größe)\b/i;
  const NOT_SIZE_RE = /\bsize\s*(guide|chart|help|&\s*fit|and fit)\b|\bfind your size\b/i;
  const SELECTED_SEL = [
    "input:checked", "[aria-checked='true']", "[aria-pressed='true']", "[aria-selected='true']", "[aria-current='true']",
    "[data-selected='true']", "[data-state='checked']", "[data-state='on']", "[data-state='active']",
  ].join(",");
  const SELECTED_CLASS_RE = /(^|[\s_-])(selected|is-selected|active|is-active|checked|is-checked|current|chosen)($|[\s_-])/i;
  const OPTION_SEL = "input[type=radio], [role=radio], [role=option], button, li, a, label";

  const shortText = (el) => (el.textContent || el.value || el.getAttribute("aria-label") || "").replace(/\s+/g, " ").trim();
  // Some stores (Salesforce Commerce sites such as PacSun) mark the choice on an element inside the option.
  const INNER_SELECTED = "input:checked, [aria-checked='true'], [aria-selected='true'], [aria-pressed='true'], .selected, .is-selected";
  const isSelected = (el) => el.matches(SELECTED_SEL) || SELECTED_CLASS_RE.test(el.className?.baseVal ?? el.className ?? "")
    || Boolean(el.querySelector?.(INNER_SELECTED));

  // Does this container describe itself as a size picker (and not a size guide link)?
  function sizeSignal(container) {
    const attrs = ["aria-label", "id", "data-testid", "data-qa", "name", "class"]
      .map((a) => container.getAttribute(a) || "").join(" ").replace(/[-_]/g, " ");
    const heading = container.querySelector(":scope > legend, :scope > label, :scope > h2, :scope > h3, :scope > h4, :scope > p, :scope > span");
    const label = heading ? shortText(heading).slice(0, 40) : "";
    const prev = container.previousElementSibling ? shortText(container.previousElementSibling).slice(0, 40) : "";
    const labelledBy = container.getAttribute("aria-labelledby");
    const named = labelledBy ? shortText(document.getElementById(labelledBy) || document.createElement("i")) : "";
    const text = `${attrs} ${label} ${prev} ${named}`;
    return SIZE_RE.test(text) && !NOT_SIZE_RE.test(text);
  }

  function sizeOptions(container) {
    return Array.from(container.querySelectorAll(OPTION_SEL)).filter((o) => {
      if (o.closest("[role=dialog], header, nav, footer")) return false;
      const t = shortText(o);
      return o.matches("input[type=radio]") || (t && t.length <= 12 && !NOT_SIZE_RE.test(t) && !NEEDS_CHOICE_RE.test(t));
    });
  }

  // On screen: hidden copies (sticky bars, mobile layouts, closed quick views, templates) never block a check.
  function isRendered(el) {
    if (!el.isConnected || !el.getClientRects().length) return false;
    if (el.closest("[aria-hidden='true'], [hidden], [inert], template")) return false;
    const style = getComputedStyle(el);
    return style.visibility !== "hidden" && style.display !== "none";
  }

  // "Size: M" or "Selected size: M" next to the picker means a size is chosen, whatever the buttons say.
  const SIZE_SHOWN_RE = /\bsize\s*:\s*(?!select|choose|pick|-)([a-z0-9][\w./ -]{0,10})/i;
  function showsChosenSize(container) {
    const near = [container.querySelector(":scope > legend, :scope > label, :scope > h2, :scope > h3, :scope > h4, :scope > p, :scope > span"),
      container.previousElementSibling, container.parentElement?.querySelector(":scope > legend, :scope > label, :scope > p, :scope > span")];
    return near.some((el) => el && SIZE_SHOWN_RE.test(shortText(el).slice(0, 40)));
  }

  // A picker inside a different form (a quick view or another product) is not this button's.
  function inScope(picker, control) {
    const form = control?.closest("form");
    const pickerForm = picker.closest("form");
    return !form || !pickerForm || form === pickerForm;
  }

  const describePicker = (p) => {
    const bits = [p.tagName.toLowerCase(), p.id && `#${p.id}`, p.getAttribute("aria-label") && `[${p.getAttribute("aria-label")}]`];
    return bits.filter(Boolean).join("") + ` (${sizeOptions(p).length} options)`;
  };

  // Size pickers without a choice. A one-option picker ("One size") counts as chosen.
  function unchosenSizePickers(control) {
    const pickers = [];
    const containers = document.querySelectorAll("fieldset, [role=radiogroup], [role=listbox], ul, ol, div, section");
    for (const c of containers) {
      if (c.closest("header, nav, footer, [role=dialog]:not([aria-modal='true'])") || !sizeSignal(c)) continue;
      if (!isRendered(c) || !inScope(c, control)) continue;
      const options = sizeOptions(c).filter(isRendered);
      if (options.length < 2) continue;
      if (pickers.some((p) => c.contains(p))) continue; // keep the innermost picker
      for (let i = pickers.length - 1; i >= 0; i--) if (pickers[i].contains(c)) pickers.splice(i, 1);
      pickers.push(c);
    }
    const unchosen = pickers.filter((p) => !sizeOptions(p).some(isSelected) && !p.querySelector("input:checked")
      && !showsChosenSize(p));
    // <select> size menus: a real value must be chosen (not "Select size").
    for (const s of document.querySelectorAll("select")) {
      if (!isRendered(s)) continue; // a custom dropdown hides the real <select>; when in doubt, check the item
      if (!inScope(s, control)) continue;
      const name = `${s.name} ${s.id} ${s.getAttribute("aria-label") || ""} ${s.labels?.[0]?.textContent || ""} ${s.options[0]?.text || ""}`;
      if (!SIZE_RE.test(name) || NOT_SIZE_RE.test(name)) continue;
      const opt = s.options[s.selectedIndex];
      if (!opt || opt.value === "" || NEEDS_CHOICE_RE.test(opt.text) || /^\s*(-+|select|choose)/i.test(opt.text)) unchosen.push(s);
    }
    return unchosen;
  }

  // Single product marked out of stock in JSON-LD (variant groups are left to the button and size checks).
  function jsonLdOutOfStock() {
    for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
      let data;
      try { data = JSON.parse(s.textContent); } catch { continue; }
      const nodes = [].concat(data?.["@graph"] || data);
      for (const n of nodes) {
        if (!n || ![].concat(n["@type"]).includes("Product") || n.hasVariant) continue;
        const offers = [].concat(n.offers || []).flatMap((o) => (o?.offers ? [].concat(o.offers) : [o]));
        const avail = offers.map((o) => String(o?.availability || "")).filter(Boolean);
        if (avail.length && avail.every((a) => /OutOfStock|SoldOut|Discontinued/i.test(a))) return true;
      }
    }
    return false;
  }

  // { ready: true } or { ready: false, reason } for an add-to-cart control.
  function readiness(control) {
    if (control.disabled || control.getAttribute("aria-disabled") === "true") return { ready: false, reason: "button disabled" };
    const label = `${shortText(control)} ${control.getAttribute("aria-label") || ""}`;
    if (UNAVAILABLE_RE.test(label)) return { ready: false, reason: "sold out" };
    if (NEEDS_CHOICE_RE.test(label)) return { ready: false, reason: "needs a size or option" };
    if (jsonLdOutOfStock()) return { ready: false, reason: "out of stock" };
    const unchosen = unchosenSizePickers(control);
    if (unchosen.length) return { ready: false, reason: `no size selected: ${unchosen.slice(0, 3).map(describePicker).join(", ")}` };
    return { ready: true };
  }

  global.SecondLookMatchers = {
    ADD_TEXT_RES, ADD_ATTR_RE, NOT_CART_RE, REMOVE_RE,
    isAddToCart, isRemove, findControl, otherProductTile, describe, candidates, readiness,
  };
})(globalThis);
