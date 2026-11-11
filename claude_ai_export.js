// claude.ai chat export (paper-ai-authorship collection kit, v1).
//
// Run it in the browser console on https://claude.ai while logged in. Set SINCE / UNTIL below to the
// writing period first (a few days before you started the draft to a week after the deadline), or set
// window.KIT_CONFIG = {since: "YYYY-MM-DD", until: "YYYY-MM-DD"} before pasting.
// It saves claude_ai_<org>.json to your Downloads folder, one file per account (organization) that has chats.
// Nothing is sent anywhere else.
//
// What it collects
//   Every conversation that was active during the window (created before UNTIL and last updated after SINCE),
//   with all messages, including edited and retried branches (tree=True), files you attached (their extracted
//   text), and text Claude wrote into artifacts or files. For conversations inside a claude.ai Project, the
//   project's knowledge files are included once per project (drafts are often uploaded there).
//
// Before sending, you can remove off-topic conversations with make_bundle.py --exclude-chat (see README).
const SINCE = (window.KIT_CONFIG || {}).since || "2026-01-01";   // EDIT: first day of the writing period (UTC)
const UNTIL = (window.KIT_CONFIG || {}).until || "2026-02-15";   // EDIT: last day of the writing period (UTC), inclusive
const DRY_RUN = !!(window.KIT_CONFIG || {}).dryRun;              // true = only print counts, save nothing
window.KIT_STATUS = { script: "claude.ai", state: "running" };
window.KIT_RUN = (async () => {
 try {
  const KIT_VERSION = "claude-ai-export-1";
  const since = new Date(SINCE + "T00:00:00Z"), until = new Date(UNTIL + "T23:59:59.999Z");
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  async function getJSON(url, tries = 6) {
    for (let i = 0; i < tries; i++) {
      const r = await fetch(url, { credentials: "include" });
      if (r.ok) return r.json();
      if (r.status === 403 || r.status === 404) return { __error: r.status };
      if (r.status === 401) throw new Error("Not logged in to claude.ai.");
      await sleep(1000 * 2 ** i); // 429 / 5xx
    }
    throw new Error(`gave up on ${url}`);
  }

  const orgs = await getJSON("/api/organizations");
  const report = [];
  for (const org of orgs) {
    const caps = org.capabilities || [];
    if (!caps.includes("chat")) { report.push(`${org.name}: no chat capability (API/Console account), skipped`); continue; }
    const base = `/api/organizations/${org.uuid}`;

    // list conversations (paged; stops when a page repeats or comes back short)
    const listed = new Map();
    for (let offset = 0, limit = 100; ; offset += limit) {
      const page = await getJSON(`${base}/chat_conversations?limit=${limit}&offset=${offset}`);
      if (!Array.isArray(page)) { report.push(`${org.name}: listing failed (${page.__error})`); break; }
      const before = listed.size;
      for (const c of page) listed.set(c.uuid, c);
      if (page.length < limit || listed.size === before) break;
    }
    const inWindow = [...listed.values()].filter((c) => new Date(c.created_at) <= until && new Date(c.updated_at) >= since);

    const conversations = [], projects = {};
    let n = 0, messages = 0;
    for (const c of inWindow) {
      const full = await getJSON(`${base}/chat_conversations/${c.uuid}?tree=True&rendering_mode=messages&render_all_tools=true`);
      if (full.__error) { report.push(`${org.name}: could not read conversation ${c.uuid} (${full.__error})`); continue; }
      full.project_uuid = c.project_uuid || (c.project && c.project.uuid) || full.project_uuid || null;
      messages += (full.chat_messages || []).length;
      if (!DRY_RUN) conversations.push(full);
      if (full.project_uuid && !(full.project_uuid in projects)) {
        const p = await getJSON(`${base}/projects/${full.project_uuid}`);
        const docs = await getJSON(`${base}/projects/${full.project_uuid}/docs`);
        projects[full.project_uuid] = {
          name: p.name || null, description: p.description || null, prompt_template: p.prompt_template || null,
          docs: Array.isArray(docs) ? docs.map((d) => ({ uuid: d.uuid, file_name: d.file_name, created_at: d.created_at,
                                                         content: DRY_RUN ? (d.content || "").length : d.content })) : docs,
        };
      }
      if (++n % 10 === 0) console.log(`  ${org.name}: ${n}/${inWindow.length}`);
    }
    const line = `${org.name}: ${listed.size} conversations in total, ${inWindow.length} active ${SINCE}..${UNTIL}, ` +
                 `${messages} messages, ${Object.keys(projects).length} projects`;
    report.push(line);
    console.log(line);
    if (DRY_RUN) continue;

    const out = { source: "claude.ai", kit_version: KIT_VERSION, org: org.uuid, org_name: org.name,
                  exported_at: new Date().toISOString(), window: `active ${SINCE}..${UNTIL}`, conversations, projects };
    const blob = new Blob([JSON.stringify(out)], { type: "application/json" });
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: `claude_ai_${org.uuid}.json` });
    document.body.appendChild(a); a.click(); a.remove();
    report.push(`  saved claude_ai_${org.uuid}.json (${(blob.size / 1e6).toFixed(1)} MB)`);
    await sleep(1500); // let the browser start the download before the next one
  }
  const summary = report.join("\n");
  console.log(summary);
  window.KIT_STATUS = { script: "claude.ai", state: "done", summary };
  return summary;
 } catch (e) {
  window.KIT_STATUS = { script: "claude.ai", state: "error", summary: String(e) };
  throw e;
 }
})();
