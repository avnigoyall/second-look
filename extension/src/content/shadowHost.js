// Shared open-shadow host for Second Look overlays, so the store page's CSS can't bleed in.
// Adapted from Syft's shadowHost.js. The host has NO transform/contain/filter, so
// position:fixed children still resolve to the viewport. The shadow root only isolates CSS.
// The host sets pointer-events:none so it never blocks the page; every interactive child
// must set pointer-events:auto itself.
(function (global) {
  "use strict";

  const HOST_ID = "secondlook-shadow-host";
  let host = null;
  let shadow = null;

  function ensure() {
    if (shadow && host && document.documentElement.contains(host)) return shadow;
    document.getElementById(HOST_ID)?.remove();
    host = document.createElement("div");
    host.id = HOST_ID;
    host.style.cssText = "position:fixed; top:0; left:0; width:0; height:0; pointer-events:none; z-index:2147483646;";
    shadow = host.attachShadow({ mode: "open" });
    document.documentElement.appendChild(host);
    return shadow;
  }

  global.SecondLookShadowHost = {
    // The shadow root to append overlay elements into.
    root() { return ensure(); },
    // The host element. Events from inside the shadow root are retargeted to it.
    hostEl() { ensure(); return host; },
    // Add a <style> once (id-guarded) into the shadow root.
    addStyle(id, cssText) {
      const sr = ensure();
      if (sr.querySelector(`style[data-secondlook-style="${id}"]`)) return;
      const style = document.createElement("style");
      style.setAttribute("data-secondlook-style", id);
      style.textContent = cssText;
      sr.appendChild(style);
    },
  };
})(globalThis);
