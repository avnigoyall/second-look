// Second Look closet review page: upload -> poll the ingest job -> review cards -> save.
(() => {
  const TOKEN_KEY = "secondLookToken"; // shared with dashboard/index.html
  const MAX_IMAGES = 15;
  const POLL_MS = 1000;
  const FIELDS = ["category", "color_family", "fabric", "cut", "style"];
  const FIELD_LABELS = { category: "Category", color_family: "Color", fabric: "Fabric", cut: "Cut", style: "Style" };
  const STEP_TEXT = {
    queued: "Waiting to start", frames: "Pulling frames from your video", extract: "Finding each garment",
    merge: "Merging items seen more than once", text: "Reading your description",
    compare: "Checking against your closet", crop: "Cropping photos", done: "Done",
  };
  // Where each step sits on the progress bar, as [start, end] percent.
  const STEP_RANGE = { queued: [0, 2], frames: [2, 12], extract: [12, 72], merge: [72, 80], text: [80, 86],
                       compare: [86, 93], crop: [93, 99], done: [100, 100] };

  const $ = (id) => document.getElementById(id);
  const state = { files: [], jobId: null, items: [], attrs: null, pollTimer: null };

  // ---------- api ----------
  const getToken = () => { try { return localStorage.getItem(TOKEN_KEY); } catch { return null; } };
  const setToken = (t) => { try { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY); } catch {} };

  async function api(path, { method = "GET", json, form } = {}) {
    const headers = {};
    const token = getToken();
    if (token) headers.Authorization = "Bearer " + token;
    if (json) headers["Content-Type"] = "application/json";
    let res;
    try {
      res = await fetch(path, { method, headers, body: json ? JSON.stringify(json) : form });
    } catch {
      throw Object.assign(new Error("Cannot reach the server. Is the backend running?"), { status: 0 });
    }
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      let msg = typeof body.detail === "string" ? body.detail : "Request failed (" + res.status + ")";
      if (Array.isArray(body.detail) && body.detail[0]?.msg) msg = body.detail[0].msg;
      throw Object.assign(new Error(msg), { status: res.status });
    }
    return body;
  }

  // ---------- views ----------
  const VIEWS = ["login-view", "upload-view", "progress-view", "review-view", "saved-view"];
  function show(view) {
    for (const v of VIEWS) $(v).hidden = v !== view;
    $("logout").hidden = view === "login-view";
    $("closet-view").hidden = view === "login-view" || view === "progress-view" || view === "review-view";
  }

  function handleAuthError(e) {
    if (e.status === 401) { setToken(null); show("login-view"); return true; }
    return false;
  }

  // ---------- upload ----------
  const isVideo = (f) => f.type.startsWith("video/") || /\.(mp4|mov|m4v|webm|avi|mkv|3gp)$/i.test(f.name);

  function addFiles(list) {
    $("upload-error").textContent = "";
    for (const f of list) {
      if (!state.files.some((x) => x.name === f.name && x.size === f.size)) state.files.push(f);
    }
    const videos = state.files.filter(isVideo);
    const images = state.files.filter((f) => !isVideo(f));
    if (videos.length > 1) {
      state.files = state.files.filter((f) => !isVideo(f) || f === videos[0]);
      $("upload-error").textContent = "Only 1 video per upload. Kept the first one.";
    }
    if (images.length > MAX_IMAGES) {
      const keep = new Set(images.slice(0, MAX_IMAGES));
      state.files = state.files.filter((f) => isVideo(f) || keep.has(f));
      $("upload-error").textContent = `Up to ${MAX_IMAGES} photos per upload. Kept the first ${MAX_IMAGES}.`;
    }
    renderFiles();
  }

  function renderFiles() {
    const ul = $("file-list");
    ul.replaceChildren();
    state.files.forEach((f, i) => {
      const li = document.createElement("li");
      li.className = "chip";
      const name = document.createElement("span");
      name.textContent = (isVideo(f) ? "Video: " : "") + f.name;
      const rm = document.createElement("button");
      rm.type = "button";
      rm.setAttribute("aria-label", "Remove " + f.name);
      rm.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>';
      rm.addEventListener("click", () => { state.files.splice(i, 1); renderFiles(); });
      li.append(name, rm);
      ul.append(li);
    });
  }

  async function startIngest() {
    const text = $("intake-text").value.trim();
    if (!state.files.length && !text) {
      $("upload-error").textContent = "Add photos, a video, or a description first.";
      return;
    }
    const form = new FormData();
    for (const f of state.files) form.append("files", f, f.name);
    form.append("text", text);
    const btn = $("start");
    btn.disabled = true;
    btn.textContent = "Uploading...";
    try {
      const { job_id } = await api("/closet/ingest", { method: "POST", form });
      state.jobId = job_id;
      setProgress({ status: "queued", progress: { step: "queued", done: 0, total: 0 } });
      $("progress-failed").hidden = true;
      show("progress-view");
      poll();
    } catch (e) {
      if (!handleAuthError(e)) $("upload-error").textContent = e.message;
    } finally {
      btn.disabled = false;
      btn.textContent = "Find my clothes";
    }
  }

  // ---------- progress ----------
  function percent(job) {
    const { step, done, total } = job.progress;
    const [a, b] = STEP_RANGE[step] || [0, 2];
    const frac = total > 0 ? Math.min(1, done / total) : 0;
    return Math.round(a + (b - a) * frac);
  }

  function setProgress(job) {
    const pct = percent(job);
    $("bar-fill").style.scale = `${Math.max(0.02, pct / 100)} 1`;
    $("bar").setAttribute("aria-valuenow", String(pct));
    const step = job.progress.step;
    let text = STEP_TEXT[step] || "Working";
    if (step === "extract" && job.progress.total > 1) text += ` (${job.progress.done} of ${job.progress.total} batches)`;
    $("progress-step").textContent = text;
  }

  async function poll() {
    clearTimeout(state.pollTimer);
    try {
      const job = await api(`/closet/ingest/${state.jobId}`);
      setProgress(job);
      if (job.status === "done") return openReview(job);
      if (job.status === "saved") return showSaved(job.saved_count || 0);
      if (job.status === "failed") {
        $("progress-title").textContent = "Could not read your clothes";
        $("progress-error").textContent = job.error || "Something went wrong.";
        $("progress-failed").hidden = false;
        return;
      }
    } catch (e) {
      if (handleAuthError(e)) return;
      $("progress-step").textContent = e.message + " Retrying...";
    }
    state.pollTimer = setTimeout(poll, POLL_MS);
  }

  // ---------- review ----------
  async function loadAttributes() {
    if (!state.attrs) state.attrs = await api("/config/attributes");
    return state.attrs;
  }

  async function openReview(job) {
    await loadAttributes();
    state.items = job.items.map((it) => ({ ...it, candidate_id: it.origin === "text" ? null : it.id }));
    const warn = $("warnings");
    warn.replaceChildren(...(job.warnings || []).map((w) => Object.assign(document.createElement("li"), { textContent: w })));
    $("grid").replaceChildren(...state.items.map(cardFor));
    updateReviewCounts();
    $("save-error").textContent = "";
    $("add-error").textContent = "";
    show("review-view");
  }

  function badge(cls, text, title) {
    const b = document.createElement("span");
    b.className = "badge " + cls;
    b.textContent = text;
    if (title) b.title = title;
    return b;
  }

  function cardFor(item) {
    const li = $("card-tpl").content.firstElementChild.cloneNode(true);
    li.dataset.key = item.id;
    const img = li.querySelector("img");
    if (item.thumbnail) {
      img.src = item.thumbnail;
      img.alt = item.name;
      li.querySelector(".thumb-text").remove();
    } else {
      img.remove();
      li.querySelector(".thumb").classList.add("text-only");
    }
    const name = li.querySelector(".name");
    name.value = item.name;
    name.addEventListener("input", () => { item.name = name.value; });

    const flags = li.querySelector(".flags");
    if (item.low_confidence) flags.append(badge("low", "Low confidence", "Check this one. The labels may be wrong"));
    if (item.likely_duplicate_of) {
      flags.append(badge("dup", "Maybe a duplicate",
        `Looks like "${item.likely_duplicate_of.name}" already in your closet (similarity ${item.likely_duplicate_of.score.toFixed(2)})`));
    }
    if (item.seen_count > 1) flags.append(badge("seen", `Seen ${item.seen_count} times`, "Merged from several photos or frames"));

    const selects = li.querySelector(".selects");
    for (const field of FIELDS) {
      const label = document.createElement("label");
      label.textContent = FIELD_LABELS[field];
      const sel = document.createElement("select");
      const values = state.attrs[field] || [];
      if (field !== "category") sel.append(new Option("Not set", ""));
      for (const v of values) sel.append(new Option(v, v));
      sel.value = item.attributes[field] ?? "";
      sel.addEventListener("change", () => {
        item.attributes[field] = sel.value || null;
        if (field === "fabric") item.attributes.fabric_confidence = sel.value && sel.value !== "unknown" ? "high" : "low";
      });
      label.append(sel);
      selects.append(label);
    }

    li.querySelector(".remove").addEventListener("click", () => removeCard(li, item));
    return li;
  }

  function removeCard(li, item) {
    state.items = state.items.filter((x) => x !== item);
    updateReviewCounts();
    li.classList.add("leaving");
    const done = () => li.remove();
    li.addEventListener("transitionend", done, { once: true });
    setTimeout(done, 300); // in case no transition runs
  }

  function updateReviewCounts() {
    const n = state.items.length;
    $("review-title").textContent = n === 1 ? "Review 1 item" : `Review ${n} items`;
    $("review-empty").hidden = n > 0;
    $("save").textContent = "Save all";
    $("save-count").textContent = n === 1 ? "1 item ready" : `${n} items ready`;
    $("save").disabled = n === 0;
  }

  async function addMissing(e) {
    e.preventDefault();
    const text = $("add-text").value.trim();
    if (!text) return;
    const btn = $("add-btn");
    btn.disabled = true;
    btn.textContent = "Adding...";
    $("add-error").textContent = "";
    try {
      await loadAttributes();
      const { items } = await api("/closet/parse-text", { method: "POST", json: { text } });
      if (!items.length) {
        $("add-error").textContent = "No clothing found in that text. Try something like \"navy wool coat\".";
        return;
      }
      for (const it of items) {
        const item = { ...it, candidate_id: null };
        state.items.push(item);
        $("grid").append(cardFor(item));
      }
      $("add-text").value = "";
      updateReviewCounts();
    } catch (err) {
      if (!handleAuthError(err)) $("add-error").textContent = err.message;
    } finally {
      btn.disabled = false;
      btn.textContent = "Add";
    }
  }

  async function saveAll() {
    const btn = $("save");
    const blank = state.items.find((it) => !it.name.trim());
    if (blank) { $("save-error").textContent = "Every item needs a name."; return; }
    btn.disabled = true;
    btn.textContent = "Saving...";
    $("save-error").textContent = "";
    const items = state.items.map((it) => ({
      candidate_id: it.candidate_id, name: it.name.trim(), description: it.description || "",
      attributes: it.attributes,
    }));
    try {
      const res = await api(`/closet/ingest/${state.jobId}/save`, { method: "POST", json: { items } });
      showSaved(res.saved);
    } catch (e) {
      if (!handleAuthError(e)) $("save-error").textContent = e.message;
      updateReviewCounts();
      btn.disabled = state.items.length === 0;
    }
  }

  function showSaved(count) {
    $("saved-title").textContent = count === 1 ? "Saved 1 item to your closet" : `Saved ${count} items to your closet`;
    show("saved-view");
    loadCloset();
  }

  function resetUpload() {
    clearTimeout(state.pollTimer);
    state.files = [];
    state.jobId = null;
    state.items = [];
    renderFiles();
    $("intake-text").value = "";
    $("files").value = "";
    $("upload-error").textContent = "";
    $("progress-title").textContent = "Reading your clothes";
    show("upload-view");
    loadCloset();
  }

  // ---------- closet gallery ----------
  const TRASH_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/></svg>';
  const UNDO_MS = 5000;
  const ROTATE_MS = 3500;
  let pending = null; // { li, next, id, timer }: a delete waiting out its undo window
  let rotateTimer = null;
  let paused = false;

  function setCount() {
    const n = $("closet").querySelectorAll(".mini:not(.removing)").length;
    $("closet-count").textContent = n ? `(${n})` : "";
    $("closet-empty").hidden = n > 0;
    updateNav();
  }

  function tile(it) {
    const li = document.createElement("li");
    li.className = "mini";
    li.dataset.id = it.id;
    const thumb = document.createElement("div");
    thumb.className = "thumb";
    if (it.image_data) {
      const img = document.createElement("img");
      img.src = it.image_data; img.alt = it.name; img.loading = "lazy";
      thumb.append(img);
    } else {
      thumb.append(Object.assign(document.createElement("span"), { className: "thumb-text", textContent: "No photo" }));
    }
    const del = document.createElement("button");
    del.type = "button";
    del.className = "trash";
    del.innerHTML = TRASH_ICON;
    del.setAttribute("aria-label", `Delete ${it.name}`);
    del.title = "Delete";
    del.addEventListener("click", () => removePiece(li, it));
    thumb.append(del);
    const name = Object.assign(document.createElement("span"), { className: "mini-name", textContent: it.name, title: it.name });
    const meta = Object.assign(document.createElement("span"), {
      className: "mini-meta",
      textContent: it.status === "incoming" ? "Incoming" : (it.attributes?.category || ""),
    });
    li.append(thumb, name, meta);
    return li;
  }

  async function loadCloset() {
    try {
      const { items } = await api("/closet?include_images=1");
      $("closet").replaceChildren(...items.slice().reverse().map(tile));
      setCount();
      startRotation();
    } catch (e) {
      handleAuthError(e);
    }
  }

  // Delete with undo: the piece leaves the gallery at once; the DELETE is sent after the undo window.
  function removePiece(li, it) {
    if (pending) commitDelete(); // one undo at a time: finish the previous delete now
    const next = li.nextSibling;
    li.classList.add("removing");
    setTimeout(() => { if (li.classList.contains("removing")) li.remove(); setCount(); }, 260);
    pending = { li, next, id: it.id, timer: setTimeout(commitDelete, UNDO_MS) };
    $("undo-text").textContent = `Removed ${it.name}.`;
    $("undo-bar").hidden = false;
    setCount();
  }

  async function commitDelete() {
    if (!pending) return;
    const { id, li, next, timer } = pending;
    pending = null;
    clearTimeout(timer);
    $("undo-bar").hidden = true;
    try {
      await api(`/closet/${encodeURIComponent(id)}`, { method: "DELETE" });
    } catch (e) {
      if (handleAuthError(e)) return;
      // Not deleted on the server: put it back so the gallery stays true.
      li.classList.remove("removing");
      $("closet").insertBefore(li, next && next.isConnected ? next : null);
      setCount();
      alert(`Could not delete ${li.querySelector(".mini-name")?.textContent || "this item"}. Try again.`);
    }
  }

  $("undo").addEventListener("click", () => {
    if (!pending) return;
    const { li, next, timer } = pending;
    pending = null;
    clearTimeout(timer);
    li.classList.remove("removing");
    if (!li.isConnected) $("closet").insertBefore(li, next && next.isConnected ? next : null);
    $("undo-bar").hidden = true;
    setCount();
  });
  // Leaving the page during the undo window still deletes (keepalive survives the unload).
  window.addEventListener("pagehide", () => {
    if (!pending) return;
    const token = getToken();
    fetch(`/closet/${encodeURIComponent(pending.id)}`, { method: "DELETE", keepalive: true,
      headers: token ? { Authorization: "Bearer " + token } : {} });
    pending = null;
  });

  // Rotating gallery: one piece every ROTATE_MS, back to the start at the end. Paused on hover, focus or
  // touch, when everything fits, and when the shopper prefers reduced motion.
  const gallery = () => $("closet");
  const step = () => (gallery().querySelector(".mini")?.getBoundingClientRect().width || 180) + 16;
  const overflowing = () => gallery().scrollWidth - gallery().clientWidth > 4;

  function updateNav() {
    const show = overflowing();
    $("gallery-prev").hidden = !show;
    $("gallery-next").hidden = !show;
  }

  function move(dir) {
    const g = gallery();
    const atEnd = g.scrollLeft + g.clientWidth >= g.scrollWidth - 4;
    const atStart = g.scrollLeft <= 4;
    if (dir > 0 && atEnd) g.scrollTo({ left: 0, behavior: "smooth" });
    else if (dir < 0 && atStart) g.scrollTo({ left: g.scrollWidth, behavior: "smooth" });
    else g.scrollBy({ left: dir * step(), behavior: "smooth" });
  }

  function startRotation() {
    clearInterval(rotateTimer);
    updateNav();
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    rotateTimer = setInterval(() => { if (!paused && !document.hidden && overflowing()) move(1); }, ROTATE_MS);
  }

  const g0 = gallery();
  g0.addEventListener("mouseenter", () => { paused = true; });
  g0.addEventListener("mouseleave", () => { paused = false; });
  g0.addEventListener("focusin", () => { paused = true; });
  g0.addEventListener("focusout", () => { paused = false; });
  g0.addEventListener("touchstart", () => { paused = true; }, { passive: true });
  $("gallery-prev").addEventListener("click", () => move(-1));
  $("gallery-next").addEventListener("click", () => move(1));
  window.addEventListener("resize", updateNav);


  // ---------- boot ----------
  async function boot() {
    try {
      const { user } = await api("/auth/me");
      $("who").textContent = "Logged in as " + user.email;
      show("upload-view");
      loadCloset();
    } catch (e) {
      if (!handleAuthError(e)) { show("upload-view"); $("upload-error").textContent = e.message; }
    }
  }

  const drop = $("drop");
  $("files").addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, () => drop.classList.remove("over")));
  drop.addEventListener("drop", (e) => { e.preventDefault(); addFiles(e.dataTransfer.files); });
  $("start").addEventListener("click", startIngest);
  $("retry").addEventListener("click", resetUpload);
  $("restart").addEventListener("click", resetUpload);
  $("again").addEventListener("click", resetUpload);
  $("add-form").addEventListener("submit", addMissing);
  $("save").addEventListener("click", saveAll);
  $("logout").addEventListener("click", () => { setToken(null); show("login-view"); });
  $("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = e.submitter; btn.disabled = true; $("login-error").textContent = "";
    try {
      const { token } = await api("/auth/login", { method: "POST", json: { email: $("email").value, password: $("password").value } });
      setToken(token);
      await boot();
    } catch (err) {
      $("login-error").textContent = err.message;
    } finally {
      btn.disabled = false;
    }
  });

  // Opened from the extension popup ("View my wardrobe"): the login token comes in the URL fragment, which the
  // browser never sends to a server. Save it, then clear it from the address bar right away.
  const handoff = new URLSearchParams(location.hash.slice(1)).get("token");
  if (handoff) {
    setToken(handoff);
    history.replaceState(null, "", location.pathname + location.search);
  }

  getToken() ? boot() : show("login-view");
})();
