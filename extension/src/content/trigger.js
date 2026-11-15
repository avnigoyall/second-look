// Trigger: one capture-phase click listener on window catches add-to-cart clicks, including after
// single-page-app navigation. Nothing runs on page load. The popup's "Check this item" button sends
// CHECK_ITEM, which always runs (no debounce) and never holds anything.
//
// Hold until decided: an add-to-cart click is stopped before the store sees it, and the card decides:
//   Buy it / Buy anyway  -> the click is replayed, the store adds the item;
//   Skip it              -> the click is dropped, nothing goes in the bag;
//   card closed          -> replayed (the shopper meant to add it);
//   any failure or HOLD_MAX_MS without a verdict -> replayed. A slow or broken backend never traps a click.
(function (global) {
  "use strict";

  if (global.__secondLookTriggerLoaded) return; // guard against double injection
  global.__secondLookTriggerLoaded = true;

  const LOG = "[SecondLook]";
  const DEBOUNCE_MS = 3000;
  const HOLD_MAX_MS = 15000; // no verdict on screen by then: let the store have the click
  const lastCheck = new Map(); // url -> timestamp
  let activeGate = null;

  // One held add-to-cart click. release() replays it to the store once; cancel() drops it.
  function createGate(control) {
    let state = "held"; // held | released | cancelled
    let timer = null;
    const gate = {
      get held() { return state === "held"; },
      release(reason) {
        if (state !== "held") return false;
        state = "released";
        clearTimeout(timer);
        if (activeGate === gate) activeGate = null;
        console.log(LOG, "add-to-cart released:", reason);
        if (!control.isConnected) {
          console.warn(LOG, "add-to-cart button is gone (page re-rendered); the shopper has to click it again");
          return false;
        }
        control.click(); // untrusted, so this listener lets it through to the store's own handlers
        return true;
      },
      cancel(reason) {
        if (state !== "held") return;
        state = "cancelled";
        clearTimeout(timer);
        if (activeGate === gate) activeGate = null;
        console.log(LOG, "add-to-cart dropped:", reason);
      },
      // The verdict is on screen: the shopper decides now, so the safety timeout no longer applies.
      decided() { clearTimeout(timer); },
    };
    timer = setTimeout(() => gate.release("no verdict in time"), HOLD_MAX_MS);
    return gate;
  }

  window.addEventListener("click", (event) => {
    if (!event.isTrusted) return; // our own replay, or a script click: not ours to hold
    const control = global.SecondLookMatchers.findControl(event, global.SecondLookMatchers.isAddToCart);
    if (!control) return;
    // Recommendation tiles ("you may also like") have their own add buttons for other products.
    const other = global.SecondLookMatchers.otherProductTile(control);
    if (other) {
      console.log(LOG, "add-to-cart click ignored: button is in a tile for another product:", other);
      return;
    }
    if (activeGate?.held) {
      // A second click while the first is held: keep holding, do not add twice.
      event.preventDefault();
      event.stopImmediatePropagation();
      console.log(LOG, "add-to-cart click held (already deciding)");
      return;
    }
    // Only check when the store would really add it: size chosen, in stock. Otherwise the store gets the
    // click and shows its own "select a size" / "sold out" message, and no analysis runs.
    const ready = global.SecondLookMatchers.readiness(control);
    if (!ready.ready) {
      console.log(LOG, "add-to-cart click passed through, not ready to add:", ready.reason);
      return;
    }
    const url = location.href;
    const now = Date.now();
    if (now - (lastCheck.get(url) || 0) < DEBOUNCE_MS) {
      console.log(LOG, "add-to-cart click ignored (debounce)");
      return;
    }
    lastCheck.set(url, now);
    event.preventDefault(); // also stops a submit button from submitting its form
    event.stopImmediatePropagation();
    console.log(LOG, "add-to-cart click held:", global.SecondLookMatchers.describe(control));
    activeGate = createGate(control);
    global.SecondLookFlow.runCheck({ gate: activeGate });
  }, true);

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "CHECK_ITEM") return false;
    global.SecondLookFlow.runCheck();
    sendResponse({ ok: true });
    return false;
  });
})(globalThis);
