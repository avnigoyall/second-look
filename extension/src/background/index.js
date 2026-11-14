// Second Look service worker. Every backend call goes through here, because content scripts
// on https pages cannot call http://localhost. Popup and content scripts send messages; this
// file routes them by type.
importScripts("/src/shared/state.js");

const API_BASE = "http://localhost:8000";

// Hardcoded item for the popup's "Send test item" button (Part 0 only).
const TEST_ITEM = {
  name: "Wide-leg barrel jeans",
  image_url: null,
  price: 59.5,
  store: "Test Store",
  url: "https://example.com/products/wide-leg-barrel-jeans",
};

async function api(path, { method = "GET", body } = {}) {
  const { token } = await SecondLookState.getState();
  const headers = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;

  let res;
  try {
    res = await fetch(API_BASE + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  } catch {
    throw new Error("Cannot reach the Second Look server. Is it running on localhost:8000?");
  }
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && token) {
    await SecondLookState.clearState();
    throw new Error("Your session expired. Log in again from the extension popup.");
  }
  if (!res.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : `Request failed (${res.status})`);
  }
  return data;
}

async function login({ email, password }) {
  const { token, user } = await api("/auth/login", { method: "POST", body: { email, password } });
  await SecondLookState.updateState({ token, email: user.email, profileType: user.profile_type });
  return { email: user.email };
}

async function analyzeItem({ item }) {
  return api("/analyze", { method: "POST", body: { item } });
}

async function sendTestItem() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) throw new Error("No active tab.");
  const result = await analyzeItem({ item: TEST_ITEM });
  try {
    await chrome.tabs.sendMessage(tab.id, { type: "SHOW_VERDICT", item: TEST_ITEM, result });
  } catch {
    throw new Error("Got a verdict, but this tab cannot show it. Reload the page (or open a normal website) and try again.");
  }
  return { verdict: result.verdict };
}

const handlers = {
  LOGIN: login,
  LOGOUT: () => SecondLookState.clearState(),
  GET_STATE: async () => {
    const { email, profileType, token } = await SecondLookState.getState();
    return { loggedIn: Boolean(token), email, profileType };
  },
  SEND_TEST_ITEM: sendTestItem,
  ANALYZE_ITEM: analyzeItem,
  SEND_ACTION: ({ verdictId, action }) =>
    api("/action", { method: "POST", body: { verdict_id: verdictId, action } }),
  SEND_FEEDBACK: ({ verdictId, value }) =>
    api("/feedback", { method: "POST", body: { verdict_id: verdictId, value } }),
};

// Every response has the shape { ok: true, data } or { ok: false, error }.
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const handler = handlers[message?.type];
  if (!handler) return false;
  Promise.resolve()
    .then(() => handler(message.payload || {}))
    .then((data) => sendResponse({ ok: true, data }))
    .catch((err) => sendResponse({ ok: false, error: err.message || String(err) }));
  return true; // keep the channel open for the async response
});
