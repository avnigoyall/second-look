// The check flow: read the item, ask for a price if missing, call /analyze through the service
// worker, and show the verdict. A read failure ends in the paste-a-link state. A backend failure
// (down, slow, or a server error) ends in the calm "Second look unavailable" state.
//
// Held add-to-cart (trigger.js passes a gate): the store gets the click only on Buy. Skip drops it.
// Every failure path, closing the card, and the gate's own 15 s timeout release it, so a slow or broken
// backend never keeps an item out of the bag (CONTRACT section 8, amended for the hold).
(function (global) {
  "use strict";

  const LOG = "[SecondLook]";
  const Card = () => global.SecondLookCard;
  const ATTR_FIELDS = ["category", "color_family", "fabric", "fabric_confidence", "cut", "style"];

  // Timing marks for one check, logged as one line when the verdict (or a failure) is shown:
  // [SecondLook] timing click=0 read=… analyze_sent=… analyze_done=… shown=… (ms since the click).
  function startTimer() {
    const t0 = performance.now();
    const marks = { click: 0 };
    const extra = {};
    return {
      mark: (name) => { marks[name] = Math.round(performance.now() - t0); },
      note: (name, value) => { if (value != null) extra[name] = value; },
      log: (outcome) => {
        const parts = [...Object.entries(marks), ...Object.entries(extra)].map(([k, v]) => `${k}=${v}`);
        console.log(LOG, "timing", outcome, parts.join(" "));
      },
    };
  }

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

  let currentGate = null; // the held add-to-cart click for the card on screen, if any

  // Release the held click (store adds the item). Safe to call when nothing is held.
  function letThrough(reason) {
    currentGate?.release(reason);
  }

  // Wraps the card's Buy / Skip so the held click follows the choice. bag: added | not_added | missing.
  function gatedAction(verdictId, gate) {
    return async (action) => {
      let bag;
      if (gate) {
        if (action === "bought") bag = gate.held ? (gate.release("Buy") ? "added" : "missing") : undefined;
        else if (gate.held) { gate.cancel("Skip"); bag = "not_added"; }
      }
      try {
        const res = await send("SEND_ACTION", { verdictId, action });
        return { ...res, bag };
      } catch (err) {
        err.bag = bag;
        throw err;
      }
    };
  }

  function showAnalyzeFailure(err, item, timer) {
    letThrough("check failed"); // never keep the item out of the bag because we failed
    if (needsLogin(err)) Card().showError(err.message);
    else Card().showUnavailable({ onRetry: () => analyzeAndShow(item).catch((e) => showAnalyzeFailure(e, item)) });
    if (timer) {
      timer.mark("shown");
      timer.log(`failed:${err.code || "error"}`);
    }
  }

  async function analyzeAndShow(item, timer = startTimer()) {
    if (item.price == null) {
      timer.mark("price_asked");
      const price = await Card().askPrice(item);
      if (price === undefined) return; // card closed (the close listener released the held click)
      timer.mark("price_entered");
      item = { ...item, price };
    }
    const gate = currentGate?.held ? currentGate : null;
    Card().showChecking(item, { held: Boolean(gate) });
    const payload = {
      name: item.name, image_url: item.image_url, price: item.price, store: item.store, url: item.url,
      description: item.description || null, breadcrumb: item.breadcrumb || null,
      materials: item.materials || null,
    };
    timer.mark("analyze_sent");
    const result = await send("ANALYZE_ITEM", { item: payload });
    timer.mark("analyze_done");
    timer.note("server_ms", result.client_timing?.server_ms);
    timer.note("stages", result.client_timing?.stages);
    console.log(LOG, "verdict", result.verdict, result.decided_by || "", result);
    gate?.decided(); // the shopper decides now; the safety timeout stops
    const view = Card().showVerdict(item, result, {
      onAction: gatedAction(result.verdict_id, gate),
    });
    timer.mark("shown");
    timer.log(result.verdict);
    if (wantsOwnershipQuestion(result)) askOwnership(item, result, view);
    if (result.verdict_id && result.verdict_id !== "unsaved") loadDetails(result, view);
  }

  // Full analysis: a second request after the verdict is on screen. Never shows an error: on failure the
  // loading line is simply removed.
  async function loadDetails(result, view) {
    view.showDetailsLoading();
    try {
      const details = await send("ANALYZE_DETAILS", { verdictId: result.verdict_id });
      if (view.isCurrent()) view.addDetails(details);
    } catch (err) {
      console.log(LOG, "full analysis skipped:", err.message);
      view.removeDetails();
    }
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
    letThrough("product not readable");
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

  async function runCheck({ gate = null } = {}) {
    const timer = startTimer();
    if (currentGate && currentGate !== gate) letThrough("a new check started");
    currentGate = gate;
    if (gate) {
      const off = Card().onHide(() => { off(); gate.release("card closed"); });
    }
    Card().showChecking(null, { held: Boolean(gate) });
    timer.mark("checking_shown");
    let item = null;
    try {
      item = await global.SecondLookReader.readItem(); // logs its own tier times
    } catch (err) {
      console.warn(LOG, "readItem failed", err);
    }
    timer.mark("read");
    if (!item) {
      showPaste("We could not read this product. Paste its link and we will read it for you.");
      return;
    }
    try {
      await analyzeAndShow(item, timer);
    } catch (err) {
      console.warn(LOG, "analyze failed", err.code, err.message);
      showAnalyzeFailure(err, item, timer);
    }
  }

  global.SecondLookFlow = { runCheck, showPaste };
})(globalThis);
