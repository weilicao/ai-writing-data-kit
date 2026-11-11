// Overleaf history export (paper-ai-authorship collection kit, v1).
//
// Run it in the browser console on any overleaf.com page while logged in. Before pasting, set the paper's
// title, e.g.   window.KIT_CONFIG = {title: "My Paper Title"}
// It then finds every project of yours whose name matches the title (drafts, copies, camera-ready, arXiv or
// resubmission versions) and exports the full edit history of each into one file, overleaf_export.json, in
// your Downloads folder. Nothing is sent anywhere else.
//   Instead of a title you can give project ids: window.KIT_CONFIG = {projects: ["<24-hex id>", ...]}.
//   With no KIT_CONFIG, it exports the project open in the current tab. Add dryRun: true to save nothing.
//
// What it collects for each project
//   updates               every grouped history update, oldest first: authors (name only, no email), times,
//                         origin (e.g. GitHub sync), file operations, and the diff of each changed file.
//                         Unchanged text inside a diff is stored as its length only ({u: 1234}).
//   full_attributed_diff  for each text file, the diff from v0 to the last version, with full text and the
//                         author of every inserted span (this is how the final text is known).
//   threads               review-panel comment threads; labels: named versions; filetree: files added/removed.
// It also records who ran it (your Overleaf name, used as the contributor's name).
//
// A few hundred updates take 1-5 minutes per project. Leave the tab open until "Saved" is printed.
// While it runs, window.KIT_STATUS.state is "running"; then "done" or "error", with a summary.
window.KIT_STATUS = { script: "overleaf", state: "running" };
window.KIT_RUN = (async () => {
 try {
  const KIT_VERSION = "overleaf-export-2";
  const CONCURRENCY = 4;
  const cfg = window.KIT_CONFIG || {};
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const status = (msg) => { console.log(msg); window.KIT_STATUS = { script: "overleaf", state: "running", summary: msg }; };
  async function getJSON(url, tries = 6, init = {}) {
    for (let i = 0; i < tries; i++) {
      const r = await fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" }, ...init });
      if (r.ok) return r.json();
      if (r.status === 401 || r.status === 403) throw new Error(`${r.status} for ${url}: are you logged in to Overleaf with access to this project?`);
      if (r.status === 404) return { __error: 404 };
      await sleep(1000 * 2 ** i); // 429 / 5xx: back off and retry
    }
    throw new Error(`gave up on ${url}`);
  }
  async function pool(items, fn) {
    let next = 0;
    const worker = async () => { while (next < items.length) { const i = next++; await fn(items[i], i); } };
    await Promise.all(Array.from({ length: CONCURRENCY }, worker));
  }
  function meta(name) {
    const el = document.querySelector(`meta[name="${name}"]`);
    if (!el) return null;
    const c = el.getAttribute("content");
    if (c && /^[\[{]/.test(c)) { try { return JSON.parse(c); } catch (e) { /* plain string */ } }
    return c;
  }
  const stripUser = (u) => ({ id: u.id, first_name: u.first_name || "", last_name: u.last_name || "" });
  function stripMeta(m0) {
    if (!m0) return m0;
    const m = { ...m0 };
    if (Array.isArray(m.users)) m.users = m.users.map((u) => (u && typeof u === "object" ? stripUser(u) : u));
    return m;
  }

  // which projects
  let projects = [];
  const STOP = new Set("a an the of for and in on to with is are via from by at as".split(" "));
  const words = (s) => (String(s).toLowerCase().match(/[a-z0-9]+/g) || []).filter((w) => !STOP.has(w));
  if (Array.isArray(cfg.projects) && cfg.projects.length) {
    projects = cfg.projects.map((id) => ({ id, name: null }));
  } else if (cfg.title) {
    let list = await getJSON("/user/projects");
    list = (list && list.projects) || [];
    if (!list.length) {
      const csrf = meta("ol-csrfToken");
      const r = await getJSON("/api/project", 3, { method: "POST", headers: { Accept: "application/json",
        "Content-Type": "application/json", "X-Csrf-Token": csrf }, body: JSON.stringify({ filters: {},
        page: { size: 2000 }, sort: { by: "lastUpdated", order: "desc" } }) });
      list = (r && r.projects) || [];
    }
    const T = new Set(words(cfg.title));
    for (const p of list) {
      if (p.trashed) continue;
      const N = new Set(words(p.name));
      const score = T.size ? [...T].filter((w) => N.has(w)).length / T.size : 0;
      if (score >= 0.6) projects.push({ id: p._id || p.id, name: p.name, title_match: Math.round(score * 100) / 100 });
    }
    if (!projects.length) throw new Error(`No Overleaf project name matches "${cfg.title}" (${list.length} projects checked). Pass window.KIT_CONFIG = {projects: [...ids]} instead.`);
  } else {
    const pid = (location.pathname.match(/\/project\/([0-9a-f]{24})/) || [])[1];
    if (!pid) throw new Error('Set window.KIT_CONFIG = {title: "..."} first, or open the project in this tab.');
    projects = [{ id: pid, name: (meta("ol-projectName") || document.title.replace(/\s+-\s+Overleaf.*$/, "")) }];
  }
  status(`Overleaf export: ${projects.length} project(s): ${projects.map((p) => p.name || p.id).join(" | ")}`);

  async function exportProject(pid, name) {
    let updates = [], before = null;
    for (;;) {
      const page = await getJSON(`/project/${pid}/updates?min_count=50${before ? `&before=${before}` : ""}`);
      updates.push(...(page.updates || []));
      if (!page.nextBeforeTimestamp) break;
      before = page.nextBeforeTimestamp;
    }
    updates.reverse();
    if (!updates.length) return { source: "overleaf", project_id: pid, project_name: name, warnings: ["no history returned"], updates: [] };
    const lastV = updates[updates.length - 1].toV;
    const warnings = [];
    if (updates[0].fromV !== 0)
      warnings.push(`history starts at v${updates[0].fromV}, not v0: older history is hidden (on the free plan Overleaf shows only the last 24 hours)`);
    for (let i = 1; i < updates.length; i++)
      if (updates[i].fromV !== updates[i - 1].toV) warnings.push(`gap between v${updates[i - 1].toV} and v${updates[i].fromV}`);

    const jobs = [];
    updates.forEach((u, i) => (u.pathnames || []).forEach((p) => jobs.push([i, p])));
    updates = updates.map((u) => ({ fromV: u.fromV, toV: u.toV, meta: stripMeta(u.meta), pathnames: u.pathnames || [],
                                    project_ops: u.project_ops || [], labels: u.labels || [], diffs: {} }));
    let done = 0;
    await pool(jobs, async ([i, p]) => {
      const u = updates[i];
      const r = await getJSON(`/project/${pid}/diff?pathname=${encodeURIComponent(p)}&from=${u.fromV}&to=${u.toV}`);
      const d = r.diff;
      u.diffs[p] = Array.isArray(d)
        ? d.map((s) => {
            const t = { ...s };
            if (typeof t.u === "string") t.u = t.u.length;
            if (t.meta) t.meta = stripMeta(t.meta);
            return t;
          })
        : d || r; // binary files: {binary: true}
      if (++done % 50 === 0) status(`  ${name || pid}: ${done}/${jobs.length} diffs`);
    });

    const ft = await getJSON(`/project/${pid}/filetree/diff?from=0&to=${lastV}`);
    const files = [];
    for (const f of ft.diff || []) {
      if (f.operation === "removed") continue;
      const p = f.newPathname || f.pathname;
      if (f.editable !== false && !/\.(pdf|png|jpe?g|gif|eps|zip|svg)$/i.test(p)) files.push(p);
    }
    const full = {};
    await pool(files, async (p) => {
      const r = await getJSON(`/project/${pid}/diff?pathname=${encodeURIComponent(p)}&from=0&to=${lastV}`);
      if (Array.isArray(r.diff)) full[p] = r.diff.map((s) => (s.meta ? { ...s, meta: stripMeta(s.meta) } : s));
    });

    const threads = await getJSON(`/project/${pid}/threads`);
    for (const t of Object.values(threads || {}))
      for (const m of (t && t.messages) || []) if (m.user && typeof m.user === "object") m.user = stripUser(m.user);
    const labels = await getJSON(`/project/${pid}/labels`);
    const users = {};
    for (const u of updates) for (const x of (u.meta && u.meta.users) || []) if (x && x.id) users[x.id] = x;
    return {
      source: "overleaf", kit_version: KIT_VERSION, project_id: pid, project_name: name,
      exported_at: new Date().toISOString(), last_version: lastV,
      notes: "updates: grouped history updates oldest-first; diffs per pathname from fromV to toV; unchanged segments " +
             "compacted to {u: length}. full_attributed_diff: diff from v0 to last_version with full text and per-span " +
             "author meta. User emails removed.",
      warnings, updates, full_attributed_diff: full, threads, labels, filetree: ft, users: Object.values(users),
    };
  }

  const out = [];
  for (const p of projects) {
    status(`Exporting ${p.name || p.id}...`);
    const o = await exportProject(p.id, p.name);
    if (p.title_match != null) o.title_match = p.title_match;
    out.push(o);
  }
  const me = meta("ol-user") || {};
  const bundle = {
    source: "overleaf", kit_version: KIT_VERSION, exported_at: new Date().toISOString(),
    exported_by: { id: meta("ol-user_id") || me.id || me._id || null, first_name: me.first_name || null,
                   last_name: me.last_name || null, email: me.email || meta("ol-usersEmail") || null },
    title: cfg.title || null, projects: out,
  };
  const blob = new Blob([JSON.stringify(bundle)], { type: "application/json" });
  if (cfg.dryRun) {
    window.KIT_RESULT = bundle; // kept in the page only, nothing saved
  } else {
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: "overleaf_export.json" });
    document.body.appendChild(a); a.click(); a.remove();
  }
  const lines = out.map((o) => `  ${o.project_name || o.project_id}: ${o.updates.length} updates` +
    (o.updates.length ? ` (v${o.updates[0].fromV}-v${o.last_version}), ${Object.keys(o.full_attributed_diff).length} files` : "") +
    (o.warnings.length ? `\n    WARNINGS: ${o.warnings.join("; ")}` : ""));
  const summary = `${cfg.dryRun ? "Dry run, nothing saved" : "Saved overleaf_export.json"} (${(blob.size / 1e6).toFixed(1)} MB), ${out.length} project(s):\n${lines.join("\n")}`;
  console.log(summary);
  window.KIT_STATUS = { script: "overleaf", state: "done", summary };
  return summary;
 } catch (e) {
  window.KIT_STATUS = { script: "overleaf", state: "error", summary: String(e) };
  throw e;
 }
})();
