// Styles for the Second Look card. Injected into the shadow root, so store CSS cannot reach it.
(function (global) {
  "use strict";

  global.SecondLookCardStyles = `
    :host { all: initial; }

    .sl {
      --bg: #fffcf7; --ink: #2a221c; --muted: #6f6154; --line: #e4d9cb; --soft: #efe6da;
      --accent: #3f2e22; --accent-ink: #faf6f0; --danger: #9a3b2b; --danger-soft: #f6e7e2;
      --have: #4e6577; --trend: #8f6026; --good: #566b41;
      --img-outline: oklch(0 0 0 / 0.1);
      --shadow:
        0px 0px 0px 1px oklch(0 0 0 / 0.06),
        0px 8px 16px -4px oklch(0 0 0 / 0.10),
        0px 24px 48px -12px oklch(0 0 0 / 0.18);
      --ease-out: cubic-bezier(0.2, 0, 0, 1);
      font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
      color: var(--ink);
      -webkit-font-smoothing: antialiased;
    }
    @media (prefers-color-scheme: dark) {
      .sl {
        --bg: #201a16; --ink: #f2ebe2; --muted: #ab9c8d; --line: #3a3029; --soft: #2a221c;
        --accent: #c9a582; --accent-ink: #1c150f; --danger: #e08a76; --danger-soft: #3a2520;
        --have: #9fb3c4; --trend: #d9aa6a; --good: #a6bd8a;
        --img-outline: oklch(1 0 0 / 0.1);
        --shadow:
          0px 0px 0px 1px oklch(1 0 0 / 0.08),
          0px 8px 16px -4px oklch(0 0 0 / 0.4),
          0px 24px 48px -12px oklch(0 0 0 / 0.5);
      }
    }

    /* ---------- card shell ---------- */
    .card {
      position: fixed; top: 16px; right: 16px; width: 360px; max-width: calc(100vw - 32px);
      box-sizing: border-box; padding: 16px; border-radius: 16px;
      max-height: calc(100vh - 32px); overflow-y: auto; overscroll-behavior: contain;
      background: var(--bg); box-shadow: var(--shadow);
      border: 1px solid transparent; /* keeps an edge in forced-colors mode */
      pointer-events: auto;
      opacity: 1;
      transition-property: opacity, translate;
      transition-duration: 150ms;
      transition-timing-function: ease-out;
    }
    /* Blurs and dims the store behind the Buy / Skip verdict. Sits under the card (DOM order). */
    .backdrop {
      position: fixed; inset: 0; pointer-events: auto;
      background: oklch(0.22 0.025 55 / 0.30); /* warm espresso veil */
      -webkit-backdrop-filter: blur(6px); backdrop-filter: blur(6px);
      opacity: 1; transition: opacity 200ms ease-out;
    }
    .backdrop[data-entering], .backdrop[data-leaving] { opacity: 0; }
    .card[data-entering] { opacity: 0; }
    .card[data-leaving] { opacity: 0; }
    @media (prefers-reduced-motion: no-preference) {
      .card { translate: 0 0; }
      .card[data-entering] { translate: 0 -12px; transition-duration: 300ms; }
      .card[data-leaving] { translate: 0 -12px; }
    }

    .top { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 12px; }
    .brand { display: flex; align-items: center; gap: 8px; font-size: 12px; font-weight: 600; color: var(--muted); letter-spacing: 0.04em; }
    .brand svg { width: 18px; height: 18px; color: var(--accent); }

    .icon-btn {
      all: unset; box-sizing: border-box; cursor: pointer; width: 28px; height: 28px;
      display: grid; place-items: center; border-radius: 8px; color: var(--muted);
      transition-property: background-color, color, scale; transition-duration: 150ms; transition-timing-function: ease-out;
    }
    .icon-btn svg { width: 16px; height: 16px; }

    h2 { margin: 0 0 4px; font-size: 16px; line-height: 1.3; font-weight: 650; letter-spacing: -0.01em; }
    p { margin: 0; }
    .muted { color: var(--muted); }
    .small { font-size: 12px; }

    /* ---------- buttons ---------- */
    button.btn {
      all: unset; box-sizing: border-box; cursor: pointer; text-align: center;
      display: inline-flex; align-items: center; justify-content: center; gap: 6px;
      min-height: 36px; padding: 8px 14px; border-radius: 10px; font-weight: 600; font-size: 13px;
      -webkit-tap-highlight-color: transparent;
      transition-property: scale, background-color, opacity; transition-duration: 150ms; transition-timing-function: ease-out;
    }
    button.btn:not(:disabled):active, .icon-btn:active { scale: 0.96; }
    button.btn:disabled { opacity: 0.55; cursor: default; }
    button.primary { background: var(--accent); color: var(--accent-ink); }
    button.secondary { background: var(--soft); color: var(--ink); }
    button.link { all: unset; cursor: pointer; color: var(--muted); font-size: 12px; text-decoration: underline; text-underline-offset: 2px; }
    @media (hover: hover) {
      .icon-btn:hover { background: var(--soft); color: var(--ink); }
      button.primary:not(:disabled):hover { background: color-mix(in oklch, var(--accent), black 8%); }
      button.secondary:not(:disabled):hover { background: color-mix(in oklch, var(--soft), var(--ink) 6%); }
      button.link:hover { color: var(--ink); }
    }
    button.btn:focus-visible, .icon-btn:focus-visible, button.link:focus-visible, input:focus-visible {
      outline: 2px solid var(--accent); outline-offset: 2px;
    }

    /* ---------- checking ---------- */
    .checking { display: flex; align-items: center; gap: 12px; padding: 4px 0; }
    .spinner { width: 20px; height: 20px; flex: none; border-radius: 50%; border: 2px solid var(--line); border-top-color: var(--accent); animation: spin 800ms linear infinite; }
    .btn .spinner { width: 14px; height: 14px; border-color: color-mix(in oklch, currentColor, transparent 65%); border-top-color: currentColor; }
    @keyframes spin { to { rotate: 360deg; } }
    @media (prefers-reduced-motion: reduce) { .spinner { animation-duration: 2400ms; } }

    /* ---------- item row ---------- */
    .item { display: flex; gap: 12px; align-items: center; margin-bottom: 12px; }
    .thumb-img { width: 56px; height: 56px; flex: none; border-radius: 10px; object-fit: cover; background: var(--soft);
                 outline: 1px solid var(--img-outline); outline-offset: -1px; }
    .item-name { font-weight: 600; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
    .item-meta { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }

    /* ---------- verdict ---------- */
    .badge { display: inline-flex; align-items: center; font-size: 12px; font-weight: 600; padding: 2px 10px; border-radius: 999px;
             color: var(--c); background: color-mix(in oklch, var(--c), transparent 88%); margin-bottom: 8px; }
    .badge.have_this_look { --c: var(--have); }
    .badge.micro_trend { --c: var(--trend); }
    .badge.good_buy { --c: var(--good); }
    .reason { margin-bottom: 12px; text-wrap: pretty; }
    .section-label { font-size: 11px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; color: var(--muted); }
    .ext { display: inline-grid; flex: none; color: var(--muted); }
    .ext svg { width: 12px; height: 12px; }

    /* have_this_look: closet photos. Tiles are 6px radius inside the 10px panel with 4px padding. */
    .closet { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
    .closet-strip { list-style: none; margin: 0; padding: 4px; display: flex; gap: 4px; flex: none;
                    border-radius: 10px; background: var(--soft); }
    .closet-tile { width: 48px; height: 48px; border-radius: 6px; overflow: hidden; background: var(--bg); }
    .closet-tile img { display: block; width: 100%; height: 100%; object-fit: cover; border-radius: 6px;
                       outline: 1px solid var(--img-outline); outline-offset: -1px; }
    .closet-ph { width: 100%; height: 100%; display: grid; place-items: center; color: var(--muted); }
    .closet-ph svg { width: 20px; height: 20px; }
    .closet-text { min-width: 0; }
    .closet-name { font-size: 13px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .closet-more { font-size: 12px; color: var(--muted); }

    /* micro_trend: trend line, source link, alternatives */
    .trend { display: grid; gap: 2px; margin-bottom: 12px; padding: 10px 12px; border-radius: 10px; background: var(--soft); font-size: 13px; }
    .trend-head { display: flex; align-items: baseline; gap: 8px; min-width: 0; }
    .trend-name { font-weight: 600; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .trend-life { color: var(--muted); font-size: 12px; }
    .source { display: inline-flex; align-items: center; gap: 4px; justify-self: start; max-width: 100%; margin-top: 2px;
              font-size: 12px; color: var(--muted); text-decoration: underline; text-decoration-color: var(--line);
              text-underline-offset: 2px; border-radius: 4px;
              transition-property: color, text-decoration-color; transition-duration: 150ms; transition-timing-function: ease-out; }
    .source span:first-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    /* alternatives: each row is one link that opens in a new tab */
    .alts { margin-bottom: 12px; }
    .alts-label { margin-bottom: 6px; font-size: 12px; font-weight: 600; color: var(--muted); }
    .alts ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
    a.alt-link {
      display: flex; gap: 10px; align-items: center; min-height: 44px; box-sizing: border-box;
      padding: 8px 12px 8px 10px; border-radius: 10px; background: var(--soft); color: var(--ink);
      text-decoration: none; -webkit-tap-highlight-color: transparent;
      transition-property: background-color, scale; transition-duration: 150ms; transition-timing-function: ease-out;
    }
    .alt-icon { width: 28px; height: 28px; flex: none; display: grid; place-items: center; border-radius: 8px;
                background: var(--bg); color: var(--accent); }
    .alt-icon svg { width: 16px; height: 16px; }
    .alt-text { display: grid; min-width: 0; }
    .alt-name { font-size: 13px; font-weight: 600; line-height: 1.3; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .alt-link.search .alt-name { white-space: normal; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
    .alt-meta { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    a.alt-link:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
    @media (hover: hover) {
      a.alt-link:hover { background: color-mix(in oklch, var(--soft), var(--ink) 6%); }
      a.alt-link:hover .alt-name { text-decoration: underline; text-underline-offset: 2px; }
    }
    @media (prefers-reduced-motion: no-preference) {
      a.alt-link:active { scale: 0.96; }
    }

    /* good_buy: wears estimate */
    .wears { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; font-size: 13px; font-variant-numeric: tabular-nums; }
    .tag { font-size: 11px; font-weight: 600; padding: 1px 6px; border-radius: 6px; color: var(--muted); background: var(--soft); }

    /* Full analysis (POST /analyze/details), collapsible, above the action buttons */
    .analysis-loading { margin-bottom: 12px; font-size: 12px; color: var(--muted); }
    .analysis { margin-bottom: 12px; border-radius: 12px; background: var(--soft); font-size: 13px; }
    .analysis summary {
      display: flex; align-items: baseline; gap: 8px; padding: 10px 12px; cursor: pointer; list-style: none;
      border-radius: 12px; outline-offset: 2px;
    }
    .analysis summary::-webkit-details-marker { display: none; }
    .analysis summary::after { content: "Show"; margin-left: auto; font-size: 12px; font-weight: 600; color: var(--accent); }
    .analysis[open] summary::after { content: "Hide"; }
    .an-title { font-weight: 600; }
    .an-sub { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
    .an-row { display: grid; gap: 3px; padding: 8px 12px; border-top: 1px solid var(--line); }
    .an-row:last-child { padding-bottom: 12px; }
    .an-label { font-size: 11px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; color: var(--muted); }
    .an-strong { font-weight: 600; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .an-health { font-size: 12px; color: var(--muted); text-wrap: pretty; }
    .an-health a, .an-list a { color: var(--accent); }
    .an-list { margin: 0; padding-left: 16px; display: grid; gap: 3px; }
    .an-list li { text-wrap: pretty; }
    .an-rating { font-size: 11px; font-weight: 600; padding: 1px 6px; border-radius: 6px; text-transform: capitalize; }
    .an-rating.good { color: var(--good); background: color-mix(in oklch, var(--good), transparent 88%); }
    .an-rating.fair { color: var(--trend); background: color-mix(in oklch, var(--trend), transparent 88%); }
    .an-rating.poor { color: var(--danger); background: color-mix(in oklch, var(--danger), transparent 88%); }

    /* small-closet ownership question: quiet, sits above the buttons */
    .own { margin-bottom: 12px; padding-top: 12px; border-top: 1px solid var(--line); }
    .own-q { font-size: 12px; color: var(--muted); margin-bottom: 8px; }
    .chips { display: flex; flex-wrap: wrap; gap: 6px; }
    button.chip {
      all: unset; box-sizing: border-box; cursor: pointer; min-height: 28px; padding: 4px 10px; border-radius: 999px;
      font-size: 12px; font-weight: 500; color: var(--ink); background: var(--bg); box-shadow: inset 0 0 0 1px var(--line);
      -webkit-tap-highlight-color: transparent;
      transition-property: scale, background-color, opacity; transition-duration: 150ms; transition-timing-function: ease-out;
    }
    button.chip.quiet { color: var(--muted); box-shadow: none; }
    button.chip:not(:disabled):active { scale: 0.96; }
    button.chip:disabled { opacity: 0.55; cursor: default; }
    button.chip[aria-busy="true"] { opacity: 1; background: var(--soft); }
    .own-note { font-size: 12px; color: var(--danger); margin-top: 6px; }
    .own-note:empty { display: none; }
    .own-done { display: flex; gap: 6px; align-items: center; font-size: 12px; color: var(--muted); }
    .own-done svg { width: 14px; height: 14px; flex: none; color: var(--accent); }

    @media (hover: hover) {
      .source:hover { color: var(--ink); text-decoration-color: currentColor; }
      button.chip:not(:disabled):hover { background: var(--soft); }
    }
    .source:focus-visible, button.chip:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
    .row { display: flex; gap: 8px; align-items: center; }
    .row .primary, .row .secondary { flex: 1; white-space: nowrap; }
    .row .secondary { flex-grow: 1.3; } /* "Skip it anyway" is the longest label; give it room on one line */
    .status { margin-top: 10px; font-size: 12px; color: var(--muted); }
    .status:empty { display: none; }
    .status.error { color: var(--danger); }
    .status.saved { display: flex; gap: 6px; align-items: center; color: var(--ink); font-variant-numeric: tabular-nums; }
    .saved-icon { display: inline-grid; flex: none; color: var(--accent); }
    .saved-icon svg { width: 14px; height: 14px; }
    /* savings goal: one quiet line under the skip message, aligned with its text (14px icon + 6px gap) */
    .goal-line { margin-top: 2px; font-size: 12px; line-height: 1.4; color: var(--muted);
                 font-variant-numeric: tabular-nums; text-wrap: pretty; overflow-wrap: anywhere; }
    .goal-line[hidden] { display: none; }
    .status.saved + .goal-line { padding-left: 20px; }
    .goal-line.reached { color: var(--accent); font-weight: 500; }

    /* ---------- forms (paste and price) ---------- */
    form { display: grid; gap: 10px; margin-top: 12px; }
    .field { display: flex; gap: 8px; }
    .field .btn { min-width: 104px; } /* fixed width so the loading label never squeezes the field */
    input {
      all: unset; box-sizing: border-box; flex: 1; min-width: 0; min-height: 36px; padding: 8px 12px; border-radius: 10px;
      border: 1px solid var(--line); background: var(--bg); color: var(--ink); font-size: 13px;
      transition-property: border-color; transition-duration: 150ms; transition-timing-function: ease-out;
    }
    input::placeholder { color: var(--muted); }
    input:disabled { opacity: 0.6; }
    input[aria-invalid="true"] { border-color: var(--danger); }
    .form-error { display: flex; gap: 6px; align-items: flex-start; font-size: 12px; color: var(--danger); }
    .form-error svg { width: 14px; height: 14px; flex: none; margin-top: 2px; }
    .form-error[hidden] { display: none; }

    /* ---------- error ---------- */
    .error-box { display: flex; gap: 10px; align-items: flex-start; padding: 10px 12px; border-radius: 10px; background: var(--danger-soft); color: var(--danger); margin-bottom: 12px; }
    .error-box svg { width: 16px; height: 16px; flex: none; margin-top: 2px; }
    .error-box p { color: var(--ink); }

    /* ---------- unavailable (calm, not an error) ---------- */
    .calm { display: flex; gap: 10px; align-items: flex-start; margin-bottom: 12px; }
    .calm svg { width: 18px; height: 18px; flex: none; margin-top: 1px; color: var(--muted); }
    .calm-title { font-weight: 600; }

    /* ---------- notice (removal confirmation) ---------- */
    .notice {
      position: fixed; top: 16px; right: 16px; max-width: min(320px, calc(100vw - 32px)); box-sizing: border-box;
      display: flex; gap: 8px; align-items: center; padding: 10px 14px; border-radius: 12px;
      background: var(--bg); box-shadow: var(--shadow); border: 1px solid transparent; font-size: 13px; pointer-events: auto;
      transition-property: opacity, translate; transition-duration: 150ms; transition-timing-function: ease-out;
    }
    .notice svg { width: 16px; height: 16px; flex: none; color: var(--accent); }
    .notice-body { display: grid; gap: 2px; min-width: 0; }
    .notice-sub { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
    .card ~ .notice { top: auto; bottom: 16px; }
    .notice[data-entering], .notice[data-leaving] { opacity: 0; }
    @media (prefers-reduced-motion: no-preference) {
      .notice[data-entering], .notice[data-leaving] { translate: 0 -12px; }
    }

    /* ---------- editorial theme (brown / beige, serif headings, square buttons) ---------- */
    .sl { --serif: "Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif; }
    .card { border-radius: 6px; }
    .brand { font-family: var(--serif); font-size: 13px; letter-spacing: 0.14em; color: var(--ink); }
    .item-name { font-family: var(--serif); font-size: 16px; font-weight: 600; }
    .reason { font-family: var(--serif); font-size: 16px; line-height: 1.45; }
    .badge { border-radius: 2px; text-transform: uppercase; letter-spacing: 0.1em; font-size: 11px; }
    button.btn { border-radius: 3px; }
    .row button.btn.primary, .row button.btn.secondary { text-transform: uppercase; letter-spacing: 0.1em; font-size: 12px; }
    .row button.btn.secondary { background: transparent; box-shadow: inset 0 0 0 1px var(--accent); color: var(--accent); }
    .analysis, .analysis summary { border-radius: 4px; }
    .an-title { font-family: var(--serif); font-size: 15px; }
    .tag, .an-rating { border-radius: 2px; }

    /* ---------- type from references/reference-3: Inter, lowercase, extra-bold, pill buttons ---------- */
    .sl { --font: "SL Inter", "Inter", -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif; --serif: var(--font); font-family: var(--font); letter-spacing: -0.01em; }
    .sl button, .sl input { font-family: var(--font); }
    .card { border-radius: 20px; }
    .brand { text-transform: lowercase; font-weight: 800; font-size: 16px; letter-spacing: -0.04em; color: var(--ink); }
    .brand span::after { content: "."; }
    .item-name { font-weight: 700; font-size: 16px; letter-spacing: -0.02em; }
    .reason { font-size: 15px; font-weight: 500; line-height: 1.45; letter-spacing: -0.01em; }
    .badge { border-radius: 999px; text-transform: lowercase; letter-spacing: 0; font-size: 12px; padding: 2px 10px; }
    button.btn { border-radius: 999px; }
    .row button.btn.primary, .row button.btn.secondary { text-transform: lowercase; letter-spacing: -0.01em; font-size: 14px; }
    .analysis, .analysis summary { border-radius: 14px; }
    .an-title { font-weight: 800; text-transform: lowercase; letter-spacing: -0.03em; }
    .an-label { text-transform: lowercase; letter-spacing: 0; font-size: 12px; font-weight: 700; }
    .analysis summary::after { text-transform: lowercase; }
    .tag, .an-rating { border-radius: 999px; text-transform: lowercase; letter-spacing: 0; }
  `;
})(globalThis);
