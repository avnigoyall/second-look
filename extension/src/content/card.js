// The Second Look card, rendered top right inside the shadow root.
// States: checking (calm, with slower copy after 5 s), price (when no price was found), verdict,
// unavailable (backend down or slow, never blocks the store), error, paste-a-link.
// A verdict can gain a quiet ownership question for small closets.
// Plus a small notice used to confirm a removal. This file only draws UI and reports clicks;
// flow.js decides what happens next.
(function (global) {
  "use strict";

  const LABELS = { have_this_look: "You have this look", micro_trend: "Micro trend", good_buy: "Good buy" };

  // Icons use currentColor so states recolor them through CSS.
  const svg = (paths, stroke = 2) =>
    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${stroke}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
  const ICONS = {
    brand: svg('<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2.5" fill="currentColor" stroke="none"/>', 2.5),
    close: svg('<path d="M18 6 6 18M6 6l12 12"/>'),
    alert: svg('<circle cx="12" cy="12" r="9"/><path d="M12 8v4.5M12 16h.01"/>'),
    check: svg('<path d="M20 6 9 17l-5-5"/>'),
    up: svg('<path d="M7 10v11"/><path d="M15 5.9 14 10h5.8a2 2 0 0 1 1.9 2.6l-2.3 7A2 2 0 0 1 17.5 21H4a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1h3l4-8a3 3 0 0 1 3 3.9Z"/>'),
    shirt: svg('<path d="M20.38 3.46 16 2a4 4 0 0 1-8 0L3.62 3.46a2 2 0 0 0-1.34 2.23l.58 3.47a1 1 0 0 0 .99.84H6v10c0 1.1.9 2 2 2h8a2 2 0 0 0 2-2V10h2.15a1 1 0 0 0 .99-.84l.58-3.47a2 2 0 0 0-1.34-2.23Z"/>', 1.5),
    external: svg('<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>'),
    pause: svg('<circle cx="12" cy="12" r="9"/><path d="M10 9v6M14 9v6"/>'),
    down: svg('<path d="M17 14V3"/><path d="M9 18.1 10 14H4.2a2 2 0 0 1-1.9-2.6l2.3-7A2 2 0 0 1 6.5 3H20a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-3l-4 8a3 3 0 0 1-3-3.9Z"/>'),
  };

  let card = null;
  let body = null;
  let notice = null;
  let noticeTimer = null;
  let onClose = null;
  let stateTimer = null; // the checking state's "still checking" timer

  const SLOW_AFTER_MS = 5000;

  function root() {
    const sr = global.SecondLookShadowHost.root();
    global.SecondLookShadowHost.addStyle("card", global.SecondLookCardStyles);
    return sr;
  }

  function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "text") node.textContent = v;
      else if (k === "html") node.innerHTML = v; // only used with the static ICONS above
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    node.append(...children);
    return node;
  }

  // Start in the entering state, force a style flush, then clear it so the transition runs.
  // No requestAnimationFrame: it pauses in background tabs and would leave the card invisible.
  function enter(node) {
    node.setAttribute("data-entering", "");
    getComputedStyle(node).opacity; // flush
    node.removeAttribute("data-entering");
  }

  function leave(node) {
    node.setAttribute("data-leaving", "");
    setTimeout(() => node.remove(), 160);
  }

  // Create the card if needed, then swap its body. State swaps are instant on purpose.
  function frame(label) {
    const sr = root();
    if (!card || !card.isConnected) {
      body = el("div");
      card = el("section", { class: "sl card", role: "dialog", "aria-label": "Second Look", "aria-live": "polite" }, [
        el("div", { class: "top" }, [
          el("span", { class: "brand", html: ICONS.brand }, [el("span", { text: "SECOND LOOK" })]),
          el("button", { class: "icon-btn", "aria-label": "Close", html: ICONS.close, onclick: () => hide() }),
        ]),
        body,
      ]);
      card.addEventListener("keydown", (e) => { if (e.key === "Escape") hide(); });
      sr.appendChild(card);
      enter(card);
    }
    card.setAttribute("aria-label", `Second Look: ${label}`);
    body.replaceChildren();
    clearTimeout(stateTimer);
    const previous = onClose; // settle a pending price prompt that a new state replaced
    onClose = null;
    previous?.();
    return body;
  }

  function hide() {
    if (!card) return;
    clearTimeout(stateTimer);
    const closing = onClose;
    onClose = null;
    leave(card);
    card = null;
    closing?.();
  }

  const money = (n) => (n == null ? "" : `$${Number(n).toFixed(2)}`);

  function itemRow(item) {
    const meta = [money(item.price), item.store].filter(Boolean).join(" · ");
    return el("div", { class: "item" }, [
      item.image_url ? el("img", { class: "thumb-img", src: item.image_url, alt: "" }) : el("div", { class: "thumb-img" }),
      el("div", {}, [el("div", { class: "item-name", text: item.name }), meta ? el("div", { class: "item-meta", text: meta }) : ""]),
    ]);
  }

  function showChecking(item) {
    const b = frame("checking");
    if (item?.name) b.append(itemRow(item));
    const text = el("p", { text: "Second Look is checking..." });
    b.append(el("div", { class: "checking", role: "status" }, [el("div", { class: "spinner" }), text]));
    // The engine can take up to about 10 s. Past 5 s, say so calmly. The store is never held.
    stateTimer = setTimeout(() => {
      text.textContent = "Still checking. Your cart is not waiting on us.";
    }, SLOW_AFTER_MS);
  }

  // Resolves with a number, null (check without a price), or undefined (card closed).
  function askPrice(item) {
    return new Promise((resolve) => {
      const b = frame("enter the price");
      const input = el("input", { type: "text", inputmode: "decimal", placeholder: "Price, for example 39.90", "aria-label": "Price" });
      const error = el("p", { class: "form-error", hidden: true }, []);
      b.append(
        itemRow(item),
        el("p", { class: "muted", text: "We could not find the price on this page. Add it so your savings stay accurate." }),
        el("form", {
          onsubmit: (e) => {
            e.preventDefault();
            const value = global.SecondLookPrice.parsePrice(input.value);
            if (input.value.trim() && value == null) {
              error.innerHTML = ICONS.alert;
              error.append(el("span", { text: "Enter a number, for example 39.90." }));
              error.hidden = false;
              input.setAttribute("aria-invalid", "true");
              return;
            }
            resolve(value);
          },
        }, [
          el("div", { class: "field" }, [input, el("button", { class: "btn primary", type: "submit", text: "Check" })]),
          error,
          el("button", { class: "link", type: "button", text: "Check without a price", onclick: () => resolve(null) }),
        ]),
      );
      onClose = () => resolve(undefined);
      input.focus({ preventScroll: true });
    });
  }

  // handlers: { onAction(action) -> Promise, onFeedback(value) -> Promise }
  function showVerdict(item, result, handlers) {
    const b = frame(LABELS[result.verdict] || result.verdict);
    const status = el("p", { class: "status", role: "status" });
    const setStatus = (text, isError = false) => {
      status.textContent = text;
      status.classList.toggle("error", isError);
    };

    const skip = el("button", { class: "btn primary", text: "Skip it" });
    const buy = el("button", { class: "btn secondary", text: "Buy anyway" });
    const up = el("button", { class: "btn thumb", "aria-label": "Thumbs up", "aria-pressed": "false", html: ICONS.up });
    const down = el("button", { class: "btn thumb", "aria-label": "Thumbs down", "aria-pressed": "false", html: ICONS.down });

    const actions = el("div", { class: "row" }, [skip, buy, up, down]);
    b.append(
      itemRow(item),
      el("span", { class: `badge ${result.verdict}`, text: LABELS[result.verdict] || result.verdict }),
      el("p", { class: "reason", text: result.reason }),
      ...verdictDetails(result),
      actions,
      status,
    );

    async function act(action, doneText) {
      skip.disabled = buy.disabled = true;
      setStatus("Saving...");
      try {
        await handlers.onAction(action);
        setStatus(doneText);
      } catch (err) {
        skip.disabled = buy.disabled = false;
        setStatus(err.message, true);
      }
    }
    skip.addEventListener("click", () => act("skipped", "Skipped. Nice save."));
    buy.addEventListener("click", () => act("bought", "Marked as bought."));

    async function rate(value, button, other) {
      setStatus("Saving...");
      try {
        await handlers.onFeedback(value);
        button.setAttribute("aria-pressed", "true");
        other.setAttribute("aria-pressed", "false");
        setStatus("Thanks for the feedback.");
      } catch (err) {
        setStatus(err.message, true);
      }
    }
    up.addEventListener("click", () => rate("up", up, down));
    down.addEventListener("click", () => rate("down", down, up));
    skip.focus({ preventScroll: true });

    // Lets flow.js add the ownership question later, only while this verdict is still on screen.
    return {
      isCurrent: () => actions.isConnected,
      addOwnership(options, onAnswer) {
        if (actions.isConnected) b.insertBefore(ownershipBlock(options, onAnswer), actions);
      },
    };
  }

  // ---------- verdict details ----------

  function verdictDetails(r) {
    const parts = r.verdict === "have_this_look" ? [closetMatches(r)]
      : r.verdict === "micro_trend" ? [trendBlock(r.matched_trend), renderAlternatives(r.alternatives)]
      : r.verdict === "good_buy" ? [wearsLine(r)]
      : [];
    return parts.filter(Boolean);
  }

  // Up to 3 closet photos. Older backends send only matched_closet_item, often without image_data.
  function closetMatches(r) {
    let items = Array.isArray(r.matched_closet_items) ? r.matched_closet_items.filter(Boolean) : [];
    if (!items.length && r.matched_closet_item) items = [r.matched_closet_item];
    items = items.slice(0, 3);
    if (!items.length) return null;
    const tiles = items.map((it) => el("li", { class: "closet-tile", title: it.name || null }, [
      it.image_data
        ? el("img", { src: it.image_data, alt: it.name || "Closet item" })
        : el("span", { class: "closet-ph", role: "img", "aria-label": it.name || "Closet item", html: ICONS.shirt }),
    ]));
    const best = items[0];
    return el("div", { class: "closet" }, [
      el("ul", { class: "closet-strip", "aria-label": "Matching items in your closet" }, tiles),
      el("div", { class: "closet-text" }, [
        el("p", { class: "section-label", text: "In your closet" }),
        best.name ? el("p", { class: "closet-name", text: best.name }) : "",
        items.length > 1 ? el("p", { class: "closet-more", text: `+${items.length - 1} similar` }) : "",
      ]),
    ]);
  }

  const isHttp = (url) => /^https?:\/\//i.test(url || "");

  // Lifespan labels are shown only next to the source that backs them (CONTRACT section 6).
  function trendBlock(trend) {
    if (!trend?.name) return null;
    const sources = Array.isArray(trend.sources) ? trend.sources.filter((s) => isHttp(s?.url)) : [];
    const source = sources.find((s) => s.url === trend.lifespan_source_url) || sources[0];
    const lifespan = source && trend.lifespan_label ? trend.lifespan_label : "";
    return el("div", { class: "trend" }, [
      el("p", { class: "trend-head" }, [el("span", { class: "section-label", text: "Trend" }), el("span", { class: "trend-name", text: trend.name })]),
      lifespan ? el("p", { class: "trend-life", text: lifespan }) : "",
      source ? el("a", { class: "source", href: source.url, target: "_blank", rel: "noopener noreferrer" }, [
        el("span", { text: `Source: ${source.title || hostOf(source.url)}` }), el("span", { class: "ext", html: ICONS.external }),
      ]) : "",
    ]);
  }

  // The only place alternatives are drawn, so a different design can replace this one function.
  function renderAlternatives(alts) {
    const list = (Array.isArray(alts) ? alts : []).filter((a) => a?.name).slice(0, 3);
    if (!list.length) return null;
    return el("div", { class: "alts" }, [
      el("p", { class: "section-label", text: "Longer-lasting picks" }),
      el("ul", {}, list.map((a) => {
        const meta = [money(a.price), isHttp(a.url) ? hostOf(a.url) : ""].filter(Boolean).join(" · ");
        const text = [el("span", { class: "alt-name", text: a.name }), meta ? el("span", { class: "alt-meta", text: meta }) : ""];
        return el("li", {}, [isHttp(a.url)
          ? el("a", { class: "alt-link", href: a.url, target: "_blank", rel: "noopener noreferrer" }, [el("span", { class: "alt-text" }, text), el("span", { class: "ext", html: ICONS.external })])
          : el("div", { class: "alt-link" }, [el("span", { class: "alt-text" }, text)])]);
      })),
    ]);
  }

  function wearsLine(r) {
    const parts = [];
    if (r.expected_wears != null) parts.push(`About ${r.expected_wears} wears`);
    if (r.cost_per_wear != null) parts.push(`${money(r.cost_per_wear)} per wear`);
    if (!parts.length) return null;
    return el("p", { class: "wears" }, [el("span", { text: parts.join(", ") }), el("span", { class: "tag", text: "Estimate" })]);
  }

  function hostOf(url) {
    try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return ""; }
  }

  // Small-closet question (Part 3): one line and a few chips above the buttons.
  // onAnswer(option) -> Promise. A null option means "None of these".
  function ownershipBlock(options, onAnswer) {
    const box = el("div", { class: "own", role: "group", "aria-label": "Do you own something like this?" });
    const note = el("p", { class: "own-note", role: "status" });
    const chips = options.slice(0, 3).map((opt) => el("button", { class: "chip", type: "button", text: opt.label }));
    const none = el("button", { class: "chip quiet", type: "button", text: "None of these" });
    const all = [...chips, none];
    async function pick(opt, button) {
      all.forEach((c) => { c.disabled = true; });
      button.setAttribute("aria-busy", "true");
      note.textContent = "";
      try {
        await onAnswer(opt);
        box.replaceChildren(el("p", { class: "own-done", role: "status", html: ICONS.check }, [
          el("span", { text: opt ? `Added "${opt.label}" to your closet.` : "Got it. We will learn your closet as you go." }),
        ]));
      } catch (err) {
        all.forEach((c) => { c.disabled = false; });
        button.removeAttribute("aria-busy");
        note.textContent = err.message || "Could not save. Try again.";
      }
    }
    chips.forEach((c, i) => c.addEventListener("click", () => pick(options[i], c)));
    none.addEventListener("click", () => pick(null, none));
    box.append(el("p", { class: "own-q", text: "Your closet is still small. Own something like this?" }), el("div", { class: "chips" }, all), note);
    return box;
  }

  // Backend down or slow. Calm, not an error: the store already handled the click.
  function showUnavailable({ onRetry } = {}) {
    const b = frame("unavailable");
    b.append(el("div", { class: "calm", role: "status", html: ICONS.pause }, [
      el("div", {}, [
        el("p", { class: "calm-title", text: "Second look unavailable" }),
        el("p", { class: "muted small", text: "Your cart is not affected. Go ahead, we will check next time." }),
      ]),
    ]));
    const row = el("div", { class: "row" }, [el("button", { class: "btn secondary", text: "Close", onclick: () => hide() })]);
    if (onRetry) row.prepend(el("button", { class: "btn secondary", text: "Try again", onclick: onRetry }));
    b.append(row);
  }

  function showError(message, { actionLabel, onAction } = {}) {
    const b = frame("error");
    b.append(el("div", { class: "error-box", role: "alert", html: ICONS.alert }, [el("p", { text: message })]));
    const row = el("div", { class: "row" }, [el("button", { class: "btn secondary", text: "Close", onclick: () => hide() })]);
    if (actionLabel) row.prepend(el("button", { class: "btn primary", text: actionLabel, onclick: onAction }));
    b.append(row);
  }

  // onSubmit(url) -> Promise. Reject with an Error to show its message under the field.
  function showPaste({ message, url = "", onSubmit }) {
    const b = frame("paste a product link");
    const input = el("input", { type: "url", placeholder: "https://store.com/product", "aria-label": "Product link", value: url, autocomplete: "off", spellcheck: "false" });
    const submit = el("button", { class: "btn primary", type: "submit", text: "Check" });
    const error = el("p", { class: "form-error", role: "alert", hidden: true });

    const setError = (text) => {
      error.replaceChildren();
      error.hidden = !text;
      input.setAttribute("aria-invalid", text ? "true" : "false");
      if (text) {
        error.innerHTML = ICONS.alert;
        error.append(el("span", { text }));
      }
    };
    const setLoading = (loading) => {
      input.disabled = submit.disabled = loading;
      submit.replaceChildren(...(loading ? [el("span", { class: "spinner" }), "Checking"] : ["Check"]));
    };

    b.append(
      el("h2", { text: "Paste the product link" }),
      el("p", { class: "muted", text: message || "We could not read this page. Paste the product's link and we will read it for you." }),
      el("form", {
        novalidate: true,
        onsubmit: async (e) => {
          e.preventDefault();
          const value = input.value.trim();
          if (!/^https?:\/\/\S+\.\S+/i.test(value)) {
            setError("Paste a full product link that starts with https://");
            input.focus();
            return;
          }
          setError("");
          setLoading(true);
          try {
            await onSubmit(value);
          } catch (err) {
            if (!input.isConnected) return;
            setLoading(false);
            setError(err.message || "Something went wrong. Try again.");
            input.focus();
          }
        },
      }, [el("div", { class: "field" }, [input, submit]), error]),
    );
    input.addEventListener("input", () => { if (!error.hidden) setError(""); });
    input.focus({ preventScroll: true });
  }

  function showNotice(text) {
    const sr = root();
    clearTimeout(noticeTimer);
    notice?.remove();
    notice = el("div", { class: "sl notice", role: "status", html: ICONS.check }, [el("span", { text })]);
    sr.appendChild(notice);
    enter(notice);
    const current = notice;
    noticeTimer = setTimeout(() => leave(current), 3500);
  }

  global.SecondLookCard = { showChecking, askPrice, showVerdict, showUnavailable, showError, showPaste, showNotice, hide };
})(globalThis);
