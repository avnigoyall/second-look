// Items checked in this shopping session, stored in chrome.storage.local under "secondLookSession".
// Entry: {id, name, price, image_url, store, url, verdict_id, verdict, status, ts}
// status is one of: checked, removed, skipped, bought.
(function (global) {
  "use strict";

  const STORAGE_KEY = "secondLookSession";
  const MAX_ENTRIES = 50;

  async function list() {
    const result = await chrome.storage.local.get(STORAGE_KEY);
    return Array.isArray(result[STORAGE_KEY]) ? result[STORAGE_KEY] : [];
  }

  async function save(entries) {
    await chrome.storage.local.set({ [STORAGE_KEY]: entries.slice(0, MAX_ENTRIES) });
  }

  async function add(fields) {
    const entry = {
      id: crypto.randomUUID(),
      name: fields.name,
      price: fields.price ?? null,
      image_url: fields.image_url ?? null,
      store: fields.store ?? null,
      url: fields.url ?? null,
      verdict_id: fields.verdict_id ?? null,
      verdict: fields.verdict ?? null,
      status: "checked",
      ts: Date.now(),
    };
    await save([entry, ...(await list())]); // newest first
    return entry;
  }

  async function update(id, partial) {
    const entries = await list();
    const i = entries.findIndex((e) => e.id === id);
    if (i < 0) return null;
    entries[i] = { ...entries[i], ...partial, id };
    await save(entries);
    return entries[i];
  }

  async function markRemoved(id) {
    return update(id, { status: "removed" });
  }

  function subscribe(callback) {
    const listener = (changes, area) => {
      if (area === "local" && changes[STORAGE_KEY]) callback(changes[STORAGE_KEY].newValue || []);
    };
    chrome.storage.onChanged.addListener(listener);
    return () => chrome.storage.onChanged.removeListener(listener);
  }

  global.SecondLookSession = { STORAGE_KEY, add, update, list, markRemoved, subscribe };
})(globalThis);
