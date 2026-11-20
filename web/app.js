/* DupeHunter UI. All text is placed with textContent (Kit.el); no innerHTML anywhere. */
(() => {
  "use strict";
  const { $, el, clear, toast, api, post, bytes, num, dt, dur, initTheme } = window.Kit;
  initTheme($("#theme"));

  const S = { status: null, view: "scan", sel: new Set(), selBytes: new Map(), kind: "exact", groups: [], total: 0, scanId: null,
              loaded: 0, busy: false, shownResultsFor: null };
  const TITLES = { scan: "Scan", exact: "Exact duplicates", similar: "Similar photos", safety: "Safety & help" };

  // ---- navigation
  function show(view) {
    S.view = view;
    document.querySelectorAll(".nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
    $("#view-title").textContent = TITLES[view];
    $("#v-scan").hidden = view !== "scan";
    $("#v-results").hidden = !(view === "exact" || view === "similar");
    $("#v-safety").hidden = view !== "safety";
    if (view === "exact" || view === "similar") { S.kind = view; loadGroups(true); }
  }
  document.querySelectorAll(".nav-btn").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));

  // ---- scan setup
  function renderRoots() {
    const roots = (S.status && S.status.settings.roots) || [];
    const box = clear($("#roots"));
    roots.forEach((r) => box.appendChild(el("span", { className: "chip" }, el("span", { className: "path", text: r, title: r }),
      el("button", { type: "button", "aria-label": "Remove " + r, title: "Remove", text: "×", on: { click: () => saveRoots(roots.filter((x) => x !== r)) } }))));
    $("#roots-empty").hidden = roots.length > 0;
  }
  async function saveRoots(roots) {
    try { const r = await post("/api/settings", { roots }); S.status.settings = r.settings; renderRoots(); } catch (e) { toast(e.message, true); }
  }
  function addRoot(p) {
    p = (p || "").trim();
    if (!p) return;
    const roots = S.status.settings.roots.slice();
    if (!roots.includes(p)) roots.push(p);
    saveRoots(roots);
  }
  $("#btn-add").addEventListener("click", () => { addRoot($("#path-input").value); $("#path-input").value = ""; });
  $("#path-input").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#btn-add").click(); });
  $("#btn-browse").addEventListener("click", async () => {
    try {
      const r = await post("/api/pick-folder", {});
      if (r.ok) addRoot(r.path); else if (r.error) toast(r.error, true);
    } catch (e) { toast(e.message, true); }
  });
  function renderQuick() {
    const box = clear($("#quick"));
    const add = (label, path) => box.appendChild(el("button", { type: "button", className: "small", title: path, text: label, on: { click: () => addRoot(path) } }));
    (S.status.folders || []).forEach((f) => add(f.name, f.path));
    (S.status.drives || []).forEach((d) => add("Drive " + d, d));
  }
  function bindOption(id, key, conv) {
    $(id).addEventListener("change", async (e) => {
      const v = e.target.type === "checkbox" ? e.target.checked : conv(e.target.value);
      try { const r = await post("/api/settings", { [key]: v }); S.status.settings = r.settings; } catch (er) { toast(er.message, true); }
    });
  }
  bindOption("#opt-near", "near"); bindOption("#opt-sys", "include_system");
  bindOption("#opt-sim", "similarity", Number); bindOption("#opt-min", "min_size", Number);
  function renderOptions() {
    const s = S.status.settings;
    $("#opt-near").checked = s.near; $("#opt-sys").checked = s.include_system;
    $("#opt-sim").value = String(s.similarity); $("#opt-min").value = String(s.min_size);
    const cap = S.status.capabilities;
    const note = $("#cap-note");
    if (!cap.images) { note.hidden = false; note.textContent = "Photo comparison is unavailable in this build (the image library is missing). Exact duplicates still work."; $("#opt-near").disabled = true; }
    else if (!cap.heic) { note.hidden = false; note.textContent = "Note: iPhone HEIC/HEIF photos are not supported yet, so they are only matched when they are exact copies."; }
    else note.hidden = true;
  }
  $("#btn-scan").addEventListener("click", async () => {
    try {
      S.sel.clear(); S.selBytes.clear();
      await post("/api/scan", {});
      toast("Scan started");
      tick();
    } catch (e) { toast(e.message, true); }
  });
  $("#btn-cancel").addEventListener("click", () => post("/api/cancel", {}).then(tick).catch((e) => toast(e.message, true)));

  // ---- status polling
  let firstRender = true;
  function renderStatus() {
    const st = S.status, sc = st.scan;
    if (firstRender) { renderRoots(); renderQuick(); renderOptions(); firstRender = false; }
    const running = sc && sc.state === "running";
    $("#btn-scan").disabled = !!running; $("#btn-cancel").hidden = !running;
    const noRoots = !st.settings.roots.length;
    $("#scan-note").textContent = running ? "" : noRoots ? "Add a folder first." : "Nothing is changed during a scan.";
    if (running) {
      $("#progress-panel").hidden = false;
      $("#phase").textContent = sc.phase;
      const bar = $("#phase-bar");
      if (sc.phase === "Listing files") {
        bar.classList.add("indet"); bar.style.width = "";
        $("#phase-detail").textContent = num(sc.files_seen) + " files (" + bytes(sc.bytes_seen) + ") found · " + dur(sc.elapsed);
      } else {
        bar.classList.remove("indet");
        bar.style.width = (sc.total ? Math.min(100, (sc.done / sc.total) * 100) : 100) + "%";
        $("#phase-detail").textContent = num(sc.done) + " of " + num(sc.total) + " · " + dur(sc.elapsed);
      }
      $("#status").textContent = sc.phase + "…";
      $("#status").className = "status";
    } else {
      $("#progress-panel").hidden = true;
      if (!sc) $("#status").textContent = "Ready";
      else if (sc.state === "error") { $("#status").textContent = "Scan failed: " + sc.error; $("#status").className = "status error"; }
      else { $("#status").className = "status"; $("#status").textContent = (sc.state === "cancelled" ? "Scan cancelled" : "Scan finished") + " in " + dur(sc.elapsed); }
    }
    const sum = sc && sc.summary;
    $("#n-exact").textContent = sum ? String(sum.exact_groups) : "";
    $("#n-similar").textContent = sum ? String(sum.similar_groups) : "";
    const rc = $("#scan-result");
    if (sum) {
      rc.hidden = false; clear(rc);
      const card = (l, v, s) => rc.appendChild(el("div", { className: "card" }, el("span", { className: "label", text: l }), el("span", { className: "value", text: v }), el("span", { className: "sub", text: s })));
      card("Files checked", num(sc.files_seen), bytes(sc.bytes_seen));
      card("Exact duplicate sets", num(sum.exact_groups), bytes(sum.exact_reclaimable) + " could be freed");
      card("Similar photo sets", num(sum.similar_groups), sc.near ? bytes(sum.similar_reclaimable) + " could be freed" : "photo search was off");
      card("Skipped", num(sc.skipped_count), "folders or files that could not be read");
    } else rc.hidden = true;
    const sk = $("#skipped-box");
    sk.hidden = !(sc && sc.state !== "running" && sc.skipped_count);
    if (!sk.hidden) $("#skipped-sum").textContent = num(sc.skipped_count) + " items were skipped (permissions, unreadable files). Show list";
    if (sc && sc.notes && sc.notes.length && !running) { $("#scan-note").textContent = sc.notes.join(" "); }
    if (sc && S.scanId !== sc.id) { S.scanId = sc.id; S.sel.clear(); S.selBytes.clear(); S.groups = []; S.shownResultsFor = null; updateSel(); }
    if (sc && !running && sc.state !== "error" && S.shownResultsFor !== sc.id + ":" + (sum ? sum.exact_groups + "/" + sum.similar_groups : "")) {
      S.shownResultsFor = sc.id + ":" + (sum ? sum.exact_groups + "/" + sum.similar_groups : "");
      if (S.view === "exact" || S.view === "similar") loadGroups(true);
    }
  }
  let skippedLoadedFor = null;
  $("#skipped-box").addEventListener("toggle", async (e) => {
    if (!e.target.open || skippedLoadedFor === S.scanId) return;
    try {
      const r = await api("/api/skipped"); skippedLoadedFor = S.scanId;
      const tb = clear($("#skipped-rows"));
      r.items.forEach((i) => tb.appendChild(el("tr", {}, el("td", { text: i.path, title: i.path }), el("td", { text: i.reason }))));
      if (r.count > r.items.length) tb.appendChild(el("tr", {}, el("td", { colspan: 2, className: "muted", text: "…and " + num(r.count - r.items.length) + " more" })));
    } catch (er) { toast(er.message, true); }
  });

  let timer;
  async function tick() {
    clearTimeout(timer);
    try { S.status = await api("/api/status"); renderStatus(); } catch (e) { $("#status").textContent = "Cannot reach DupeHunter"; $("#status").className = "status error"; }
    const running = S.status && S.status.scan && S.status.scan.state === "running";
    timer = setTimeout(tick, running ? 700 : 3000);
  }

  // ---- results
  const PAGE = 30;
  async function loadGroups(reset) {
    if (!S.status || !S.status.scan || S.status.scan.state === "running") { clear($("#groups")).appendChild(el("p", { className: "empty", text: "Run a scan to see results here." })); $("#btn-more").hidden = true; return; }
    if (reset) { S.groups = []; clear($("#groups")); }
    try {
      const q = new URLSearchParams({ kind: S.kind, sort: $("#sort").value, filter: $("#filter").value, offset: String(S.groups.length), limit: String(PAGE) });
      const r = await api("/api/groups?" + q);
      S.total = r.total; S.groups.push(...r.groups);
      const box = $("#groups");
      r.groups.forEach((g) => box.appendChild(renderGroup(g)));
      if (!S.groups.length) box.appendChild(el("p", { className: "empty", text: S.kind === "exact" ? "No exact duplicates found. Nice and tidy." : "No similar photos found." }));
      $("#btn-more").hidden = S.groups.length >= S.total;
      $("#btn-more").textContent = "Show more sets (" + num(S.total - S.groups.length) + " left)";
      renderCards();
    } catch (e) { toast(e.message, true); }
  }
  function renderCards() {
    const sum = S.status.scan.summary, box = clear($("#res-cards"));
    const ex = S.kind === "exact";
    const card = (l, v, s) => box.appendChild(el("div", { className: "card" }, el("span", { className: "label", text: l }), el("span", { className: "value", text: v }), el("span", { className: "sub", text: s })));
    card("Sets", num(ex ? sum.exact_groups : sum.similar_groups), num(ex ? sum.exact_files : sum.similar_files) + " files in total");
    card("Could be freed", bytes(ex ? sum.exact_reclaimable : sum.similar_reclaimable), "keeping one file per set");
  }
  function renderGroup(g) {
    const head = el("div", { className: "group-head" },
      el("span", { className: "title", text: g.count + " " + (g.kind === "exact" ? "identical copies" : "similar photos") }),
      el("span", { className: "muted small", text: (g.kind === "exact" ? bytes(g.size) + " each" : "up to " + bytes(g.size)) + " · " + bytes(g.reclaimable) + " could be freed" }),
      el("span", { className: "spacer" }));
    const wrap = el("div", { className: "group", "data-gid": g.id }, head);
    g.files.forEach((f) => wrap.appendChild(renderFile(f, wrap)));
    return wrap;
  }
  function renderFile(f, wrap) {
    const cb = el("input", { type: "checkbox", "aria-label": "Remove " + f.path });
    cb.checked = S.sel.has(f.id);
    const row = el("div", { className: "frow" + (cb.checked ? " picked" : ""), "data-fid": f.id });
    cb.addEventListener("change", () => {
      if (cb.checked) { S.sel.add(f.id); S.selBytes.set(f.id, f.size); } else { S.sel.delete(f.id); S.selBytes.delete(f.id); }
      row.classList.toggle("picked", cb.checked); updateSel();
    });
    const pic = f.image ? el("img", { className: "thumb", src: "/api/thumb?id=" + f.id, alt: "", loading: "lazy", width: 56, height: 56 }) : el("div", { className: "ph", text: (f.name.split(".").pop() || "file").slice(0, 5).toUpperCase() });
    if (f.image) pic.addEventListener("error", () => { const ph = el("div", { className: "ph", text: "no preview" }); if (pic.parentNode) pic.parentNode.replaceChild(ph, pic); });
    row.append(cb, pic,
      el("div", { className: "frow-txt" }, el("div", { className: "p-name", text: f.name, title: f.path }), el("div", { className: "p-dir", title: f.path }, el("bdi", { text: f.dir }))),
      el("div", { className: "num hide-s" }, bytes(f.size), f.w ? el("div", { className: "muted small", text: f.w + "×" + f.h }) : null),
      el("div", { className: "muted small hide-s", text: dt(f.mtime) }),
      el("button", { type: "button", className: "small", text: "Show in folder", on: { click: () => post("/api/reveal", { id: f.id }).then((r) => { if (!r.ok) toast("Only works on Windows", true); }).catch((e) => toast(e.message, true)) } }));
    return row;
  }
  function updateSel() {
    let b = 0; S.selBytes.forEach((v) => { b += v; });
    $("#sel-text").textContent = S.sel.size ? num(S.sel.size) + " selected · " + bytes(b) : "Nothing selected";
    $("#btn-delete").disabled = !S.sel.size;
  }
  function refreshChecks() {
    document.querySelectorAll(".frow").forEach((r) => {
      const on = S.sel.has(Number(r.dataset.fid)); r.classList.toggle("picked", on);
      const cb = r.querySelector("input[type=checkbox]"); if (cb) cb.checked = on;
    });
    updateSel();
  }
  document.querySelectorAll("[data-rule]").forEach((b) => b.addEventListener("click", async () => {
    try {
      const r = await post("/api/suggest", { rule: b.dataset.rule, kind: S.kind });
      S.sel.clear(); S.selBytes.clear();
      r.items.forEach(([i, sz]) => { S.sel.add(i); S.selBytes.set(i, sz); });
      refreshChecks();
      toast(num(r.ids.length) + " files selected (" + bytes(r.bytes) + "). Nothing has been removed yet.");
    } catch (e) { toast(e.message, true); }
  }));
  $("#btn-clear").addEventListener("click", () => { S.sel.clear(); S.selBytes.clear(); refreshChecks(); });
  $("#sort").addEventListener("change", () => loadGroups(true));
  let ft; $("#filter").addEventListener("input", () => { clearTimeout(ft); ft = setTimeout(() => loadGroups(true), 300); });
  $("#btn-more").addEventListener("click", () => loadGroups(false));

  // ---- delete flow
  function modal(children) {
    const root = $("#modal-root");
    const back = el("div", { className: "modal-back", role: "presentation" }, el("div", { className: "modal", role: "dialog", "aria-modal": "true" }, children));
    root.appendChild(back);
    const close = () => { if (back.parentNode) back.parentNode.removeChild(back); document.removeEventListener("keydown", onKey); };
    const onKey = (e) => { if (e.key === "Escape") close(); };
    document.addEventListener("keydown", onKey);
    return { close, back };
  }
  $("#btn-delete").addEventListener("click", async () => {
    const ids = Array.from(S.sel);
    let pv;
    try { pv = await post("/api/preview-delete", { ids }); } catch (e) { toast(e.message, true); return; }
    const confirm = el("button", { type: "button", className: "danger", text: "Move " + num(pv.count) + " files to Recycle Bin" });
    const cancel = el("button", { type: "button", text: "Cancel" });
    const m = modal([
      el("h2", { text: "Move " + num(pv.count) + " files to the Recycle Bin?" }),
      el("p", { text: "That frees about " + bytes(pv.bytes) + ". You can restore them from the Recycle Bin. One copy in every set stays where it is." }),
      el("ul", {}, pv.sample.map((p) => el("li", { text: p })), pv.count > pv.sample.length ? el("li", { text: "…and " + num(pv.count - pv.sample.length) + " more" }) : null),
      el("div", { className: "actions" }, cancel, confirm)]);
    cancel.addEventListener("click", m.close);
    cancel.focus();
    confirm.addEventListener("click", async () => {
      confirm.disabled = true; cancel.disabled = true;
      try {
        const r = await post("/api/delete", { ids, confirm: "recycle", scan: S.scanId });
        m.close();
        r.failed.forEach(() => {});
        S.sel.clear(); S.selBytes.clear();
        S.status.scan.summary = r.summary;
        $("#n-exact").textContent = String(r.summary.exact_groups); $("#n-similar").textContent = String(r.summary.similar_groups);
        toast(num(r.recycled) + " files moved to the Recycle Bin (" + bytes(r.freed) + ")" + (r.failed_count ? ". " + r.failed_count + " left alone: " + r.failed[0].reason : ""), r.failed_count > 0);
        updateSel(); loadGroups(true);
      } catch (e) { m.close(); toast(e.message, true); }
    });
  });

  tick();
})();
