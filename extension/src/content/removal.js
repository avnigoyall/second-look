// Removal capture. A remove / delete / trash click does NOT mark the item removed right away,
// because many stores first ask "Are you sure?". Instead it creates a pending removal:
//   1. Fuzzy-match the clicked row to a session item (ancestor text, 60% token overlap).
//   2. Watch the page for up to 6 seconds (longer while a confirm dialog is open, up to 60).
//   3. Mark removed only when the item is gone (its cart row was removed or no longer shows the
//      name, or the name is gone from the main content), the cart count drops, or the user
//      clicks a remove / delete / yes / confirm button inside a dialog. Gone checks wait while
//      a dialog is open, so opening "Are you sure?" never counts as a removal.
//   4. A cancel button in the dialog, or the item still being there when time runs out, does nothing.
(function (global) {
  "use strict";

  if (global.__secondLookRemovalLoaded) return;
  global.__secondLookRemovalLoaded = true;

  const LOG = "[SecondLook]";
  const MIN_OVERLAP = 0.6;
  const MAX_DEPTH = 15;
  const MAX_TEXT = 5000; // bigger than this is the whole page, not a cart row
  const WATCH_MS = 6000;
  const DIALOG_MAX_MS = 60000;
  const POLL_MS = 300;
  const DIALOG_SELECTOR = 'dialog[open], [role="dialog"], [role="alertdialog"], [aria-modal="true"]';
  const CONFIRM_RE = /remove|delete|yes|confirm/i;
  const CANCEL_RE = /cancel|\bno\b|keep|close|back/i;

  let pending = null; // { entry, row, startedAt, startCount, timer }

  function tokens(text) {
    return (text || "")
      .toLowerCase()
      .replace(/[^\p{L}\p{N}\s]/gu, " ")
      .split(/\s+/)
      .filter((t) => t.length > 1);
  }

  // Share of the item name's tokens that appear in the text.
  function overlap(name, textTokens) {
    const nameTokens = [...new Set(tokens(name))];
    if (!nameTokens.length) return 0;
    return nameTokens.filter((t) => textTokens.has(t)).length / nameTokens.length;
  }

  // Read ancestor texts right away, before the store changes the row.
  function ancestorTexts(control) {
    const texts = [];
    let node = control;
    for (let depth = 0; node && node !== document.body && depth < MAX_DEPTH; depth++) {
      const text = node.innerText || "";
      if (text.length > MAX_TEXT) break;
      if (text.trim()) texts.push({ node, text });
      node = node.parentElement || node.getRootNode()?.host || null;
    }
    return texts;
  }

  // The closest ancestor whose text covers a session item's name. Returns { entry, row, score }.
  function bestMatch(texts, entries) {
    for (const { node, text } of texts) {
      const textTokens = new Set(tokens(text));
      let best = null;
      for (const entry of entries) {
        const score = overlap(entry.name, textTokens);
        if (score >= MIN_OVERLAP && (!best || score > best.score)) best = { entry, row: node, score };
      }
      if (best) return best;
    }
    return null;
  }

  function openDialog() {
    return Array.from(document.querySelectorAll(DIALOG_SELECTOR)).find((d) => d.getClientRects().length > 0) || null;
  }

  // Main content text, without dialogs and without Second Look's own UI.
  function mainTokens() {
    const root = document.querySelector("main, [role='main']") || document.body;
    const clone = root.cloneNode(true);
    clone.querySelectorAll(`${DIALOG_SELECTOR}, dialog, #secondlook-shadow-host`).forEach((n) => n.remove());
    return new Set(tokens(clone.innerText || clone.textContent));
  }

  // "Bag (2)", "Cart 3 items", aria-label="Cart, 2 items". Returns a number or null.
  function cartCount() {
    for (const el of document.querySelectorAll("[aria-label], [class*='count'], [class*='Count'], [data-count], header a, header button")) {
      const text = `${el.getAttribute("aria-label") || ""} ${(el.innerText || "").slice(0, 40)}`;
      const m = text.match(/\b(?:cart|bag|basket)\b\D{0,12}(\d{1,3})\b/i) || (el.hasAttribute("data-count") ? [null, el.getAttribute("data-count")] : null);
      if (m && m[1] != null && /^\d+$/.test(m[1])) return Number(m[1]);
    }
    return null;
  }

  async function markRemoved(entry, reason) {
    clearPending();
    await global.SecondLookSession.markRemoved(entry.id);
    console.log(LOG, "removed from session:", entry.name, `(${reason})`);
    global.SecondLookCard.showNotice(`Removed from your Second Look list: ${entry.name}`);
  }

  function clearPending() {
    if (pending) clearTimeout(pending.timer);
    pending = null;
  }

  // Poll until the item is gone (mark removed) or time runs out (do nothing).
  function watch() {
    if (!pending) return;
    const p = pending;
    const elapsed = Date.now() - p.startedAt;
    const dialogOpen = Boolean(openDialog());
    const count = cartCount();
    const dropped = p.startCount != null && count != null && count < p.startCount;
    let gone = false;
    if (!dialogOpen) {
      const rowGone = !p.row.isConnected || overlap(p.entry.name, new Set(tokens(p.row.innerText))) < MIN_OVERLAP;
      gone = rowGone || overlap(p.entry.name, mainTokens()) < MIN_OVERLAP;
    }
    if (gone || dropped) {
      markRemoved(p.entry, gone ? "item no longer on the page" : `cart count ${p.startCount} -> ${count}`);
      return;
    }
    const limit = dialogOpen ? DIALOG_MAX_MS : WATCH_MS;
    if (elapsed >= limit) {
      console.log(LOG, "remove click: item still on the page, not marked removed:", p.entry.name);
      clearPending();
      return;
    }
    p.timer = setTimeout(watch, POLL_MS);
  }

  async function startPending(texts) {
    const entries = (await global.SecondLookSession.list()).filter((e) => e.status !== "removed" && e.name);
    if (!entries.length) return;
    const match = bestMatch(texts, entries);
    if (!match) {
      console.log(LOG, "remove click: no session item matched");
      return;
    }
    clearPending();
    pending = { entry: match.entry, row: match.row, startedAt: Date.now(), startCount: cartCount(), timer: null };
    console.log(LOG, "remove click matched", match.entry.name, `(overlap ${match.score.toFixed(2)}), waiting for the store to remove it`);
    pending.timer = setTimeout(watch, POLL_MS);
  }

  function controlText(el) {
    return `${(el.innerText || el.value || "").trim()} ${el.getAttribute("aria-label") || ""} ${el.getAttribute("title") || ""}`.trim();
  }

  document.addEventListener("click", (event) => {
    if (!event.isTrusted) return;
    const m = global.SecondLookMatchers;

    // Clicks inside a confirm dialog answer the pending removal.
    const path = event.composedPath().filter((n) => n instanceof Element);
    const dialog = path.find((n) => n.matches(DIALOG_SELECTOR) || n.tagName === "DIALOG");
    if (dialog) {
      const button = path.find((n) => n.matches('button, a, [role="button"], input[type="submit"], input[type="button"]'));
      if (!button) return;
      const text = controlText(button);
      if (CONFIRM_RE.test(text)) {
        if (pending) {
          markRemoved(pending.entry, `confirmed in dialog: "${text}"`);
        } else {
          // No pending click (the dialog opened another way): match the dialog's own text.
          startPending(ancestorTexts(button));
        }
      } else if (CANCEL_RE.test(text) && pending) {
        console.log(LOG, "removal cancelled in dialog:", pending.entry.name);
        clearPending();
      }
      return;
    }

    const control = m.findControl(event, m.isRemove);
    if (!control) return;
    startPending(ancestorTexts(control)).catch((err) => console.warn(LOG, "removal match failed", err));
  }, true);

  global.SecondLookRemoval = { tokens, overlap };
})(globalThis);
