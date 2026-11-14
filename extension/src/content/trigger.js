// Trigger: one capture-phase click listener on the document catches add-to-cart clicks, including
// after single-page-app navigation. Nothing runs on page load. The popup's "Check this item"
// button sends CHECK_ITEM, which always runs (no debounce).
(function (global) {
  "use strict";

  if (global.__secondLookTriggerLoaded) return; // guard against double injection
  global.__secondLookTriggerLoaded = true;

  const LOG = "[SecondLook]";
  const DEBOUNCE_MS = 3000;
  const lastCheck = new Map(); // url -> timestamp

  document.addEventListener("click", (event) => {
    if (!event.isTrusted) return;
    const control = global.SecondLookMatchers.findControl(event, global.SecondLookMatchers.isAddToCart);
    if (!control) return;
    const url = location.href;
    const now = Date.now();
    if (now - (lastCheck.get(url) || 0) < DEBOUNCE_MS) {
      console.log(LOG, "add-to-cart click ignored (debounce)");
      return;
    }
    lastCheck.set(url, now);
    console.log(LOG, "add-to-cart click:", global.SecondLookMatchers.describe(control));
    global.SecondLookFlow.runCheck(); // never blocks the store's own handler
  }, true);

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "CHECK_ITEM") return false;
    global.SecondLookFlow.runCheck();
    sendResponse({ ok: true });
    return false;
  });
})(globalThis);
