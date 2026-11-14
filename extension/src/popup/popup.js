// Popup: log in, "Check this item", the session list (manual removal fallback), and Diagnose.
// All network calls go through the service worker.
const $ = (id) => document.getElementById(id);
const STATUS_LABELS = { checked: "Checked", skipped: "Skipped", bought: "Bought", removed: "Removed" };
const X_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>';

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

function render({ loggedIn, email, phone }) {
  $("login-view").hidden = loggedIn || authMode !== "login";
  $("signup-view").hidden = loggedIn || authMode !== "signup";
  $("main-view").hidden = !loggedIn;
  $("logout").hidden = !loggedIn;
  if (loggedIn) $("who").textContent = phone ? `Logged in as ${email}\niMessage linked: ${formatPhone(phone)}` : `Logged in as ${email}`;
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

// ---------- session list ----------

function renderSession(entries) {
  const list = $("session-list");
  list.replaceChildren();
  $("session-empty").hidden = entries.length > 0;
  const active = entries.filter((e) => e.status !== "removed").length;
  $("session-count").textContent = entries.length ? `${active} in cart` : "";

  for (const entry of entries) {
    const li = document.createElement("li");
    if (entry.status === "removed") li.className = "removed";

    let thumb;
    if (entry.image_url) {
      thumb = document.createElement("img");
      thumb.src = entry.image_url;
      thumb.alt = "";
    } else {
      thumb = document.createElement("div");
      thumb.className = "ph";
    }

    const text = document.createElement("div");
    text.className = "text";
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = entry.name;
    name.title = entry.name;
    const meta = document.createElement("div");
    meta.className = "meta";
    const price = document.createElement("span");
    price.textContent = entry.price != null ? `$${Number(entry.price).toFixed(2)}` : "No price";
    const chip = document.createElement("span");
    chip.className = `chip ${entry.status}`;
    chip.textContent = STATUS_LABELS[entry.status] || entry.status;
    meta.append(price, chip);
    text.append(name, meta);

    li.append(thumb, text);
    if (entry.status !== "removed") {
      const x = document.createElement("button");
      x.className = "x";
      x.innerHTML = X_ICON;
      x.setAttribute("aria-label", `Mark ${entry.name} as removed`);
      x.title = "Mark as removed";
      x.addEventListener("click", () => SecondLookSession.markRemoved(entry.id));
      li.append(x);
    } else {
      li.append(document.createElement("span"));
    }
    list.append(li);
  }
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
    authMode = "login";
    render({ loggedIn: true, email, phone });
    setStatus("login-status", "");
    setStatus("status", phone ? "Account created. You can also text photos to Second Look on iMessage." : "Account created.");
  } catch (err) {
    setStatus("login-status", err.message, true);
  } finally {
    button.disabled = false;
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
  render({ loggedIn: false });
  setStatus("status", "");
});

// ---------- start ----------

send("GET_STATE").then(render).catch((err) => setStatus("login-status", err.message, true));
SecondLookSession.list().then(renderSession);
SecondLookSession.subscribe(renderSession);
