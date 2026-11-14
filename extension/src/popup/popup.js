// Popup: log in, then send a hardcoded test item. All network calls go through the service worker.
const $ = (id) => document.getElementById(id);
const LABELS = { have_this_look: "You have this look", micro_trend: "Micro trend", good_buy: "Good buy" };

function send(type, payload) {
  return chrome.runtime.sendMessage({ type, payload }).then((res) => {
    if (!res?.ok) throw new Error(res?.error || "Something went wrong");
    return res.data;
  });
}

function setStatus(text, isError = false) {
  $("status").textContent = text;
  $("status").classList.toggle("error", isError);
}

function render({ loggedIn, email }) {
  $("login-view").hidden = loggedIn;
  $("main-view").hidden = !loggedIn;
  if (loggedIn) $("who").textContent = `Logged in as ${email}`;
}

$("login-view").addEventListener("submit", async (e) => {
  e.preventDefault();
  const button = e.submitter;
  button.disabled = true;
  setStatus("Logging in...");
  try {
    const { email } = await send("LOGIN", { email: $("email").value.trim(), password: $("password").value });
    $("password").value = "";
    render({ loggedIn: true, email });
    setStatus("");
  } catch (err) {
    setStatus(err.message, true);
  } finally {
    button.disabled = false;
  }
});

$("send-test").addEventListener("click", async () => {
  $("send-test").disabled = true;
  setStatus("Checking test item...");
  try {
    const { verdict } = await send("SEND_TEST_ITEM");
    setStatus(`Verdict shown on the page: ${LABELS[verdict] || verdict}.`);
  } catch (err) {
    setStatus(err.message, true);
    // The service worker clears the token on an expired session.
    render(await send("GET_STATE"));
  } finally {
    $("send-test").disabled = false;
  }
});

$("logout").addEventListener("click", async () => {
  await send("LOGOUT");
  render({ loggedIn: false });
  setStatus("");
});

send("GET_STATE").then(render).catch((err) => setStatus(err.message, true));
