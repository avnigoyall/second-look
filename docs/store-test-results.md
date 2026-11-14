# Store test results

How to test one product page:

1. Open the product page in Chrome with Second Look loaded and logged in.
2. Open the Second Look popup and click **Diagnose this page**. Click **Copy JSON** and keep it for the notes column if something looks wrong.
3. Choose any size, then click the store's add-to-cart button. Check that the card shows "Second Look is checking..." and then a verdict.
4. Add a second product, open the cart, and remove one item. Check that a "Removed from your Second Look list" notice appears and the popup's Session items list shows it as Removed.

Columns:

- **Tier used:** 1 (JSON-LD), 2 (meta tags), 3 (Gemini), or "paste" if the card fell back to paste-a-link. Diagnose shows this.
- **Name / image / price found:** yes or no, from Diagnose `fields_found`.
- **Add-to-cart detected:** yes if clicking the store's button opened the card. Diagnose `add_to_cart_candidates` lists the buttons it matched.
- **Remove detected:** yes if removing the item from the cart marked it Removed.

| Store | Product URL | Tier used | Name found | Image found | Price found | Add-to-cart detected | Remove detected | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Zara | | | | | | | | |
| Zara | | | | | | | | |
| Uniqlo | | | | | | | | |
| Uniqlo | | | | | | | | |
| H&M | | | | | | | | |
| H&M | | | | | | | | |
| Hollister | | | | | | | | |
| Hollister | | | | | | | | |
| Pacsun | | | | | | | | |
| Pacsun | | | | | | | | |
