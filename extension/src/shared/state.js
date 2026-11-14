// Login state stored in chrome.storage.local under "secondLookState".
// Same get / update / subscribe pattern as Syft's settings.js, with Second Look's own fields.
(function (global) {
  "use strict";

  const STORAGE_KEY = "secondLookState";
  const DEFAULT_STATE = Object.freeze({
    token: null,
    email: null,
    profileType: null,
  });

  function normalizeState(state) {
    const input = state && typeof state === "object" ? state : {};
    const str = (v) => (typeof v === "string" && v ? v : null);
    return {
      token: str(input.token),
      email: str(input.email),
      profileType: str(input.profileType),
    };
  }

  async function getState() {
    const result = await chrome.storage.local.get(STORAGE_KEY);
    return normalizeState({ ...DEFAULT_STATE, ...(result[STORAGE_KEY] || {}) });
  }

  async function updateState(partial) {
    const next = normalizeState({ ...(await getState()), ...(partial || {}) });
    await chrome.storage.local.set({ [STORAGE_KEY]: next });
    return next;
  }

  async function clearState() {
    await chrome.storage.local.set({ [STORAGE_KEY]: { ...DEFAULT_STATE } });
    return { ...DEFAULT_STATE };
  }

  function subscribeToState(callback) {
    if (typeof callback !== "function") return () => {};
    const listener = (changes, areaName) => {
      if (areaName !== "local" || !changes[STORAGE_KEY]) return;
      callback(normalizeState(changes[STORAGE_KEY].newValue));
    };
    chrome.storage.onChanged.addListener(listener);
    return () => chrome.storage.onChanged.removeListener(listener);
  }

  global.SecondLookState = { STORAGE_KEY, DEFAULT_STATE, getState, updateState, clearState, subscribeToState };
})(globalThis);
