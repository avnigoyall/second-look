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

function render({ loggedIn, email, phone }) {
  $("login-view").hidden = loggedIn || authMode !== "login";
  $("signup-view").hidden = loggedIn || authMode !== "signup";
  $("main-view").hidden = !loggedIn;
  $("logout").hidden = !loggedIn;
  if (loggedIn) $("who").textContent = phone ? `Logged in as ${email}\niMessage linked: ${formatPhone(phone)}` : `Logged in as ${email}`;
  if (loggedIn && !pageModeLoaded) loadPageMode();
}

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
  const kind = await send("PAGE_KIND").catch(() => ({ product: false }));
  showProductTools(kind.product);
}

const money = (n) => Number(n || 0).toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: n % 1 ? 2 : 0 });
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

async function loadSummary() {
  $("summary").classList.add("loading");
  try {
    const data = await send("IMPACT");
    $("money-kept").textContent = money(data.money_kept);
    const sub = document.createElement("sub");
    sub.textContent = "2";
    $("co2e").replaceChildren(`${Number(data.co2e_kg).toLocaleString("en-US", { maximumFractionDigits: 1 })} kg CO`, sub, "e");

    let detail;
    if (!data.items_skipped) {
      detail = "Nothing skipped yet. When you press Skip it on an item, its price and carbon footprint add up here.";
    } else {
      detail = `From ${plural(data.items_skipped, "skipped item")}.`;
      if (data.co2e_items_counted < data.items_skipped) {
        detail += ` CO2e counts ${data.co2e_items_counted} of them: only garment types with a published footprint are included.`;
      }
    }
    $("summary-detail").textContent = detail;

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
