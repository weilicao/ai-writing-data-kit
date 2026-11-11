// ChatGPT chat export (paper-ai-authorship collection kit, v1).
//
// Run it in the browser console on https://chatgpt.com while logged in. Set SINCE / UNTIL below to the
// writing period first (or set window.KIT_CONFIG = {since, until} before pasting). It saves chatgpt_export.json
// to your Downloads folder. Nothing is sent anywhere else. This replaces waiting for ChatGPT's "Export data"
// email, which still works as a fallback (bundle.py import).
//
// What it collects
//   Every conversation that was active during the window (created before UNTIL and last updated after SINCE),
//   including archived ones and chats inside ChatGPT Projects, with every message and edited/regenerated branch
//   (the full message tree, as in ChatGPT's own export).
const SINCE = (window.KIT_CONFIG || {}).since || "2026-01-01";   // EDIT: first day of the writing period (UTC)
const UNTIL = (window.KIT_CONFIG || {}).until || "2026-02-15";   // EDIT: last day of the writing period (UTC), inclusive
const DRY_RUN = !!(window.KIT_CONFIG || {}).dryRun;              // true = only print counts, save nothing
window.KIT_STATUS = { script: "chatgpt", state: "running" };
window.KIT_RUN = (async () => {
  const KIT_VERSION = "chatgpt-export-1";
  const since = new Date(SINCE + "T00:00:00Z"), until = new Date(UNTIL + "T23:59:59.999Z");
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const when = (x) => (x == null ? null : typeof x === "number" ? new Date(x * 1000) : new Date(x));
  try {
    const session = await fetch("/api/auth/session", { credentials: "include" }).then((r) => r.json());
    if (!session.accessToken) throw new Error("Not logged in to chatgpt.com.");
    const headers = { Authorization: `Bearer ${session.accessToken}` };
    async function getJSON(url, tries = 7) {
      for (let i = 0; i < tries; i++) {
        const r = await fetch(url, { credentials: "include", headers });
        if (r.ok) return r.json();
        if (r.status === 401) throw new Error("ChatGPT session expired: reload the page, log in, run again.");
        if (r.status === 403 || r.status === 404) return { __error: r.status };
        await sleep(2000 * 2 ** i); // 429 / 5xx
      }
      throw new Error(`gave up on ${url}`);
    }

    // list conversations, newest activity first; stop paging once a page is entirely older than SINCE
    const listed = new Map(), report = [];
    async function page(urlFor) {
      for (let offset = 0, limit = 100; ; offset += limit) {
        const p = await getJSON(urlFor(offset, limit));
        const items = (p && p.items) || [];
        const before = listed.size;
        for (const c of items) listed.set(c.id, c);
        const allOld = items.length && items.every((c) => when(c.update_time) < since);
        if (items.length < limit || listed.size === before || allOld) return;
      }
    }
    await page((o, l) => `/backend-api/conversations?offset=${o}&limit=${l}&order=updated`);
    await page((o, l) => `/backend-api/conversations?offset=${o}&limit=${l}&order=updated&is_archived=true`);
    // chats inside ChatGPT Projects are listed per project
    let projects = 0;
    const side = await getJSON("/backend-api/gizmos/snorlax/sidebar?conversations_per_gizmo=0");
    for (const it of (side && side.items) || []) {
      const g = (it.gizmo && (it.gizmo.gizmo || it.gizmo)) || {};
      if (!g.id) continue;
      projects++;
      let cursor = 0;
      for (let n = 0; n < 50 && cursor != null; n++) {
        const p = await getJSON(`/backend-api/gizmos/${g.id}/conversations?cursor=${cursor}`);
        for (const c of (p && p.items) || []) listed.set(c.id, c);
        cursor = p && p.cursor;
      }
    }

    const inWindow = [...listed.values()].filter((c) => when(c.create_time) <= until && when(c.update_time || c.create_time) >= since);
    const conversations = [];
    let messages = 0, failed = 0;
    for (const [i, c] of inWindow.entries()) {
      const full = await getJSON(`/backend-api/conversation/${c.id}`);
      if (full.__error) { failed++; continue; }
      full.conversation_id = full.conversation_id || c.id;
      messages += Object.values(full.mapping || {}).filter((n) => n.message && ["user", "assistant"].includes(n.message.author.role)).length;
      if (!DRY_RUN) conversations.push(full);
      if ((i + 1) % 10 === 0) console.log(`  ${i + 1}/${inWindow.length}`);
      await sleep(250);
    }
    report.push(`ChatGPT: ${listed.size} conversations listed (${projects} projects), ${inWindow.length} active ${SINCE}..${UNTIL}, ` +
                `${messages} messages` + (failed ? `, ${failed} could not be read` : ""));
    if (!DRY_RUN) {
      const out = { source: "chatgpt.com", kit_version: KIT_VERSION, exported_at: new Date().toISOString(),
                    window: `active ${SINCE}..${UNTIL}`, conversations };
      const blob = new Blob([JSON.stringify(out)], { type: "application/json" });
      const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: "chatgpt_export.json" });
      document.body.appendChild(a); a.click(); a.remove();
      report.push(`saved chatgpt_export.json (${(blob.size / 1e6).toFixed(1)} MB)`);
    }
    const summary = report.join("\n");
    console.log(summary);
    window.KIT_STATUS = { script: "chatgpt", state: "done", summary };
    return summary;
  } catch (e) {
    window.KIT_STATUS = { script: "chatgpt", state: "error", summary: String(e) };
    throw e;
  }
})();
