"""Assemble and check a data bundle for the paper AI-authorship study (collection kit v1).

Python >= 3.9, standard library only. Everything runs on your own machine; nothing is uploaded.

    python3 bundle.py init      BUNDLE --title "TITLE"      create the folder and about.json (title and consent)
    python3 bundle.py fill      BUNDLE                      read the Overleaf export from Downloads and fill in the
                                                            rest of about.json: window, people, files, contributor
    python3 bundle.py add       BUNDLE FILE...              copy in overleaf_export / claude_ai_* / chatgpt_export downloads
    python3 bundle.py import    BUNDLE EXPORT.zip           ChatGPT (or claude.ai) "Export data" zip, cut to the window
    python3 bundle.py agent-logs BUNDLE DIR --tool NAME     coding-agent sessions from the window that touched the paper
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
        "agent_logs": [{"dir": "~/.claude/projects", "tool": "claude-code"},
                       {"dir": "~/.codex/sessions", "tool": "codex"}],
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
SECRET_RE = re.compile("|".join(f"(?P<g{i}>{p})" for i, p in enumerate(SECRET_PATTERNS.values())))
DOWNLOAD_NAME = re.compile(r"^(overleaf_export|overleaf_[0-9a-f]{24}|claude_ai_[0-9a-f]{8}-[0-9a-f-]{27}|chatgpt_export)"
                           r"(?: ?\(\d+\))?\.json$")
DEFAULT_KEYWORDS = [r"overleaf", r"\blatex\b", r"\.(?:tex|bib)\b",
                    r"\\(?:section|subsection|cite[pt]?|begin|textbf|emph|label|ref|paragraph)\b"]
WORD = re.compile(r"[a-z0-9]+")
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
    return sum(1 for n in (c.get("mapping") or {}).values()
               if n.get("message") and n["message"]["author"]["role"] in ("user", "assistant"))


def apply_exclusions(bundle, path):
    """Drop chats already excluded in about.json, so re-adding or re-importing a file cannot bring them back."""
    gone = {e["id"] for e in load(about_path(bundle)).get("excluded_chats") or []}
    d = load(path)
    if gone and isinstance(d.get("conversations"), list):
        keep = [c for c in d["conversations"] if conv_id(c) not in gone]
        if len(keep) != len(d["conversations"]):
            print(f"  dropped {len(d['conversations']) - len(keep)} chats excluded earlier")
            d["conversations"] = keep
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
        for c in sorted(d["conversations"], key=lambda c: str(c.get("created_at") or c.get("create_time"))):
            created = to_dt(c.get("created_at") or c.get("create_time"))
            print(f"  {conv_id(c)}  {created:%Y-%m-%d}  {conv_messages(c):3d} msgs  {c.get('name') or c.get('title') or ''}")


def cmd_exclude_chat(args):
    about = load(about_path(args.bundle))
    wanted = set(args.ids)
    for path in chat_files(args.bundle):
        d = load(path)
        keep = []
        for c in d["conversations"]:
            cid = conv_id(c)
            if any(cid == w or (len(w) >= 8 and cid.startswith(w)) for w in wanted):
                about.setdefault("excluded_chats", []).append({
                    "file": os.path.basename(path), "id": cid,
                    "created_at": str(c.get("created_at") or to_dt(c.get("create_time")).isoformat()),
                    "messages": conv_messages(c), "reason": args.reason})
                print(f"removed {cid} from {os.path.basename(path)}")
            else:
                keep.append(c)
        if len(keep) != len(d["conversations"]):
            d["conversations"] = keep
            save(path, d)
    with open(about_path(args.bundle), "w", encoding="utf-8") as f:
        json.dump(about, f, indent=2, ensure_ascii=False)


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
        fad = o.get("full_attributed_diff") or {}
        final = {p: "".join(x.get("u", x.get("i", "")) for x in segs if "d" not in x) for p, segs in fad.items()}
        tex = sorted((p for p in final if p.endswith(".tex")), key=lambda p: -len(final[p]))
        projects.append({"project_id": o.get("project_id"), "name": o.get("project_name"),
                         "first_edit": t0 and t0.isoformat(), "last_edit": t1 and t1.isoformat(),
                         "text_edits": len(edits), "tex_files": tex})
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
    consent = (about.get("consent") or {}).get("share_chats_and_overleaf_history_including_coauthors") is True
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
            for segs in (o.get("full_attributed_diff") or {}).values():
                out |= shingles("".join(x.get("u", x.get("i", "")) for x in segs if "d" not in x))
    return out


def filter_chats(bundle, about, cfg):
    """Drop chats unrelated to the paper. Related = shares exact 6-word sequences with the paper's history,
    or matches a keyword. Returns (kept, dropped) rows for the review file."""
    fc = cfg["chat_filter"]
    keywords = [re.compile(k, re.I) for k in list(fc.get("keywords") or []) + DEFAULT_KEYWORDS]
    tr = title_regex(bundle)
    if tr:
        keywords.insert(0, tr)
    need = int(fc.get("min_shared_6word_sequences") or 3)
    keep_ids, drop_ids = set(fc.get("always_keep_ids") or []), set(fc.get("always_drop_ids") or [])
    paper = paper_shingles(bundle) if fc.get("keep") != "all" else set()
    kept, dropped = [], []
    for path in chat_files(bundle):
        d = load(path)
        keep = []
        for c in d["conversations"]:
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
                why, ok = f"keyword /{kw}/", True
            else:
                why, ok = f"auto: unrelated to the paper ({shared} shared sequences, no keyword)", False
            created = to_dt(c.get("created_at") or c.get("create_time"))
            row = (os.path.basename(path), cid, f"{created:%Y-%m-%d}", conv_messages(c),
                   c.get("name") or c.get("title") or "", why)
            if ok:
                keep.append(c)
                kept.append(row)
            else:
                dropped.append(row)
                about["excluded_chats"].append({"file": row[0], "id": cid, "created_at": created.isoformat(),
                                                "messages": row[3], "reason": why})
        if len(keep) != len(d["conversations"]):
            d["conversations"] = keep
            save(path, d)
    return kept, dropped


def cmd_auto(args):
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

    print("== 2. coding-agent sessions")
    for spec in cfg.get("agent_logs") or []:
        src = os.path.expanduser(spec["dir"])
        if not os.path.isdir(src):
            print(f"  {spec['dir']}: not on this machine, skipped")
            continue
        shutil.rmtree(os.path.join(bundle, "agent_logs", spec["tool"]), ignore_errors=True)
        copy_agent_logs(bundle, src, spec["tool"], cfg.get("agent_log_match") or None)

    print("== 3. chats unrelated to the paper")
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

    print("== 4. check and pack")
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


def check_overleaf(path, about, problems, notes, summary):
    name = os.path.basename(path)
    o = load(path)
    ups = o.get("updates") or []
    if not ups:
        problems.append(f"{name}: no history updates")
        return
    fad = o.get("full_attributed_diff") or {}
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
    if any(isinstance(u, dict) and "email" in u for x in ups for u in (x.get("meta") or {}).get("users") or []):
        notes.append(f"{name}: contains co-authors' email addresses (the kit's script removes them)")
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
                       f"{len(projects)} projects ({docs} knowledge files); models: "
                       + ", ".join(f"{m} {n}" for m, n in models.most_common()))
    else:
        summary.append(f"{name}: {len(convs)} conversations, {msgs} messages")
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
    if (about.get("consent") or {}).get("share_chats_and_overleaf_history_including_coauthors") is not True:
        problems.append("about.json: consent.share_chats_and_overleaf_history_including_coauthors is not true; "
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
    lines += ["", "RESULT: " + ("READY TO SEND" if not problems else f"NOT READY ({len(problems)} problems)")]
    report = "\n".join(lines) + "\n"
    with open(os.path.join(bundle, "CHECK_REPORT.txt"), "w", encoding="utf-8") as f:
        f.write(report)
    return report, problems


def cmd_check(args):
    report, problems = run_check(args.bundle)
    print(report)
    sys.exit(1 if problems else 0)


def cmd_pack(args):
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
