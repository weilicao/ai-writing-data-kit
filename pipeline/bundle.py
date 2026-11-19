"""Assemble and check a data bundle for the paper AI-authorship study.

In the published kit (pipeline/bundle.py) only `check` is part of the procedure; `pack` and `auto` refuse to run
there, because the bundle (mydata/) never leaves the contributor's machine. The study team uses the other
commands as reference tools.

Python >= 3.9, standard library only. Everything runs on your own machine; nothing is uploaded.

    python3 bundle.py init      BUNDLE --title "TITLE"      create the folder and about.json (title and consent)
    python3 bundle.py fill      BUNDLE                      read the Overleaf export from Downloads and fill in the
                                                            rest of about.json: window, people, files, contributor
    python3 bundle.py add       BUNDLE FILE...              copy in overleaf_export / claude_ai_* / chatgpt_export downloads
    python3 bundle.py import    BUNDLE EXPORT.zip           ChatGPT (or claude.ai) "Export data" zip, cut to the window
    python3 bundle.py agents    BUNDLE [--archive F.tgz]    find coding-agent sessions about the paper on this computer
                                                            (Claude Code, Codex, Gemini CLI, Cursor, VS Code/Copilot,
                                                            Windsurf), or in an archive brought from a server
    python3 bundle.py remote-command BUNDLE                 the command to run on a server or cluster to fetch its sessions
    python3 bundle.py git       BUNDLE [--repo O/N | --path DIR]  history of the paper files in a GitHub-synced repo
    python3 bundle.py agent-logs BUNDLE DIR --tool NAME     sessions from one folder (other tools or locations)
    python3 bundle.py list-chats BUNDLE                     list the chats in the bundle, to review before sending
    python3 bundle.py exclude-chat BUNDLE ID... [--reason]  remove off-topic chats (only the id and date are kept)
    python3 bundle.py check     BUNDLE                      check everything, write CHECK_REPORT.txt
    python3 bundle.py pack      BUNDLE                      check, then zip the bundle for sending
    python3 bundle.py auto      BUNDLE                      all of the above in one unattended run (see README,
                                                            "Unattended mode"): reads the browser downloads, keeps
                                                            the chats related to the paper, checks and packs

Only the paper title and consent come from you; `fill` works out the rest from the Overleaf history, including
the window (writing period) that the chat exports and the other commands use.
"""
import argparse
import json
import socket
import sqlite3
import subprocess
import tarfile
import tempfile
import os
import re
import shutil
import sys
import zipfile
from collections import Counter, deque
from datetime import datetime, timedelta, timezone

KIT_VERSION = "1"
HERE = os.path.dirname(os.path.abspath(__file__))

ABOUT_TEMPLATE = {
    "kit_version": KIT_VERSION,
    "_help": "You fill in paper.title and consent. `bundle.py fill` fills in everything else from the Overleaf "
             "export. The optional section can stay empty.",
    "paper": {"title": "", "overleaf_projects": [], "text_files": [], "comment_macros": []},
    "consent": {
        "share_chats_and_overleaf_history_including_coauthors": False,
        "ok_to_quote_short_excerpts_in_publications": False,
    },
    "contributor": {"name": "", "email": ""},
    "window": {"since": "", "until": ""},
    "people": [],
    "coding_agents_used": [],
    "agent_sessions": {},
    "git_repos": [],
    "optional": {"venue": "", "deadline_utc": "", "submitted_overleaf_version": None, "public_link": "",
                 "ai_tools_used_for": "", "writing_outside_overleaf": ""},
    "auto": {
        "downloads_dir": "~/Downloads",
        "chat_filter": {
            "keep": "related",
            "keywords": [],
            "min_shared_6word_sequences": 3,
            "always_keep_ids": [],
            "always_drop_ids": [],
        },
        "agent_logs": [],
        "agent_log_match": "",
        "upload": "after my review",
    },
    "excluded_chats": [],
    "notes": "",
}

SECRET_PATTERNS = {
    "Anthropic key": r"sk-ant-[A-Za-z0-9_-]{20,}",
    "OpenAI key": r"sk-(?:proj-)?[A-Za-z0-9_-]{32,}",
    "GitHub token": r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}",
    "AWS key": r"AKIA[0-9A-Z]{16}",
    "Slack token": r"xox[baprs]-[A-Za-z0-9-]{10,}",
    "Hugging Face token": r"hf_[A-Za-z0-9]{30,}",
    "private key": r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
}
# not preceded by a word character or "-", so slugs like "...-task-at-..." don't look like "sk-..." keys
SECRET_RE = re.compile("|".join(f"(?P<g{i}>(?<![A-Za-z0-9_-])(?:{p}))" for i, p in enumerate(SECRET_PATTERNS.values())))
DOWNLOAD_NAME = re.compile(r"^(overleaf_export|overleaf_[0-9a-f]{24}|claude_ai_[0-9a-f]{8}-[0-9a-f-]{27}|chatgpt_export)"
                           r"(?: ?\(\d+\))?\.json$")
DEFAULT_KEYWORDS = [r"overleaf", r"\blatex\b", r"\.(?:tex|bib)\b",
                    r"\\(?:section|subsection|cite[pt]?|begin|textbf|emph|label|ref|paragraph)\b"]
WORD = re.compile(r"[a-z0-9]+")
CHAT_LISTS = ("conversations", "code_sessions", "codex_tasks")
# where coding agents keep their session logs, relative to the home folder (macOS, Linux, Windows)
EDITORS = {"cursor": "Cursor", "vscode": "Code", "vscode-insiders": "Code - Insiders", "windsurf": "Windsurf"}
AGENT_STORES = {
    "claude-code": [".claude/projects"],
    "codex": [".codex/sessions", ".codex/archived_sessions"],
    "gemini-cli": [".gemini/tmp"],
    **{tool: [f"Library/Application Support/{app}/User", f".config/{app}/User", f"AppData/Roaming/{app}/User"]
       for tool, app in EDITORS.items()},
}
EDITOR_SUBDIRS = ("workspaceStorage", "globalStorage")
TEXT_EXT = (".jsonl", ".json", ".md", ".txt")
DB_EXT = (".vscdb", ".db", ".sqlite")
REMOTE_DIRS = ".claude/projects .codex/sessions .codex/archived_sessions .gemini/tmp"
EMAIL_IN_TEXT = re.compile(r"<([^<>@\s]+)@([^<>\s]+)>")
AI_TRAILER = re.compile(r"co-authored-by:[^\n]*(claude|anthropic|codex|openai|copilot|cursor|gemini|devin|aider)"
                        r"|generated with[^\n]*(claude|codex|copilot|cursor|aider)", re.I)
COMMENT_DEF = re.compile(r"\\(?:re)?newcommand\*?\{?\\([A-Za-z]+)\}?\s*(?:\[\d\])?\s*\{[^\n]{0,120}?"
                         r"(?:color|todo|marginpar|\\textbf\{\[)")


# ---------------------------------------------------------------- helpers

def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def about_path(bundle):
    return os.path.join(bundle, "about.json")


def write_about(bundle, about):
    with open(about_path(bundle), "w", encoding="utf-8") as f:
        json.dump(about, f, indent=2, ensure_ascii=False)


def window(bundle):
    a = load(about_path(bundle))
    try:
        since = datetime.strptime(a["window"]["since"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        until = datetime.strptime(a["window"]["until"], "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
    except (KeyError, ValueError):
        sys.exit("The window is not set yet: run `bundle.py fill` after the Overleaf export (or set window.since "
                 "and window.until, YYYY-MM-DD, in about.json).")
    return since, until


def to_dt(x):
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return datetime.fromtimestamp(x / 1000 if x > 1e11 else x, tz=timezone.utc)
    s = str(x).replace("Z", "+00:00")
    m = re.match(r"(.*T\d\d:\d\d:\d\d)\.(\d+)(.*)", s)
    if m:  # Python 3.9 accepts only 3 or 6 fractional digits
        s = f"{m.group(1)}.{m.group(2)[:6].ljust(6, '0')}{m.group(3)}"
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def active(created, updated, since, until):
    c, u = to_dt(created), to_dt(updated) or to_dt(created)
    return c is not None and c < until and u >= since


def chat_files(bundle):
    for name in sorted(os.listdir(bundle)):
        if name.endswith(".json") and (name.startswith("claude_ai_") or name.startswith("chatgpt")):
            yield os.path.join(bundle, name)


def conv_id(c):
    return c.get("uuid") or c.get("conversation_id") or c.get("id")


def conv_messages(c):
    if "chat_messages" in c:
        return len(c["chat_messages"])
    if "events" in c:
        ev = c["events"]
        return ev if isinstance(ev, int) else sum(1 for e in ev if e.get("event_type") in ("user", "assistant"))
    if "mapping" in c:
        return sum(1 for n in (c.get("mapping") or {}).values()
                   if n.get("message") and n["message"]["author"]["role"] in ("user", "assistant"))
    return len(((c.get("detail") or {}).get("turns")) or []) or 1


def records(d):
    """(list name, record) for every chat, web agent session and Codex task in an export."""
    for key in CHAT_LISTS:
        for c in d.get(key) or []:
            yield key, c


def rec_created(c):
    return to_dt(c.get("created_at") or c.get("create_time"))


def apply_exclusions(bundle, path):
    """Drop chats already excluded in about.json, so re-adding or re-importing a file cannot bring them back."""
    gone = {e["id"] for e in load(about_path(bundle)).get("excluded_chats") or []}
    d = load(path)
    dropped = 0
    for key in CHAT_LISTS:
        if gone and isinstance(d.get(key), list):
            keep = [c for c in d[key] if conv_id(c) not in gone]
            dropped += len(d[key]) - len(keep)
            d[key] = keep
    if dropped:
        print(f"  dropped {dropped} chats excluded earlier")
        save(path, d)


def write_download(bundle, data, name):
    dest = os.path.join(bundle, name)
    save(dest, data)
    apply_exclusions(bundle, dest)
    print(f"wrote {os.path.relpath(dest)} ({os.path.getsize(dest) / 1e6:.1f} MB)")


# ---------------------------------------------------------------- commands

def cmd_init(args):
    os.makedirs(args.bundle, exist_ok=True)
    p = about_path(args.bundle)
    if os.path.exists(p):
        print(f"{p} exists, left as is")
    else:
        about = json.loads(json.dumps(ABOUT_TEMPLATE))
        about["paper"]["title"] = args.title or ""
        write_about(args.bundle, about)
        print(f"created {p}" + ("" if args.title else ": put the paper's title in paper.title") +
              "; set consent once you agree, and run `fill` after the Overleaf export")


def bundle_name(d):
    """File name inside the bundle for an export from the kit's browser scripts, or None."""
    if not isinstance(d, dict):
        return None
    if d.get("source") == "overleaf" and isinstance(d.get("projects"), list):
        return "overleaf_export"
    if d.get("source") == "overleaf" and d.get("project_id"):
        return f"overleaf_{d['project_id']}.json"
    if d.get("source") == "claude.ai":
        return f"claude_ai_{d.get('org', 'unknown')}.json"
    if d.get("source") in ("chatgpt", "chatgpt.com"):
        return "chatgpt.json"
    return None


def add_file(bundle, src):
    d = load(src)
    name = bundle_name(d)
    if not name:
        sys.exit(f"{src}: not a file from the kit's export scripts (use `import` for Export-data zips)")
    if name == "overleaf_export":  # one download with every project matching the title
        about = load(about_path(bundle))
        about["_overleaf_export"] = {"exported_by": d.get("exported_by") or {}, "title": d.get("title"),
                                     "exported_at": d.get("exported_at")}
        write_about(bundle, about)
        for o in d["projects"]:
            o.setdefault("source", "overleaf")
            save(os.path.join(bundle, f"overleaf_{o['project_id']}.json"), o)
            print(f"added {os.path.basename(src)}: {o.get('project_name') or o['project_id']}")
        return name
    shutil.copyfile(src, os.path.join(bundle, name))
    print(f"added {os.path.basename(src)} as {name}")
    if not name.startswith("overleaf_"):
        apply_exclusions(bundle, os.path.join(bundle, name))
    return name


def cmd_add(args):
    for src in args.files:
        add_file(args.bundle, src)


def _read_export(path):
    """Yield the conversation lists inside an Export-data zip (or a bare conversations.json)."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                if re.search(r"(^|/)conversations[^/]*\.json$", n):
                    yield json.loads(z.read(n).decode("utf-8"))
    else:
        yield load(path)


def cmd_import(args):
    import_export(args.bundle, args.export)


def import_export(bundle, export):
    since, until = window(bundle)
    gpt, claude = [], []
    for convs in _read_export(export):
        for c in convs if isinstance(convs, list) else convs.get("conversations", []):
            if "mapping" in c:
                if active(c.get("create_time"), c.get("update_time"), since, until):
                    gpt.append(c)
            elif "chat_messages" in c:
                if active(c.get("created_at"), c.get("updated_at"), since, until):
                    claude.append(c)
    stamp = {"exported_at": datetime.now(timezone.utc).isoformat(), "kit_version": KIT_VERSION,
             "window": f"active {since.date()}..{(until - timedelta(days=1)).date()}", "imported_from": "export-data zip"}
    if not gpt and not claude:
        sys.exit("No conversations from the window found in that export.")
    if gpt:
        write_download(bundle, {"source": "chatgpt.com", **stamp, "conversations": gpt}, "chatgpt.json")
    if claude:
        write_download(bundle, {"source": "claude.ai", "org": "export", **stamp, "conversations": claude},
                       "claude_ai_export.json")
        print("note: claude.ai's Export data has no edited/retried branches; the browser script is preferred")
    print(f"kept {len(gpt)} ChatGPT and {len(claude)} claude.ai conversations active in the window")


def cmd_agent_logs(args):
    copy_agent_logs(args.bundle, args.dir, args.tool, args.match, args.dry_run)


def copy_agent_logs(bundle, src_dir, tool, pattern=None, dry_run=False):
    """Copy session files from the window. With a pattern, keep files matching it; without, keep files that
    contain the paper's text or title (the same test as for chats)."""
    since, until = window(bundle)
    if pattern:
        rx = re.compile(pattern)
        match = rx.search
        label = f"/{pattern}/"
    else:
        paper, title_rx, need = paper_shingles(bundle), title_regex(bundle), 3
        if not paper:
            sys.exit("No Overleaf export in the bundle yet, so the paper's text is unknown: add it first, or pass --match")
        def match(text):
            return (title_rx and title_rx.search(text)) or count_shared(session_text(text), paper, need) >= need
        label = "the paper's text or title"
    stamp = re.compile(r'"(?:timestamp|created_at|ts)"\s*:\s*"(\d{4}-\d\d-\d\d)')
    picked = []
    for root, _, files in os.walk(os.path.expanduser(src_dir)):
        for fn in files:
            if not fn.endswith((".jsonl", ".json")):
                continue
            p = os.path.join(root, fn)
            if datetime.fromtimestamp(os.path.getmtime(p), tz=timezone.utc) < since:
                continue
            with open(p, encoding="utf-8", errors="replace") as f:
                text = f.read()
            days = {d for d in stamp.findall(text)}
            in_window = any(since.date().isoformat() <= d < until.date().isoformat() for d in days) if days else True
            if in_window and match(text):
                picked.append(p)
    base = os.path.expanduser(src_dir)
    total = 0
    for p in picked:
        rel = os.path.relpath(p, base)
        total += os.path.getsize(p)
        print(f"  {'(dry run) ' if dry_run else ''}{rel}  {os.path.getsize(p) / 1e3:.0f} kB")
        if not dry_run:
            dest = os.path.join(bundle, "agent_logs", tool, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(p, dest)
    print(f"{len(picked)} session files ({total / 1e6:.1f} MB) from {src_dir} matched the window and {label}"
          + (" (nothing copied)" if dry_run else f", copied to agent_logs/{tool}/"))
    return picked


def cmd_list_chats(args):
    for path in chat_files(args.bundle):
        d = load(path)
        print(f"== {os.path.basename(path)}")
        for key, c in sorted(records(d), key=lambda kc: str(rec_created(kc[1]))):
            kind = "" if key == "conversations" else f"[{key}] "
            print(f"  {conv_id(c)}  {rec_created(c):%Y-%m-%d}  {conv_messages(c):3d} msgs  {kind}{c.get('name') or c.get('title') or ''}")


def cmd_exclude_chat(args):
    about = load(about_path(args.bundle))
    wanted = set(args.ids)
    for path in chat_files(args.bundle):
        d = load(path)
        changed = False
        for key in CHAT_LISTS:
            keep = []
            for c in d.get(key) or []:
                cid = conv_id(c)
                if any(cid == w or (len(w) >= 8 and cid.startswith(w)) for w in wanted):
                    about.setdefault("excluded_chats", []).append({
                        "file": os.path.basename(path), "id": cid, "created_at": rec_created(c).isoformat(),
                        "messages": conv_messages(c), "reason": args.reason})
                    print(f"removed {cid} from {os.path.basename(path)}")
                    changed = True
                else:
                    keep.append(c)
            if key in d:
                d[key] = keep
        if changed:
            save(path, d)
    write_about(args.bundle, about)


# ---------------------------------------------------------------- unattended run

def count_shared(text, paper, need, k=6):
    """Distinct 6-word sequences of text that also occur in the paper, counted up to `need`."""
    if not paper:
        return 0
    window_, seen = deque(maxlen=k), set()
    for m in WORD.finditer(text.lower()):
        window_.append(m.group())
        if len(window_) == k:
            sh = " ".join(window_)
            if sh in paper and sh not in seen:
                seen.add(sh)
                if len(seen) >= need:
                    break
    return len(seen)


def session_text(raw):
    """Text of a JSONL session log, with JSON escapes undone so words are not glued together."""
    parts = []
    for line in raw.splitlines():
        try:
            parts.extend(strings(json.loads(line)))
        except ValueError:
            parts.append(line)
    return "\n".join(parts)


def title_regex(bundle):
    t = ((load(about_path(bundle)).get("paper") or {}).get("title") or "").strip()
    return re.compile(r"\s+".join(map(re.escape, t.split())), re.I) if len(t.split()) >= 3 else None


def text_edits(o):
    """Updates that changed text (not uploads only, not history resyncs)."""
    for up in o.get("updates") or []:
        if ((up.get("meta") or {}).get("origin") or {}).get("kind") == "history-resync":
            continue
        if any(isinstance(segs, list) and any("i" in x or "d" in x for x in segs) for segs in (up.get("diffs") or {}).values()):
            yield up


def fill_about(bundle, quiet=False):
    """Fill about.json from the Overleaf exports in the bundle: window, people, files, macros, contributor."""
    about = load(about_path(bundle))
    files = sorted(f for f in os.listdir(bundle) if f.startswith("overleaf_") and f.endswith(".json"))
    if not files:
        sys.exit("No Overleaf export yet: run overleaf_export.js, then `bundle.py fill` (it reads ~/Downloads).")
    projects, first, last, people, macros, inserted_by = [], None, None, {}, set(), Counter()
    for f in files:
        o = load(os.path.join(bundle, f))
        edits = list(text_edits(o))
        t0 = to_dt(edits[0]["meta"]["start_ts"]) if edits else None
        t1 = to_dt(edits[-1]["meta"]["end_ts"]) if edits else None
        fad = text_fad(o)
        final = {p: "".join(x.get("u", x.get("i", "")) for x in segs if "d" not in x) for p, segs in fad.items()}
        tex = sorted((p for p in final if p.endswith(".tex")), key=lambda p: -len(final[p]))
        projects.append({"project_id": o.get("project_id"), "name": o.get("project_name"),
                         "first_edit": t0 and t0.isoformat(), "last_edit": t1 and t1.isoformat(),
                         "text_edits": len(edits), "tex_files": tex, "github_sync": o.get("github_sync")})
        if t0:
            first = min(first, t0) if first else t0
            last = max(last, t1) if last else t1
        for up in o.get("updates") or []:
            origin = up["meta"].get("origin")
            for segs in (up.get("diffs") or {}).values():
                if isinstance(segs, list):
                    for x in segs:
                        if "i" in x:
                            inserted_by[who(x.get("meta"), origin) if x.get("meta") else who(up["meta"], origin)] += len(x["i"])
            for u in (up.get("meta") or {}).get("users") or []:
                if isinstance(u, dict) and u.get("id"):
                    people[u["id"]] = f"{u.get('first_name', '')} {u.get('last_name', '')}".strip() or u["id"]
        macros |= set(COMMENT_DEF.findall("".join(final.values())))
    projects.sort(key=lambda p: p["first_edit"] or "")
    paper = about.setdefault("paper", {})
    paper["overleaf_projects"] = projects
    paper["text_files"] = sorted({t for p in projects for t in p["tex_files"]})
    paper["comment_macros"] = sorted("\\" + m for m in macros)
    consent = consented(about)
    about["people"] = [{"overleaf_name": n, "inserted_chars": inserted_by.get(n, 0), "agreed_to_share": consent}
                       for _, n in sorted(people.items(), key=lambda kv: -inserted_by.get(kv[1], 0))]
    me = (about.get("_overleaf_export") or {}).get("exported_by") or {}
    contributor = about.setdefault("contributor", {"name": "", "email": ""})
    if not contributor.get("name"):
        contributor["name"] = people.get(me.get("id")) or " ".join(x for x in (me.get("first_name"), me.get("last_name")) if x)
    if not contributor.get("email") and me.get("email"):
        contributor["email"] = me["email"]
    w = about.setdefault("window", {"since": "", "until": ""})
    if first and not (w.get("since") and w.get("set_by") != "fill"):
        w.update(since=(first - timedelta(days=7)).date().isoformat(), until=(last + timedelta(days=7)).date().isoformat(),
                 set_by="fill", rule="a week before the first text edit to a week after the last, over all projects")
    write_about(bundle, about)
    if not quiet:
        print(f"{len(projects)} project(s), text edits {first and first.date()} to {last and last.date()}; "
              f"window {w.get('since')} to {w.get('until')}; {len(people)} people; contributor: {contributor['name'] or '?'}")
        print(f'For the chat exports: window.KIT_CONFIG = {{since: "{w.get("since")}", until: "{w.get("until")}"}}')
    return about


def related_matcher(bundle):
    """A test for 'this text is about the paper': 3 shared 6-word sequences, the title, or a synced repo's name."""
    paper, title_rx, extra = paper_shingles(bundle), title_regex(bundle), repo_regexes(bundle)
    if not paper and not title_rx:
        sys.exit("No Overleaf export in the bundle yet, so the paper is unknown: run the Overleaf export and `fill` first")
    def match(text):
        if (title_rx and title_rx.search(text)) or any(r.search(text) for r in extra):
            return True
        return count_shared(session_text(text), paper, 3) >= 3
    return match


def synced_repos(bundle):
    out = []
    for f in sorted(os.listdir(bundle)):
        if f.startswith("overleaf_") and f.endswith(".json"):
            o = load(os.path.join(bundle, f))
            gs = o.get("github_sync") or {}
            if gs.get("enabled") and gs.get("repo"):
                paths = set((o.get("full_attributed_diff") or {}).keys())
                paths |= {x.get("newPathname") or x.get("pathname") for x in (o.get("filetree") or {}).get("diff") or []}
                out.append((gs["repo"], sorted(p for p in paths if p)))
    return out


def repo_regexes(bundle):
    names = {r.split("/")[-1] for r, _ in synced_repos(bundle)}
    names |= {g.get("repo", "").split("/")[-1] for g in load(about_path(bundle)).get("git_repos") or []}
    return [re.compile(re.escape(n), re.I) for n in sorted(names) if len(n) >= 8]


def run(cmd, env=None):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, errors="replace", env=env, timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(cmd, 1, "", str(e))


def collect_git(bundle, repo=None, path=None):
    """History of the paper's files in each GitHub-synced repo: commits, authors, dates, messages, patches.
    Only the paper's files (the Overleaf project's files, plus any .tex/.bib/.sty/.cls/.bst) are kept."""
    targets = [(repo or os.path.basename(os.path.abspath(path)), path, [])] if (repo or path) else \
              [(r, None, paths) for r, paths in synced_repos(bundle)]
    if not targets:
        print("  no GitHub-synced Overleaf project")
        return []
    about = load(about_path(bundle))
    done = []
    for name, local, paths in targets:
        tmp = None
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        try:
            if not local:
                tmp = tempfile.mkdtemp(prefix="kit_git_")
                r = run(["git", "clone", "--quiet", "--no-checkout", f"https://github.com/{name}.git", tmp], env)
                if r.returncode != 0 and shutil.which("gh"):
                    shutil.rmtree(tmp, ignore_errors=True)
                    r = run(["gh", "repo", "clone", name, tmp, "--", "--quiet", "--no-checkout"], env)
                if r.returncode != 0:
                    msg = (r.stderr or "").strip().splitlines()[-1:] or ["unknown error"]
                    print(f"  {name}: could not clone ({msg[0][:120]}). Log in to GitHub (`gh auth login`) or clone it and "
                          f"run `bundle.py git {bundle} --path <clone>`")
                    about.setdefault("git_repos", [])
                    about["git_repos"] = [g for g in about["git_repos"] if g.get("repo") != name] + \
                        [{"repo": name, "status": "clone failed"}]
                    continue
                local = tmp
            specs = ["*.tex", "*.bib", "*.sty", "*.cls", "*.bst"] + [f":(literal){p}" for p in paths]
            fmt = "%x1e%H%x1f%P%x1f%an%x1f%aI%x1f%cn%x1f%cI%x1f%B%x1f"
            r = run(["git", "-C", local, "log", "--all", "--no-color", "--no-ext-diff", "-p", f"--format={fmt}", "--"] + specs, env)
            commits = []
            for chunk in r.stdout.split("\x1e")[1:]:
                parts = chunk.split("\x1f")
                if len(parts) < 8:
                    continue
                sha, parents, an, ad, cn, cd, msg = parts[:7]
                commits.append({"sha": sha, "parents": parents.split(), "author": an, "author_date": ad,
                                "committer": cn, "commit_date": cd,
                                "message": EMAIL_IN_TEXT.sub(lambda m: f"<...@{m.group(2)}>", msg.strip()),
                                "patch": "\x1f".join(parts[7:]).strip("\n")})
            refs = run(["git", "-C", local, "for-each-ref", "--format=%(refname:short) %(objectname)"], env).stdout.split("\n")
            out = {"source": "git", "kit_version": KIT_VERSION, "repo": name, "collected_at": datetime.now(timezone.utc).isoformat(),
                   "paths": specs, "refs": [x for x in refs if x], "commits": commits}
            dest = os.path.join(bundle, f"git_{name.replace('/', '__')}.json")
            save(dest, out)
            ai = sum(1 for c in commits if AI_TRAILER.search(c["message"]))
            print(f"  {name}: {len(commits)} commits touching the paper's files ({ai} marked as made with an AI agent)")
            about.setdefault("git_repos", [])
            about["git_repos"] = [g for g in about["git_repos"] if g.get("repo") != name] + \
                [{"repo": name, "status": "collected", "commits": len(commits), "ai_marked_commits": ai}]
            done.append(dest)
        finally:
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)
    write_about(bundle, about)
    return done


def cmd_git(args):
    collect_git(args.bundle, args.repo, args.path)


def _db_rows(path, match):
    """Rows of an editor's SQLite store (e.g. Cursor's state.vscdb) whose text is about the paper."""
    tmp = tempfile.mkdtemp(prefix="kit_db_")
    rows = []
    try:
        copy = os.path.join(tmp, "db")
        shutil.copyfile(path, copy)  # the editor may hold a lock on the original
        con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        con.text_factory = lambda b: b.decode("utf-8", "replace")
        for (table,) in con.execute("select name from sqlite_master where type='table'"):
            try:
                cur = con.execute(f'select * from "{table}"')
            except sqlite3.Error:
                continue
            cols = [c[0] for c in cur.description]
            for row in cur:
                vals = [v for v in row if isinstance(v, str) and len(v) > 200]
                if vals and any(match(v) for v in vals):
                    rows.append({"table": table, **{c: v for c, v in zip(cols, row) if isinstance(v, (str, int, float))}})
        con.close()
    except (sqlite3.Error, OSError):
        pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return rows


def scan_agent_store(bundle, tool, base, label, match, since, until):
    """Copy the files (or database rows) of one session store that fall in the window and are about the paper."""
    stamp = re.compile(r'"(?:timestamp|created_at|createdAt|ts|startTime|lastUpdated)"\s*:\s*"(\d{4}-\d\d-\d\d)')
    roots = [os.path.join(base, d) for d in EDITOR_SUBDIRS] if tool in EDITORS else [base]
    dest_root = os.path.join(bundle, "agent_logs", tool, label)
    stats = Counter()
    for root in roots:
        for r, _, files in os.walk(root):
            for fn in files:
                p = os.path.join(r, fn)
                if not fn.endswith(TEXT_EXT + DB_EXT):
                    continue
                try:
                    if datetime.fromtimestamp(os.path.getmtime(p), tz=timezone.utc) < since or os.path.getsize(p) > 2e9:
                        continue
                except OSError:
                    continue
                stats["files_in_window"] += 1
                rel = os.path.relpath(p, base)
                if fn.endswith(DB_EXT):
                    rows = _db_rows(p, match)
                    if rows:
                        dest = os.path.join(dest_root, rel + ".related_rows.jsonl")
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        with open(dest, "w", encoding="utf-8") as f:
                            for row in rows:
                                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                        stats["kept"] += 1
                    continue
                with open(p, encoding="utf-8", errors="replace") as f:
                    text = f.read()
                days = set(stamp.findall(text))
                if days and not any(since.date().isoformat() <= d < until.date().isoformat() for d in days):
                    continue
                if match(text):
                    dest = os.path.join(dest_root, rel)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    shutil.copyfile(p, dest)
                    stats["kept"] += 1
    return stats


def explore_agents(bundle, home=None, label="this-computer", extra=()):
    """Look in every known session store under `home` (default: this computer's home folder)."""
    since, until = window(bundle)
    match = related_matcher(bundle)
    home = os.path.expanduser(home or "~")
    found = {}
    stores = [(tool, os.path.join(home, rel)) for tool, rels in AGENT_STORES.items() for rel in rels]
    stores += [(x["tool"], os.path.expanduser(x["dir"])) for x in extra]
    for tool, base in stores:
        if not os.path.isdir(base):
            continue
        shutil.rmtree(os.path.join(bundle, "agent_logs", tool, label), ignore_errors=True)
        st = scan_agent_store(bundle, tool, base, label, match, since, until)
        key = f"{tool} ({label})"
        prev = found.get(key, Counter())
        found[key] = prev + st if prev else st
        print(f"  {tool} [{label}]: {base}: {st['files_in_window']} files from the window, {st['kept']} about the paper")
    about = load(about_path(bundle))
    sessions = about.get("agent_sessions") or {}
    sessions = {k: v for k, v in sessions.items() if not k.endswith(f"({label})")}
    sessions.update({k: dict(v) for k, v in found.items()})
    about["agent_sessions"] = sessions
    old = (datetime.now(timezone.utc) - until).days > 30
    if old and any(k.startswith("claude-code") and not v.get("kept") for k, v in sessions.items()) \
            or old and not any(k.startswith("claude-code") for k in sessions):
        about["agent_sessions_note"] = ("The writing period ended more than 30 days ago. Claude Code deletes session "
                                        "logs about 30 days after their last activity by default (cleanupPeriodDays), "
                                        "so missing Claude Code sessions do not mean it was not used.")
        print("  note: " + about["agent_sessions_note"])
    write_about(bundle, about)
    return found


def extract_archive(path):
    tmp = tempfile.mkdtemp(prefix="kit_sessions_")
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            for m in z.namelist():
                if not (m.startswith("/") or ".." in m.split("/")):
                    z.extract(m, tmp)
    else:
        with tarfile.open(path) as t:
            safe = [m for m in t.getmembers() if (m.isfile() or m.isdir()) and not m.name.startswith("/")
                    and ".." not in m.name.split("/")]
            t.extractall(tmp, members=safe, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
    return tmp


def cmd_agents(args):
    if not args.no_local:
        explore_agents(args.bundle, extra=auto_cfg(load(about_path(args.bundle))).get("agent_logs") or [])
    for a in args.archive or []:
        tmp = extract_archive(a)
        try:
            label = re.sub(r"^agent_sessions_|\.(tgz|tar\.gz|tar|zip)$", "", os.path.basename(a)) or "archive"
            explore_agents(args.bundle, home=tmp, label=label)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def cmd_remote_command(args):
    since, _ = window(args.bundle)
    find = f"cd ~ && find {REMOTE_DIRS} -type f -newermt {since.date().isoformat()} 2>/dev/null | tar czf - -T -"
    print("Coding-agent sessions on a server or cluster stay on that machine. To fetch the ones from the writing period:")
    print(f"\n  From this computer, if you can ssh to it (replace HOST):\n    ssh HOST '{find}' > agent_sessions_HOST.tgz")
    print(f"\n  Or on the server itself, then copy the file here:\n    ({find}) > ~/agent_sessions_$(hostname -s).tgz")
    print(f"\n  Then:\n    python3 bundle.py agents {args.bundle} --no-local --archive agent_sessions_HOST.tgz")
    print("\n  Only sessions about the paper are kept from the archive; delete the .tgz afterwards.")


def newest_downloads(ddir):
    newest = {}
    for fn in os.listdir(ddir) if os.path.isdir(ddir) else []:
        if DOWNLOAD_NAME.match(fn):
            p = os.path.join(ddir, fn)
            try:
                name = bundle_name(load(p))
            except (ValueError, OSError):
                continue
            if name and (name not in newest or os.path.getmtime(p) > os.path.getmtime(newest[name])):
                newest[name] = p
    return newest


def cmd_fill(args):
    about = load(about_path(args.bundle))
    ddir = os.path.expanduser(args.downloads or auto_cfg(about)["downloads_dir"])
    found = newest_downloads(ddir)
    for name, p in sorted(found.items()):
        if name.startswith("overleaf"):
            add_file(args.bundle, p)
    fill_about(args.bundle)


def auto_cfg(about):
    cfg = dict(ABOUT_TEMPLATE["auto"])
    cfg.update(about.get("auto") or {})
    cfg["chat_filter"] = {**ABOUT_TEMPLATE["auto"]["chat_filter"], **((about.get("auto") or {}).get("chat_filter") or {})}
    return cfg


def strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from strings(v)


def shingles(text, k=6):
    w = WORD.findall(text.lower())
    return {" ".join(w[i:i + k]) for i in range(len(w) - k + 1)}


def paper_shingles(bundle):
    """Every 6-word sequence that was ever in the paper: all inserted text plus the final text of each file."""
    out = set()
    for f in os.listdir(bundle):
        if f.startswith("overleaf_") and f.endswith(".json"):
            o = load(os.path.join(bundle, f))
            for up in o.get("updates") or []:
                for segs in (up.get("diffs") or {}).values():
                    if isinstance(segs, list):
                        out |= shingles(" ".join(x["i"] for x in segs if "i" in x))
            for segs in text_fad(o).values():
                out |= shingles("".join(x.get("u", x.get("i", "")) for x in segs if "d" not in x))
    return out


def text_fad(o):
    """full_attributed_diff without binary files (stored as {"binary": true} or similar, not a segment list)."""
    return {p: v for p, v in (o.get("full_attributed_diff") or {}).items() if isinstance(v, list)}


def filter_chats(bundle, about, cfg):
    """Drop chats unrelated to the paper. Related = shares exact 6-word sequences with the paper's history,
    or matches a keyword. Returns (kept, dropped) rows for the review file."""
    fc = cfg["chat_filter"]
    keywords = [re.compile(k, re.I) for k in list(fc.get("keywords") or []) + DEFAULT_KEYWORDS]
    tr = title_regex(bundle)
    if tr:
        keywords.insert(0, tr)
    keywords += repo_regexes(bundle)
    need = int(fc.get("min_shared_6word_sequences") or 3)
    keep_ids, drop_ids = set(fc.get("always_keep_ids") or []), set(fc.get("always_drop_ids") or [])
    paper = paper_shingles(bundle) if fc.get("keep") != "all" else set()
    kept, dropped = [], []
    for path in chat_files(bundle):
        d = load(path)
        changed = False
        for key in CHAT_LISTS:
            if not isinstance(d.get(key), list):
                continue
            keep = []
            for c in d[key]:
                cid = conv_id(c)
                text = "\n".join(strings(c))
                shared = count_shared(text, paper, need)
                kw = next((k.pattern for k in keywords if k.search(text)), None)
                if cid in drop_ids:
                    why, ok = "auto: listed in always_drop_ids", False
                elif cid in keep_ids:
                    why, ok = "listed in always_keep_ids", True
                elif fc.get("keep") == "all":
                    why, ok = "keep: all", True
                elif shared >= need:
                    why, ok = f"shares {shared}+ 6-word sequences with the paper", True
                elif kw:
                    why, ok = f"keyword /{kw[:40]}/", True
                else:
                    why, ok = f"auto: unrelated to the paper ({shared} shared sequences, no keyword)", False
                created = rec_created(c)
                kind = "" if key == "conversations" else f"[{key}] "
                row = (os.path.basename(path), cid, f"{created:%Y-%m-%d}", conv_messages(c),
                       kind + (c.get("name") or c.get("title") or ""), why)
                if ok:
                    keep.append(c)
                    kept.append(row)
                else:
                    dropped.append(row)
                    about["excluded_chats"].append({"file": row[0], "id": cid, "created_at": created.isoformat(),
                                                    "messages": row[3], "reason": why})
            if len(keep) != len(d[key]):
                d[key] = keep
                changed = True
        if changed:
            save(path, d)
    return kept, dropped


def cmd_auto(args):
    if os.path.basename(HERE) == "pipeline":  # the published kit: the bundle itself is never packed or sent
        sys.exit("In the kit only `check` is used; mydata/ stays on this machine. Run pipeline/analyze.py instead.")
    bundle = args.bundle
    about = load(about_path(bundle))
    cfg = auto_cfg(about)
    # auto exclusions are recomputed on every run; exclusions made by hand stay
    about["excluded_chats"] = [e for e in about.get("excluded_chats") or []
                               if not str(e.get("reason", "")).startswith("auto:")]
    with open(about_path(bundle), "w", encoding="utf-8") as f:
        json.dump(about, f, indent=2, ensure_ascii=False)

    print("== 1. exports from the browser")
    ddir = os.path.expanduser(args.downloads or cfg["downloads_dir"])
    newest = newest_downloads(ddir)
    if not newest:
        print(f"  no kit exports found in {ddir}")
    for name, p in sorted(newest.items()):
        add_file(bundle, p)
    fill_about(bundle)
    for z in args.export or []:
        import_export(bundle, z)

    print("== 2. GitHub-synced repos")
    collect_git(bundle)

    print("== 3. coding-agent sessions")
    explore_agents(bundle, extra=cfg.get("agent_logs") or [])
    for a in sorted(set(os.path.join(d, f) for d in (".", ddir) if os.path.isdir(d) for f in os.listdir(d)
                        if re.match(r"agent_sessions_.*\.(tgz|tar\.gz|zip)$", f))):
        tmp = extract_archive(a)
        try:
            label = re.sub(r"^agent_sessions_|\.(tgz|tar\.gz|zip)$", "", os.path.basename(a))
            explore_agents(bundle, home=tmp, label=label)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    print("== 4. chats unrelated to the paper")
    about = load(about_path(bundle))
    kept, dropped = filter_chats(bundle, about, cfg)
    with open(about_path(bundle), "w", encoding="utf-8") as f:
        json.dump(about, f, indent=2, ensure_ascii=False)
    print(f"  kept {len(kept)}, removed {len(dropped)}")

    # the review file lists chat titles, so it is written next to the bundle, never inside it
    review = os.path.abspath(bundle).rstrip(os.sep) + "_REVIEW.txt"
    with open(review, "w", encoding="utf-8") as f:
        f.write("Chats in the bundle and chats removed from it. This file stays on your computer (it is not in the zip).\n"
                "To change a decision, add the id to auto.chat_filter.always_keep_ids or always_drop_ids in about.json\n"
                "and run `bundle.py auto` again.\n")
        for title, rows in (("KEPT (will be sent)", kept), ("REMOVED (not sent; only id, date and count are recorded)", dropped)):
            f.write(f"\n{title}: {len(rows)}\n")
            for file, cid, day, n, name, why in sorted(rows, key=lambda r: r[2]):
                f.write(f"  {day}  {cid}  {n:3d} msgs  {name[:70]}\n      {file}; {why}\n")

    print("== 5. check and pack")
    report, problems = run_check(bundle)
    print(report)
    print(f"Review list: {review}")
    if problems:
        sys.exit("Not packed: fix the problems above, then run `bundle.py auto` again.")
    print(f"Packed: {pack(bundle)}")
    print(f"Upload: {cfg.get('upload')}")


# ---------------------------------------------------------------- check

def undo_diff(new, segs):
    pos, old = 0, []
    for s in segs:
        if "u" in s:
            old.append(new[pos:pos + s["u"]])
            pos += s["u"]
        elif "i" in s:
            if new[pos:pos + len(s["i"])] != s["i"]:
                raise ValueError("inserted text does not match")
            pos += len(s["i"])
        elif "d" in s:
            old.append(s["d"])
    if pos != len(new):
        raise ValueError(f"diff consumed {pos} of {len(new)} chars")
    return "".join(old)


def who(meta, origin=None):
    users = (meta or {}).get("users") or []
    if users:
        return "+".join(f"{u.get('first_name', '')} {u.get('last_name', '')}".strip() or str(u.get("id"))
                        if isinstance(u, dict) else str(u) for u in users)
    return (origin or {}).get("kind", "unknown")


CONSENT_KEYS = ("use_data_locally_and_share_results",  # INSTRUCTIONS.md, instructions-2 (local analysis)
                "share_chats_sessions_and_histories_including_coauthors",  # instructions-1
                "share_chats_and_overleaf_history_including_coauthors")  # this script's own bundles


def consented(about):
    return any((about.get("consent") or {}).get(k) is True for k in CONSENT_KEYS)


def check_overleaf(path, about, problems, notes, summary):
    name = os.path.basename(path)
    o = load(path)
    ups = o.get("updates") or []
    if not ups:
        problems.append(f"{name}: no history updates")
        return
    fad = text_fad(o)
    if not fad:
        problems.append(f"{name}: full_attributed_diff is missing, so the final text is unknown")
    if ups[0]["fromV"] != 0:
        problems.append(f"{name}: history starts at v{ups[0]['fromV']}, not v0. Older history is hidden (Overleaf "
                        "shows only 24 hours of history unless the project owner has a paid or institutional plan)")
    gaps = [(a["toV"], b["fromV"]) for a, b in zip(ups, ups[1:]) if a["toV"] != b["fromV"]]
    if gaps:
        problems.append(f"{name}: {len(gaps)} gaps in the version sequence, first v{gaps[0][0]}->v{gaps[0][1]}")
    missing = sum(1 for u in ups for p in u.get("pathnames", []) if p not in u.get("diffs", {}))
    failed = sum(1 for u in ups for s in u.get("diffs", {}).values() if isinstance(s, dict) and "__error" in s)
    if missing or failed:
        problems.append(f"{name}: {missing} file diffs missing and {failed} failed to download; run the export again")
    metas = [x.get("meta") or {} for x in ups]
    metas += [s.get("meta") or {} for x in ups for segs in (x.get("diffs") or {}).values() if isinstance(segs, list)
              for s in segs if isinstance(s, dict)]
    metas += [s.get("meta") or {} for segs in (o.get("full_attributed_diff") or {}).values() if isinstance(segs, list)
              for s in segs if isinstance(s, dict)]
    with_email = sum(1 for m in metas for u in m.get("users") or [] if isinstance(u, dict) and "email" in u)
    if with_email:
        notes.append(f"{name}: {with_email} user entries still hold email addresses (in update or segment meta); "
                     "keep only id, first_name, last_name")
    if re.search(r"@[A-Za-z0-9-]+\.[A-Za-z]", json.dumps(o.get("github_sync") or {})):
        notes.append(f"{name}: github_sync contains an email address (keep only enabled, repo, merge_status)")
    for w in o.get("warnings") or []:
        notes.append(f"{name}: export warning: {w}")

    # replay the history backwards from the final text; it must land on v0 (as scripts/parse_raw.py requires)
    final = {p: "".join(s.get("u", s.get("i", "")) for s in segs if "d" not in s) for p, segs in fad.items()}
    v0 = {p: "".join(s.get("u", s.get("d", "")) for s in segs if "i" not in s) for p, segs in fad.items()}
    texts, broken = dict(final), set()
    for up in reversed(ups):
        for p, segs in up.get("diffs", {}).items():
            if isinstance(segs, list) and p in texts and p not in broken:
                try:
                    texts[p] = undo_diff(texts[p], segs)
                except (ValueError, TypeError, KeyError):
                    broken.add(p)
    bad = sorted(p for p in fad if p in broken or texts[p] != v0[p])
    if bad:
        notes.append(f"{name}: replaying the history does not reach v0 for {', '.join(bad)} (renamed or re-uploaded "
                     "files?). Send it anyway; this needs handling on the analysis side")

    authors, origins = Counter(), Counter()
    for up in ups:
        origin = up["meta"].get("origin")
        origins[(origin or {}).get("kind", "editor")] += 1
        for segs in up.get("diffs", {}).values():
            if isinstance(segs, list):
                for s in segs:
                    if "i" in s:
                        authors[who(s.get("meta"), origin) if s.get("meta") else who(up["meta"], origin)] += len(s["i"])
    inserted = "".join(s["i"] for up in ups for segs in up.get("diffs", {}).values() if isinstance(segs, list)
                       for s in segs if "i" in s)
    defined = sorted(set(COMMENT_DEF.findall(inserted + "".join(final.values()))))
    macro_counts = {m: len(re.findall(r"\\" + m + r"\{", inserted)) for m in defined}
    labels = o.get("labels")
    t0, t1 = to_dt(ups[0]["meta"]["start_ts"]), to_dt(ups[-1]["meta"]["end_ts"])
    summary.append(
        f"{name}: \"{o.get('project_name', '')}\", {len(ups)} updates v{ups[0]['fromV']}-v{ups[-1]['toV']}, "
        f"{t0:%Y-%m-%d} to {t1:%Y-%m-%d}, {len(fad)} text files, {len(o.get('threads') or {})} review threads, "
        f"{len(labels) if isinstance(labels, list) else 0} labels; replay to v0 {'OK' if not bad else 'FAILED'}\n"
        f"    inserted chars by author: " + ", ".join(f"{a} {n / 1e3:.1f}k" for a, n in authors.most_common()) + "\n"
        f"    updates by origin: " + ", ".join(f"{k} {n}" for k, n in origins.most_common()) + "\n"
        f"    comment macros (insertions): " + (", ".join(f"\\{m} {n}" for m, n in macro_counts.items()) or "none found"))


def check_chats(path, since, until, problems, notes, summary):
    name = os.path.basename(path)
    d = load(path)
    convs = d.get("conversations")
    if not isinstance(convs, list):
        problems.append(f"{name}: no conversations list")
        return
    msgs = sum(conv_messages(c) for c in convs)
    outside = sum(1 for c in convs if not active(c.get("created_at") or c.get("create_time"),
                                                 c.get("updated_at") or c.get("update_time"), since, until))
    if outside:
        notes.append(f"{name}: {outside} conversations are not active in the window (kept, but check they belong)")
    if d.get("source") == "claude.ai":
        no_tree = sum(1 for c in convs
                      if c.get("current_leaf_message_uuid") not in {m.get("uuid") for m in c.get("chat_messages", [])})
        if no_tree:
            notes.append(f"{name}: {no_tree} conversations have no branch information (official export?)")
        atts = sum(len(m.get("attachments") or []) for c in convs for m in c.get("chat_messages", []))
        projects = d.get("projects") or {}
        docs = sum(len(p.get("docs") or []) for p in projects.values() if isinstance(p, dict))
        models = Counter(c.get("model") or "?" for c in convs)
        summary.append(f"{name}: {len(convs)} conversations, {msgs} messages, {atts} attachments, "
                       f"{len(projects)} projects ({docs} knowledge files), "
                       f"{len(d.get('code_sessions') or [])} Claude Code web sessions; models: "
                       + ", ".join(f"{m} {n}" for m, n in models.most_common()))
    else:
        summary.append(f"{name}: {len(convs)} conversations, {msgs} messages, "
                       f"{len(d.get('codex_tasks') or [])} Codex cloud tasks")
    if not convs:
        notes.append(f"{name}: empty")


def scan_secrets(bundle):
    hits = Counter()
    names = list(SECRET_PATTERNS)
    for root, _, files in os.walk(bundle):
        for fn in files:
            p = os.path.join(root, fn)
            if fn.endswith((".json", ".jsonl", ".txt", ".md", ".tex")):
                with open(p, encoding="utf-8", errors="replace") as f:
                    for m in SECRET_RE.finditer(f.read()):
                        hits[(os.path.relpath(p, bundle), names[int(m.lastgroup[1:])])] += 1
    return hits


def run_check(bundle):
    problems, notes, summary = [], [], []
    if not os.path.exists(about_path(bundle)):
        sys.exit(f"{bundle} has no about.json: run `python3 bundle.py init {bundle}` first")
    about = load(about_path(bundle))
    since, until = window(bundle)

    if not (about.get("paper") or {}).get("title"):
        problems.append("about.json: paper.title is empty")
    if not consented(about):
        problems.append("about.json: the consent to share (consent.share_...) is not true; "
                        "don't send until it is")
    if not (about.get("contributor") or {}).get("name"):
        notes.append("about.json: contributor.name is empty (fill could not read it from Overleaf); add your name")
    if not about.get("people"):
        notes.append("about.json: people is empty: run `bundle.py fill`")

    overleaf = sorted(f for f in os.listdir(bundle) if f.startswith("overleaf_") and f.endswith(".json"))
    if not overleaf:
        problems.append("no overleaf_<project id>.json file (run overleaf_export.js on the project)")
    for f in overleaf:
        try:
            check_overleaf(os.path.join(bundle, f), about, problems, notes, summary)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as e:
            problems.append(f"{f}: unreadable ({type(e).__name__}: {e})")
    chats = list(chat_files(bundle))
    if not chats:
        notes.append("no chat logs in the bundle (fine only if no chat assistant was used)")
    for p in chats:
        try:
            check_chats(p, since, until, problems, notes, summary)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as e:
            problems.append(f"{os.path.basename(p)}: unreadable ({type(e).__name__}: {e})")
    for f in sorted(x for x in os.listdir(bundle) if x.startswith("git_") and x.endswith(".json")):
        g = load(os.path.join(bundle, f))
        cs = g.get("commits") or []
        ai = sum(1 for c in cs if AI_TRAILER.search(c.get("message", "")))
        # The spec names the field "author name"; agents write it as "author" or "author_name".
        dates = sorted((c.get("author_date") or c.get("date") or "")[:10] for c in cs)
        authors = Counter(c.get("author") or c.get("author_name") or "unknown" for c in cs)
        summary.append(f"{f}: {len(cs)} commits to the paper's files" + (f", {dates[0]} to {dates[-1]}" if dates else "") +
                       f", {ai} marked as made with an AI agent; authors: " +
                       ", ".join(f"{a} {n}" for a, n in authors.most_common(6)))
    for g in about.get("git_repos") or []:
        if g.get("status") == "clone failed":
            notes.append(f"GitHub-synced repo {g['repo']} could not be collected: log in to GitHub (`gh auth login`) "
                         f"or clone it and run `bundle.py git BUNDLE --path <clone>`")
    if about.get("agent_sessions_note"):
        notes.append(about["agent_sessions_note"])
    logs = os.path.join(bundle, "agent_logs")
    if os.path.isdir(logs):
        for tool in sorted(os.listdir(logs)):
            files = [os.path.join(r, f) for r, _, fs in os.walk(os.path.join(logs, tool)) for f in fs]
            summary.append(f"agent_logs/{tool}: {len(files)} files, {sum(map(os.path.getsize, files)) / 1e6:.1f} MB")
    if about.get("excluded_chats"):
        summary.append(f"excluded chats: {len(about['excluded_chats'])}")
    for (f, kind), n in sorted(scan_secrets(bundle).items()):
        problems.append(f"{f}: {n} possible {kind}(s). Remove them, or revoke the key and say so in about.json notes")

    size = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(bundle) for f in fs
               if f != "CHECK_REPORT.txt")
    lines = [f"Bundle check (kit v{KIT_VERSION}) of {os.path.basename(os.path.abspath(bundle))}, "
             f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC, {size / 1e6:.1f} MB",
             f"window: {about['window']['since']} to {about['window']['until']}", "",
             "PROBLEMS (fix these, or ask before sending):"] + [f"  - {p}" for p in problems or ["none"]]
    lines += ["", "NOTES:"] + [f"  - {n}" for n in notes or ["none"]]
    lines += ["", "CONTENTS:"] + [f"  {s}" for s in summary]
    lines += ["", "RESULT: " + ("CHECK PASSED (now run the analysis; mydata/ itself is never sent)" if not problems
                                else f"NOT READY ({len(problems)} problems)")]
    report = "\n".join(lines) + "\n"
    with open(os.path.join(bundle, "CHECK_REPORT.txt"), "w", encoding="utf-8") as f:
        f.write(report)
    return report, problems


def cmd_check(args):
    report, problems = run_check(args.bundle)
    print(report)
    sys.exit(1 if problems else 0)


def cmd_pack(args):
    if os.path.basename(HERE) == "pipeline":  # the published kit: the bundle itself is never packed or sent
        sys.exit("In the kit only `check` is used; mydata/ stays on this machine. Run pipeline/analyze.py instead.")
    report, problems = run_check(args.bundle)
    print(report)
    if problems and not args.force:
        sys.exit("Not packed: fix the problems above (or pass --force after talking to the study contact).")
    print(f"packed {pack(args.bundle)}")


def pack(bundle):
    root = os.path.abspath(bundle)
    dest = root.rstrip(os.sep) + ".zip"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for r, _, fs in os.walk(root):
            for f in sorted(fs):
                p = os.path.join(r, f)
                z.write(p, os.path.join(os.path.basename(root), os.path.relpath(p, root)))
    return f"{dest} ({os.path.getsize(dest) / 1e6:.1f} MB)"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("check", cmd_check), ("list-chats", cmd_list_chats)):
        p = sub.add_parser(name)
        p.add_argument("bundle")
        p.set_defaults(fn=fn)
    p = sub.add_parser("init")
    p.add_argument("bundle")
    p.add_argument("--title", help="the paper's title (enough to find its Overleaf projects)")
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("fill")
    p.add_argument("bundle")
    p.add_argument("--downloads", help="folder with overleaf_export.json (default: auto.downloads_dir)")
    p.set_defaults(fn=cmd_fill)
    p = sub.add_parser("add")
    p.add_argument("bundle")
    p.add_argument("files", nargs="+")
    p.set_defaults(fn=cmd_add)
    p = sub.add_parser("import")
    p.add_argument("bundle")
    p.add_argument("export", help="Export-data .zip from chatgpt.com or claude.ai (or its conversations.json)")
    p.set_defaults(fn=cmd_import)
    p = sub.add_parser("agent-logs")
    p.add_argument("bundle")
    p.add_argument("dir", help="e.g. ~/.claude/projects or ~/.codex/sessions")
    p.add_argument("--tool", required=True, help="e.g. claude-code, codex")
    p.add_argument("--match", help="keep sessions matching this regex instead of those containing the paper's text")
    p.add_argument("--dry-run", action="store_true", help="only list what would be copied")
    p.set_defaults(fn=cmd_agent_logs)
    p = sub.add_parser("agents")
    p.add_argument("bundle")
    p.add_argument("--archive", action="append", help="agent_sessions_<host>.tgz from `remote-command` (repeatable)")
    p.add_argument("--no-local", action="store_true", help="only the archives, not this computer")
    p.set_defaults(fn=cmd_agents)
    p = sub.add_parser("remote-command")
    p.add_argument("bundle")
    p.set_defaults(fn=cmd_remote_command)
    p = sub.add_parser("git")
    p.add_argument("bundle")
    p.add_argument("--repo", help="owner/name on GitHub (default: every GitHub-synced Overleaf project)")
    p.add_argument("--path", help="a local clone instead of cloning")
    p.set_defaults(fn=cmd_git)
    p = sub.add_parser("exclude-chat")
    p.add_argument("bundle")
    p.add_argument("ids", nargs="+", help="conversation ids (or their first 8+ characters) from list-chats")
    p.add_argument("--reason", default="off-topic")
    p.set_defaults(fn=cmd_exclude_chat)
    p = sub.add_parser("pack")
    p.add_argument("bundle")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_pack)
    p = sub.add_parser("auto")
    p.add_argument("bundle")
    p.add_argument("--downloads", help="folder with the browser exports (default: auto.downloads_dir in about.json)")
    p.add_argument("--export", action="append", help="an Export-data zip to import as well (repeatable)")
    p.set_defaults(fn=cmd_auto)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
