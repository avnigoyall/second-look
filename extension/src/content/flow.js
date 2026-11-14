// The check flow: read the item, ask for a price if missing, call /analyze through the service
// worker, and show the verdict. Any read failure or backend failure ends in the paste-a-link state.
(function (global) {
  "use strict";

  const LOG = "[SecondLook]";
  const Card = () => global.SecondLookCard;

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

  // Not logged in or server down: pasting a link would not help, so show the error state.
  function isBlocking(err) {
    return err.code === "auth" || err.code === "offline";
  }

  function showBlocking(err) {
    Card().showError(err.message);
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
    console.log(LOG, "verdict", result.verdict, result);
    Card().showVerdict(item, result, {
      onAction: (action) => send("SEND_ACTION", { verdictId: result.verdict_id, action }),
      onFeedback: (value) => send("SEND_FEEDBACK", { verdictId: result.verdict_id, value }),
    });
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
          if (isBlocking(err)) return showBlocking(err);
          throw err;
        }
        if (!item) throw new Error("We could not find a product on that page. Check the link and try again.");
        try {
          await analyzeAndShow(item);
        } catch (err) {
          if (isBlocking(err)) return showBlocking(err);
          throw err;
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
      console.warn(LOG, "analyze failed", err);
      if (isBlocking(err)) showBlocking(err);
      else showPaste(`${err.message} You can paste the product link to try again.`);
    }
  }

  global.SecondLookFlow = { runCheck, showPaste };
})(globalThis);
