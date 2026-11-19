"""Readers: turn raw data (raw/ or a contributor's bundle) into the structures the analysis uses.

One reader per source. Bundles are written by each contributor's own agent from collect/INSTRUCTIONS.md,
so formats are close to the spec but vary; the variations seen so far are handled here:
  Overleaf  every overleaf_<id>.json; `u` stored as a length or as text; binary files as an object instead
            of a segment list; extra keys; users as {id, first_name, last_name} or ids only; files added by
            a project op with no diff (e.g. pushed through the Git bridge); renames; file-restore updates
  claude.ai conversations (tree export), Claude Code on the web sessions (events hold Claude Code records)
  ChatGPT   conversations (mapping tree); Codex cloud tasks are counted but not read (no sample yet)
  agents    Claude Code JSONL (main sessions and subagents), Codex rollouts (.jsonl or .jsonl.zst, read with
            the `zstd` binary), Cursor rows (`*.related_rows.jsonl` from state.vscdb); other files are listed
            as not read
  Git       git_<owner>__<repo>.json; author as `author` or `author_name`; Windows paths
Anything a reader can't read is returned in `notes`, never guessed.

Chat-like rows all have the schema of parsed/chat_messages.jsonl: source, conversation_id, title,
paper_related, message_id, parent_id, on_main_branch, index, role (user / assistant / commit), created_at,
text, attachments. Rows from bundles also carry `label` (the provenance label match_provenance.py uses).
"""
import glob
import json
import os
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone

AI_TRAILER = re.compile(r"co-authored-by:[^\n]*(claude|anthropic|codex|openai|copilot|cursor|gemini|devin|aider)"
                        r"|generated with[^\n]*(claude|codex|copilot|cursor|aider)", re.I)
SYSTEM_TAGS = re.compile(r"<(system-reminder|command-name|command-message|command-args|local-command-stdout|"
                         r"local-command-stderr|local-command-caveat|bash-stdout|bash-stderr|bash-input|"
                         r"task-notification|user-prompt-submit-hook)>.*?</\1>", re.S)
FILE_TOOLS = {"Write": ("content",), "Edit": ("new_string",), "MultiEdit": ("edits",), "NotebookEdit": ("new_source",),
              "Bash": ("command",), "create_file": ("file_text",), "str_replace": ("new_str",)}


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def iso_s(s):
    return datetime.fromtimestamp(s, tz=timezone.utc).isoformat()


# ---------------------------------------------------------------- Overleaf

def who(meta, origin=None, names=None):
    """Author of an update or span: 'First Last' joined with '+', else the origin kind."""
    users = (meta or {}).get("users") or []
    if users:
        out = []
        for u in users:
            if isinstance(u, dict):
                n = f"{u.get('first_name') or ''} {u.get('last_name') or ''}".strip()
                out.append(n or (names or {}).get(u.get("id")) or str(u.get("id")))
            else:
                out.append((names or {}).get(u, str(u)))
        return "+".join(out)
    return (origin or {}).get("kind", "unknown")


def norm_segs(segs):
    """A diff as a list of {u: length} / {i: text, meta} / {d: text, meta}; None for binary or broken diffs."""
    if not isinstance(segs, list):
        return None
    out = []
    for s in segs:
        if not isinstance(s, dict):
            return None
        if "u" in s:
            out.append(dict(s, u=len(s["u"]) if isinstance(s["u"], str) else s["u"]))
        elif "i" in s or "d" in s:
            out.append(s)
    return out


def load_overleaf(path):
    o = json.load(open(path))
    names = {}
    for u in o.get("users") or []:
        if isinstance(u, dict) and u.get("id"):
            names[u["id"]] = f"{u.get('first_name') or ''} {u.get('last_name') or ''}".strip() or u["id"]
    ups = sorted(o.get("updates") or [], key=lambda u: u["fromV"])
    for u in ups:
        u["diffs"] = {p.replace("\\", "/"): s for p, s in ((p, norm_segs(s)) for p, s in (u.get("diffs") or {}).items())
                      if s is not None}
        for meta in [u["meta"]] + [s.get("meta") for segs in u["diffs"].values() for s in segs]:
            for x in (meta or {}).get("users") or []:
                if isinstance(x, dict) and x.get("id") and x["id"] not in names:
                    names[x["id"]] = f"{x.get('first_name') or ''} {x.get('last_name') or ''}".strip() or x["id"]
    fad = {p.replace("\\", "/"): segs for p, segs in (o.get("full_attributed_diff") or {}).items()
           if isinstance(segs, list) and all(isinstance(s, dict) for s in segs)}
    text_edits = sum(1 for u in ups if (u["meta"].get("origin") or {}).get("kind") != "history-resync"
                     and any("i" in s or "d" in s for segs in u["diffs"].values() for s in segs))
    return {"project_id": o.get("project_id") or os.path.basename(path)[9:-5], "project_name": o.get("project_name", ""),
            "updates": ups, "fad": fad, "user_names": names, "text_edits": text_edits,
            "first_ts": ups[0]["meta"]["start_ts"] if ups else 0, "raw": o, "file": os.path.basename(path)}


def overleaf_files(inp):
    if os.path.exists(os.path.join(inp, "overleaf_first_draft_history.json")):  # raw/ of this repo
        return [os.path.join(inp, "overleaf_first_draft_history.json")]
    return sorted(glob.glob(os.path.join(inp, "overleaf_*.json")))


def overleaf_projects(inp):
    """Every Overleaf project, oldest first."""
    return sorted((load_overleaf(p) for p in overleaf_files(inp)), key=lambda p: p["first_ts"])


def final_texts(p):
    return {path: "".join(s["u"] if isinstance(s.get("u"), str) else s.get("i", "") for s in segs if "d" not in s)
            for path, segs in p["fad"].items()}


def first_texts(p):
    """Text of each file where Overleaf's full diff starts (v0, or when the file first appeared)."""
    return {path: "".join(s["u"] if isinstance(s.get("u"), str) else s.get("d", "") for s in segs if "i" not in s)
            for path, segs in p["fad"].items()}


def inserted_text(p):
    return "".join(s["i"] for u in p["updates"] for segs in u["diffs"].values() for s in segs if "i" in s)


def inputs(finals, main):
    """Files reached from `main` by \\input / \\include, in order."""
    out, todo = [], [main]
    while todo:
        f = todo.pop(0)
        if f in out or f not in finals:
            continue
        out.append(f)
        for m in re.finditer(r"(?m)^[^%\n]*?\\(?:input|include)\s*\{([^}]+)\}", finals[f]):
            name = m.group(1).strip()
            todo += [n for n in (name, name + ".tex") if n in finals]
    return out


def undo_diff(new, segs):
    """Recover the pre-update text from the post-update text and one Overleaf diff."""
    pos, old = 0, []
    for s in segs:
        if "u" in s:
            old.append(new[pos:pos + s["u"]])
            pos += s["u"]
        elif "i" in s:
            assert new[pos:pos + len(s["i"])] == s["i"], "inserted text does not match"
            pos += len(s["i"])
        elif "d" in s:
            old.append(s["d"])
    assert pos == len(new), f"diff consumed {pos} of {len(new)} chars"
    return "".join(old)


def replay(p):
    """Replay the history backwards from the final text.

    Returns (after, base, added):
      after[i]  {path: text} after update i, for the files that exist then
      base      {path: text} at v0 (files created with the project, e.g. the template)
      added     [(i, path, text)] files a later update added with content but no diff (an upload, a Git push,
                a restore), with the content they arrived with
    Project ops are undone too: a rename maps the file back to its old name, an add removes it, and a remove
    makes the file unknown before that point (its content then was never exported).
    """
    texts = final_texts(p)
    after, added = [], []
    p["start_name"] = {path: path for path in texts}  # each final file's name at its start (renames undone)
    for i in range(len(p["updates"]) - 1, -1, -1):
        up = p["updates"][i]
        after.append(dict(texts))
        for path, segs in up["diffs"].items():
            if path in texts:
                texts[path] = undo_diff(texts[path], segs)
        for op in reversed(up.get("project_ops") or []):
            if "rename" in op:
                a, b = op["rename"].get("pathname"), op["rename"].get("newPathname")
                if b in texts:
                    texts[a] = texts.pop(b)
                    for f, n in p["start_name"].items():
                        if n == b:
                            p["start_name"][f] = a
            elif "add" in op and i > 0:
                path = op["add"].get("pathname")
                if path in texts:
                    content = texts.pop(path)
                    if content:
                        added.append((i, path, content))
    after.reverse()
    added.reverse()
    return after, texts, added


def replay_problems(p, base, added):
    """Files whose replayed start differs from the start of Overleaf's full diff."""
    start = dict(base)
    for i, path, text in reversed(added):  # the earliest add of a file is its start
        start[path] = text
    first = first_texts(p)
    name = p.get("start_name") or {}
    return sorted(path for path in first if start.get(name.get(path, path), "") != first[path])


def macro_inserters(projects, macros):
    """{macro: Counter(Overleaf name -> insertions)} of comment macros, over every project."""
    out = {m: Counter() for m in macros}
    if not macros:
        return out
    pat = re.compile(r"\\(%s)\{" % "|".join(sorted(map(re.escape, macros), key=len, reverse=True)))
    for p in projects:
        for u in p["updates"]:
            for segs in u["diffs"].values():
                for s in segs:
                    if "i" in s:
                        for m in pat.finditer(s["i"]):
                            out[m.group(1)][who(s.get("meta") or u["meta"], u["meta"].get("origin"), p["user_names"])] += 1
    return out


# ---------------------------------------------------------------- chats

def claude_text(msg):
    """Visible text of a message, plus text Claude wrote into files (create_file / str_replace)."""
    parts = []
    for c in msg.get("content", []):
        if c.get("type") == "text":
            parts.append(c.get("text", ""))
        elif c.get("type") == "tool_use" and c.get("name") in ("create_file", "str_replace"):
            inp = c.get("input") or {}
            if not str(inp.get("path", "")).endswith(".svg"):
                parts.append(inp.get("file_text") or inp.get("new_str") or "")
    text = "\n".join(p for p in parts if p) or msg.get("text", "")
    atts = [{"name": a.get("file_name"), "content": a.get("extracted_content", "")} for a in msg.get("attachments") or []]
    return text, atts


def claude_ai_rows(conversations, related):
    """claude.ai conversations -> (rows, convs). related(conv) -> bool."""
    rows, convs = [], []
    for conv in conversations:
        by_id = {m["uuid"]: m for m in conv["chat_messages"]}
        main_path, cur = set(), conv.get("current_leaf_message_uuid")
        while cur in by_id:
            main_path.add(cur)
            cur = by_id[cur].get("parent_message_uuid")
        on_topic = related(conv)
        convs.append({"source": "claude.ai", "id": conv["uuid"], "title": conv["name"], "model": conv.get("model"),
                      "created_at": conv["created_at"], "n_messages": len(conv["chat_messages"]), "paper_related": on_topic})
        for m in sorted(conv["chat_messages"], key=lambda m: m["index"]):
            text, atts = claude_text(m)
            rows.append({"source": "claude.ai", "conversation_id": conv["uuid"], "title": conv["name"],
                         "paper_related": on_topic, "message_id": m["uuid"], "parent_id": m.get("parent_message_uuid"),
                         "on_main_branch": m["uuid"] in main_path, "index": m["index"],
                         "role": "user" if m["sender"] == "human" else "assistant",
                         "created_at": m["created_at"], "text": text, "attachments": atts})
    return rows, convs


def chatgpt_rows(conversations, related):
    rows, convs = [], []
    for conv in conversations:
        cid = conv.get("conversation_id") or conv.get("id")
        on_topic = related(conv)
        msgs = [n["message"] for n in (conv.get("mapping") or {}).values()
                if n.get("message") and n["message"]["author"]["role"] in ("user", "assistant")]
        msgs.sort(key=lambda m: m.get("create_time") or 0)
        convs.append({"source": "chatgpt", "id": cid, "title": conv["title"],
                      "model": conv.get("default_model_slug"),
                      "created_at": datetime.fromtimestamp(conv["create_time"], tz=timezone.utc).isoformat(),
                      "n_messages": len(msgs), "paper_related": on_topic})
        for i, m in enumerate(msgs):
            parts = (m.get("content") or {}).get("parts") or []
            text = "\n".join(p for p in parts if isinstance(p, str))
            if not text.strip():
                continue
            rows.append({"source": "chatgpt", "conversation_id": cid, "title": conv["title"],
                         "paper_related": on_topic, "message_id": m["id"], "parent_id": None, "on_main_branch": True,
                         "index": i, "role": m["author"]["role"],
                         "created_at": datetime.fromtimestamp(m["create_time"], tz=timezone.utc).isoformat() if m.get("create_time") else None,
                         "text": text, "attachments": []})
    return rows, convs


# ---------------------------------------------------------------- coding agents

def tool_text(name, inp):
    """Text an agent wrote through a tool call: file contents, edits, shell commands (heredocs, scripts)."""
    if not isinstance(inp, dict):
        return inp if isinstance(inp, str) else ""
    if name == "MultiEdit":
        return "\n".join(e.get("new_string", "") for e in inp.get("edits") or [] if isinstance(e, dict))
    for key in FILE_TOOLS.get(name, ()):
        if isinstance(inp.get(key), str):
            return inp[key]
    return ""


def claude_code_message(rec):
    """(role, text) of one Claude Code record, or None. Tool results, thinking and injected context are dropped."""
    if rec.get("type") not in ("user", "assistant") or rec.get("isMeta") or rec.get("isCompactSummary"):
        return None
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return None
    content = msg.get("content")
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content or []
    parts = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            parts.append(b.get("text") or "")
        elif b.get("type") == "tool_use" and rec["type"] == "assistant":
            parts.append(tool_text(b.get("name"), b.get("input")))
    text = SYSTEM_TAGS.sub("", "\n".join(p for p in parts if p)).strip()
    if not text:
        return None
    role = rec["type"]
    if role == "user" and rec.get("isSidechain"):
        role = "assistant"  # a subagent's prompt was written by the parent agent
    return role, text


def agent_row(source, conv_id, title, idx, role, text, ts, label):
    return {"source": source, "conversation_id": conv_id, "title": title, "paper_related": True,
            "message_id": f"{conv_id}#{idx}", "parent_id": None, "on_main_branch": True, "index": idx,
            "role": role, "created_at": ts, "text": text, "attachments": [], "label": label}


def read_jsonl(path, notes):
    if path.endswith(".zst"):
        try:
            data = subprocess.run(["zstd", "-dcq", path], capture_output=True, check=True).stdout.decode("utf-8", "replace")
        except (OSError, subprocess.CalledProcessError) as e:
            notes.append(f"{path}: could not decompress ({type(e).__name__}); install zstd")
            return []
        lines = data.splitlines()
    else:
        lines = open(path, encoding="utf-8", errors="replace")
    out, bad = [], 0
    for line in lines:
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1
    if bad:
        notes.append(f"{path}: {bad} unreadable lines skipped")
    return out


def claude_code_rows(recs, conv_id, title, label, source="claude-code", ts_key="timestamp"):
    rows = []
    for r in recs:
        got = claude_code_message(r)
        if got and r.get(ts_key):
            role, text = got
            rows.append(agent_row(source, conv_id, title, len(rows), role, text, r[ts_key],
                                  label if role == "assistant" else None))
    return rows


def codex_rows(recs, conv_id, title):
    rows = []
    for r in recs:
        p = r.get("payload") if isinstance(r.get("payload"), dict) else r
        ts = r.get("timestamp")
        if r.get("type") not in ("response_item", None) or not ts:
            continue
        t, role, text = p.get("type"), None, ""
        if t == "message" and p.get("role") in ("user", "assistant"):
            role = p["role"]
            text = "\n".join(c.get("text", "") for c in p.get("content") or []
                             if isinstance(c, dict) and c.get("type") in ("input_text", "output_text"))
            if role == "user" and text.lstrip().startswith("<"):  # environment context, AGENTS.md, instructions
                continue
        elif t in ("function_call", "custom_tool_call", "local_shell_call"):
            role = "assistant"
            text = p.get("input") or p.get("arguments") or json.dumps(p.get("action") or "")
            if isinstance(text, str) and text.startswith("{"):
                try:
                    a = json.loads(text)
                    c = a.get("cmd") or a.get("command") or a.get("input") or a.get("patch") or ""
                    text = " ".join(c) if isinstance(c, list) else str(c)
                except json.JSONDecodeError:
                    pass
        if role and text.strip():
            rows.append(agent_row("codex", conv_id, title, len(rows), role, text, ts, "ai:codex" if role == "assistant" else None))
    return rows


def cursor_rows(recs, notes, name):
    by_conv, skipped = {}, Counter()
    for r in recs:
        try:
            v = json.loads(r["value"]) if isinstance(r.get("value"), str) else r.get("value")
        except json.JSONDecodeError:
            v = None
        key = str(r.get("key", ""))
        if not isinstance(v, dict) or not key.startswith("bubbleId:") or v.get("type") not in (1, 2) or not v.get("createdAt"):
            skipped[key.split(":")[0].split(".")[0]] += 1
            continue
        conv = key.split(":")[1]
        role = "user" if v["type"] == 1 else "assistant"
        parts = [v.get("text") or ""]
        if role == "assistant":
            parts += [c.get("content", "") for c in v.get("codeBlocks") or [] if isinstance(c, dict)]
            tf = v.get("toolFormerData") or {}
            if any(k in str(tf.get("name", "")) for k in ("edit", "write", "replace", "terminal")):
                parts.append(tf.get("rawArgs") or json.dumps(tf.get("params") or ""))
        text = "\n".join(p for p in parts if isinstance(p, str) and p).strip()
        if text:
            by_conv.setdefault(conv, []).append((v["createdAt"], role, text))
    if skipped:
        notes.append(f"{name}: rows not read (not chat bubbles): " + ", ".join(f"{k} {n}" for k, n in skipped.items()))
    rows = []
    for conv, items in by_conv.items():
        for ts, role, text in sorted(items):
            rows.append(agent_row("cursor", conv, f"Cursor {conv[:8]}", sum(r["conversation_id"] == conv for r in rows),
                                  role, text, ts, "ai:cursor" if role == "assistant" else None))
    return rows


def agent_log_rows(inp):
    """Rows from agent_logs/<tool>/<machine>/..., plus notes on files not read."""
    rows, notes, unread = [], [], Counter()
    base = os.path.join(inp, "agent_logs")
    if not os.path.isdir(base):
        return rows, notes
    for root, _, files in sorted(os.walk(base)):
        for fn in sorted(files):
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, base).replace("\\", "/")
            tool = rel.split("/")[0]
            if tool == "claude-code" and fn.endswith(".jsonl") and fn != "journal.jsonl":
                recs = read_jsonl(path, notes)
                if not any(isinstance(r.get("message"), dict) for r in recs):
                    unread[f"{tool}: jsonl without messages"] += 1
                    continue
                conv = rel[:-6]
                rows += claude_code_rows(recs, conv, f"Claude Code {os.path.basename(conv)[:14]}", "ai:claude-code")
            elif tool in ("codex", "codex-archived") and re.search(r"\.jsonl(\.zst)?$", fn):
                conv = re.sub(r"\.jsonl(\.zst)?$", "", rel)
                rows += codex_rows(read_jsonl(path, notes), conv, f"Codex {os.path.basename(conv)[-12:]}")
            elif tool == "cursor" and fn.endswith(".related_rows.jsonl"):
                rows += cursor_rows(read_jsonl(path, notes), notes, rel)
            else:
                unread[f"{tool}: {os.path.splitext(fn)[1] or fn}"] += 1
    if unread:
        notes.append("agent_logs files not read as sessions: " + ", ".join(f"{k} x{n}" for k, n in sorted(unread.items())))
    return rows, notes


def code_session_rows(sessions):
    """Claude Code on the web: each event's payload is a Claude Code record."""
    rows = []
    for s in sessions:
        recs = []
        for e in s.get("events") or []:
            p = e.get("payload")
            if isinstance(p, dict):
                recs.append(dict(p, _ts=e.get("created_at")))
        rows += claude_code_rows(recs, s.get("id"), f"Claude Code web: {s.get('title') or s.get('id')}",
                                 "ai:claude-code", source="claude-code-web", ts_key="_ts")
    return rows


# ---------------------------------------------------------------- Git

def git_hunks(patch):
    """{file: [added text per hunk]} from a `git log -p` patch (binary files skipped)."""
    out, f, cur = {}, None, None
    for line in patch.splitlines():
        if line.startswith("diff --git"):
            f, cur = None, None
        elif line.startswith("+++ "):
            f = line[4:].strip()
            f = None if f == "/dev/null" else f[2:] if f.startswith("b/") else f
        elif line.startswith("@@"):
            cur = None
        elif f and line.startswith("+"):
            if cur is None:
                cur = []
                out.setdefault(f.replace("\\", "/"), []).append(cur)
            cur.append(line[1:])
        elif line.startswith((" ", "-")):
            cur = None
    return {k: ["\n".join(h) for h in v] for k, v in out.items()}


def git_rows(inp, person_of, trailer_as_ai=False):
    """Commits -> rows. Overleaf's own commits in a git.overleaf.com repo (committer differs from author, e.g.
    'node') mirror the Overleaf history and are skipped.

    A commit with an AI co-author trailer is labeled `git-ai`: Unclear, because the trailer doesn't show who wrote
    the text (decided 2026-11-12: on the first contributor's paper, two thirds of the text first seen in such
    commits had been read by the agent from a file before it committed). Text the agent wrote in a collected
    session is credited to the session anyway, which comes first. trailer_as_ai=True labels them `ai:agent-commit`.
    Commits without a trailer are `git:<author>`."""
    rows, notes = [], []
    for path in sorted(glob.glob(os.path.join(inp, "git_*.json"))):
        g = json.load(open(path))
        repo = g.get("repo") or os.path.basename(path)
        overleaf = "git.overleaf.com" in repo
        skipped = 0
        for c in g.get("commits") or []:
            author = c.get("author") or c.get("author_name") or "unknown"
            committer = c.get("committer") or c.get("committer_name") or author
            if overleaf and committer.lower() != author.lower():
                skipped += 1
                continue
            ai = bool(AI_TRAILER.search(c.get("message") or ""))
            text = "\n".join(h for hs in git_hunks(c.get("patch") or "").values() for h in hs)
            if not text.strip():
                continue
            label = ("ai:agent-commit" if trailer_as_ai else "git-ai") if ai else person_of(author)
            rows.append({"source": "git", "conversation_id": repo, "title": f"git {repo}",
                         "paper_related": True, "message_id": f"{repo}@{c['sha'][:12]}", "parent_id": None,
                         "on_main_branch": True, "index": len(rows),
                         "role": "assistant" if label.startswith("ai:") else "commit",
                         "created_at": c.get("author_date") or c.get("date"), "text": text, "attachments": [],
                         "label": label, "author": author})
        if skipped:
            notes.append(f"{repo}: {skipped} commits made by Overleaf itself skipped (they mirror the Overleaf history)")
    return rows, notes
