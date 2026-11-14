// Verdict toast, rendered inside the Second Look shadow root. Listens for SHOW_VERDICT from the
// service worker. Buttons send SEND_ACTION / SEND_FEEDBACK back through the service worker.
(function () {
  "use strict";

  const LABELS = {
    have_this_look: "You have this look",
    micro_trend: "Micro trend",
    good_buy: "Good buy",
  };

  const CSS = `
    :host { all: initial; }
    .toast {
      --bg: #ffffff; --ink: #1d1b18; --muted: #6b665e; --line: #e7e2d9; --soft: #f4f1ea;
      --accent: #2f6b4f; --accent-ink: #ffffff; --danger: #a33a2b;
      --have: #3b5ba5; --trend: #a8620a; --good: #2f7d4f;
      position: fixed; right: 20px; bottom: 20px; width: 340px; max-width: calc(100vw - 32px);
      box-sizing: border-box; padding: 16px; border-radius: 12px;
      background: var(--bg); color: var(--ink); border: 1px solid var(--line);
      box-shadow: 0 12px 32px rgba(0, 0, 0, 0.18), 0 2px 6px rgba(0, 0, 0, 0.08);
      font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
      pointer-events: auto;
      animation: enter 180ms ease-out;
    }
    @media (prefers-color-scheme: dark) {
      .toast {
        --bg: #1f1e1b; --ink: #f1ede6; --muted: #a39d93; --line: #34312c; --soft: #2a2825;
        --accent: #5fae86; --accent-ink: #10221a; --danger: #e07a6a;
        --have: #8aa6e8; --trend: #e6a454; --good: #6cc495;
      }
    }
    @media (prefers-reduced-motion: reduce) { .toast { animation: none; } }
    @keyframes enter { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
    .top { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; }
    .brand { font-size: 12px; font-weight: 600; color: var(--muted); letter-spacing: 0.02em; }
    .close { all: unset; cursor: pointer; width: 24px; height: 24px; display: grid; place-items: center;
             border-radius: 6px; color: var(--muted); font-size: 18px; line-height: 1; }
    .close:hover { background: var(--soft); color: var(--ink); }
    .badge { display: inline-block; font-size: 12px; font-weight: 600; padding: 2px 8px;
             border-radius: 999px; border: 1px solid currentColor; }
    .badge.have_this_look { color: var(--have); }
    .badge.micro_trend { color: var(--trend); }
    .badge.good_buy { color: var(--good); }
    .item { font-weight: 600; margin: 8px 0 2px; }
    .reason { margin: 0 0 12px; color: var(--ink); }
    .alt { margin: 0 0 12px; padding: 8px 10px; border-radius: 8px; background: var(--soft); font-size: 13px; }
    .alt b { font-weight: 600; }
    .row { display: flex; gap: 8px; align-items: center; }
    button.btn { all: unset; cursor: pointer; box-sizing: border-box; text-align: center;
                 padding: 8px 12px; border-radius: 8px; font-weight: 600; font-size: 13px; }
    button.primary { flex: 1; background: var(--accent); color: var(--accent-ink); }
    button.secondary { flex: 1; border: 1px solid var(--line); color: var(--ink); }
    button.thumb { width: 36px; padding: 8px 0; border: 1px solid var(--line); font-size: 15px; }
    button.btn:hover { filter: brightness(0.96); }
    button.btn:focus-visible, .close:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
    button.btn[aria-pressed="true"] { background: var(--soft); border-color: var(--accent); }
    button.btn:disabled { opacity: 0.55; cursor: default; }
    .status { margin: 10px 0 0; font-size: 12px; color: var(--muted); min-height: 1.45em; }
    .status.error { color: var(--danger); }
  `;

  let currentToast = null;

  function el(tag, props = {}, children = []) {
    const node = document.createElement(tag);
    Object.assign(node, props);
    node.append(...children);
    return node;
  }

  function send(type, payload) {
    return chrome.runtime.sendMessage({ type, payload }).then((res) => {
      if (!res?.ok) throw new Error(res?.error || "Something went wrong");
      return res.data;
    });
  }

  function showVerdict(item, result) {
    const root = SecondLookShadowHost.root();
    SecondLookShadowHost.addStyle("toast", CSS);
    currentToast?.remove();

    const status = el("p", { className: "status", role: "status" });
    const setStatus = (text, isError = false) => {
      status.textContent = text;
      status.classList.toggle("error", isError);
    };

    const close = el("button", { className: "close", ariaLabel: "Close", textContent: "×" });
    const skip = el("button", { className: "btn primary", textContent: "Skip it" });
    const buy = el("button", { className: "btn secondary", textContent: "Buy anyway" });
    const up = el("button", { className: "btn thumb", ariaLabel: "Thumbs up", textContent: "👍" });
    const down = el("button", { className: "btn thumb", ariaLabel: "Thumbs down", textContent: "👎" });
    up.setAttribute("aria-pressed", "false");
    down.setAttribute("aria-pressed", "false");

    const alt = result.alternatives?.[0];
    const toast = el("div", { className: "toast", role: "dialog", ariaLabel: "Second Look verdict" }, [
      el("div", { className: "top" }, [el("span", { className: "brand", textContent: "SECOND LOOK" }), close]),
      el("span", { className: `badge ${result.verdict}`, textContent: LABELS[result.verdict] || result.verdict }),
      el("p", { className: "item", textContent: item.name + (item.price != null ? ` · $${Number(item.price).toFixed(2)}` : "") }),
      el("p", { className: "reason", textContent: result.reason }),
      ...(alt ? [el("p", { className: "alt" }, [el("b", { textContent: "Try instead: " }), alt.name + (alt.price != null ? ` ($${alt.price.toFixed(2)})` : "")])] : []),
      el("div", { className: "row" }, [skip, buy, up, down]),
      status,
    ]);

    close.addEventListener("click", () => toast.remove());

    async function onAction(action, label) {
      skip.disabled = buy.disabled = true;
      setStatus("Saving...");
      try {
        await send("SEND_ACTION", { verdictId: result.verdict_id, action });
        setStatus(`${label}. Saved.`);
      } catch (err) {
        skip.disabled = buy.disabled = false;
        setStatus(err.message, true);
      }
    }
    skip.addEventListener("click", () => onAction("skipped", "Skipped"));
    buy.addEventListener("click", () => onAction("bought", "Marked as bought"));

    async function onFeedback(value, button, other) {
      setStatus("Saving...");
      try {
        await send("SEND_FEEDBACK", { verdictId: result.verdict_id, value });
        button.setAttribute("aria-pressed", "true");
        other.setAttribute("aria-pressed", "false");
        setStatus(`Thanks. Feedback saved (${value === "up" ? "thumbs up" : "thumbs down"}).`);
      } catch (err) {
        setStatus(err.message, true);
      }
    }
    up.addEventListener("click", () => onFeedback("up", up, down));
    down.addEventListener("click", () => onFeedback("down", down, up));

    root.appendChild(toast);
    currentToast = toast;
    skip.focus({ preventScroll: true });
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "SHOW_VERDICT") return false;
    showVerdict(message.item, message.result);
    sendResponse({ ok: true });
    return false;
  });
})();
