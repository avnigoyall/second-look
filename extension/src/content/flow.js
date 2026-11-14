// The check flow: read the item, ask for a price if missing, call /analyze through the service
// worker, and show the verdict. A read failure ends in the paste-a-link state. A backend failure
// (down, slow, or a server error) ends in the calm "Second look unavailable" state: the store
// already handled the click, so nothing here may block it (CONTRACT section 8).
(function (global) {
  "use strict";

  const LOG = "[SecondLook]";
  const Card = () => global.SecondLookCard;
  const ATTR_FIELDS = ["category", "color_family", "fabric", "fabric_confidence", "cut", "style"];

  function send(type, payload) {
    return chrome.runtime.sendMessage({ type, payload }).then((res) => {
      if (!res?.ok) {
        const err = new Error(res?.error || "Something went wrong");
        err.code = res?.code;
        throw err;
      }
      return res.data;
    });
  }

  // Not logged in: the user has to act, so show the error state with the message.
  const needsLogin = (err) => err.code === "auth";

  function showAnalyzeFailure(err, item) {
    if (needsLogin(err)) return Card().showError(err.message);
    Card().showUnavailable({ onRetry: () => analyzeAndShow(item).catch((e) => showAnalyzeFailure(e, item)) });
  }

  async function analyzeAndShow(item) {
    if (item.price == null) {
      const price = await Card().askPrice(item);
      if (price === undefined) return; // card closed
      item = { ...item, price };
    }
    Card().showChecking(item);
    const payload = { name: item.name, image_url: item.image_url, price: item.price, store: item.store, url: item.url };
    const result = await send("ANALYZE_ITEM", { item: payload });
    console.log(LOG, "verdict", result.verdict, result.decided_by || "", result);
    const view = Card().showVerdict(item, result, {
      onAction: (action) => send("SEND_ACTION", { verdictId: result.verdict_id, action }),
      onFeedback: (value) => send("SEND_FEEDBACK", { verdictId: result.verdict_id, value }),
    });
    if (wantsOwnershipQuestion(result)) askOwnership(item, result, view);
  }

  // Part 3 ownership question: only for a small closet with no closet match at all.
  // An older backend sends no zone, so then "no matched closet item" stands in for zone none.
  function wantsOwnershipQuestion(result) {
    if (result.closet_small !== true) return false;
    if (result.zone != null) return result.zone === "none";
    return !result.matched_closet_item && !(result.matched_closet_items || []).length;
  }

  function pickAttributes(src) {
    if (!src || src.status === "attributes_failed" || !src.category) return null;
    return Object.fromEntries(ATTR_FIELDS.map((f) => [f, src[f] ?? null]));
  }

  // Runs after the verdict is on screen and never shows an error: if anything fails, the question
  // simply does not appear.
  async function askOwnership(item, result, view) {
    try {
      let attributes = pickAttributes(result.attributes);
      if (!attributes) attributes = pickAttributes(await send("GET_ATTRIBUTES", { name: item.name, image_url: item.image_url }));
      if (!attributes || !view.isCurrent()) return;
      const opts = await send("OWNERSHIP_OPTIONS", { name: item.name, attributes });
      if (!opts?.show || !opts.options?.length) return;
      view.addOwnership(opts.options, async (option) => {
        if (!option) return; // "None of these": nothing to save
        await send("OWNERSHIP_ANSWER", { attributes: option.attributes, name: option.label });
      });
    } catch (err) {
      console.log(LOG, "ownership question skipped:", err.message);
    }
  }

  // Paste-a-link: the service worker reads the URL in a background tab, then we analyze it here.
  function showPaste(message) {
    Card().showPaste({
      message,
      onSubmit: async (url) => {
        let item;
        try {
          item = await send("READ_URL", { url });
        } catch (err) {
          if (needsLogin(err)) return Card().showError(err.message);
          throw err;
        }
        if (!item) throw new Error("We could not find a product on that page. Check the link and try again.");
        try {
          await analyzeAndShow(item);
        } catch (err) {
          console.warn(LOG, "analyze failed", err.code, err.message);
          showAnalyzeFailure(err, item);
        }
      },
    });
  }

  async function runCheck() {
    Card().showChecking();
    let item = null;
    try {
      item = await global.SecondLookReader.readItem();
    } catch (err) {
      console.warn(LOG, "readItem failed", err);
    }
    if (!item) {
      showPaste("We could not read this product. Paste its link and we will read it for you.");
      return;
    }
    try {
      await analyzeAndShow(item);
    } catch (err) {
      console.warn(LOG, "analyze failed", err.code, err.message);
      showAnalyzeFailure(err, item);
    }
  }

  global.SecondLookFlow = { runCheck, showPaste };
})(globalThis);
