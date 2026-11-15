// Diagnose: runs readItem() on the current page and reports which tier worked, which fields were
// found, and every add-to-cart and remove control detected. Used for store testing.
(function (global) {
  "use strict";

  async function diagnose() {
    const details = await global.SecondLookReader.readItemWithDetails();
    const item = details.item;
    const report = {
      url: location.href,
      tier_used: item?.source_tier ?? null,
      price_source: item?.price_source ?? null,
      price_check: details.price_check ?? null,
      fields_found: {
        name: Boolean(item?.name),
        image_url: Boolean(item?.image_url),
        price: item?.price != null,
        currency: Boolean(item?.currency),
      },
      item,
      tiers: details.tiers,
      add_to_cart_candidates: global.SecondLookMatchers.candidates(global.SecondLookMatchers.isAddToCart),
      // Why a click on each add button would be checked or passed through to the store.
      add_to_cart_readiness: Array.from(document.querySelectorAll('button, a, [role="button"], input[type="submit"], input[type="button"]'))
        .filter(global.SecondLookMatchers.isAddToCart).slice(0, 5)
        .map((el) => ({ control: global.SecondLookMatchers.describe(el), ...global.SecondLookMatchers.readiness(el) })),
      remove_candidates: global.SecondLookMatchers.candidates(global.SecondLookMatchers.isRemove),
    };
    console.log("[SecondLook] diagnose", report);
    return report;
  }

  global.SecondLookDiagnose = { diagnose };
})(globalThis);
