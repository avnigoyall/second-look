#!/usr/bin/env node
/* Playwright harness for the dashboard savings goal (dashboard/goal.js).
   Serves dashboard/ from disk and mocks every API call with page.route, so no
   backend is needed. Run from the repo root:

     node dashboard/tests/goal.harness.cjs
     PLAYWRIGHT_PATH=/path/to/node_modules/playwright SHOTS=/tmp/goal node dashboard/tests/goal.harness.cjs

   Screenshots go to extension/tests/e2e/shots/goal/ (gitignored) unless SHOTS is set.
   Exit code is non-zero when any check fails. */
const fs = require("fs");
const path = require("path");

const PW = process.env.PLAYWRIGHT_PATH || "/Users/hiresh/.npm/_npx/361ceb562f3b3235/node_modules/playwright";
const { chromium } = require(PW);

const DASH = path.resolve(__dirname, "..");
const SHOTS = process.env.SHOTS || path.resolve(__dirname, "../../extension/tests/e2e/shots/goal");
const ORIGIN = "http://goal.test";
fs.mkdirSync(SHOTS, { recursive: true });

const results = [];
function check(name, ok, extra) {
  results.push({ name, ok: !!ok });
  console.log((ok ? "PASS " : "FAIL ") + name + (ok || !extra ? "" : "  -> " + extra));
}

// ---------- fixtures ----------
const COLORS = ["#c9b8e8", "#e8c9b8", "#b8e8d0", "#e8e0b8", "#b8cfe8"];
function itemsFixture(n, opts = {}) {
  const out = [];
  for (let i = 0; i < n; i++) {
    let image_url = "https://img.test/ok/" + i + ".svg";
    if (opts.mixed && i % 3 === 1) image_url = null;
    if (opts.mixed && i % 3 === 2) image_url = "https://img.test/broken/" + i + ".jpg";
    out.push({
      verdict_id: "v" + i,
      name: opts.longNames && i === 0
        ? "Oversized double-breasted wool blend cocoon coat with detachable faux fur collar <b>bold</b>"
        : ["Linen shirt", "Wide leg jeans", "Leather loafers", "Cropped cardigan", "Silk scarf"][i % 5],
      store: ["Zara", "Uniqlo", "COS", "Aritzia", null][i % 5],
      price_cents: i === 3 ? null : 1999 + i * 1250,
      image_url,
      skipped_at: new Date(Date.UTC(2026, 10, 14, 15, 30) - i * 86400000 * 3).toISOString().replace(".000Z", "Z"),
      moved_cents: null,
    });
  }
  return out;
}
const VERDICTS = [
  { id: "a", verdict: "micro_trend", action: "skipped", feedback: "none", created_at: "2026-11-14T15:00:00Z", item: { name: "Barrel jeans", price: 89.5 } },
  { id: "b", verdict: "good_buy", action: "bought", feedback: "up", created_at: "2026-11-13T12:00:00Z", item: { name: "White tee", price: 18 } },
];

// ---------- page setup ----------
async function setup(browser, { width = 1280, scheme = "light", reducedMotion = "no-preference", api }) {
  const context = await browser.newContext({
    viewport: { width, height: width < 500 ? 844 : 900 },
    colorScheme: scheme, reducedMotion, deviceScaleFactor: 2,
  });
  await context.addInitScript(() => { try { localStorage.setItem("secondLookToken", "test-token"); } catch {} });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("dialog", (d) => { errors.push("dialog: " + d.message()); d.dismiss(); });
  const calls = [];

  await page.route("**/*", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    if (url.host === "img.test") {
      if (url.pathname.startsWith("/broken/")) return route.fulfill({ status: 404, body: "" });
      const n = Number(url.pathname.match(/(\d+)/)[1]);
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40"><rect width="40" height="40" fill="${COLORS[n % COLORS.length]}"/><circle cx="20" cy="17" r="8" fill="#fff" opacity=".7"/></svg>`;
      return route.fulfill({ status: 200, contentType: "image/svg+xml", body: svg });
    }
    if (url.origin !== ORIGIN) return route.abort();
    const p = url.pathname;
    if (p === "/dashboard-ui") return route.fulfill({ path: path.join(DASH, "index.html"), contentType: "text/html" });
    if (p.startsWith("/dashboard-assets/")) {
      const f = path.join(DASH, p.slice("/dashboard-assets/".length));
      if (!f.startsWith(DASH) || !fs.existsSync(f)) return route.fulfill({ status: 404, body: "" });
      return route.fulfill({ path: f });
    }
    if (p === "/auth/me") return route.fulfill({ json: { user: { email: "test_goaldash@secondlook.test" } } });
    if (p === "/dashboard") return route.fulfill({ json: { money_kept: 107.5, verdicts: VERDICTS } });
    if (p === "/goal" || p === "/goal/items") {
      const body = req.postData() ? JSON.parse(req.postData()) : null;
      calls.push({ method: req.method(), path: p, body });
      const r = await api(req.method(), p, body);
      if (r && r.abort) return route.abort();
      if (r && r.raw != null) return route.fulfill({ status: r.status || 200, contentType: "application/json", body: r.raw });
      return route.fulfill({ status: r.status || 200, json: r.json });
    }
    return route.fulfill({ status: 404, json: { detail: "Not Found" } });
  });
  await page.goto(ORIGIN + "/dashboard-ui");
  await page.waitForSelector("#dash-view:not([hidden])");
  await page.waitForTimeout(150);
  return { context, page, errors, calls };
}

// Stateful goal mock with the contract's shapes.
function goalApi({ goal = null, saved = 34000, items = itemsFixture(8, { mixed: true }) } = {}) {
  const s = { goal };
  const view = () => {
    if (!s.goal) return { goal: null };
    const t = s.goal.target_cents;
    return { goal: { name: s.goal.name, target_cents: t, saved_cents: saved,
      percent: Math.min(100, Math.floor(saved * 100 / t)), reached: saved >= t, source: "local" } };
  };
  return async (method, p, body) => {
    if (p === "/goal/items") return { json: { items } };
    if (method === "GET") return { json: view() };
    if (method === "DELETE") { s.goal = null; return { json: { goal: null } }; }
    if (method === "PUT") {
      const name = (body.name || "").trim();
      if (!name || name.length > 40) return { status: 422, json: { detail: "Name must be 1 to 40 characters." } };
      if (body.target_cents === 66600) return { status: 422, json: { detail: [{ msg: "Value error, server says no" }] } };
      s.goal = { name, target_cents: body.target_cents };
      return { json: view() };
    }
    return { status: 405, json: {} };
  };
}

async function shot(page, name) {
  await page.screenshot({ path: path.join(SHOTS, name + ".png"), fullPage: true });
}

const MATRIX = [
  { width: 1280, scheme: "light", tag: "1280-light" },
  { width: 1280, scheme: "dark", tag: "1280-dark" },
  { width: 390, scheme: "light", tag: "390-light" },
  { width: 390, scheme: "dark", tag: "390-dark" },
];

(async () => {
  const browser = await chromium.launch();
  try {
    // 1. No goal
    for (const m of MATRIX) {
      const { context, page, errors } = await setup(browser, { ...m, api: goalApi() });
      const ok = await page.locator("#goal .goal-set").isVisible();
      if (m.tag === "1280-light") {
        check("no goal: Set a goal button visible", ok);
        check("no goal: progressbar hidden", !(await page.locator("#goal [role=progressbar]").isVisible()));
        check("no goal: component is first in #dash-view", await page.evaluate(() => document.getElementById("dash-view").firstElementChild.id === "goal"));
      }
      await shot(page, "01-no-goal-" + m.tag);
      await page.click("#goal .goal-set");
      await shot(page, "02-set-panel-" + m.tag);
      check("set panel: no Delete when creating (" + m.tag + ")", !(await page.locator("#goal .goal-btn.danger").isVisible()));
      check("no page errors (" + m.tag + ")", errors.length === 0, errors.join(" | "));
      await context.close();
    }

    // 2. In progress, list, edit panel
    for (const m of MATRIX) {
      const { context, page, errors } = await setup(browser, { ...m, api: goalApi({ goal: { name: "Trip to Lisbon", target_cents: 200000 } }) });
      const bar = page.locator("#goal [role=progressbar]");
      const txt = (await page.locator("#goal .goal-text:not(.on-fill) .goal-text-main").textContent()).trim();
      if (m.tag === "1280-light") {
        check("in progress: text is $340 / $2,000", txt === "$340 / $2,000", txt);
        check("in progress: whole bar text is exactly the progress", (await page.locator("#goal .goal-text:not(.on-fill)").textContent()) === "$340 / $2,000");
        check("in progress: aria values", (await bar.getAttribute("aria-valuenow")) === "34000" &&
          (await bar.getAttribute("aria-valuemin")) === "0" && (await bar.getAttribute("aria-valuemax")) === "200000");
        check("in progress: accessible name", (await page.evaluate(() => document.getElementById("goal-label").textContent)) === "Saving for Trip to Lisbon");
        check("in progress: only one button besides the bar (edit icon)", (await page.locator("#goal .goal-view button:visible").count()) === 1);
      }
      await shot(page, "03-in-progress-" + m.tag);
      await bar.click();
      await page.waitForSelector("#goal .goal-list li");
      await page.waitForTimeout(250);
      await shot(page, "04-list-mixed-images-" + m.tag);
      if (m.tag === "1280-light") {
        check("list: 8 items", (await page.locator("#goal .goal-list li").count()) === 8);
        check("list: null image -> neutral tile", (await page.locator("#goal .goal-list li").nth(1).locator("img").count()) === 0);
        check("list: broken image -> neutral tile", (await page.locator("#goal .goal-list li").nth(2).locator("img").count()) === 0 &&
          (await page.locator("#goal .goal-list li").nth(2).locator("svg").count()) === 1);
        check("list: date in local time", (await page.locator("#goal .goal-item-date").first().textContent()) === "Nov 14");
        await bar.press("Enter");
        check("keyboard: Enter collapses list", await page.locator("#goal .goal-items").isHidden());
        await bar.press(" ");
        check("keyboard: Space expands list", await page.locator("#goal .goal-items").isVisible());
      }
      await page.click("#goal .goal-edit");
      await shot(page, "05-edit-panel-" + m.tag);
      check("no page errors (" + m.tag + ")", errors.length === 0, errors.join(" | "));
      await context.close();
    }

    // 3. Save: validation, 422, success; create from no goal
    {
      const { context, page, errors, calls } = await setup(browser, { api: goalApi() });
      await page.click("#goal .goal-set");
      await page.click("#goal button[type=submit]");
      check("validation: empty name", (await page.textContent("#goal-msg")) === "Give your goal a name.");
      await page.fill("#goal-name", "x".repeat(41));
      await page.fill("#goal-target", "100");
      await page.click("#goal button[type=submit]");
      check("validation: 41 char name", (await page.textContent("#goal-msg")).includes("40 characters"));
      await page.fill("#goal-name", "New bike");
      await page.fill("#goal-target", "4.99");
      await page.click("#goal button[type=submit]");
      check("validation: target below $5", (await page.textContent("#goal-msg")) === "Pick a target from $5 to $100,000.");
      await shot(page, "06-validation-1280-light");
      await page.fill("#goal-target", "100000.01");
      await page.click("#goal button[type=submit]");
      check("validation: target above $100,000", (await page.textContent("#goal-msg")) === "Pick a target from $5 to $100,000.");
      await page.fill("#goal-target", "abc");
      await page.click("#goal button[type=submit]");
      check("validation: non-number", (await page.textContent("#goal-msg")).startsWith("Enter the target"));
      check("validation: no PUT sent for bad input", !calls.some((c) => c.method === "PUT"));
      await page.fill("#goal-target", "666");
      await page.click("#goal button[type=submit]");
      await page.waitForFunction(() => document.getElementById("goal-msg").textContent === "server says no");
      check("server 422 message shown inline", true);
      await shot(page, "07-server-422-1280-light");
      await page.fill("#goal-target", "$1,250.50");
      await page.click("#goal button[type=submit]");
      await page.waitForSelector("#goal [role=progressbar]:visible");
      const put = calls.filter((c) => c.method === "PUT").pop();
      check("save: dollars converted to cents", put.body.target_cents === 125050 && put.body.name === "New bike", JSON.stringify(put.body));
      check("save: bar shows $340 / $1,251", (await page.locator("#goal .goal-text:not(.on-fill) .goal-text-main").textContent()) === "$340 / $1,251");
      check("save: focus returns to edit icon", await page.evaluate(() => document.activeElement.classList.contains("goal-edit")));
      check("no page errors (save)", errors.length === 0, errors.join(" | "));
      await context.close();
    }

    // 4. Delete confirm and cancel
    {
      const { context, page, errors, calls } = await setup(browser, { api: goalApi({ goal: { name: "Trip to Lisbon", target_cents: 200000 } }) });
      await page.click("#goal .goal-edit");
      await page.click("#goal .goal-btn.danger");
      check("delete: inline Are you sure", await page.locator("#goal .goal-confirm-text").isVisible());
      await shot(page, "08-delete-confirm-1280-light");
      await page.click("text=Keep it");
      check("delete cancel: back to Save row, no DELETE sent", (await page.locator("#goal button[type=submit]").isVisible()) && !calls.some((c) => c.method === "DELETE"));
      await page.click("#goal .goal-btn.danger");
      await page.click("text=Delete goal");
      await page.waitForSelector("#goal .goal-set:visible");
      check("delete confirm: DELETE sent, Set a goal back", calls.some((c) => c.method === "DELETE"));
      check("no page errors / dialogs (delete)", errors.length === 0, errors.join(" | "));
      await context.close();
    }
    {
      const { context, page } = await setup(browser, { width: 390, scheme: "dark", api: goalApi({ goal: { name: "Trip to Lisbon", target_cents: 200000 } }) });
      await page.click("#goal .goal-edit");
      await page.click("#goal .goal-btn.danger");
      await shot(page, "08-delete-confirm-390-dark");
      await context.close();
    }

    // 5. Reached + celebration once per session
    for (const m of MATRIX) {
      const { context, page, errors } = await setup(browser, { ...m, api: goalApi({ goal: { name: "Trip to Lisbon", target_cents: 30000 } }) });
      if (m.tag === "1280-light") {
        check("reached: text", (await page.locator("#goal .goal-text:not(.on-fill) .goal-text-main").textContent()) === "Trip to Lisbon: you made it");
        check("reached: celebrating", (await page.getAttribute("#goal", "data-celebrate")) === "true");
        check("reached: session flag set", (await page.evaluate(() => sessionStorage.getItem("secondLookGoalCelebrated"))) === "1");
      }
      await page.waitForTimeout(700);
      await shot(page, "09-reached-celebrating-" + m.tag);
      await page.waitForTimeout(1600);
      await shot(page, "10-reached-" + m.tag);
      if (m.tag === "1280-light") {
        await page.reload();
        await page.waitForSelector("#goal [role=progressbar]:visible");
        check("reached: no second celebration in the same session", (await page.getAttribute("#goal", "data-celebrate")) === null);
        await page.click("#goal .goal-edit");
        check("reached: edit still works", await page.locator("#goal-panel").isVisible());
        await page.click("#goal .goal-btn.danger");
        check("reached: delete still works", await page.locator("#goal .goal-confirm-text").isVisible());
      }
      check("no page errors (reached " + m.tag + ")", errors.length === 0, errors.join(" | "));
      await context.close();
    }
    {
      const { context, page } = await setup(browser, { reducedMotion: "reduce", api: goalApi({ goal: { name: "Trip to Lisbon", target_cents: 30000 } }) });
      const anim = await page.evaluate(() => getComputedStyle(document.querySelector("#goal .goal-sparks i")).animationName);
      check("reduced motion: no spark movement", anim === "none", anim);
      await context.close();
    }

    // 6. Long names, escaping
    for (const m of [MATRIX[0], MATRIX[3]]) {
      const evil = "<img src=x onerror=alert(1)>Ultra long goal name 40c";
      const { context, page, errors } = await setup(browser, { ...m, api: goalApi({ goal: { name: evil, target_cents: 10000000 }, saved: 9999999, items: itemsFixture(4, { longNames: true }) }) });
      await page.locator("#goal [role=progressbar]").click();
      await page.waitForSelector("#goal .goal-list li");
      await page.waitForTimeout(250);
      await shot(page, "11-long-names-" + m.tag);
      if (m.tag === "1280-light") {
        check("escape: goal name rendered as text", (await page.evaluate(() => document.querySelectorAll("#goal img[src=x]").length)) === 0 &&
          (await page.textContent("#goal-label")).includes("<img"));
        check("escape: item name rendered as text", (await page.locator("#goal .goal-item-name").first().textContent()).includes("<b>bold</b>"));
        check("large numbers: $99,999 / $100,000", (await page.locator("#goal .goal-text:not(.on-fill) .goal-text-main").textContent()) === "$99,999 / $100,000");
      }
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
      check("long names: no horizontal overflow (" + m.tag + ")", !overflow);
      check("no page errors (long " + m.tag + ")", errors.length === 0, errors.join(" | "));
      await context.close();
    }

    // 7. 0 items and 100 items
    {
      const { context, page } = await setup(browser, { api: goalApi({ goal: { name: "Trip to Lisbon", target_cents: 200000 }, saved: 0, items: [] }) });
      await page.locator("#goal [role=progressbar]").click();
      await page.waitForSelector("#goal .goal-items-status:text('No skipped items')");
      check("0 items: empty message", true);
      check("0 saved: aria-valuenow 0", (await page.getAttribute("#goal [role=progressbar]", "aria-valuenow")) === "0");
      await shot(page, "12-zero-items-1280-light");
      await context.close();
    }
    for (const m of [MATRIX[0], MATRIX[2]]) {
      const { context, page } = await setup(browser, { ...m, api: goalApi({ goal: { name: "Trip to Lisbon", target_cents: 200000 }, items: itemsFixture(100) }) });
      await page.locator("#goal [role=progressbar]").click();
      await page.waitForSelector("#goal .goal-list li");
      const dims = await page.evaluate(() => { const e = document.getElementById("goal-items"); return { sh: e.scrollHeight, ch: e.clientHeight, n: e.querySelectorAll("li").length }; });
      check("100 items: rendered and scrollable (" + m.tag + ")", dims.n === 100 && dims.sh > dims.ch && dims.ch <= 362, JSON.stringify(dims));
      await page.locator("#goal-items").evaluate((e) => { e.scrollTop = 600; });
      await page.waitForTimeout(250);
      await shot(page, "13-hundred-items-scrolled-" + m.tag);
      await context.close();
    }

    // 8. Failures hide the component, rest of the page fine
    const failures = {
      "500": async () => ({ status: 500, json: { detail: "boom" } }),
      "401": async () => ({ status: 401, json: { detail: "Not authenticated" } }),
      "404": async () => ({ status: 404, json: { detail: "Not Found" } }),
      "network": async () => ({ abort: true }),
      "malformed": async () => ({ raw: "<html>nope" }),
      "bad shape": async () => ({ json: { goal: { name: 5 } } }),
    };
    for (const [label, api] of Object.entries(failures)) {
      const { context, page, errors } = await setup(browser, { api });
      const hidden = await page.locator("#goal").isHidden();
      const restOk = (await page.textContent("#money-kept")) === "$107.50" && (await page.locator("#verdicts li").count()) === 2;
      check("GET /goal " + label + ": component hidden, dashboard intact, no errors", hidden && restOk && errors.length === 0, errors.join(" | "));
      if (label === "500") { await shot(page, "14-goal-500-1280-light"); }
      await context.close();
    }
    {
      const { context, page } = await setup(browser, { width: 390, scheme: "dark", api: async () => ({ status: 500, json: {} }) });
      await shot(page, "14-goal-500-390-dark");
      await context.close();
    }

    // 9. Logout hides, login loads
    {
      const { context, page } = await setup(browser, { api: goalApi({ goal: { name: "Trip to Lisbon", target_cents: 200000 } }) });
      await page.click("#logout");
      check("logout: component hidden with dash view", await page.locator("#goal").isHidden());
      await context.close();
    }
  } finally {
    await browser.close();
  }
  const failed = results.filter((r) => !r.ok);
  console.log("\n" + (results.length - failed.length) + "/" + results.length + " checks passed. Screenshots: " + SHOTS);
  process.exit(failed.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
