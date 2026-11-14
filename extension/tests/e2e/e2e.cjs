// End-to-end test: loads the real unpacked extension into Chromium and walks the Part 1 acceptance list.
// Needs: backend on :8000 (with GEMINI_API_KEY), fixtures served on :8765. See README "Extension E2E test".
// Env: PW_PATH = path to a playwright package, CHROME_PATH = optional Chromium executable.
const path = require("path");
const fs = require("fs");
const { chromium } = require(process.env.PW_PATH || "playwright");

const EXT = process.env.EXT_PATH || path.resolve(__dirname, "../..");
const OUT = path.join(__dirname, "shots");
const FIX = "http://127.0.0.1:8765";
const API = process.env.API_BASE || "http://localhost:8000";
fs.mkdirSync(OUT, { recursive: true });

const results = [];
const pass = (name, detail = "") => { results.push(["PASS", name, detail]); console.log("PASS", name, detail); };
const fail = (name, detail = "") => { results.push(["FAIL", name, detail]); console.log("FAIL", name, detail); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Records every card state the page shows (states can be very short).
const RECORDER = `(() => {
  window.__states = [];
  const record = (card) => {
    const label = card.getAttribute("aria-label");
    if (label && window.__states[window.__states.length - 1] !== label) window.__states.push(label);
  };
  const watchRoot = (sr) => new MutationObserver(() => { const c = sr.querySelector(".card"); if (c) record(c); })
    .observe(sr, { subtree: true, childList: true, attributes: true, attributeFilter: ["aria-label"] });
  const host = document.getElementById("secondlook-shadow-host");
  if (host) watchRoot(host.shadowRoot);
  new MutationObserver((muts) => {
    for (const m of muts) for (const n of m.addedNodes) if (n.id === "secondlook-shadow-host") watchRoot(n.shadowRoot);
  }).observe(document.documentElement, { childList: true });
})()`;

(async () => {
  const email = `e2e${Date.now()}@example.com`;
  const signup = await fetch(`${API}/auth/signup`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password: "secret123" }) });
  if (!signup.ok) throw new Error("signup failed " + signup.status);

  const ctx = await chromium.launchPersistentContext("", {
    channel: "chromium",
    executablePath: process.env.CHROME_PATH || undefined,
    headless: true,
    viewport: { width: 1280, height: 800 },
    args: [`--disable-extensions-except=${EXT}`, `--load-extension=${EXT}`],
  });
  const errors = [];
  let [sw] = ctx.serviceWorkers();
  if (!sw) sw = await ctx.waitForEvent("serviceworker");
  sw.on("console", (m) => { if (m.type() === "error") errors.push("sw: " + m.text()); if (process.env.DEBUG) console.log("  SW>", m.type(), m.text().slice(0, 200)); });
  const extId = sw.url().split("/")[2];
  if (process.env.DEBUG) await sw.evaluate(() => {
    chrome.tabs.onUpdated.addListener((id, info, tab) => { if (info.status) console.log("onUpdated", id, info.status, tab.url, "active=" + tab.active); });
    chrome.tabs.onCreated.addListener((tab) => console.log("onCreated", tab.id, tab.pendingUrl || tab.url));
  });
  const storage = () => sw.evaluate(() => chrome.storage.local.get("secondLookSession").then((r) => r.secondLookSession || []));

  // ---- login through the real popup page ----
  const popup = await ctx.newPage();
  popup.on("pageerror", (e) => errors.push("popup: " + e.message));
  await popup.goto(`chrome-extension://${extId}/popup.html`);
  await popup.fill("#email", email);
  await popup.fill("#password", "secret123");
  await popup.click('#login-view button[type="submit"]');
  await popup.waitForSelector("#who:has-text('Logged in as')");
  pass("popup login", await popup.textContent("#who"));
  await popup.screenshot({ path: `${OUT}/popup-loggedin.png` });

  async function openStore(file) {
    const page = await ctx.newPage();
    page.on("pageerror", (e) => errors.push(`${file}: ${e.message}`));
    page.on("console", (m) => { if (m.type() === "error" && !/favicon|Failed to load resource/.test(m.text())) errors.push(`${file}: ${m.text()}`); });
    page.slLogs = [];
    page.on("console", (m) => { if (m.text().includes("[SecondLook]")) page.slLogs.push(m.text()); });
    await page.goto(`${FIX}/${file}`);
    await page.evaluate(RECORDER);
    return page;
  }
  const card = (page) => page.locator("#secondlook-shadow-host .card");

  // ---- 1. JSON-LD page: add-to-cart shows checking, then the verdict ----
  {
    const page = await openStore("product-jsonld.html");
    await page.click("#add");
    await card(page).locator(".badge").waitFor({ timeout: 15000 });
    const states = await page.evaluate(() => window.__states);
    const name = await card(page).locator(".item-name").textContent();
    const meta = await card(page).locator(".item-meta").textContent();
    const sawChecking = states.some((s) => s.includes("checking"));
    (sawChecking ? pass : fail)("1. JSON-LD add-to-cart: checking state shown", JSON.stringify(states));
    (name === "Wide Leg Barrel Jeans" && meta.includes("$49.90") ? pass : fail)("1. JSON-LD item read", `${name} | ${meta}`);
    (await page.title() === "added" ? pass : fail)("1. store's own add-to-cart handler still ran");
    await sleep(400); await page.screenshot({ path: `${OUT}/1-verdict.png` });
    // debounce: a second click within 3s does nothing new
    const before = (await storage()).length;
    await page.click("#add");
    await sleep(800);
    ((await storage()).length === before ? pass : fail)("1. 3s debounce per URL");
    await card(page).getByRole("button", { name: "Skip it" }).click();
    await card(page).locator(".status", { hasText: "Skipped" }).waitFor();
    const entry = (await storage()).find((e) => e.name === "Wide Leg Barrel Jeans");
    (entry?.status === "skipped" ? pass : fail)("1. Skip it updates session status", entry?.status);
    await card(page).getByRole("button", { name: "Thumbs up" }).click();
    await card(page).locator(".status", { hasText: "Thanks" }).waitFor();
    pass("1. thumbs up saved");
  }

  // ---- 2a. meta-only page (tier 2), icon button with aria-label ----
  {
    const page = await openStore("product-meta.html");
    await page.click('button[aria-label="Add to shopping cart"]');
    await card(page).locator(".badge").waitFor({ timeout: 15000 });
    const name = await card(page).locator(".item-name").textContent();
    const meta = await card(page).locator(".item-meta").textContent();
    (name === "Ribbed Cropped Cardigan" && meta.includes("34.90") ? pass : fail)("2a. tier 2 meta read (store suffix stripped, EU price)", `${name} | ${meta}`);
    // The stub cycles verdicts per user, so this second check is micro_trend: alternatives show, each one a link.
    const badge = await card(page).locator(".badge").textContent();
    const alts = await card(page).locator("a.alt-link").evaluateAll((as) => as.map((a) => ({
      href: a.getAttribute("href"), target: a.target, rel: a.rel, kind: a.dataset.linkKind, text: a.innerText.trim() })));
    (badge === "Micro trend" && alts.length >= 1 && alts.length <= 2 ? pass : fail)("2a. micro_trend card shows 1 or 2 alternatives", `${badge} | ${alts.length}`);
    const linkOk = (a) => /^https?:\/\//.test(a.href) && a.target === "_blank" && /noopener/.test(a.rel) && /noreferrer/.test(a.rel) && a.text.length > 0;
    (alts.length && alts.every(linkOk) ? pass : fail)("2a. every alternative is an http(s) link with target _blank and rel noopener noreferrer", JSON.stringify(alts));
    (alts.every((a) => a.kind !== "search" || /^Search \S/.test(a.text)) ? pass : fail)("2a. search fallback links read 'Search <store> for <type>'", alts.map((a) => `${a.kind}: ${a.text.replace(/\n/g, " | ")}`).join(" ; "));
    await page.screenshot({ path: `${OUT}/2a-meta-verdict.png` });
  }

  // ---- 2b. no product tags: tier 3 (Gemini) fills the item ----
  {
    const page = await openStore("product-notags.html");
    await page.click("text=ADD TO BASKET");
    await card(page).locator(".badge").waitFor({ timeout: 20000 });
    const name = await card(page).locator(".item-name").textContent();
    const meta = await card(page).locator(".item-meta").textContent();
    (/linen shirt/i.test(name) && meta.includes("39.90") ? pass : fail)("2b. tier 3 Gemini read", `${name} | ${meta}`);
    await page.screenshot({ path: `${OUT}/2b-tier3-verdict.png` });
  }

  // ---- 2d. name and image but no price anywhere: the card asks for the price ----
  {
    const page = await openStore("product-noprice.html");
    await page.click("text=Add to cart");
    await card(page).locator('input[aria-label="Price"]').waitFor({ timeout: 20000 });
    await sleep(400);
    await page.screenshot({ path: `${OUT}/2d-price-prompt.png` });
    await card(page).locator('input[aria-label="Price"]').fill("abc");
    await card(page).getByRole("button", { name: "Check", exact: true }).click();
    const err = await card(page).locator(".form-error").textContent();
    (/number/.test(err) ? pass : fail)("2d. invalid price shows an error", err);
    await card(page).locator('input[aria-label="Price"]').fill("$79.00");
    await card(page).getByRole("button", { name: "Check", exact: true }).click();
    await card(page).locator(".badge").waitFor({ timeout: 15000 });
    const meta = await card(page).locator(".item-meta").textContent();
    const entry = (await storage()).find((e) => e.name === "Pleated Wool Trousers");
    (meta.includes("$79.00") && entry?.price === 79 ? pass : fail)("2d. entered price is used", `${meta} | session price ${entry?.price}`);
  }

  // ---- 2c + 3. not a product: paste-a-link, then paste reads through a background tab ----
  {
    const page = await openStore("not-a-product.html");
    await page.click("text=Add to cart");
    await card(page).locator("h2", { hasText: "Paste the product link" }).waitFor({ timeout: 20000 });
    pass("2c. no product found shows paste-a-link");
    await sleep(400); await page.screenshot({ path: `${OUT}/2c-paste.png` });

    // invalid input shows an inline error
    await card(page).locator("input").fill("not a link");
    await card(page).getByRole("button", { name: "Check" }).click();
    const err = await card(page).locator(".form-error").textContent();
    (/https/.test(err) ? pass : fail)("3. paste validation error shown", err);
    await page.screenshot({ path: `${OUT}/3-paste-error.png` });

    const tabsBefore = ctx.pages().length;
    await card(page).locator("input").fill(`${FIX}/product-jsonld.html`);
    await card(page).getByRole("button", { name: "Check" }).click();
    await card(page).locator(".spinner").first().waitFor({ timeout: 2000 }).then(() => page.screenshot({ path: `${OUT}/3-paste-loading.png` })).catch(() => {});
    try { await card(page).locator(".badge").waitFor({ timeout: 30000 }); }
    catch (e) { console.log("CARD TEXT:", await card(page).innerText()); await page.screenshot({ path: `${OUT}/3-stuck.png` }); throw e; }
    const name = await card(page).locator(".item-name").textContent();
    (name === "Wide Leg Barrel Jeans" ? pass : fail)("3. pasted URL read in background tab, verdict shown", name);
    await sleep(500);
    (ctx.pages().length === tabsBefore ? pass : fail)("3. background tab closed", `${ctx.pages().length} pages`);
    await page.screenshot({ path: `${OUT}/3-paste-verdict.png` });
  }

  // ---- 4. removal: remove one of two items from a cart page ----
  {
    let entries = await storage();
    const active = entries.filter((e) => e.status !== "removed").map((e) => e.name);
    console.log("session before removal:", active);
    const page = await openStore("cart.html");
    await page.click("#r2 .rm"); // "Delete" on Ribbed Cropped Cardigan
    await page.locator("#secondlook-shadow-host .notice").waitFor({ timeout: 3000 });
    const notice = await page.locator("#secondlook-shadow-host .notice").textContent();
    await sleep(400); await page.screenshot({ path: `${OUT}/4-removed-notice.png` });
    entries = await storage();
    const cardigan = entries.find((e) => e.name === "Ribbed Cropped Cardigan");
    const jeans = entries.filter((e) => e.name === "Wide Leg Barrel Jeans");
    (cardigan?.status === "removed" ? pass : fail)("4. matching item marked removed", notice);
    (jeans.every((e) => e.status !== "removed") ? pass : fail)("4. other items untouched");
    // a remove control with no matching item does nothing
    await page.evaluate(() => { document.body.insertAdjacentHTML("beforeend", '<div class="row"><span>Gift card</span><button id="rx">Remove</button></div>'); });
    const snapshot = JSON.stringify(await storage());
    await page.click("#rx");
    await sleep(500);
    (JSON.stringify(await storage()) === snapshot ? pass : fail)("4. unmatched remove does nothing");

    await popup.reload();
    await popup.waitForSelector("#session-list li");
    const listText = await popup.locator("#session-list").innerText();
    (/Ribbed Cropped Cardigan[\s\S]*Removed/.test(listText) ? pass : fail)("4. popup list shows Removed");
    // manual fallback: x button marks an item removed
    const firstX = popup.locator("#session-list li .x").first();
    const label = await firstX.getAttribute("aria-label");
    await firstX.click();
    await sleep(300);
    const removedCount = (await storage()).filter((e) => e.status === "removed").length;
    (removedCount >= 2 ? pass : fail)("4. popup x marks removed", label);
    await popup.setViewportSize({ width: 340, height: 600 });
    await popup.screenshot({ path: `${OUT}/4-popup-list.png`, fullPage: true });
  }

  // ---- 5. Diagnose: run from the popup against a store tab, copy the JSON ----
  {
    const store = await openStore("product-notags.html");
    await store.bringToFront();
    const report = await sw.evaluate(() => diagnose());
    const ok = report && report.tier_used === 3 && report.add_to_cart_candidates.includes("ADD TO BASKET");
    (ok ? pass : fail)("5. diagnose report", JSON.stringify({ tier: report?.tier_used, fields: report?.fields_found, add: report?.add_to_cart_candidates }));

    // Popup UI: the real popup is not a tab, so in this test the popup page would be the active tab.
    // Feed the popup the real report from the service worker, then press its buttons.
    const pop2 = popup;
    await pop2.evaluate((rep) => {
      const real = chrome.runtime.sendMessage.bind(chrome.runtime);
      chrome.runtime.sendMessage = (msg) => msg?.type === "DIAGNOSE" ? Promise.resolve({ ok: true, data: rep }) : real(msg);
    }, report);
    await pop2.click("#diagnose");
    await pop2.waitForSelector("#diag-output:not(:empty)", { timeout: 20000 });
    const text = await pop2.textContent("#diag-output");
    let parsed = null;
    try { parsed = JSON.parse(text); } catch {}
    (parsed ? pass : fail)("5. popup shows diagnose JSON", parsed ? await pop2.textContent("#diag-summary") : text?.slice(0, 80));
    await ctx.grantPermissions(["clipboard-read", "clipboard-write"], { origin: `chrome-extension://${extId}` }).catch(() => {});
    await pop2.evaluate(() => {
      const real = navigator.clipboard.writeText.bind(navigator.clipboard);
      navigator.clipboard.writeText = (t) => { window.__copied = t; return real(t); };
    });
    await pop2.click("#copy");
    await sleep(200);
    const copyLabel = await pop2.textContent("#copy");
    const clip = await pop2.evaluate(() => window.__copied);
    (clip === text ? pass : fail)("5. Copy JSON copies the block", `button: ${copyLabel} | clipboard: ${String(clip).slice(0, 60).replace(/\n/g, " ")} | len ${String(clip).length} vs ${text.length}`);
    await pop2.screenshot({ path: `${OUT}/5-diagnose.png`, fullPage: true });
  }

  // ---- 6. store-scan fixes ----
  // Clicks a control and confirms Second Look did nothing (no card, no new session entry).
  async function expectNoTrigger(page, selector, name) {
    const before = (await storage()).length;
    await page.evaluate(() => { window.__states = []; });
    await page.click(selector);
    await sleep(1500);
    const states = await page.evaluate(() => window.__states);
    (states.length === 0 && (await storage()).length === before ? pass : fail)(name, JSON.stringify(states));
  }
  async function diagnoseOn(page) {
    await page.bringToFront();
    return sw.evaluate(() => diagnose());
  }

  // 6a. Zara-style: bare "ADD" with data-qa-action, DOM sale price, wishlist and promo "ADD" ignored.
  {
    const page = await openStore("zara-style.html");
    const rep = await diagnoseOn(page);
    const adds = rep.add_to_cart_candidates;
    (adds.length === 1 && adds[0] === "ADD" ? pass : fail)("6a. Zara-style: only the data-qa-action ADD button detected", JSON.stringify(adds));
    (rep.price_source === "dom" && rep.item?.price === 39.9 && rep.tiers.gemini?.used === false ? pass : fail)(
      "6a. Zara-style: DOM sale price used, Gemini not called", `price_source=${rep.price_source} price=${rep.item?.price} dom=${JSON.stringify(rep.tiers.dom)}`);
    (rep.item?.name === "LINEN BLEND BLAZER" ? pass : fail)("6a. Zara-style: name from og:title, store suffix stripped", rep.item?.name);
    await expectNoTrigger(page, "#wish", "6a. wishlist button (data-testid add-to-bag-wishlist) does not trigger");
    await expectNoTrigger(page, "#promo", "6a. bare ADD without a data attribute does not trigger");
    await page.click('button[data-qa-action="add-to-cart"]');
    await card(page).locator(".badge").waitFor({ timeout: 15000 });
    const meta = await card(page).locator(".item-meta").textContent();
    (meta.includes("$39.90") ? pass : fail)("6a. Zara-style ADD click shows verdict with DOM price", meta);
    const logs = page.slLogs.join(" | ");
    (/price source: dom/.test(logs) ? pass : fail)("6a. console logs price source", logs.match(/price source: \w+[^|]*/)?.[0]);
    await sleep(400); await page.screenshot({ path: `${OUT}/6a-zara-style.png` });
  }

  // 6b. Spanish (Hollister-style): JSON-LD without price, data-price in the DOM, Spanish buttons.
  {
    const page = await openStore("spanish.html");
    await expectNoTrigger(page, "#wish-es", "6b. Spanish wishlist button (lista) does not trigger");
    await page.click("#add-es");
    await card(page).locator(".badge").waitFor({ timeout: 15000 });
    const name = await card(page).locator(".item-name").textContent();
    const meta = await card(page).locator(".item-meta").textContent();
    (name === "Camisa de lino de manga corta" && meta.includes("$49.95") ? pass : fail)("6b. 'Añadir a la bolsa' triggers, price from data-price", `${name} | ${meta}`);
    const rep = await diagnoseOn(page);
    (rep.tier_used === 1 && rep.price_source === "dom" ? pass : fail)("6b. Diagnose: tier 1 name, price_source dom", `tier ${rep.tier_used}, ${rep.price_source}, ${JSON.stringify(rep.tiers.dom)}`);
  }

  // 6c. Pacsun-style: recommendation tiles with their own Add to Bag buttons.
  {
    const page = await openStore("pacsun-style.html");
    const rep = await diagnoseOn(page);
    const ignored = rep.add_to_cart_candidates.filter((c) => c.includes("ignored")).length;
    (rep.add_to_cart_candidates.length === 4 && ignored === 2 ? pass : fail)("6c. Diagnose marks the 2 other-product tiles as ignored", JSON.stringify(rep.add_to_cart_candidates));
    (rep.item?.price === 24.95 && rep.price_source === "dom" ? pass : fail)("6c. DOM price range takes the lower value", `${rep.item?.price} via ${JSON.stringify(rep.tiers.dom)}`);
    await expectNoTrigger(page, "#add-tile-1", "6c. Add to Bag in a tile for another product does not trigger");
    (page.slLogs.some((l) => /tile for another product/.test(l)) ? pass : fail)("6c. ignored tile click is logged");
    await page.click("#add-main");
    await card(page).locator(".badge").waitFor({ timeout: 15000 });
    const name = await card(page).locator(".item-name").textContent();
    (name === "Sunset Graphic Tee" ? pass : fail)("6c. main Add to Bag triggers for the page's product", name);
    await sleep(400); await page.screenshot({ path: `${OUT}/6c-pacsun-style.png` });
  }
  // 6d. A tile that links to the current page still triggers (fresh page, so no debounce).
  {
    const page = await openStore("pacsun-style.html");
    await page.click("#add-tile-self");
    const shown = await card(page).locator(".badge").waitFor({ timeout: 15000 }).then(() => true, () => false);
    (shown ? pass : fail)("6d. Add to Bag in a tile that links to the current page triggers");
  }

  // ---- 7. Bug A: ProductGroup variant choice and displayed price ----
  // 7a. Product code in the URL picks the Navy blue variant; displayed regular price beats a
  //     stale JSON-LD price; the "Member price" is skipped.
  {
    const page = await openStore("productpage.1101074044.html");
    const rep = await diagnoseOn(page);
    const j = rep.tiers.jsonld;
    (j.variant_match === "code" && j.variant_color === "navy blue" && j.price === 24.99 ? pass : fail)(
      "7a. variant chosen by product code in URL (Navy blue, not first variant White)", `match=${j.variant_match} color=${j.variant_color} jsonld price=${j.price}`);
    (rep.item?.image_url?.endsWith("/img/navy.jpg") ? pass : fail)("7a. matched variant's image used", rep.item?.image_url);
    (rep.item?.name === "Regular Fit T-shirt" ? pass : fail)("7a. group name used", rep.item?.name);
    const pc = rep.price_check;
    (rep.item?.price === 19.99 && pc?.structured === 24.99 && pc?.displayed === 19.99 && pc?.winner === "displayed" ? pass : fail)(
      "7a. displayed regular price $19.99 wins over JSON-LD $24.99, member $15.99 skipped", JSON.stringify(pc));
    const logs = page.slLogs.join(" | ");
    (/price check: jsonld 24\.99 vs displayed 19\.99 -> using displayed/.test(logs) ? pass : fail)("7a. both prices and the winner are logged", logs.match(/price check:[^|]*/)?.[0]);
    await page.click("text=Add to bag");
    await card(page).locator(".badge").waitFor({ timeout: 15000 });
    const meta = await card(page).locator(".item-meta").textContent();
    const img = await card(page).locator(".thumb-img").getAttribute("src");
    (meta.includes("$19.99") && img.endsWith("/img/navy.jpg") ? pass : fail)("7a. card shows Navy image and $19.99", `${meta} | ${img}`);
    await sleep(400); await page.screenshot({ path: `${OUT}/7a-hm-variant.png` });
  }
  // 7b. No code in the URL: color named in og:title picks "Navy blue" over "Blue"; no variant
  //     image, so og:image is used; JSON-LD price agrees with the page.
  {
    const page = await openStore("hm-color.html");
    const rep = await diagnoseOn(page);
    const j = rep.tiers.jsonld;
    (j.variant_match === "color" && j.variant_color === "navy blue" ? pass : fail)("7b. variant chosen by color (Navy blue beats Blue)", `${j.variant_match} ${j.variant_color}`);
    (rep.item?.image_url?.endsWith("/img/navy.jpg") && rep.item?.price === 19.99 && rep.price_check?.winner === "jsonld" ? pass : fail)(
      "7b. og:image fallback, JSON-LD price kept when it matches the page", `${rep.item?.image_url} ${rep.item?.price} ${JSON.stringify(rep.price_check)}`);
  }
  // 7c. Nothing matches: group name, price null from JSON-LD, so the DOM price tier runs.
  {
    const page = await openStore("hm-nomatch.html");
    const rep = await diagnoseOn(page);
    (rep.tiers.jsonld.variant_match === "none" && rep.tiers.jsonld.price === null && rep.item?.name === "Basic Tee" && rep.item?.price === 12.99 && rep.price_source === "dom" ? pass : fail)(
      "7c. no variant match: group name, DOM price", `${rep.tiers.jsonld.variant_match} ${rep.item?.name} ${rep.item?.price} ${rep.price_source}`);
  }

  // ---- 8. Bug B: removal waits for the store's confirm dialog ----
  {
    const entries = await storage();
    const shirt = () => storage().then((all) => all.find((e) => e.name === "Relaxed Linen Shirt"));
    const trousers = () => storage().then((all) => all.find((e) => e.name === "Pleated Wool Trousers"));
    if ((await shirt())?.status === "removed" || (await trousers())?.status === "removed") fail("8. setup: items already removed");
    const page = await openStore("cart-confirm.html");
    const notice = page.locator("#secondlook-shadow-host .notice");

    // 8a. Remove opens "Are you sure?"; nothing is marked yet. Cancel: still nothing after the watch window.
    await page.click("#r-shirt .remove");
    await page.locator("#overlay.open").waitFor();
    await sleep(1000);
    ((await shirt()).status !== "removed" ? pass : fail)("8a. first Remove click (dialog open) does not mark removed", (await shirt()).status);
    await page.click("#confirm-cancel");
    await sleep(7000);
    ((await shirt()).status !== "removed" && (await notice.count()) === 0 ? pass : fail)("8a. Cancel in the dialog: item not marked removed", (await shirt()).status);

    // 8b. Remove, keep the dialog open past 6 seconds, then confirm: marked removed.
    await page.click("#r-trousers .remove");
    await page.locator("#overlay.open").waitFor();
    await sleep(6500);
    ((await trousers()).status !== "removed" ? pass : fail)("8b. dialog open for 6.5s: still not marked removed", (await trousers()).status);
    await page.click("#confirm-remove");
    await notice.waitFor({ timeout: 3000 });
    ((await trousers()).status === "removed" && (await shirt()).status !== "removed" ? pass : fail)("8b. Confirm in the dialog marks only that item removed", await notice.textContent());
    const logs = page.slLogs.join(" | ");
    (/removal cancelled in dialog: Relaxed Linen Shirt/.test(logs) && /confirmed in dialog/.test(logs) ? pass : fail)("8. cancel and confirm are logged");
    await sleep(300); await page.screenshot({ path: `${OUT}/8b-confirm-removed.png` });
  }

  // ---- 9. API: every stored alternative comes back with a url (read-only) ----
  {
    const login = await fetch(`${API}/auth/login`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password: "secret123" }) }).then((r) => r.json());
    const dash = await fetch(`${API}/dashboard`, { headers: { Authorization: `Bearer ${login.token}` } }).then((r) => r.json());
    const alts = (dash.verdicts || []).flatMap((v) => v.alternatives || []);
    (alts.length > 0 && alts.every((a) => typeof a.url === "string" && /^https?:\/\//.test(a.url) && ["product", "search"].includes(a.link_kind)) ? pass : fail)(
      "9. /dashboard: every alternative has a url and link_kind", `${alts.length} alternatives`);
  }
  // ---- 10. Part 5 card: backend down shows "Second look unavailable" and never blocks the store ----
  // The service worker's fetch to /analyze is made to fail, as if the server were down.
  {
    await sw.evaluate(() => {
      self.__realFetch = self.__realFetch || self.fetch;
      self.fetch = (url, opts) => String(url).endsWith("/analyze") ? Promise.reject(new TypeError("Failed to fetch")) : self.__realFetch(url, opts);
    });
    const page = await openStore("product-jsonld.html");
    await page.click("#add");
    const shown = await card(page).locator(".calm-title").waitFor({ timeout: 10000 }).then(() => true, () => false);
    const title = shown ? await card(page).locator(".calm-title").textContent() : await card(page).innerText().catch(() => "no card");
    (title === "Second look unavailable" ? pass : fail)("10. backend down: card says Second look unavailable", title);
    (await page.title() === "added" ? pass : fail)("10. backend down: store's own add-to-cart handler still ran");
    (await card(page).locator(".error-box").count() === 0 ? pass : fail)("10. backend down: calm state, not the error box");
    await sleep(400); await page.screenshot({ path: `${OUT}/10-unavailable.png` });
    await sw.evaluate(() => { self.fetch = self.__realFetch; });
    // Try again with the server back: the verdict shows, with its reason.
    await card(page).getByRole("button", { name: "Try again" }).click();
    await card(page).locator(".badge").waitFor({ timeout: 15000 });
    const reason = (await card(page).locator(".reason").textContent()) || "";
    (reason.trim().length > 0 ? pass : fail)("10. Try again shows the verdict with a reason", reason.slice(0, 80));
  }

  // ---- console errors ----
  (errors.length ? fail : pass)("no console or page errors", errors.join(" | "));

  await ctx.close();
  console.log(`\n${results.filter((r) => r[0] === "PASS").length}/${results.length} passed`);
  fs.writeFileSync(path.join(OUT, "results.json"), JSON.stringify({ email, results }, null, 2));
  process.exit(results.some((r) => r[0] === "FAIL") ? 1 : 0);
})().catch((e) => { console.error("E2E crashed:", e); process.exit(2); });
