/* Second Look savings goal (CONTRACT.md section 15).
   Mounted by one script tag in dashboard/index.html. Injects goal.css and a
   <section class="goal"> at the top of #dash-view, loads when the dashboard
   is shown, hides itself whenever GET /goal fails. Never throws into the page.
   All user text goes through textContent, never innerHTML. */
(() => {
  "use strict";

  const TOKEN_KEY = "secondLookToken"; // same key index.html uses
  const CELEBRATED_KEY = "secondLookGoalCelebrated";
  const NAME_MAX = 40;
  const TARGET_MIN = 500;       // cents, $5
  const TARGET_MAX = 10000000;  // cents, $100,000
  const SVG_NS = "http://www.w3.org/2000/svg";
  const ICONS = {
    plus: "M12 5v14M5 12h14",
    pencil: "M4 20h4L18.5 9.5a2.12 2.12 0 0 0-4-4L4 16v4zM13.5 6.5l4 4",
    chevron: "m6 9 6 6 6-6",
    check: "M20 6 9 17l-5-5",
    tag: "M12.6 2.6A2 2 0 0 0 11.2 2H4a2 2 0 0 0-2 2v7.2a2 2 0 0 0 .6 1.4l8.7 8.7a2.4 2.4 0 0 0 3.4 0l6.6-6.6a2.4 2.4 0 0 0 0-3.4zM7.5 7.5h.01",
  };

  // ---------- small helpers ----------
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : String(v));
    }
    for (const c of children) if (c != null) el.append(c);
    return el;
  }
  function icon(name, cls) {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    if (cls) svg.setAttribute("class", cls);
    const p = document.createElementNS(SVG_NS, "path");
    p.setAttribute("d", ICONS[name]);
    svg.append(p);
    return svg;
  }
  const usd = (n, digits) => new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD", minimumFractionDigits: digits, maximumFractionDigits: digits,
  }).format(n);
  // Whole dollars for the bar. Saved rounds down so "$2,000 / $2,000" never shows before it is reached.
  const dollarsDown = (cents) => usd(Math.floor(cents / 100), 0);
  const dollarsUp = (cents) => usd(Math.ceil(cents / 100), 0);
  const price = (cents) => usd(cents / 100, cents % 100 === 0 ? 0 : 2);

  function getToken() { try { return localStorage.getItem(TOKEN_KEY); } catch { return null; } }
  function storageGet(k) { try { return sessionStorage.getItem(k); } catch { return null; } }
  function storageSet(k, v) { try { sessionStorage.setItem(k, v); } catch { /* private mode */ } }

  async function api(path, options = {}) {
    const headers = { "Content-Type": "application/json" };
    const token = getToken();
    if (token) headers.Authorization = "Bearer " + token;
    const res = await fetch(path, { ...options, headers });
    const body = await res.json().catch(() => null);
    if (!res.ok) {
      const err = new Error(detailMessage(body) || "Request failed (" + res.status + ")");
      err.status = res.status;
      throw err;
    }
    return body;
  }
  // FastAPI 422s carry either a string detail or a list of {msg}.
  function detailMessage(body) {
    const d = body && body.detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d) && d[0] && typeof d[0].msg === "string") return d[0].msg.replace(/^Value error, /, "");
    return "";
  }

  function validGoal(g) {
    return g && typeof g === "object" && typeof g.name === "string" && g.name.length > 0 &&
      Number.isFinite(g.target_cents) && g.target_cents > 0 &&
      Number.isFinite(g.saved_cents) && g.saved_cents >= 0 && typeof g.reached === "boolean";
  }
  function parseGoalResponse(body) {
    if (!body || typeof body !== "object" || !("goal" in body)) throw new Error("malformed");
    if (body.goal === null) return null;
    if (!validGoal(body.goal)) throw new Error("malformed");
    return body.goal;
  }
  function safeImageUrl(u) {
    if (typeof u !== "string" || !u) return null;
    try {
      const url = new URL(u, location.href);
      return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
    } catch { return null; }
  }
  function formatDate(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    const sameYear = d.getFullYear() === new Date().getFullYear();
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: sameYear ? undefined : "numeric" });
  }

  // ---------- state ----------
  const state = { goal: null, listOpen: false, panelOpen: false, loadSeq: 0, itemsSeq: 0, celebratedHere: false };

  // ---------- DOM ----------
  const root = h("section", { class: "goal", id: "goal", "aria-label": "Savings goal", hidden: true });

  const setBtn = h("button", { type: "button", class: "goal-btn goal-set", "aria-expanded": "false", "aria-controls": "goal-panel" },
    icon("plus"), h("span", { text: "Set a goal" }));

  const labelName = h("span", { class: "goal-label-name", id: "goal-label" });
  const labelPct = h("span", { class: "goal-label-pct", "aria-hidden": "true" });
  const hint = h("span", { class: "goal-sr", id: "goal-hint" });

  const fill = h("span", { class: "goal-fill" });
  const textUnder = h("span", { class: "goal-text", "aria-hidden": "true" });
  const textOver = h("span", { class: "goal-text on-fill", "aria-hidden": "true" });
  const sparks = h("span", { class: "goal-sparks", "aria-hidden": "true" }, h("i"), h("i"), h("i"), h("i"), h("i"));
  const bar = h("div", {
    class: "goal-bar", role: "progressbar", tabindex: "0",
    "aria-labelledby": "goal-label", "aria-describedby": "goal-hint", "aria-valuemin": "0",
    "data-open": "false",
  }, fill, h("span", { class: "goal-sheen", "aria-hidden": "true" }), textUnder, textOver, sparks);

  const editBtn = h("button", {
    type: "button", class: "goal-edit", "aria-label": "Edit goal", title: "Edit goal",
    "aria-expanded": "false", "aria-controls": "goal-panel",
  }, icon("pencil"));

  const goalView = h("div", { class: "goal-view" },
    h("p", { class: "goal-label" }, labelName, labelPct),
    h("div", { class: "goal-row" }, bar, editBtn),
    hint);

  // Edit panel
  const panelTitle = h("h3", { id: "goal-panel-title" });
  const nameInput = h("input", { id: "goal-name", type: "text", autocomplete: "off", maxlength: String(NAME_MAX + 20), "aria-describedby": "goal-msg" });
  const targetInput = h("input", { id: "goal-target", type: "text", inputmode: "decimal", autocomplete: "off", placeholder: "2,000", "aria-describedby": "goal-msg" });
  const msg = h("p", { class: "goal-msg", id: "goal-msg", role: "alert" });
  const saveBtn = h("button", { type: "submit", class: "goal-btn" }, "Save");
  const cancelBtn = h("button", { type: "button", class: "goal-btn ghost" }, "Cancel");
  const deleteBtn = h("button", { type: "button", class: "goal-btn danger" }, "Delete");
  const actions = h("div", { class: "goal-actions" }, saveBtn, cancelBtn, h("span", { class: "goal-spacer" }), deleteBtn);
  const confirmYes = h("button", { type: "button", class: "goal-btn danger-solid" }, "Delete goal");
  const confirmNo = h("button", { type: "button", class: "goal-btn ghost" }, "Keep it");
  const confirmRow = h("div", { class: "goal-actions", hidden: true },
    h("p", { class: "goal-confirm-text" }, h("strong", { text: "Are you sure? " }), h("span", { text: "Your skips and savings stay." })),
    confirmYes, confirmNo);
  const form = h("form", { class: "goal-form", novalidate: true },
    h("div", { class: "goal-fields" },
      h("label", { class: "goal-field", for: "goal-name" }, "Goal name", nameInput),
      h("label", { class: "goal-field", for: "goal-target" }, "Target",
        h("span", { class: "goal-money" }, h("span", { "aria-hidden": "true", text: "$" }), targetInput))),
    msg, actions, confirmRow);
  const panel = h("div", { class: "goal-panel", id: "goal-panel", role: "group", "aria-labelledby": "goal-panel-title", hidden: true }, panelTitle, form);

  // Skipped items
  const items = h("div", { class: "goal-items", id: "goal-items", role: "region", "aria-label": "Skipped items", tabindex: "0", hidden: true });

  root.append(setBtn, goalView, panel, items);

  // ---------- render ----------
  function render() {
    const g = state.goal;
    setBtn.hidden = !!g;
    goalView.hidden = !g;
    root.dataset.reached = String(!!(g && g.reached));
    if (!g) { closeList(); return; }

    const pct = g.reached ? 100 : Math.max(0, Math.min(100, Math.floor(g.saved_cents * 100 / g.target_cents)));
    const main = g.reached ? g.name + ": you made it" : dollarsDown(g.saved_cents) + " / " + dollarsUp(g.target_cents);
    bar.style.setProperty("--p", pct + "%");
    bar.setAttribute("aria-valuemax", String(g.target_cents));
    bar.setAttribute("aria-valuenow", String(Math.min(g.saved_cents, g.target_cents)));
    bar.setAttribute("aria-valuetext", g.reached
      ? "Goal reached, " + dollarsDown(g.saved_cents) + " saved"
      : dollarsDown(g.saved_cents) + " of " + dollarsUp(g.target_cents) + ", " + pct + " percent");
    for (const layer of [textUnder, textOver]) {
      layer.replaceChildren(...[
        g.reached ? icon("check") : null,
        h("span", { class: "goal-text-main", text: main }),
        icon("chevron", "goal-chevron"),
      ].filter(Boolean));
    }
    labelName.replaceChildren(
      document.createTextNode(g.reached ? "Goal reached: " : "Saving for "),
      h("strong", { text: g.name }));
    labelPct.textContent = g.reached ? "100%" : pct + "%";
    updateHint();

    if (g.reached) maybeCelebrate();
  }

  function maybeCelebrate() {
    if (state.celebratedHere || storageGet(CELEBRATED_KEY)) return;
    state.celebratedHere = true;
    storageSet(CELEBRATED_KEY, "1");
    root.dataset.celebrate = "true";
    setTimeout(() => { delete root.dataset.celebrate; }, 2000);
  }

  function updateHint() {
    hint.textContent = state.listOpen ? "Press Enter to hide skipped items." : "Press Enter to show skipped items.";
    bar.dataset.open = String(state.listOpen);
  }

  // ---------- load ----------
  function hide() {
    root.hidden = true;
    closePanel(false);
    closeList();
  }

  async function load() {
    const seq = ++state.loadSeq;
    if (!getToken()) { hide(); return; }
    try {
      const goal = parseGoalResponse(await api("/goal"));
      if (seq !== state.loadSeq) return;
      state.goal = goal;
      render();
      root.hidden = false;
    } catch {
      if (seq === state.loadSeq) { state.goal = null; hide(); }
    }
  }

  // ---------- list ----------
  function closeList() {
    state.listOpen = false;
    state.itemsSeq++;
    items.hidden = true;
    updateHint();
  }

  async function toggleList() {
    if (state.listOpen) { closeList(); return; }
    state.listOpen = true;
    updateHint();
    items.hidden = false;
    items.replaceChildren(h("p", { class: "goal-items-status", text: "Loading skipped items" }));
    const seq = ++state.itemsSeq;
    try {
      const body = await api("/goal/items");
      if (seq !== state.itemsSeq) return;
      const list = body && Array.isArray(body.items) ? body.items : null;
      if (!list) throw new Error("malformed");
      renderItems(list);
    } catch {
      if (seq !== state.itemsSeq) return;
      items.replaceChildren(h("p", { class: "goal-items-status", text: "Could not load your skipped items. Try again in a moment." }));
    }
  }

  function thumb(url) {
    const tile = h("span", { class: "goal-thumb" });
    const fallback = () => tile.replaceChildren(icon("tag"));
    const src = safeImageUrl(url);
    if (!src) { fallback(); return tile; }
    const img = h("img", { alt: "", loading: "lazy", decoding: "async", referrerpolicy: "no-referrer" });
    img.addEventListener("error", fallback, { once: true });
    img.src = src;
    tile.append(img);
    return tile;
  }

  function renderItems(list) {
    if (!list.length) {
      items.replaceChildren(h("p", { class: "goal-items-status", text: "No skipped items yet. Skip something from the extension and it shows up here." }));
      return;
    }
    const ul = h("ul", { class: "goal-list" });
    for (const it of list) {
      if (!it || typeof it !== "object") continue;
      const name = typeof it.name === "string" && it.name ? it.name : "Untitled item";
      const store = typeof it.store === "string" ? it.store : "";
      const cents = Number.isFinite(it.price_cents) ? it.price_cents : null;
      ul.append(h("li", null,
        thumb(it.image_url),
        h("span", { class: "goal-item-name", title: name, text: name }),
        h("span", { class: "goal-item-price", text: cents != null ? price(cents) : "" }),
        h("span", { class: "goal-item-meta", text: store }),
        h("span", { class: "goal-item-date", text: formatDate(it.skipped_at) })));
    }
    items.replaceChildren(ul);
  }

  // ---------- panel ----------
  function openPanel() {
    const g = state.goal;
    panelTitle.textContent = g ? "Edit goal" : "Set a goal";
    nameInput.value = g ? g.name : "";
    targetInput.value = g ? String(g.target_cents % 100 === 0 ? g.target_cents / 100 : (g.target_cents / 100).toFixed(2)) : "";
    deleteBtn.hidden = !g;
    setConfirm(false);
    setMsg("");
    state.panelOpen = true;
    panel.hidden = false;
    setBtn.setAttribute("aria-expanded", "true");
    editBtn.setAttribute("aria-expanded", "true");
    nameInput.focus();
  }

  function closePanel(restoreFocus) {
    if (!state.panelOpen) return;
    state.panelOpen = false;
    panel.hidden = true;
    setBtn.setAttribute("aria-expanded", "false");
    editBtn.setAttribute("aria-expanded", "false");
    if (restoreFocus) (state.goal ? editBtn : setBtn).focus();
  }

  function setConfirm(on) {
    actions.hidden = on;
    confirmRow.hidden = !on;
    nameInput.disabled = on;
    targetInput.disabled = on;
    if (on) confirmNo.focus();
  }

  function setMsg(text, field) {
    msg.textContent = text;
    for (const [input, key] of [[nameInput, "name"], [targetInput, "target"]]) {
      if (field === key) input.setAttribute("aria-invalid", "true");
      else input.removeAttribute("aria-invalid");
    }
  }

  function readForm() {
    const name = nameInput.value.trim();
    if (!name) return { error: "Give your goal a name.", field: "name" };
    if (name.length > NAME_MAX) return { error: "Keep the name to " + NAME_MAX + " characters or fewer.", field: "name" };
    const raw = targetInput.value.replace(/[$,\s]/g, "");
    if (!/^\d+(\.\d{0,2})?$/.test(raw)) return { error: "Enter the target as a dollar amount, like 2,000.", field: "target" };
    const cents = Math.round(Number(raw) * 100);
    if (cents < TARGET_MIN || cents > TARGET_MAX) return { error: "Pick a target from $5 to $100,000.", field: "target" };
    return { name, target_cents: cents };
  }

  function busy(on) {
    for (const b of [saveBtn, cancelBtn, deleteBtn, confirmYes, confirmNo]) b.disabled = on;
  }

  async function save(e) {
    e.preventDefault();
    if (!confirmRow.hidden) return;
    const v = readForm();
    if (v.error) {
      setMsg(v.error, v.field);
      (v.field === "name" ? nameInput : targetInput).focus();
      return;
    }
    setMsg("");
    busy(true);
    try {
      const goal = parseGoalResponse(await api("/goal", { method: "PUT", body: JSON.stringify(v) }));
      state.goal = goal;
      closePanel(false);
      render();
      (state.goal ? editBtn : setBtn).focus();
      if (state.listOpen) { state.listOpen = false; toggleList(); }
    } catch (err) {
      if (err.status === 401) { hide(); return; }
      setMsg(err.status === 422 ? (err.message || "Check the name and target.") : "Could not save the goal. Try again.");
    } finally {
      busy(false);
    }
  }

  async function remove() {
    busy(true);
    try {
      await api("/goal", { method: "DELETE" });
      state.goal = null;
      closePanel(false);
      render();
      setBtn.focus();
    } catch (err) {
      if (err.status === 401) { hide(); return; }
      setConfirm(false);
      setMsg("Could not delete the goal. Try again.");
    } finally {
      busy(false);
    }
  }

  // ---------- events ----------
  setBtn.addEventListener("click", () => (state.panelOpen ? closePanel(true) : openPanel()));
  editBtn.addEventListener("click", () => (state.panelOpen ? closePanel(true) : openPanel()));
  bar.addEventListener("click", () => { toggleList(); });
  bar.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleList(); }
  });
  form.addEventListener("submit", save);
  cancelBtn.addEventListener("click", () => closePanel(true));
  deleteBtn.addEventListener("click", () => { setMsg(""); setConfirm(true); });
  confirmNo.addEventListener("click", () => { setConfirm(false); deleteBtn.focus(); });
  confirmYes.addEventListener("click", remove);
  panel.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    e.preventDefault();
    if (!confirmRow.hidden) { setConfirm(false); deleteBtn.focus(); } else closePanel(true);
  });
  for (const input of [nameInput, targetInput]) {
    input.addEventListener("input", () => { if (msg.textContent) setMsg(""); });
  }

  // ---------- mount ----------
  function mount() {
    try {
      const css = h("link", { rel: "stylesheet", href: "/dashboard-assets/goal.css" });
      document.head.append(css);
      const dash = document.getElementById("dash-view");
      if (!dash) return;
      dash.prepend(root);
      let wasVisible = false;
      const sync = () => {
        const visible = !dash.hidden;
        if (visible && !wasVisible) load();
        if (!visible && wasVisible) { state.loadSeq++; state.goal = null; hide(); }
        wasVisible = visible;
      };
      new MutationObserver(sync).observe(dash, { attributes: true, attributeFilter: ["hidden"] });
      sync();
    } catch { /* never break the dashboard */ }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount, { once: true });
  else mount();
})();
