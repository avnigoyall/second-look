# Store test results

How to test one product page:

1. Open the product page in Chrome with Second Look loaded and logged in.
2. Open the DevTools Console on the page and type `SecondLook` in the filter box.
3. Open the Second Look popup and click **Diagnose this page**. Click **Copy JSON** and keep it for the notes column if something looks wrong.
4. Choose any size, then click the store's add-to-cart button. Check that the card shows "Second Look is checking..." and then a verdict with the right name, image and price.
5. Add a second product, open the cart, and remove one item. If the store asks "Are you sure?", the item must stay in the list until you confirm. Check that a "Removed from your Second Look list" notice appears after you confirm and the popup's Session items list shows it as Removed.

Columns:

- **Tier used:** 1 (JSON-LD), 2 (meta tags), 3 (Gemini), or "paste" if the card fell back to paste-a-link. Diagnose shows this as `tier_used`.
- **Name / image / price found:** yes or no, from Diagnose `fields_found`. Note `price_source` (jsonld, meta, dom, gemini) in the notes.
- **Add-to-cart detected:** yes if clicking the store's button opened the card. Diagnose `add_to_cart_candidates` lists the buttons it matched.
- **Remove detected:** yes if removing the item from the cart marked it Removed, and only after any confirm dialog.

| Store | Product URL | Tier used | Name found | Image found | Price found | Add-to-cart detected | Remove detected | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Zara | | | | | | | | Scan: no JSON-LD Product, no meta price; button text "ADD" with `data-qa-action="add-to-cart"`. Fixed in patch 1 (DOM price, data attribute match). Retest. |
| Zara | | | | | | | | |
| Uniqlo | | | | | | | | Scan: no JSON-LD Product, no meta price (patch 1, DOM price). Cart "Remove" opens "Are you sure you want to remove this item?" and the item was marked removed on the first click. Fixed in patch 2 (waits for confirm). Retest. |
| Uniqlo | | | | | | | | |
| H&M | https://www2.hm.com/en_us/productpage.1101074044.html | | | | | | | Scan: JSON-LD ProductGroup with about 81 variants; reader took the first (White, $7.49) instead of Navy blue, $19.99. Fixed in patch 2 (variant by product code, then color; displayed price check). Retest. |
| H&M | | | | | | | | |
| Hollister | | | | | | | | Scan: JSON-LD Product with name and image, no price (patch 1, DOM price). Can render in Spanish ("Añadir a la bolsa"), matched since patch 1. Retest. |
| Hollister | | | | | | | | |
| Pacsun | | | | | | | | Scan: no JSON-LD Product, no meta price (patch 1, DOM price). About 5 "Add to Bag" buttons, including recommendation tiles; tiles for other products are ignored since patch 1. Retest. |
| Pacsun | | | | | | | | |

## Retest after patch 2 (H&M and Uniqlo)

Reload first: open `chrome://extensions`, click the reload icon on the Second Look card, then reload every store tab. The backend must be running on port 8000.

**H&M variant and price**

1. Open https://www2.hm.com/en_us/productpage.1101074044.html (Navy blue).
2. Click **Diagnose this page**. Expect:
   - `tiers.jsonld.variant_match` is `"code"` and `tiers.jsonld.variant_color` is the color shown on the page (navy blue).
   - `item.image_url` is the navy image, not the white one.
   - `price_check` shows the JSON-LD price, the displayed price, and `winner`. `item.price` is the regular price shown on the page ($19.99), not a member price.
3. Console: look for `[SecondLook] price check: jsonld ... vs displayed ... -> using ...`.
4. Choose a size and click **Add to bag**. The card shows Navy blue's image and $19.99.
5. Repeat on a second color of the same product (change the color on the page, then Diagnose again). The variant and price should follow the page.

**Uniqlo remove with confirm**

1. Add two different products to the bag (each one shows a verdict card).
2. Open the cart. Click **Remove** on one item. The "Are you sure?" dialog opens. Console: `[SecondLook] remove click matched ... waiting for the store to remove it`. The popup still shows the item as Checked.
3. Click **Cancel**. Wait 10 seconds. The item is still Checked. Console: `removal cancelled in dialog`.
4. Click **Remove** again, then confirm in the dialog. A "Removed from your Second Look list" notice appears and the popup shows that item as Removed. Console: `removed from session: ... (confirmed in dialog: "...")`.
5. The other item stays Checked.

If a check fails, paste the Diagnose JSON and every `[SecondLook]` console line into the notes column.
