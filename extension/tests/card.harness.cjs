// Card harness: renders the real card.js and flow.js on a plain page with a mocked service worker,
// so every verdict state can be checked without the backend. Takes screenshots and asserts.
// Run: PW_PATH=<path to playwright> node extension/tests/card.harness.cjs
// Screenshots go to extension/tests/e2e/shots/card-*.png (gitignored).
const path = require("path");
const fs = require("fs");
const { chromium } = require(process.env.PW_PATH || "playwright");

const SRC = path.resolve(__dirname, "../src");
const IMG = path.join(__dirname, "e2e/fixtures/img");
const OUT = path.join(__dirname, "e2e/shots");
fs.mkdirSync(OUT, { recursive: true });

const SCRIPTS = ["content/shadowHost.js", "content/reader/price.js", "content/card.styles.js", "content/card.js", "content/flow.js"]
  .map((f) => fs.readFileSync(path.join(SRC, f), "utf8"));
const dataUrl = (f) => `data:image/jpeg;base64,${fs.readFileSync(path.join(IMG, f)).toString("base64")}`;

const ITEM = { name: "Wide Leg Barrel Jeans", image_url: dataUrl("jeans.jpg"), price: 49.9, store: "Example Store", url: "https://store.example/jeans" };
const closet = (id, name, img) => ({ id, user_id: "u1", source: "ingest_photo", status: "owned", name, image_data: img ? dataUrl(img) : null,
  attributes: { category: "jeans", color_family: "blue" }, description: "", created_at: "2026-11-01T00:00:00Z" });

const BASE = { status: "ok", verdict_id: "v1", matched_closet_item: null, matched_closet_items: [], matched_trend: null,
  alternatives: [], outfits: [], expected_wears: null, cost_per_wear: null, decided_by: "code", closet_small: false };

const RESPONSES = {
  have: { ...BASE, verdict: "have_this_look", zone: "forced",
    reason: "Your blue straight jeans already give this look. Same wash and a similar relaxed leg.",
    matched_closet_item: closet("c1", "Blue straight jeans", "jeans.jpg"),
    matched_closet_items: [closet("c1", "Blue straight jeans", "jeans.jpg"), closet("c2", "Navy relaxed jeans", "navy.jpg"), closet("c3", "Black wide jeans", null)] },
  trend: { ...BASE, verdict: "micro_trend", decided_by: "claude", zone: "none",
    reason: "Barrel-leg jeans are peaking now. Sources expect the look to fade within a few months.",
    matched_trend: { id: "t1", name: "Barrel-leg jeans", lifespan_label: "Expected to fade in about 3 months", lifespan_bucket: "short",
      lifespan_source_url: "https://www.vogue.com/article/barrel-jeans", sources: [{ title: "Vogue: The barrel jean moment", url: "https://www.vogue.com/article/barrel-jeans" }] },
    alternatives: [
      { name: "Levi's 501 Original Fit Jeans", url: "https://www.levi.com/US/en_US/501", price: 79.5, reason: null },
      { name: "Straight-leg mid-wash jeans", url: "https://www.uniqlo.com/us/en/straight-jeans", price: 49.9, reason: null },
      { name: "Search wide straight jeans", url: "https://www.google.com/search?q=wide+straight+jeans", price: null, reason: null },
    ] },
  good: { ...BASE, verdict: "good_buy", decided_by: "claude", zone: "gray",
    reason: "Nothing like this in your closet yet, and it pairs with three tops you own.",
    expected_wears: 30, cost_per_wear: 1.66 },
  small: { ...BASE, verdict: "good_buy", zone: "none", closet_small: true,
    reason: "I don't see this in your closet. It is a versatile basic that should get steady wear.",
    expected_wears: 25, cost_per_wear: 2.0 },
  // Today's stub backend: none of the new fields.
  old: { status: "ok", verdict_id: "v0", verdict: "micro_trend", reason: "This look is peaking now. (Stub reason.)",
    matched_closet_item: null, matched_trend: { id: "t0", name: "Old trend", sources: [] },
    alternatives: [{ name: "Classic straight-leg version", url: null, price: 48, reason: "Stub" }], outfits: [], expected_wears: null, cost_per_wear: null },
};

const OWNERSHIP = { show: true, staple_idea: "Idea: a white tee pairs with these.", options: [
  { label: "Blue straight jeans", attributes: { category: "jeans", color_family: "blue", cut: "straight" } },
  { label: "Black slim jeans", attributes: { category: "jeans", color_family: "black", cut: "slim" } },
  { label: "Light wash wide jeans", attributes: { category: "jeans", color_family: "blue", cut: "wide" } },
] };

const results = [];
const check = (ok, name, detail = "") => { results.push([ok, name]); console.log(ok ? "PASS" : "FAIL", name, detail); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// mode: "ok" | "never" (loading) | "offline" | "timeout"
async function open(browser, { width, scheme, response, mode = "ok" }) {
  const ctx = await browser.newContext({ viewport: { width, height: width < 500 ? 844 : 800 }, colorScheme: scheme, deviceScaleFactor: 2 });
  const page = await ctx.newPage();
  page.errors = [];
  page.on("pageerror", (e) => page.errors.push(e.message));
  page.on("console", (m) => { if (m.type() === "error") page.errors.push(m.text()); });
  await page.setContent(`<!doctype html><html><head><meta name="viewport" content="width=device-width">
    <style>body{margin:0;font:15px system-ui;color:#222;background:#fafafa} header{padding:18px 24px;border-bottom:1px solid #ddd;font-weight:700}
    main{display:flex;gap:32px;padding:24px;flex-wrap:wrap} .ph{width:420px;max-width:100%;aspect-ratio:3/4;background:#e9e6e1}
    button{padding:12px 24px;background:#111;color:#fff;border:0;font:inherit}</style></head>
    <body><header>EXAMPLE STORE</header><main><div class="ph"></div><div><h1>${ITEM.name}</h1><p>$49.90</p><button id="add">Add to cart</button></div></main></body></html>`);
  await page.evaluate(({ response, mode, item, ownership }) => {
    window.__calls = [];
    window.chrome = { runtime: { sendMessage: (msg) => {
      window.__calls.push(msg.type);
      const ok = (data) => Promise.resolve({ ok: true, data });
      switch (msg.type) {
        case "ANALYZE_ITEM":
          if (mode === "never") return new Promise(() => {});
          if (mode === "offline") return Promise.resolve({ ok: false, code: "offline", error: "Cannot reach the Second Look server." });
          if (mode === "timeout") return new Promise((r) => setTimeout(() => r({ ok: false, code: "timeout", error: "Took too long" }), 300));
          return ok(response);
        case "GET_ATTRIBUTES": return ok({ status: "ok", category: "jeans", color_family: "blue", fabric: "denim", fabric_confidence: "high", cut: "wide", style: "streetwear", description: "x", is_apparel: true, attribute_string: "x" });
        case "OWNERSHIP_OPTIONS": return ok(ownership);
        case "OWNERSHIP_ANSWER": window.__answer = msg.payload; return ok({ item: { id: "new" } });
        default: return ok({ ok: true });
      }
    } } };
    window.SecondLookReader = { readItem: async () => item };
  }, { response, mode, item: ITEM, ownership: OWNERSHIP });
  for (const code of SCRIPTS) await page.addScriptTag({ content: code });
  await page.evaluate(() => { globalThis.SecondLookFlow.runCheck(); }); // do not wait: "never" mode stays pending
  return { ctx, page, card: page.locator("#secondlook-shadow-host .card") };
}

async function shot(page, name) {
  await sleep(450); // let the entry transition settle
  const file = path.join(OUT, `card-${name}.png`);
  await page.screenshot({ path: file });
  return file;
}

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH || undefined });
  const shots = [];
  const errors = [];
  const variants = [
    { width: 1280, scheme: "light", tag: "desktop-light" },
    { width: 390, scheme: "light", tag: "390-light" },
    { width: 1280, scheme: "dark", tag: "desktop-dark" },
    { width: 390, scheme: "dark", tag: "390-dark" },
  ];

  for (const v of variants) {
    const done = async (s) => { errors.push(...s.page.errors); await s.ctx.close(); };

    // have_this_look: reason and up to 3 closet photos
    let s = await open(browser, { ...v, response: RESPONSES.have });
    await s.card.locator(".badge").waitFor();
    if (v.tag === "desktop-light") {
      check(await s.card.locator(".closet-tile").count() === 3, "have_this_look: 3 closet tiles");
      check(await s.card.locator(".closet-tile img").count() === 2, "have_this_look: 2 photos plus 1 placeholder");
      check((await s.card.locator(".closet-name").textContent()) === "Blue straight jeans", "have_this_look: best match named");
      check(/\+2 similar/.test(await s.card.innerText()), "have_this_look: +2 similar");
      check(await s.card.locator(".trend, .alts, .wears").count() === 0, "have_this_look: no trend, alternatives or wears");
    }
    shots.push(await shot(s.page, `have-${v.tag}`)); await done(s);

    // micro_trend: trend name, source link, alternatives as links
    s = await open(browser, { ...v, response: RESPONSES.trend });
    await s.card.locator(".badge").waitFor();
    if (v.tag === "desktop-light") {
      check((await s.card.locator(".trend-name").textContent()) === "Barrel-leg jeans", "micro_trend: trend name");
      const src = s.card.locator("a.source");
      check((await src.getAttribute("href")) === RESPONSES.trend.matched_trend.sources[0].url && (await src.getAttribute("target")) === "_blank", "micro_trend: source link opens in a new tab");
      check(/fade in about 3 months/.test(await s.card.innerText()), "micro_trend: lifespan shown next to its source");
      const links = s.card.locator("a.alt-link");
      check(await links.count() === 3, "micro_trend: 3 alternative links");
      check((await links.nth(0).getAttribute("href")) === RESPONSES.trend.alternatives[0].url && (await links.nth(0).getAttribute("rel")).includes("noopener"), "micro_trend: alternative href and rel");
      check(/\$79\.50/.test(await links.nth(0).innerText()), "micro_trend: alternative price");
    }
    shots.push(await shot(s.page, `trend-${v.tag}`)); await done(s);

    // good_buy: reason and the wears line labeled estimate
    s = await open(browser, { ...v, response: RESPONSES.good });
    await s.card.locator(".badge").waitFor();
    if (v.tag === "desktop-light") {
      const wears = await s.card.locator(".wears").innerText();
      check(/About 30 wears, \$1\.66 per wear/.test(wears) && /Estimate/i.test(wears), "good_buy: wears line labeled estimate", wears);
      check(await s.card.locator(".own").count() === 0, "good_buy: no ownership question when closet is not small");
    }
    shots.push(await shot(s.page, `good-${v.tag}`)); await done(s);

    // closet_small and zone none: quiet ownership question
    s = await open(browser, { ...v, response: RESPONSES.small });
    await s.card.locator(".own").waitFor({ timeout: 3000 });
    shots.push(await shot(s.page, `ownership-${v.tag}`));
    if (v.tag === "desktop-light") {
      check(await s.card.locator("button.chip").count() === 4, "ownership: 3 options plus None of these");
      const calls = await s.page.evaluate(() => window.__calls);
      check(calls.includes("GET_ATTRIBUTES") && calls.includes("OWNERSHIP_OPTIONS"), "ownership: attributes then options requested", JSON.stringify(calls));
      await s.card.getByRole("button", { name: "Black slim jeans" }).click();
      await s.card.locator(".own-done").waitFor();
      const answer = await s.page.evaluate(() => window.__answer);
      check(answer?.name === "Black slim jeans" && answer?.attributes?.color_family === "black", "ownership: answer posts the chosen option", JSON.stringify(answer));
      shots.push(await shot(s.page, `ownership-answered-${v.tag}`));
    }
    await done(s);

    // loading: calm, then the slower copy after 5 s
    s = await open(browser, { ...v, response: null, mode: "never" });
    await s.card.locator(".checking").waitFor();
    shots.push(await shot(s.page, `loading-${v.tag}`));
    if (v.tag === "desktop-light") {
      check(/checking\.\.\./.test(await s.card.locator(".checking").innerText()), "loading: first copy");
      await sleep(5000);
      check(/Still checking/.test(await s.card.locator(".checking").innerText()), "loading: calm slower copy after 5 s");
      shots.push(await shot(s.page, `loading-slow-${v.tag}`));
    }
    await done(s);

    // unavailable: backend down
    s = await open(browser, { ...v, response: null, mode: "offline" });
    await s.card.locator(".calm").waitFor();
    if (v.tag === "desktop-light") {
      check((await s.card.locator(".calm-title").textContent()) === "Second look unavailable", "unavailable: offline shows Second look unavailable");
      check(await s.card.locator(".error-box").count() === 0, "unavailable: not styled as an error");
      // the store's own button still works with the card open
      await s.page.evaluate(() => document.getElementById("add").addEventListener("click", () => { document.title = "added"; }));
      await s.page.click("#add");
      check((await s.page.title()) === "added", "unavailable: store button still clickable");
    }
    shots.push(await shot(s.page, `unavailable-${v.tag}`)); await done(s);
  }

  // timeout also lands in unavailable; Try again re-runs the check
  {
    const s = await open(browser, { width: 1280, scheme: "light", response: null, mode: "timeout" });
    await s.card.locator(".calm").waitFor();
    check(true, "unavailable: timeout shows Second look unavailable");
    await s.card.getByRole("button", { name: "Try again" }).click();
    await s.card.locator(".calm").waitFor();
    const n = (await s.page.evaluate(() => window.__calls)).filter((c) => c === "ANALYZE_ITEM").length;
    check(n === 2, "unavailable: Try again calls analyze again", String(n));
    errors.push(...s.page.errors); await s.ctx.close();
  }

  // older backend response with none of the new fields
  {
    const s = await open(browser, { width: 1280, scheme: "light", response: RESPONSES.old });
    await s.card.locator(".badge").waitFor();
    check(await s.card.locator(".alt-link").count() === 1 && await s.card.locator("a.alt-link").count() === 0, "old backend: alternative without url renders as text");
    check(await s.card.locator("a.source").count() === 0 && await s.card.locator(".trend-name").count() === 1, "old backend: trend without sources has no link");
    check(await s.card.locator(".own").count() === 0, "old backend: no ownership question");
    shots.push(await shot(s.page, "old-backend-desktop-light"));
    errors.push(...s.page.errors); await s.ctx.close();
  }

  check(errors.length === 0, "no console or page errors", errors.join(" | "));
  await browser.close();
  console.log("\nScreenshots:\n" + shots.join("\n"));
  const passed = results.filter((r) => r[0]).length;
  console.log(`\n${passed}/${results.length} passed`);
  process.exit(passed === results.length ? 0 : 1);
})().catch((e) => { console.error("harness crashed:", e); process.exit(2); });
