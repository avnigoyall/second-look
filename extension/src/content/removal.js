// Removal capture: a capture-phase click listener for remove / delete / trash controls.
// It walks up from the control until a container's text fuzzy-matches a session item, then marks
// that item removed. If nothing matches, it does nothing.
(function (global) {
  "use strict";

  if (global.__secondLookRemovalLoaded) return;
  global.__secondLookRemovalLoaded = true;

  const LOG = "[SecondLook]";
  const MIN_OVERLAP = 0.6;
  const MAX_DEPTH = 15;
  const MAX_TEXT = 5000; // bigger than this is the whole page, not a cart row

  function tokens(text) {
    return (text || "")
      .toLowerCase()
      .replace(/[^\p{L}\p{N}\s]/gu, " ")
      .split(/\s+/)
      .filter((t) => t.length > 1);
  }

  // Share of the item name's tokens that appear in the container text.
  function overlap(name, containerTokens) {
    const nameTokens = [...new Set(tokens(name))];
    if (!nameTokens.length) return 0;
    const hits = nameTokens.filter((t) => containerTokens.has(t)).length;
    return hits / nameTokens.length;
  }

  // Read ancestor texts right away, before the store's handler removes the row from the page.
  function ancestorTexts(control) {
    const texts = [];
    let node = control;
    for (let depth = 0; node && node !== document.body && depth < MAX_DEPTH; depth++) {
      const text = node.innerText || "";
      if (text.length > MAX_TEXT) break;
      if (text.trim()) texts.push(text);
      node = node.parentElement || node.getRootNode()?.host || null;
    }
    return texts;
  }

  async function matchAndRemove(texts) {
    const entries = (await global.SecondLookSession.list()).filter((e) => e.status !== "removed" && e.name);
    if (!entries.length) return;
    for (const text of texts) {
      const containerTokens = new Set(tokens(text));
      let best = null;
      for (const entry of entries) {
        const score = overlap(entry.name, containerTokens);
        if (score >= MIN_OVERLAP && (!best || score > best.score)) best = { entry, score };
      }
      if (best) {
        await global.SecondLookSession.markRemoved(best.entry.id);
        console.log(LOG, "removed from session:", best.entry.name, `(overlap ${best.score.toFixed(2)})`);
        global.SecondLookCard.showNotice(`Removed from your Second Look list: ${best.entry.name}`);
        return;
      }
    }
    console.log(LOG, "remove click: no session item matched");
  }

  document.addEventListener("click", (event) => {
    if (!event.isTrusted) return;
    const control = global.SecondLookMatchers.findControl(event, global.SecondLookMatchers.isRemove);
    if (!control) return;
    matchAndRemove(ancestorTexts(control)).catch((err) => console.warn(LOG, "removal match failed", err));
  }, true);

  global.SecondLookRemoval = { tokens, overlap };
})(globalThis);
