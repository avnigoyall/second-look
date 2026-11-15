// Popup: log in, the savings dashboard (always), "Check this item" on product pages, and Diagnose.
// All network calls go through the service worker.
const $ = (id) => document.getElementById(id);

function send(type, payload) {
  return chrome.runtime.sendMessage({ type, payload }).then((res) => {
    if (!res?.ok) throw new Error(res?.error || "Something went wrong");
    return res.data;
  });
}

function setStatus(id, text, isError = false) {
  $(id).textContent = text;
  $(id).classList.toggle("error", isError);
}

let authMode = "login"; // "login" | "signup", which form shows while logged out

// Account line: "email | (510) 573-3065". The phone comes from the login state, or from the iMessage status.
let whoEmail = null;
function setWho(email, phone) {
  if (email) whoEmail = email;
  $("who").textContent = phone ? `${whoEmail} | ${formatPhone(phone)}` : whoEmail || "";
}

function render({ loggedIn, email, phone }) {
  $("login-view").hidden = loggedIn || authMode !== "login";
  $("signup-view").hidden = loggedIn || authMode !== "signup";
  $("main-view").hidden = !loggedIn;
  $("logout").hidden = !loggedIn;
  if (loggedIn) setWho(email, phone);
  if (loggedIn && !pageModeLoaded) loadPageMode();
  if (loggedIn && !imsgLoaded) { imsgPolls = 0; loadImessage(); }
  if (!loggedIn) { imsgLoaded = false; clearTimeout(imsgPoll); $("imsg").hidden = true; }
}

// ---------- iMessage line (GET /imessage/status, POST /imessage/connect) ----------

let imsgLoaded = false;
let imsgPoll = null;
let imsgPolls = 0;
let imsgPhone = null; // the account phone from the last status
const IMSG_POLL_MS = 1500;
const IMSG_POLL_MAX = 20; // signup's background assignment usually takes a second or two

function lineLink(line) {
  const a = document.createElement("a");
  a.className = "imsg-line";
  a.href = `sms:${line}`;
  a.textContent = formatPhone(line);
  a.title = "Open Messages";
  return a;
}

// status: {status: none|assigning|pending_optin|connected|error, line, phone, message}
function renderImessage(s) {
  const box = $("imsg");
  box.hidden = false;
  box.dataset.state = s.status;
  imsgPhone = s.phone;
  if (s.phone) setWho(null, s.phone);
  const text = $("imsg-text");
  const hint = $("imsg-hint");
  hint.hidden = true;
  $("imsg-form").hidden = true;
  $("imsg-phone").hidden = Boolean(s.phone);
  $("imsg-connect").disabled = false;

  if (s.status === "connected") {
    text.replaceChildren("iMessage connected ", lineLink(s.line));
  } else if (s.status === "pending_optin") {
    text.replaceChildren("Text Second Look at ", lineLink(s.line), " to start.");
    hint.textContent = "Then send a photo of anything you are thinking about buying.";
    hint.hidden = false;
  } else if (s.status === "assigning") {
    text.textContent = "Setting up your iMessage line...";
  } else {
    // none or error: offer Connect (with a phone field when the account has no phone yet).
    text.textContent = s.status === "error" ? s.message : "Text photos to Second Look on iMessage.";
    $("imsg-connect").textContent = s.status === "error" ? "Try again" : "Connect iMessage";
    $("imsg-form").hidden = false;
  }

  clearTimeout(imsgPoll);
  if (s.status === "assigning" && imsgPolls++ < IMSG_POLL_MAX) imsgPoll = setTimeout(loadImessage, IMSG_POLL_MS);
}

async function loadImessage() {
  imsgLoaded = true;
  try {
    renderImessage(await send("IMESSAGE_STATUS"));
  } catch {
    $("imsg").hidden = true; // an older backend without /imessage/status: show nothing
  }
}

$("imsg-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const phone = $("imsg-phone").hidden ? null : $("imsg-phone").value.trim();
  if (phone === "") { $("imsg-phone").focus(); return; }
  $("imsg-connect").disabled = true;
  $("imsg-text").textContent = "Setting up your iMessage line...";
  try {
    renderImessage(await send("IMESSAGE_CONNECT", { phone }));
  } catch (err) {
    renderImessage({ status: "error", line: null, phone: imsgPhone, message: err.message });
  }
});

// ---------- product page vs savings summary ----------

let pageModeLoaded = false;

// The savings dashboard is always shown. Product pages add "Check this item" and Diagnose.
function showProductTools(isProduct) {
  $("product-tools").hidden = !isProduct;
  $("diag-section").hidden = !isProduct;
  $("check-anyway-row").hidden = isProduct;
}

async function loadPageMode() {
  pageModeLoaded = true;
  loadSummary();
  loadGoal();
  const kind = await send("PAGE_KIND").catch(() => ({ product: false }));
  showProductTools(kind.product);
}

const money = (n) => Number(n || 0).toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: n % 1 ? 2 : 0 });

// ---------- savings goal (GET/PUT/DELETE /goal) ----------
// Wording and limits match dashboard/goal.js: $5 to $100,000, a name of 1 to 40 characters.
const GOAL_MIN_CENTS = 500;
const GOAL_MAX_CENTS = 10000000;
let currentGoal = null;
let removeArmed = false;
let newGoalMode = false; // the form is starting a new goal after a reached one: it counts from zero

const dollars = (cents) => money(Math.floor(cents) / 100);

function renderGoal(goal) {
  currentGoal = goal;
  $("goal").hidden = false;
  $("goal-form").hidden = true;
  $("goal-start").hidden = Boolean(goal);
  $("goal-view").hidden = !goal;
  if (!goal) return;
  $("goal-title").textContent = goal.reached ? "you reached your goal!" : `saving for ${goal.name}`;
  $("goal-pct").textContent = `${goal.percent}%`;
  $("goal-fill").style.width = `${goal.percent}%`;
  $("goal-bar").setAttribute("aria-valuenow", String(goal.percent));
  $("goal-bar").classList.toggle("reached", goal.reached);
  $("goal-amounts").textContent = goal.reached
    ? `${dollars(goal.saved_cents)} saved for ${goal.name}`
    : `${dollars(goal.saved_cents)} of ${dollars(goal.target_cents)} saved`;
  $("goal-edit").hidden = goal.reached;
  $("goal-new").hidden = !goal.reached;
  if (goal.reached) celebrateOnce(goal);
}

// ---------- goal reached: confetti once per goal ----------
const celebratedKey = (goal) => `sl-goal-celebrated:${goal.name}|${goal.target_cents}`;
const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function celebrateOnce(goal) {
  try {
    if (localStorage.getItem(celebratedKey(goal))) return;
    localStorage.setItem(celebratedKey(goal), "1");
  } catch {
    // no storage: still celebrate this time
  }
  $("celebrate-text").textContent = `You saved ${dollars(goal.saved_cents)} for ${goal.name} by skipping.`;
  $("celebrate").hidden = false;
  $("celebrate-new").focus();
  if (!reducedMotion()) confetti($("confetti"));
}

function closeCelebrate() {
  $("celebrate").hidden = true;
}

// A short burst of falling paper in the popup's browns and beiges. Pointer events pass through.
function confetti(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const w = (canvas.width = window.innerWidth * dpr);
  const h = (canvas.height = window.innerHeight * dpr);
  const ctx = canvas.getContext("2d");
  const colors = ["#6b4a2f", "#a47551", "#d8c3a5", "#c9a227", "#7a8450", "#efe6d8"];
  const pieces = Array.from({ length: 140 }, () => ({
    x: w / 2 + (Math.random() - 0.5) * w * 0.3,
    y: h * 0.35,
    vx: (Math.random() - 0.5) * 14 * dpr,
    vy: (-Math.random() * 12 - 4) * dpr,
    size: (4 + Math.random() * 5) * dpr,
    turn: Math.random() * Math.PI,
    spin: (Math.random() - 0.5) * 0.3,
    color: colors[Math.floor(Math.random() * colors.length)],
  }));
  const start = performance.now();
  const DURATION = 2600;
  function frame(now) {
    const t = now - start;
    ctx.clearRect(0, 0, w, h);
    ctx.globalAlpha = Math.max(0, 1 - Math.max(0, t - DURATION + 700) / 700);
    for (const p of pieces) {
      p.vy += 0.35 * dpr;
      p.vx *= 0.99;
      p.x += p.vx;
      p.y += p.vy;
      p.turn += p.spin;
      ctx.save();
      ctx.translate(p.x, p.y);
      ctx.rotate(p.turn);
      ctx.fillStyle = p.color;
      ctx.fillRect(-p.size / 2, -p.size / 4, p.size, p.size / 2);
      ctx.restore();
    }
    if (t < DURATION) requestAnimationFrame(frame);
    else ctx.clearRect(0, 0, w, h);
  }
  requestAnimationFrame(frame);
}

function startNewGoal() {
  closeCelebrate();
  openGoalForm({ fresh: true });
}
$("goal-new").addEventListener("click", startNewGoal);
$("celebrate-new").addEventListener("click", startNewGoal);
$("celebrate-later").addEventListener("click", closeCelebrate);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("celebrate").hidden) closeCelebrate();
});

async function loadGoal() {
  try {
    const { goal } = await send("GOAL_GET");
    renderGoal(goal);
  } catch {
    $("goal").hidden = true; // the goal is extra: never an error on the dashboard
  }
}

function openGoalForm({ fresh = false } = {}) {
  removeArmed = false;
  newGoalMode = fresh;
  const editing = fresh ? null : currentGoal;
  $("goal-start").hidden = true;
  $("goal-view").hidden = true;
  $("goal-form").hidden = false;
  $("goal-name").value = editing?.name || "";
  $("goal-target").value = editing ? String(editing.target_cents / 100) : "";
  $("goal-remove").hidden = !editing;
  $("goal-remove").textContent = "remove goal";
  setStatus("goal-msg", "");
  $("goal-name").focus();
}

function parseTarget(text) {
  const n = Number(String(text).replace(/[$,\s]/g, ""));
  return Number.isFinite(n) && n > 0 ? Math.round(n * 100) : null;
}

$("goal-start").addEventListener("click", () => openGoalForm());
$("goal-edit").addEventListener("click", () => openGoalForm());
$("goal-cancel").addEventListener("click", () => renderGoal(currentGoal));

$("goal-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = $("goal-name").value.trim();
  const target = parseTarget($("goal-target").value);
  if (!name) return setStatus("goal-msg", "Give your goal a name.", true);
  if (target == null || target < GOAL_MIN_CENTS || target > GOAL_MAX_CENTS) {
    return setStatus("goal-msg", "Enter an amount from $5 to $100,000.", true);
  }
  const button = e.submitter;
  if (button) button.disabled = true;
  try {
    const { goal } = await send("GOAL_SET", { name, target_cents: target, fresh: newGoalMode });
    newGoalMode = false;
    renderGoal(goal);
  } catch (err) {
    setStatus("goal-msg", err.message, true);
  } finally {
    if (button) button.disabled = false;
  }
});

// Remove asks once more in place: your skips and savings stay either way.
$("goal-remove").addEventListener("click", async () => {
  if (!removeArmed) {
    removeArmed = true;
    $("goal-remove").textContent = "sure? remove (skips and savings stay)";
    return;
  }
  try {
    await send("GOAL_DELETE");
    renderGoal(null);
  } catch (err) {
    setStatus("goal-msg", err.message, true);
  }
});

async function loadSummary() {
  $("summary").classList.add("loading");
  try {
    const data = await send("IMPACT");
    $("money-kept").textContent = money(data.money_kept);
    $("money-source").hidden = data.money_source !== "nessie"; // the tile shows the real Savings balance
    const sub = document.createElement("sub");
    sub.textContent = "2";
    $("co2e").replaceChildren(`${Number(data.co2e_kg).toLocaleString("en-US", { maximumFractionDigits: 1 })} kg CO`, sub, "e");

    $("summary-detail").textContent = ""; // only errors show here

    const list = $("sources-list");
    list.replaceChildren();
    for (const s of data.co2e_sources || []) {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = s.url;
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = s.source;
      li.append(`${s.category}: ${s.kg_co2e} kg per item, `, a);
      list.append(li);
    }
    $("summary-sources").hidden = list.children.length === 0;
  } catch (err) {
    $("summary-detail").textContent = err.message;
  } finally {
    $("summary").classList.remove("loading");
  }
}

// "+15105550123" -> "(510) 555-0123". Other countries stay in E.164.
function formatPhone(e164) {
  const us = /^\+1(\d{3})(\d{3})(\d{4})$/.exec(e164 || "");
  return us ? `(${us[1]}) ${us[2]}-${us[3]}` : e164;
}

function showAuth(mode) {
  authMode = mode;
  setStatus("login-status", "");
  render({ loggedIn: false });
  $(mode === "signup" ? "su-email" : "email").focus();
}

// ---------- actions ----------

$("login-view").addEventListener("submit", async (e) => {
  e.preventDefault();
  const button = e.submitter;
  button.disabled = true;
  setStatus("login-status", "Logging in...");
  try {
    const { email, phone } = await send("LOGIN", { email: $("email").value.trim(), password: $("password").value });
    $("password").value = "";
    render({ loggedIn: true, email, phone });
    setStatus("login-status", "");
  } catch (err) {
    setStatus("login-status", err.message, true);
  } finally {
    button.disabled = false;
  }
});

$("signup-view").addEventListener("submit", async (e) => {
  e.preventDefault();
  // The savings goal is part of signing up: check it before the account exists.
  const goalName = $("su-goal-name").value.trim();
  const goalTarget = parseTarget($("su-goal-target").value);
  if (!goalName) return setStatus("login-status", "Give your savings goal a name.", true);
  if (goalTarget == null || goalTarget < GOAL_MIN_CENTS || goalTarget > GOAL_MAX_CENTS) {
    return setStatus("login-status", "Enter a goal amount from $5 to $100,000.", true);
  }
  const button = e.submitter;
  button.disabled = true;
  setStatus("login-status", "Creating your account...");
  try {
    const { email, phone } = await send("SIGNUP", {
      email: $("su-email").value.trim(),
      password: $("su-password").value,
      phone: $("su-phone").value.trim() || null,
    });
    $("su-password").value = "";
    let goalSaved = true;
    try {
      await send("GOAL_SET", { name: goalName, target_cents: goalTarget, fresh: true });
    } catch {
      goalSaved = false; // the account exists; the goal can be set from the dashboard
    }
    authMode = "login";
    render({ loggedIn: true, email, phone });
    setStatus("login-status", "");
    setStatus("status", goalSaved ? `Account created. Saving for ${goalName}.` : "Account created. Set your savings goal below.", !goalSaved);
  } catch (err) {
    setStatus("login-status", err.message, true);
  } finally {
    button.disabled = false;
  }
});

// Fallback when a store page is not detected: show the product tools anyway.
$("check-anyway").addEventListener("click", () => showProductTools(true));

// Opens the closet page in a new tab, already logged in. The popup closes on its own when the tab opens.
$("wardrobe").addEventListener("click", async () => {
  try {
    await send("OPEN_CLOSET");
  } catch (err) {
    setStatus("status", err.message, true);
  }
});

$("to-signup").addEventListener("click", () => showAuth("signup"));
$("to-login").addEventListener("click", () => showAuth("login"));

$("check").addEventListener("click", async () => {
  $("check").disabled = true;
  setStatus("status", "Checking on the page...");
  try {
    await send("CHECK_ACTIVE_TAB");
    setStatus("status", "The result shows on the page, top right.");
  } catch (err) {
    setStatus("status", err.message, true);
  } finally {
    $("check").disabled = false;
  }
});

$("diagnose").addEventListener("click", async () => {
  $("diagnose").disabled = true;
  $("diag-box").hidden = false;
  $("diag-summary").textContent = "Reading the page...";
  $("diag-summary").className = "small muted";
  $("diag-output").textContent = "";
  try {
    const report = await send("DIAGNOSE");
    console.log("[SecondLook] diagnose", report);
    const f = report.fields_found;
    const ok = report.tier_used != null;
    $("diag-summary").textContent = ok
      ? `Tier ${report.tier_used} · name ${f.name ? "yes" : "no"} · image ${f.image_url ? "yes" : "no"} · price ${f.price ? "yes" : "no"} · ${report.add_to_cart_candidates.length} add-to-cart`
      : `No product found · ${report.add_to_cart_candidates.length} add-to-cart`;
    $("diag-summary").className = `small ${ok ? "ok" : "bad"}`;
    $("diag-output").textContent = JSON.stringify(report, null, 2);
  } catch (err) {
    $("diag-summary").textContent = err.message;
    $("diag-summary").className = "small bad";
  } finally {
    $("diagnose").disabled = false;
  }
});

$("copy").addEventListener("click", async () => {
  const text = $("diag-output").textContent;
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    $("copy").textContent = "Copied";
  } catch {
    // Fallback: select the text so the user can press Cmd+C.
    const range = document.createRange();
    range.selectNodeContents($("diag-output"));
    getSelection().removeAllRanges();
    getSelection().addRange(range);
    $("copy").textContent = "Press Cmd+C";
  }
  setTimeout(() => { $("copy").textContent = "Copy JSON"; }, 1600);
});

$("logout").addEventListener("click", async () => {
  await send("LOGOUT");
  pageModeLoaded = false; // the next login reloads the page mode and this account's summary
  render({ loggedIn: false });
  setStatus("status", "");
});

// ---------- start ----------

send("GET_STATE").then(render).catch((err) => setStatus("login-status", err.message, true));
