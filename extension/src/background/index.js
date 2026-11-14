// Second Look service worker. Every backend call goes through here (content scripts on https pages
// cannot call http://localhost). Popup and content scripts send typed messages; this file routes them.
importScripts("/src/shared/state.js", "/src/shared/sessionStore.js");

const API_BASE = "http://localhost:8000";
const API_TIMEOUT_MS = 15000;
// The verdict engine can take about 10 s worst case (Claude retry included). Past this the card
// shows "Second look unavailable" instead of waiting.
const ANALYZE_TIMEOUT_MS = 12000;
// /read-price gives Claude 4 s; the rest is network margin. The reader then asks the shopper for the price.
const READ_PRICE_TIMEOUT_MS = 4800;
const TAB_LOAD_TIMEOUT_MS = 20000;
const LOG = "[SecondLook]";

// Must match the content_scripts order in manifest.json.
const READER_FILES = [
  "src/content/reader/price.js",
  "src/content/reader/jsonld.js",
  "src/content/reader/meta.js",
  "src/content/reader/domPrice.js",
  "src/content/reader/details.js",
  "src/content/reader/priceWindows.js",
  "src/content/reader/readItem.js",
  "src/content/matchers.js",
];
const CONTENT_FILES = [
  "src/shared/sessionStore.js",
  "src/content/shadowHost.js",
  ...READER_FILES,
  "src/content/card.styles.js",
  "src/content/card.js",
  "src/content/flow.js",
  "src/content/trigger.js",
  "src/content/removal.js",
];

// Errors carry a code so the card can choose between the error state and paste-a-link.
class AppError extends Error {
  constructor(message, code) {
    super(message);
    this.code = code;
  }
}

// onTiming(timing) receives {server_ms, request_ms, stages}: the X-Server-Ms and Server-Timing headers and
// the request's wall time seen from here, for the check's timing line.
async function api(path, { method = "GET", body, auth = true, timeoutMs = API_TIMEOUT_MS, onTiming } = {}) {
  const { token } = await SecondLookState.getState();
  if (auth && !token) throw new AppError("Log in from the Second Look popup first.", "auth");
  const headers = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const started = Date.now();
  let res;
  try {
    res = await fetch(API_BASE + path, {
      method, headers, body: body ? JSON.stringify(body) : undefined, signal: controller.signal,
    });
  } catch (err) {
    if (err.name === "AbortError") throw new AppError(`Second Look took longer than ${Math.round(timeoutMs / 1000)} seconds to answer.`, "timeout");
    throw new AppError("Cannot reach the Second Look server. Is it running on localhost:8000?", "offline");
  } finally {
    clearTimeout(timer);
  }
  const data = await res.json().catch(() => ({}));
  onTiming?.({
    server_ms: Number(res.headers.get("X-Server-Ms")) || null,
    request_ms: Date.now() - started,
    stages: (res.headers.get("Server-Timing") || "").replace(/;dur=/g, ":").replace(/\s+/g, "") || null,
  });
  if (res.status === 401 && auth) {
    await SecondLookState.clearState();
    throw new AppError("Your session expired. Log in again from the Second Look popup.", "auth");
  }
  if (!res.ok) {
    throw new AppError(typeof data.detail === "string" ? data.detail : `Request failed (${res.status})`, "server");
  }
  return data;
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) throw new AppError("No active tab.", "tab");
  if (!/^https?:/.test(tab.url || "")) throw new AppError("Open a store's product page first.", "tab");
  return tab;
}

// Resolves when the tab has finished loading a real page. Listens for the event AND polls,
// because a fast page can finish before the listener is attached.
function waitForTabComplete(tabId, timeoutMs) {
  return new Promise((resolve, reject) => {
    let done = false;
    const finish = (err) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      clearInterval(poll);
      chrome.tabs.onUpdated.removeListener(listener);
      err ? reject(err) : resolve();
    };
    const isLoaded = (tab) => tab.status === "complete" && tab.url && tab.url !== "about:blank";
    function listener(id, _info, tab) {
      if (id === tabId && isLoaded(tab)) finish();
    }
    const check = () => chrome.tabs.get(tabId).then((tab) => { if (isLoaded(tab)) finish(); })
      .catch(() => finish(new AppError("The page closed before it loaded.", "read")));
    const timer = setTimeout(() => finish(new AppError("That page took too long to load.", "read")), timeoutMs);
    const poll = setInterval(check, 250);
    chrome.tabs.onUpdated.addListener(listener);
    check();
  });
}

// ---------- handlers ----------

async function login({ email, password }) {
  const { token, user } = await api("/auth/login", { method: "POST", body: { email, password }, auth: false });
  await SecondLookState.updateState({ token, email: user.email, profileType: user.profile_type, phone: user.phone || null });
  return { email: user.email, phone: user.phone || null };
}

// Create an account. phone is optional; the backend stores it as E.164, which links the account to iMessage.
async function signup({ email, password, phone }) {
  const { token, user } = await api("/auth/signup", { method: "POST", body: { email, password, phone }, auth: false });
  await SecondLookState.updateState({ token, email: user.email, profileType: user.profile_type, phone: user.phone || null });
  return { email: user.email, phone: user.phone || null };
}

async function analyzeItem({ item }) {
  let timing = null;
  const result = await api("/analyze", {
    method: "POST", body: { item }, timeoutMs: ANALYZE_TIMEOUT_MS, onTiming: (t) => { timing = t; },
  });
  await SecondLookSession.add({ ...item, verdict_id: result.verdict_id, verdict: result.verdict });
  return { ...result, client_timing: timing };
}

// A timeout may still have reached the server, so the session records the action then too and
// the card shows the skip as pending. Other failures recorded nothing and are rethrown as is.
async function sendAction({ verdictId, action }) {
  let result;
  try {
    result = await api("/action", { method: "POST", body: { verdict_id: verdictId, action } });
  } catch (err) {
    if (err.code === "timeout") await markSession(verdictId, action);
    throw err;
  }
  await markSession(verdictId, action);
  return result;
}

async function markSession(verdictId, action) {
  const entry = (await SecondLookSession.list()).find((e) => e.verdict_id === verdictId);
  if (entry) await SecondLookSession.update(entry.id, { status: action }); // skipped | bought
}

// Paste-a-link: open the URL in a background tab, run readItem() there, then close the tab.
async function readUrl({ url }) {
  if (!/^https?:\/\//i.test(url || "")) throw new AppError("Paste a full product link that starts with https://", "read");
  const { token } = await SecondLookState.getState();
  if (!token) throw new AppError("Log in from the Second Look popup first.", "auth");

  const tab = await chrome.tabs.create({ url, active: false });
  try {
    await waitForTabComplete(tab.id, TAB_LOAD_TIMEOUT_MS);
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: READER_FILES });
    const [injection] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: async () => {
        await globalThis.SecondLookReader.waitForProductData(4000);
        return globalThis.SecondLookReader.readItem();
      },
    });
    console.log(LOG, "read from background tab", injection?.result);
    return injection?.result || null;
  } catch (err) {
    if (err instanceof AppError) throw err;
    console.warn(LOG, "background read failed", err);
    throw new AppError("We could not open that page. Check the link and try again.", "read");
  } finally {
    chrome.tabs.remove(tab.id).catch(() => {});
  }
}

// Popup "Check this item": ask the page to run the check. Tabs opened before the extension was
// installed have no content script yet, so inject it once and retry.
async function checkActiveTab() {
  const tab = await activeTab();
  try {
    await chrome.tabs.sendMessage(tab.id, { type: "CHECK_ITEM" });
  } catch {
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: CONTENT_FILES });
    await chrome.tabs.sendMessage(tab.id, { type: "CHECK_ITEM" });
  }
  return { started: true };
}

// Is the active tab a product page? Local signals only (no backend, no Gemini), so the popup opens fast:
// Product JSON-LD, og:type "product", a product price meta tag, or an Add to cart button (stores like Zara
// have no JSON-LD). Any other page (Google, news, chrome://) gets the savings summary instead of "Check this item".
async function pageKind() {
  let tab;
  try {
    tab = await activeTab();
  } catch {
    return { product: false, reason: "not a web page" };
  }
  try {
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["src/content/reader/price.js", "src/content/reader/jsonld.js", "src/content/matchers.js"],
    });
    const [injection] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => {
        const meta = (sel) => document.querySelector(sel)?.getAttribute("content")?.trim() || "";
        const jsonld = Boolean(globalThis.SecondLookJsonLd?.readJsonLd()?.name);
        const ogProduct = /product/i.test(meta('meta[property="og:type"]'));
        const priceMeta = Boolean(meta('meta[property="product:price:amount"]') || meta('meta[property="og:price:amount"]')
          || document.querySelector('[itemprop="price"]'));
        const m = globalThis.SecondLookMatchers;
        const addToCart = Boolean(m && m.candidates(m.isAddToCart).length);
        return { product: jsonld || ogProduct || priceMeta || addToCart, signals: { jsonld, ogProduct, priceMeta, addToCart } };
      },
    });
    return injection?.result ?? { product: false, reason: "no result" };
  } catch (err) {
    // Pages the extension cannot script (Chrome Web Store, PDF viewer) are not stores.
    return { product: false, reason: String(err?.message || err).slice(0, 120) };
  }
}

async function diagnose() {
  const tab = await activeTab();
  await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: [...READER_FILES, "src/content/diagnose.js"] });
  const [injection] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: () => globalThis.SecondLookDiagnose.diagnose(),
  });
  return injection?.result;
}

const handlers = {
  LOGIN: login,
  SIGNUP: signup,
  LOGOUT: () => SecondLookState.clearState(),
  GET_STATE: async () => {
    const { email, profileType, token, phone } = await SecondLookState.getState();
    return { loggedIn: Boolean(token), email, profileType, phone };
  },
  CHECK_ACTIVE_TAB: checkActiveTab,
  PAGE_KIND: pageKind,
  IMPACT: () => api("/impact"),
  DIAGNOSE: diagnose,
  ANALYZE_ITEM: analyzeItem,
  READ_PAGE: ({ url, text, image_url }) => api("/read-page", { method: "POST", body: { url, text, image_url } }),
  READ_PRICE: ({ url, name, windows }) =>
    api("/read-price", { method: "POST", body: { url, name, windows }, timeoutMs: READ_PRICE_TIMEOUT_MS }),
  READ_URL: readUrl,
  SEND_ACTION: sendAction,
  // Full analysis after a verdict: materials, lifespan, cost per wear, closet sentiment, outfits.
  ANALYZE_DETAILS: ({ verdictId }) =>
    api("/analyze/details", { method: "POST", body: { verdict_id: verdictId }, timeoutMs: 25000 }),
  SEND_FEEDBACK: ({ verdictId, value }) =>
    api("/feedback", { method: "POST", body: { verdict_id: verdictId, value } }),
  // Small-closet ownership question (Part 3). Only used when /analyze sends no attributes.
  GET_ATTRIBUTES: ({ name, image_url }) => api("/attributes", { method: "POST", body: { name, image_url } }),
  OWNERSHIP_OPTIONS: ({ name, attributes }) =>
    api("/closet/ownership-options", { method: "POST", body: { name, attributes } }),
  OWNERSHIP_ANSWER: ({ attributes, name }) =>
    api("/closet/ownership-answer", { method: "POST", body: { attributes, name } }),
};

// Every response has the shape { ok: true, data } or { ok: false, error, code }.
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const handler = handlers[message?.type];
  if (!handler) return false;
  Promise.resolve()
    .then(() => handler(message.payload || {}))
    .then((data) => sendResponse({ ok: true, data }))
    .catch((err) => {
      console.warn(LOG, message.type, "failed:", err.message);
      sendResponse({ ok: false, error: err.message || String(err), code: err.code || "error" });
    });
  return true; // keep the channel open for the async response
});
