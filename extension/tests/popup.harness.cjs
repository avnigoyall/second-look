// Popup harness: opens the real popup.html with a mocked service worker, walks every iMessage line state
// (none, connect, assigning, pending opt-in, connected, error) in light and dark, asserts and takes screenshots.
// Run: PW_PATH=<path to playwright> node extension/tests/popup.harness.cjs
// Screenshots go to extension/tests/e2e/shots/popup-imsg-*.png (gitignored).
const path = require("path");
const fs = require("fs");
const { chromium } = require(process.env.PW_PATH || "playwright");

const POPUP = "file://" + path.resolve(__dirname, "../popup.html");
const OUT = path.join(__dirname, "e2e/shots");
fs.mkdirSync(OUT, { recursive: true });

const LINE = "+15550199999";
const PHONE = "+15550100001";
const S = {
  none: { status: "none", line: null, phone: PHONE, message: "Connect iMessage to text Second Look photos." },
  nophone: { status: "none", line: null, phone: null, message: "Add your phone number to use iMessage." },
  assigning: { status: "assigning", line: null, phone: PHONE, message: "Setting up your iMessage line." },
  pending: { status: "pending_optin", line: LINE, phone: PHONE, message: "Text Second Look at this number to start." },
  connected: { status: "connected", line: LINE, phone: PHONE, message: "iMessage connected." },
  seats: { status: "error", line: null, phone: PHONE,
    message: "All Second Look iMessage lines are taken right now. The extension still works as usual." },
};

// Mocked chrome APIs. statusQueue: answers to IMESSAGE_STATUS in order (the last one repeats).
const mockChrome = ({ statusQueue, connect }) => {
  const calls = (window.__calls = []);
  const queue = [...statusQueue];
  const answers = {
    GET_STATE: { loggedIn: true, email: "test_autoassign@secondlook.test", profileType: "real", phone: null },
    PAGE_KIND: { product: false },
    IMPACT: { money_kept: 129.8, co2e_kg: 41.5, items_skipped: 3, co2e_items_counted: 3, co2e_sources: [] },
  };
  window.chrome = {
    runtime: {
      sendMessage: async ({ type, payload }) => {
        calls.push({ type, payload });
        if (type === "IMESSAGE_STATUS") return { ok: true, data: queue.length > 1 ? queue.shift() : queue[0] };
        if (type === "IMESSAGE_CONNECT") return connect.ok ? { ok: true, data: connect.data } : { ok: false, error: connect.error };
        return { ok: true, data: answers[type] ?? {} };
      },
    },
    storage: {
      local: { get: async () => ({}), set: async () => {} },
      onChanged: { addListener() {}, removeListener() {} },
    },
  };
};

let failures = 0;
const check = (cond, name, detail = "") => {
  console.log(cond ? "PASS" : "FAIL", name, detail);
  if (!cond) failures++;
};

async function open(browser, scheme, opts) {
  const page = await browser.newPage({ viewport: { width: 372, height: 480 }, colorScheme: scheme });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  await page.addInitScript(mockChrome, opts);
  await page.goto(POPUP);
  await page.waitForSelector("#imsg:not([hidden])");
  page.errors = errors;
  return page;
}

const shot = (page, name) => page.locator("body").screenshot({ path: path.join(OUT, `popup-imsg-${name}.png`) });
const state = (page) => page.getAttribute("#imsg", "data-state");
const visible = (page, sel) => page.isVisible(sel);

(async () => {
  const browser = await chromium.launch();
  for (const scheme of ["light", "dark"]) {
    // Pending opt-in: the line is a tappable sms: link.
    let page = await open(browser, scheme, { statusQueue: [S.pending] });
    const text = await page.textContent("#imsg-text");
    check(text === "Text Second Look at (555) 019-9999 to start.", `${scheme} pending text`, text);
    check(await page.getAttribute("#imsg-text a", "href") === `sms:${LINE}`, `${scheme} pending sms link`);
    check(!(await visible(page, "#imsg-form")), `${scheme} pending has no connect button`);
    await shot(page, `pending-${scheme}`);
    check(page.errors.length === 0, `${scheme} pending no console errors`, page.errors.join(" | "));
    await page.close();

    // Connected: one quiet line.
    page = await open(browser, scheme, { statusQueue: [S.connected] });
    check((await page.textContent("#imsg-text")) === "iMessage connected (555) 019-9999", `${scheme} connected text`);
    check(!(await visible(page, "#imsg-form")) && !(await visible(page, "#imsg-hint")), `${scheme} connected is quiet`);
    await shot(page, `connected-${scheme}`);
    await page.close();

    // Phone, no line: Connect iMessage, then pending.
    page = await open(browser, scheme, { statusQueue: [S.none], connect: { ok: true, data: S.pending } });
    check(await visible(page, "#imsg-connect") && !(await visible(page, "#imsg-phone")), `${scheme} connect button, no phone field`);
    check((await page.textContent("#imsg-connect")) === "Connect iMessage", `${scheme} connect label`);
    await shot(page, `connect-${scheme}`);
    await page.click("#imsg-connect");
    await page.waitForSelector('#imsg[data-state="pending_optin"]');
    const call = (await page.evaluate(() => window.__calls)).find((c) => c.type === "IMESSAGE_CONNECT");
    check(call && call.payload.phone === null, `${scheme} connect sends no phone`, JSON.stringify(call));
    await page.close();

    // No phone on the account: phone field plus Connect.
    page = await open(browser, scheme, { statusQueue: [S.nophone], connect: { ok: true, data: S.pending } });
    check(await visible(page, "#imsg-phone"), `${scheme} no-phone shows phone field`);
    await shot(page, `nophone-${scheme}`);
    await page.click("#imsg-connect");
    check((await state(page)) === "none", `${scheme} empty phone does not submit`);
    await page.fill("#imsg-phone", "(555) 010-0001");
    await page.click("#imsg-connect");
    await page.waitForSelector('#imsg[data-state="pending_optin"]');
    const sent = (await page.evaluate(() => window.__calls)).find((c) => c.type === "IMESSAGE_CONNECT");
    check(sent.payload.phone === "(555) 010-0001", `${scheme} connect sends typed phone`);
    await page.close();

    // Seats full: short error line and Try again.
    page = await open(browser, scheme, { statusQueue: [S.seats] });
    check((await page.textContent("#imsg-text")) === S.seats.message, `${scheme} error text`);
    check((await page.textContent("#imsg-connect")) === "Try again", `${scheme} error retry label`);
    await shot(page, `error-${scheme}`);
    await page.close();

    // Connect fails at the HTTP level (409 phone taken): error with the phone field still there.
    page = await open(browser, scheme, { statusQueue: [S.nophone],
      connect: { ok: false, error: "This phone number is already linked to another account" } });
    await page.fill("#imsg-phone", "5550100002");
    await page.click("#imsg-connect");
    await page.waitForSelector('#imsg[data-state="error"]');
    check(await visible(page, "#imsg-phone"), `${scheme} 409 keeps phone field`);
    await page.close();

    // Right after signup: assigning, then the popup polls until the line is there.
    page = await open(browser, scheme, { statusQueue: [S.assigning, S.assigning, S.pending] });
    check((await state(page)) === "assigning", `${scheme} assigning first`);
    await shot(page, `assigning-${scheme}`);
    await page.waitForSelector('#imsg[data-state="pending_optin"]', { timeout: 6000 });
    check(true, `${scheme} assigning polls to pending`);
    await page.close();
  }
  await browser.close();
  console.log(failures ? `${failures} FAILED` : "ALL PASS", "screenshots in", OUT);
  process.exit(failures ? 1 : 0);
})();
