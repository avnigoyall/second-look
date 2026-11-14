// Styles for the Second Look card. Injected into the shadow root, so store CSS cannot reach it.
(function (global) {
  "use strict";

  global.SecondLookCardStyles = `
    :host { all: initial; }

    .sl {
      --bg: #ffffff; --ink: #1d1b18; --muted: #6b665e; --line: #e2ddd3; --soft: #f4f1ea;
      --accent: #2f6b4f; --accent-ink: #ffffff; --danger: #a33a2b; --danger-soft: #fbeeeb;
      --have: #3b5ba5; --trend: #a8620a; --good: #2f7d4f;
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
        --bg: #1f1e1b; --ink: #f1ede6; --muted: #a39d93; --line: #3a3732; --soft: #2a2825;
        --accent: #5fae86; --accent-ink: #10221a; --danger: #e07a6a; --danger-soft: #3a2420;
        --have: #8aa6e8; --trend: #e6a454; --good: #6cc495;
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
      background: var(--bg); box-shadow: var(--shadow);
      border: 1px solid transparent; /* keeps an edge in forced-colors mode */
      pointer-events: auto;
      opacity: 1;
      transition-property: opacity, translate, filter;
      transition-duration: 150ms;
      transition-timing-function: ease-out;
    }
    .card[data-entering] { opacity: 0; }
    .card[data-leaving] { opacity: 0; }
    @media (prefers-reduced-motion: no-preference) {
      .card { translate: 0 0; filter: blur(0); }
      .card[data-entering] { translate: 0 -12px; filter: blur(4px); transition-duration: 300ms; }
      .card[data-leaving] { translate: 0 -12px; filter: blur(4px); }
    }

    .top { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 12px; }
    .brand { display: flex; align-items: center; gap: 8px; font-size: 12px; font-weight: 600; color: var(--muted); letter-spacing: 0.04em; }
    .brand svg { width: 16px; height: 16px; color: var(--accent); }

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
    button.thumb { width: 36px; padding: 8px 0; background: var(--soft); color: var(--muted); }
    button.thumb svg { width: 16px; height: 16px; }
    button.thumb[aria-pressed="true"] { background: var(--accent); color: var(--accent-ink); }
    button.link { all: unset; cursor: pointer; color: var(--muted); font-size: 12px; text-decoration: underline; text-underline-offset: 2px; }
    @media (hover: hover) {
      .icon-btn:hover { background: var(--soft); color: var(--ink); }
      button.primary:not(:disabled):hover { background: color-mix(in oklch, var(--accent), black 8%); }
      button.secondary:not(:disabled):hover, button.thumb:not(:disabled):not([aria-pressed="true"]):hover { background: color-mix(in oklch, var(--soft), var(--ink) 6%); }
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
    .reason { margin-bottom: 12px; }
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
    .row { display: flex; gap: 8px; align-items: center; }
    .row .primary, .row .secondary { flex: 1; }
    .status { margin-top: 10px; font-size: 12px; color: var(--muted); }
    .status:empty { display: none; }
    .status.error { color: var(--danger); }

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

    /* ---------- notice (removal confirmation) ---------- */
    .notice {
      position: fixed; top: 16px; right: 16px; max-width: min(320px, calc(100vw - 32px)); box-sizing: border-box;
      display: flex; gap: 8px; align-items: center; padding: 10px 14px; border-radius: 12px;
      background: var(--bg); box-shadow: var(--shadow); border: 1px solid transparent; font-size: 13px; pointer-events: auto;
      transition-property: opacity, translate, filter; transition-duration: 150ms; transition-timing-function: ease-out;
    }
    .notice svg { width: 16px; height: 16px; flex: none; color: var(--accent); }
    .card ~ .notice { top: auto; bottom: 16px; }
    .notice[data-entering], .notice[data-leaving] { opacity: 0; }
    @media (prefers-reduced-motion: no-preference) {
      .notice[data-entering], .notice[data-leaving] { translate: 0 -12px; filter: blur(4px); }
    }
  `;
})(globalThis);
