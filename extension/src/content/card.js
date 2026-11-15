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
    // Logo: a "2" drawn as a hanger (hook = top of the 2, light stroke = the hanger's other shoulder).
    brand: svg('<path d="M12.6 11.2 20.2 18.6" stroke-width="2" opacity="0.45"/><path d="M8.4 7.1C8.4 4.9 10 3.4 12.1 3.4c2.1 0 3.6 1.5 3.6 3.4 0 1.7-1.1 2.8-3.1 4.4l-8.8 7.4h16.4"/>', 2.4),
    close: svg('<path d="M18 6 6 18M6 6l12 12"/>'),
    alert: svg('<circle cx="12" cy="12" r="9"/><path d="M12 8v4.5M12 16h.01"/>'),
    check: svg('<path d="M20 6 9 17l-5-5"/>'),
    external: svg('<path d="M7 17 17 7"/><path d="M8 7h9v9"/>'),
    search: svg('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>'),
    shirt: svg('<path d="M20.38 3.46 16 2a4 4 0 0 1-8 0L3.62 3.46a2 2 0 0 0-1.34 2.23l.58 3.47a1 1 0 0 0 .99.84H6v10c0 1.1.9 2 2 2h8a2 2 0 0 0 2-2V10h2.15a1 1 0 0 0 .99-.84l.58-3.47a2 2 0 0 0-1.34-2.23Z"/>', 1.5),
    pause: svg('<circle cx="12" cy="12" r="9"/><path d="M10 9v6M14 9v6"/>'),
  };

  let card = null;
  let body = null;
  let notice = null;
  let noticeTimer = null;
  let onClose = null;
  let backdrop = null; // blurs the page behind the Buy / Skip verdict
  const hideListeners = new Set(); // flow.js: release a held add-to-cart click when the card is closed
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
    setBackdrop(false);
    const previous = onClose; // settle a pending price prompt that a new state replaced
    onClose = null;
    previous?.();
    return body;
  }

  // A full-page layer under the card that blurs and dims the store, so the verdict is the only thing in
  // focus. Clicks on it do nothing: the shopper chooses Buy or Skip (or closes the card with x / Escape).
  function setBackdrop(on) {
    if (on) {
      if (backdrop?.isConnected) return;
      backdrop = el("div", { class: "sl backdrop", "aria-hidden": "true" });
      backdrop.addEventListener("pointerdown", (e) => { e.preventDefault(); });
      root().insertBefore(backdrop, card && card.isConnected ? card : null);
      enter(backdrop);
      card?.setAttribute("aria-modal", "true");
    } else if (backdrop) {
      const b = backdrop;
      backdrop = null;
      leave(b);
      card?.removeAttribute("aria-modal");
    }
  }

  function hide() {
    setBackdrop(false);
    if (!card) return;
    clearTimeout(stateTimer);
    const closing = onClose;
    onClose = null;
    leave(card);
    card = null;
    closing?.();
    for (const fn of [...hideListeners]) fn();
  }

  // Calls fn when the shopper closes the card (close button, Escape). Returns an unsubscribe function.
  function onHide(fn) {
    hideListeners.add(fn);
    return () => hideListeners.delete(fn);
  }

  const money = (n) => (n == null ? "" : `$${Number(n).toFixed(2)}`);

  function itemRow(item) {
    const meta = [money(item.price), item.store].filter(Boolean).join(" · ");
    return el("div", { class: "item" }, [
      item.image_url ? el("img", { class: "thumb-img", src: item.image_url, alt: "" }) : el("div", { class: "thumb-img" }),
      el("div", {}, [el("div", { class: "item-name", text: item.name }), meta ? el("div", { class: "item-meta", text: meta }) : ""]),
    ]);
  }

  // Alternatives (micro_trend verdicts). Each one is a single link that opens in a new tab: a product
  // page (name, price, store) or, when link_kind is "search", a store search ("Search Gap for ...").
  // Alternatives without an http(s) url are skipped, so nothing shows without a link.
  const httpUrl = (u) => typeof u === "string" && /^https?:\/\//i.test(u);

  function alternativesBlock(alternatives) {
    const items = (alternatives || []).filter((a) => a && a.name && httpUrl(a.url)).slice(0, 2);
    if (!items.length) return "";
    return el("div", { class: "alts" }, [
      el("p", { class: "alts-label", id: "sl-alts-label", text: "Try instead" }),
      el("ul", { "aria-labelledby": "sl-alts-label" }, items.map((a) => {
        const search = a.link_kind === "search";
        const store = a.store || (() => { try { return new URL(a.url).hostname.replace(/^www\./, ""); } catch { return ""; } })();
        const name = search ? `Search ${store} for ${a.name.charAt(0).toLowerCase()}${a.name.slice(1)}` : a.name;
        const meta = search ? "" : [money(a.price), store].filter(Boolean).join(" · ");
        return el("li", {}, [el("a", {
          class: `alt-link${search ? " search" : ""}`, href: a.url, target: "_blank", rel: "noopener noreferrer",
          "data-link-kind": search ? "search" : "product",
          title: name,
          "aria-label": `${name}${meta ? `, ${meta}` : ""} (opens in a new tab)`,
        }, [
          el("span", { class: "alt-icon", html: search ? ICONS.search : ICONS.external }),
          el("span", { class: "alt-text" }, [
            el("span", { class: "alt-name", text: name }),
            meta ? el("span", { class: "alt-meta", text: meta }) : "",
          ]),
        ])]);
      })),
    ]);
  }

  // held: an add-to-cart click is waiting for the verdict (trigger.js). It goes through on Buy, on close,
  // on any failure, or after 15 s without a verdict.
  function showChecking(item, { held = false } = {}) {
    const b = frame("checking");
    if (item?.name) b.append(itemRow(item));
    const text = el("p", { text: held ? "Loading analysis before it goes in your bag..." : "Loading analysis..." });
    b.append(el("div", { class: "checking", role: "status" }, [el("div", { class: "spinner" }), text]));
    // The engine can take up to about 10 s. Past 5 s, say so calmly.
    stateTimer = setTimeout(() => {
      text.textContent = held
        ? "Still loading. If this takes much longer, it goes in your bag anyway."
        : "Still loading. Your cart is not waiting on us.";
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

  // handlers: { onAction(action) -> Promise }
  function showVerdict(item, result, handlers) {
    const b = frame(LABELS[result.verdict] || result.verdict);
    const status = el("p", { class: "status", role: "status" });
    // Savings goal progress (CONTRACT section 15): one quiet line under the skip message.
    const setStatus = (text, isError = false) => {
      status.textContent = text;
      status.classList.toggle("error", isError);
      status.classList.remove("saved");
    };

    // The highlighted (primary) button follows the verdict: "Buy it" for a good buy, "Skip it" otherwise.
    const goodBuy = result.verdict === "good_buy";
    const skip = el("button", { class: `btn ${goodBuy ? "secondary" : "primary"}`, text: goodBuy ? "Skip it anyway" : "Skip it" });
    const buy = el("button", { class: `btn ${goodBuy ? "primary" : "secondary"}`, text: goodBuy ? "Buy it" : "Buy anyway" });
    // Feedback (thumbs up/down) removed from the card for now; POST /feedback still exists on the backend.
    const actions = el("div", { class: "row" }, goodBuy ? [buy, skip] : [skip, buy]);
    b.append(
      itemRow(item),
      el("span", { class: `badge ${result.verdict}`, text: LABELS[result.verdict] || result.verdict }),
      el("p", { class: "reason", text: result.reason }),
      ...verdictDetails(result),
      ...socialDetails(result.social_context),
      actions,
      status,
    );
    setBackdrop(true);

    // Buy: the held add-to-cart click goes to the store (flow.js), then the card closes.
    // Skip: the held click is dropped and the card closes; nothing else happens on the page.
    // The action is still recorded for the savings dashboard, in the background.
    async function act(action) {
      skip.disabled = buy.disabled = true;
      let sending;
      try {
        sending = handlers.onAction(action); // flow.js releases or drops the held click before its first await
      } catch (err) {
        sending = Promise.reject(err);
      }
      hide();
      let res = null;
      try {
        res = await sending;
        // Rare: the store re-rendered its button while the card was open, so the replay had nothing to click.
        if (action === "bought" && res?.bag === "missing") showNotice("Press the store's add button again to add it.");
      } catch (err) {
        console.warn("[SecondLook] action not saved:", err.message);
      }
      // The card is closed by now, so the savings result (and the goal line when there is a goal) shows
      // in the same small notice Avni's design uses for other confirmations. Buy stays quiet.
      if (action === "skipped" && res) {
        const moved = movedCents(res);
        const text = res.transfer?.status === "no_price" ? SKIP_NO_PRICE
          : moved == null ? SKIP_PENDING : `Skipped. ${money(moved / 100)} moved to savings`;
        showNotice(text, goalText(res.goal));
      }
    }
    skip.addEventListener("click", () => act("skipped"));
    buy.addEventListener("click", () => act("bought"));

    (goodBuy ? buy : skip).focus({ preventScroll: true }); // focus the highlighted button

    // Lets flow.js add the ownership question later, only while this verdict is still on screen.
    let analysis = null; // the Full analysis block, filled in by flow.js after /analyze/details answers
    return {
      isCurrent: () => actions.isConnected,
      addOwnership(options, onAnswer) {
        if (actions.isConnected) b.insertBefore(ownershipBlock(options, onAnswer), actions);
      },
      showDetailsLoading() {
        if (!actions.isConnected || analysis) return;
        analysis = el("p", { class: "analysis-loading", role: "status", text: "Loading full analysis..." });
        b.insertBefore(analysis, actions);
      },
      addDetails(details) {
        if (!actions.isConnected) return;
        const block = analysisBlock(details, item);
        if (!block) return this.removeDetails();
        if (analysis) analysis.replaceWith(block); else b.insertBefore(block, actions);
        analysis = block;
        // The analysis estimate (this fabric and garment) replaces the category average on the wears line,
        // so the card never shows two different wear counts.
        const wears = details.durability?.expected_wears;
        const line = b.querySelector(".wears span:first-child");
        if (wears && line) {
          const cpw = details.cost?.cost_per_wear;
          line.textContent = `About ${wears} wears${cpw != null ? `, ${money(cpw)} per wear` : ""}`;
        }
      },
      removeDetails() {
        analysis?.remove();
        analysis = null;
      },
    };
  }

  // ---------- Full analysis (POST /analyze/details) ----------

  const PCT = (n) => (n == null ? "" : `${Number.isInteger(n) ? n : n.toFixed(1)}% `);
  const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

  function row(label, children) {
    return el("div", { class: "an-row" }, [el("p", { class: "an-label", text: label }), ...children.filter(Boolean)]);
  }

  function analysisBlock(d, item) {
    const dur = d.durability || {};
    const cost = d.cost || {};
    const summaryBits = [
      dur.expected_wears ? `about ${dur.expected_wears} wears` : null,
      cost.cost_per_wear != null ? `${money(cost.cost_per_wear)}/wear` : null,
    ].filter(Boolean);

    const rows = [];

    // Materials: composition, durability, sourced health notes.
    const fibers = (d.materials || []).map((f) => `${PCT(f.pct)}${f.fiber}`).join(" · ");
    const fromPhoto = d.materials_source === "attributes" ? " (from the photo, no label found)" : "";
    const health = (d.health_notes || []).map((h) => el("p", { class: "an-health" }, [
      el("span", { text: `${h.note} ` }),
      el("a", { href: h.url, target: "_blank", rel: "noopener noreferrer", text: `Source: ${h.source}` }),
    ]));
    if (fibers || dur.note || health.length) {
      rows.push(row("Materials", [
        fibers ? el("p", { class: "an-strong", text: `${cap(fibers)}${fromPhoto}` }) : null,
        dur.note ? el("p", { text: `${dur.pilling_risk ? `${cap(dur.pilling_risk)} pilling risk. ` : ""}${dur.note}` }) : null,
        ...health,
      ]));
    }

    // Lifespan and trend.
    if (dur.expected_wears || dur.expected_months || d.trend_note) {
      const life = [dur.expected_wears && `About ${dur.expected_wears} wears`, dur.expected_months && `${dur.expected_months} months`]
        .filter(Boolean).join(" · ");
      rows.push(row("Lifespan", [
        life ? el("p", { class: "an-strong" }, [el("span", { text: `${life} ` }), el("span", { class: "tag", text: "Estimate" })]) : null,
        d.trend_note ? el("p", { text: d.trend_note }) : null,
      ]));
    }

    // Cost per wear.
    if (cost.cost_per_wear != null) {
      rows.push(row("Cost per wear", [el("p", { class: "an-strong" }, [
        el("span", { text: `${money(cost.cost_per_wear)} per wear ` }),
        cost.rating ? el("span", { class: `an-rating ${cost.rating}`, text: cost.rating }) : null,
      ].filter(Boolean))]));
    }

    // Closet: pieces with the same feel.
    if (d.closet_checked && (d.similar_closet_items || []).length) {
      rows.push(row("Similar in your closet", [el("ul", { class: "an-list" },
        d.similar_closet_items.map((c) => el("li", { text: c.name })))]));
    }

    // Outfits.
    if ((d.outfits || []).length) {
      const newName = item?.name ? `this ${item.name.split(" ").slice(-2).join(" ").toLowerCase()}` : "this piece";
      rows.push(row(d.outfits[0].uses_new_item ? "Outfits with this piece" : "Outfits with what you own", [
        el("ul", { class: "an-list" }, d.outfits.map((o) => el("li", {}, [
          el("span", { class: "an-strong", text: `${o.name}: ` }),
          el("span", { text: [...(o.uses_new_item ? [newName] : []), ...(o.closet_item_names || [])].join(" + ") }),
        ]))),
      ]));
    }

    // General-purpose alternatives (only sent when cost per wear is poor).
    const alts = (d.alternatives || []).filter((a) => a && a.name && httpUrl(a.url));
    if (alts.length) {
      rows.push(row("Wears more for the money", [el("ul", { class: "an-list" }, alts.map((a) => el("li", {}, [
        el("a", { href: a.url, target: "_blank", rel: "noopener noreferrer", text: `Search ${a.store || "a store"} for ${a.name.toLowerCase()}` }),
      ])))]));
    }

    if (!rows.length) return null; // nothing to add: the section disappears
    return el("details", { class: "analysis" }, [
      el("summary", {}, [
        el("span", { class: "an-title", text: "Full analysis" }),
        summaryBits.length ? el("span", { class: "an-sub", text: summaryBits.join(" · ") }) : null,
      ].filter(Boolean)),
      ...rows,
    ]);
  }

  // ---------- savings goal line ----------

  // Whole dollars when the cents are 00, otherwise two decimals, with a thousands separator.
  function dollars(cents) {
    const digits = cents % 100 === 0 ? 0 : 2;
    return `$${(cents / 100).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
  }

  // POST /action adds goal { name, target_cents, saved_cents, reached } only when the user has one.
  // The name is user text, so it only ever goes through textContent. Missing or bad data: no line.
  const SKIP_PENDING = "Skipped. Savings update pending";
  const SKIP_NO_PRICE = "Skipped. No price, so nothing moved";

  // POST /action returns transfer { status, amount_cents } when the savings transfer ran.
  // Only "done" with a positive amount counts as moved. no_price says nothing moved. pending,
  // failed, or no transfer field at all (older backend) read as pending, never as an error.
  function movedCents(res) {
    const t = res?.transfer;
    if (t?.status !== "done") return null;
    const cents = Number(t.amount_cents);
    return Number.isFinite(cents) && cents > 0 ? cents : null;
  }

  function goalText(goal) {
    const name = typeof goal?.name === "string" ? goal.name.trim() : "";
    if (!name) return "";
    if (goal.reached === true) return `${name}: you made it`;
    const saved = Math.round(Number(goal.saved_cents));
    const target = Math.round(Number(goal.target_cents));
    if (!Number.isFinite(saved) || !Number.isFinite(target) || saved < 0 || target <= 0) return "";
    return `${dollars(saved)} of ${dollars(target)} for ${name}`;
  }

  // ---------- verdict details ----------

  // Counts describe sampled search chatter. A missing or incomplete estimate adds no copy.
  function socialDetails(context) {
    if (!context || typeof context.term !== "string" || !context.term.trim()) return [];
    const peak = Date.parse(context.peak_week);
    const ratio = context.decay_ratio;
    if (!Number.isFinite(peak) || typeof ratio !== "number" || !Number.isFinite(ratio) || ratio < 0) return [];
    const elapsed = Math.max(0, Math.floor((Date.now() - peak) / (7 * 86400000)));
    const change = Math.round(Math.abs(1 - ratio) * 100);
    const direction = ratio > 1 ? "up" : "down";
    const copy = `Bluesky chatter on ${context.term}: peaked ${elapsed} weeks ago, ${direction} ${change}%`;
    let page = null;
    try {
      const url = new URL(context.trends_ui_url);
      if (/^https?:$/.test(url.protocol) && url.pathname === "/trends-ui") page = url.href;
    } catch { /* No page was advertised by the backend. */ }
    return [el("p", { class: "wears social-chatter", "data-social-estimate": "true" }, [
      page ? el("a", { href: page, target: "_blank", rel: "noopener noreferrer", text: copy }) : el("span", { text: copy }),
      el("span", { class: "tag", text: "Estimate" }),
    ])];
  }

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
  // Alternatives render through Fix B's alternativesBlock: one link per alternative, product or store search.
  function renderAlternatives(alts) {
    return alternativesBlock(alts) || null;
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

  // sub: an optional second, smaller line (the savings goal after a skip). Text only, never HTML.
  function showNotice(text, sub = "") {
    const sr = root();
    clearTimeout(noticeTimer);
    notice?.remove();
    const body = sub ? el("span", { class: "notice-body" }, [el("span", { text }), el("span", { class: "notice-sub", text: sub })])
      : el("span", { text });
    notice = el("div", { class: "sl notice", role: "status", html: ICONS.check }, [body]);
    sr.appendChild(notice);
    enter(notice);
    const current = notice;
    noticeTimer = setTimeout(() => leave(current), 3500);
  }

  global.SecondLookCard = { showChecking, askPrice, showVerdict, showUnavailable, showError, showPaste, showNotice, hide, onHide };
})(globalThis);
