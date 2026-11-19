"""Parse the raw exports in ../raw into analysis-ready tables in ../parsed.

Outputs
  overleaf_edits.jsonl        one row per (update, file, inserted/deleted span)
  overleaf_snapshots/<v>/...  full file text after selected updates (first, every advisor edit, last)
  final_attribution.jsonl     final text of each file as spans tagged with who inserted them and when
  final/<file>                final text of each file (v4079)
  advisor_comments.jsonl      every \\bd / \\sz / \\wl comment ever inserted, with whether it survives
  chat_messages.jsonl         claude.ai + ChatGPT messages, one row per message (attachments inlined)
  chat_conversations.csv      one row per conversation with an on/off-topic flag
"""
import csv
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import readers  # noqa: E402
import settings  # noqa: E402
from readers import iso, undo_diff  # noqa: E402,F401 (used by link_comments.py)


def who(meta, origin=None):
    return readers.who(meta, origin)


def comment_re():
    macros = sorted(settings.current().comment_macros, key=len, reverse=True)
    return re.compile(r"\\(%s)\{" % "|".join(map(re.escape, macros)))


def find_comments(text):
    """Yield (macro, body) for \\bd{...} etc. (the paper's comment macros), with balanced braces."""
    for m in comment_re().finditer(text):
        depth, i = 1, m.end()
        while i < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        if depth == 0:
            yield m.group(1), text[m.end():i - 1]


def parse_overleaf(p, main=True):
    """Edits of one project; for the main project also snapshots, final text, attribution and comments."""
    S = settings.current()
    after, base, added = readers.replay(p)
    bad = readers.replay_problems(p, base, added)
    if S.mode == "root":
        assert not bad, f"backward replay does not reach Overleaf's v0 for {bad}"
    final = readers.final_texts(p)

    edits, snapshots, seen_comments = [], {}, {}
    ups = p["updates"]
    for k, up in enumerate(ups):
        origin = up["meta"].get("origin")
        author = readers.who(up["meta"], origin, p["user_names"])
        for path, segs in up["diffs"].items():
            for s in segs:
                for kind in ("i", "d"):
                    if kind in s:
                        row = {
                            "fromV": up["fromV"], "toV": up["toV"], "file": path,
                            "op": "insert" if kind == "i" else "delete",
                            "text": s[kind], "chars": len(s[kind]),
                            "author": readers.who(s.get("meta"), origin, p["user_names"]) if s.get("meta") else author,
                            "update_author": author,
                            "origin": (origin or {}).get("kind", "editor"),
                            "start": iso(up["meta"]["start_ts"]), "end": iso(up["meta"]["end_ts"]),
                            "span_start_ts": (s.get("meta") or up["meta"])["start_ts"] / 1000,
                            "span_end_ts": (s.get("meta") or up["meta"])["end_ts"] / 1000,
                        }
                        if S.mode != "root":
                            row["project"] = p["project_id"]
                        edits.append(row)
                        if kind == "i" and main:
                            for mac, body in find_comments(s["i"]):
                                key = (path, mac, body.strip())
                                seen_comments.setdefault(key, {
                                    "file": path, "macro": mac, "commenter": S.comment_macros[mac],
                                    "text": body.strip(), "inserted_by": edits[-1]["author"],
                                    "inserted_at": edits[-1]["start"], "toV": up["toV"]})
            if main and (author != S.me or k == 0 or k == len(ups) - 1) and path in after[k]:
                snapshots.setdefault(up["toV"], {})[path] = after[k][path]
    if not main:
        return edits, bad
    snapshots[0] = readers.first_texts(p)

    attribution = []
    for path, segs in p["fad"].items():
        offset = 0
        for s in segs:
            if "d" in s:
                continue
            t = s["u"] if "u" in s else s.get("i", "")
            attribution.append({
                "file": path, "offset": offset, "chars": len(t), "text": t,
                "author": readers.who(s["meta"], s["meta"].get("origin"), p["user_names"]) if "i" in s else "v0 (initial upload)",
                "inserted_at": iso(s["meta"]["start_ts"]) if "i" in s else None,
            })
            offset += len(t)

    for c in seen_comments.values():
        c["survives_in_final"] = c["text"] in final.get(c["file"], "")
    return edits, snapshots, final, attribution, list(seen_comments.values()), bad, added


def parse_chats():
    """Weili's raw/ exports, with her hand-made off-topic lists."""
    S = settings.current()
    off_topic = re.compile(S.off_topic_claude, re.I)
    c = json.load(open(os.path.join(S.inp, "claude_ai_chats_2026-01-20_to_02-12.json")))
    rows, convs = readers.claude_ai_rows(c["conversations"], lambda conv: not off_topic.search(conv["name"] or ""))
    g = json.load(open(os.path.join(S.inp, "chatgpt_chats_2026-01-20_to_02-12.json")))
    r2, c2 = readers.chatgpt_rows(g["conversations"], lambda conv: conv["title"] in S.on_topic_gpt)
    return rows + r2, convs + c2


# ---------------------------------------------------------------- bundles

PAPER_WORD = re.compile(r"[a-z0-9]+")
LATEX_TERMS = re.compile(r"latex|overleaf|\.tex\b|\.bib\b|\\section|\\cite|\\begin\{", re.I)
NON_PAPER = (".bib", ".sty", ".cls", ".bst")


def runs6(text):
    w = PAPER_WORD.findall(text.lower())
    return {tuple(w[i:i + 6]) for i in range(len(w) - 5)}


def relatedness(projects, title, repos):
    """Section 9 of collect/INSTRUCTIONS.md, as a function of an item's text."""
    paper = set()
    for p in projects:
        for u in p["updates"]:
            for path, segs in u["diffs"].items():
                if not path.endswith(NON_PAPER):
                    paper |= runs6("".join(s.get("i", "") for s in segs))
        for path, t in readers.final_texts(p).items():
            if path.endswith(".tex"):
                paper |= runs6(t)
    title_n = re.sub(r"\s+", " ", title.lower()).strip()

    def test(text):
        shared = len(runs6(text) & paper)
        if shared >= 3:
            return f"{shared} shared 6-word runs"
        flat = re.sub(r"\s+", " ", text.lower())
        if title_n and title_n in flat:
            return "title"
        if any(r and r.lower() in flat for r in repos):
            return "repo name"
        if LATEX_TERMS.search(text):
            return "LaTeX terms"
        return ""
    return test


def bundle_sources(projects):
    """Chats, web sessions, agent sessions and Git commits of a bundle, as chat rows."""
    S = settings.current()
    rows, convs, notes = [], [], []
    for path in sorted(os.listdir(S.inp)):
        full = os.path.join(S.inp, path)
        if path.startswith("claude_ai_") and path.endswith(".json"):
            if not settings.allowed("claude.ai"):
                notes.append(f"{path}: left out (the contributor did not allow claude.ai)")
                continue
            d = json.load(open(full))
            r, c = readers.claude_ai_rows(d.get("conversations") or [], lambda conv: True)
            rows += r
            convs += c
            for uuid, proj in (d.get("projects") or {}).items():
                for doc in (proj or {}).get("docs") or []:
                    if isinstance(doc, dict) and doc.get("content") and doc.get("created_at"):
                        rows.append(readers.agent_row("claude.ai-project", uuid, f"Project: {proj.get('name', '')}", len(rows),
                                                      "user", "", doc["created_at"], None))
                        rows[-1]["attachments"] = [{"name": doc.get("file_name"), "content": doc["content"]}]
            web = readers.code_session_rows(d.get("code_sessions") or []) if settings.allowed("claude-code") else []
            rows += web
            notes.append(f"{path}: {len(d.get('conversations') or [])} conversations, "
                         f"{len(d.get('code_sessions') or [])} Claude Code web sessions ({len(web)} messages), "
                         f"{sum(len((p or {}).get('docs') or []) for p in (d.get('projects') or {}).values())} project docs")
        elif path == "chatgpt.json":
            if not settings.allowed("chatgpt"):
                notes.append(f"{path}: left out (the contributor did not allow ChatGPT)")
                continue
            d = json.load(open(full))
            r, c = readers.chatgpt_rows(d.get("conversations") or [], lambda conv: True)
            rows += r
            convs += c
            if d.get("codex_tasks"):
                notes.append(f"chatgpt.json: {len(d['codex_tasks'])} Codex cloud tasks NOT read (format not seen yet)")
    agent, n = readers.agent_log_rows(S.inp)
    left = sorted({r["source"] for r in agent if not settings.allowed(settings.agent_source(r["source"]))})
    agent = [r for r in agent if settings.allowed(settings.agent_source(r["source"]))]
    if left:
        notes.append("agent sessions left out (not allowed by the contributor): " + ", ".join(left))
    rows += agent
    notes += n

    people = {}
    for p in projects:
        for name in p["user_names"].values():
            people[name.lower()] = settings.display_name(name)

    def person_of(author):
        name = people.get(author.lower(), settings.display_name(author))
        return f"git:{name}"
    git, n = readers.git_rows(S.inp, person_of, trailer_as_ai=S.get("trailer_commits") == "ai") \
        if settings.allowed("git") else ([], ["git_*.json left out (the contributor did not allow Git)"])
    rows += git
    notes += n

    # one conversation row per agent session / repo, and the relatedness re-check for everything
    test = relatedness(projects, S.title, [((p["raw"].get("github_sync") or {}).get("repo") or "") for p in projects])
    by_conv = {}
    for r in rows:
        by_conv.setdefault((r["source"], r["conversation_id"]), []).append(r)
    known = {(c["source"], c["id"]) for c in convs}
    for (src, cid), rs in by_conv.items():
        if (src, cid) not in known:
            convs.append({"source": src, "id": cid, "title": rs[0]["title"], "model": "",
                          "created_at": min(r["created_at"] for r in rs), "n_messages": len(rs), "paper_related": True})
    for c in convs:
        rs = by_conv.get((c["source"], c["id"]), [])
        why = test("\n".join([r["text"] for r in rs] + [a["content"] or "" for r in rs for a in r["attachments"]]))
        c["paper_related"] = bool(why)
        c["related_by"] = why
        for r in rs:
            r["paper_related"] = bool(why)
    return rows, convs, notes


def write_jsonl(name, rows):
    with open(os.path.join(OUT, name), "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_text(dest, text):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    open(dest, "w").write(text)


def main():
    S = settings.parse_args()
    global OUT
    OUT = S.parsed
    os.makedirs(OUT, exist_ok=True)
    projects = readers.overleaf_projects(S.inp)
    main_p = projects[0] if S.mode == "root" else next(p for p in projects if p["project_id"] == S.main_project)
    edits, snapshots, final, attribution, comments, bad, added = parse_overleaf(main_p)
    notes = [f"main project {main_p['project_id']} ({main_p['project_name']}): {len(main_p['updates'])} updates; "
             f"replay to v0 {'OK' if not bad else 'differs for ' + ', '.join(bad)}; "
             f"{len(added)} files added with content but no diff"]
    for p in projects:
        if p is not main_p:
            e, b = parse_overleaf(p, main=False)
            edits += e
            notes.append(f"project {p['project_id']} ({p['project_name']}): {len(p['updates'])} updates; "
                         f"replay to v0 {'OK' if not b else 'differs for ' + ', '.join(b)}")
    write_jsonl("overleaf_edits.jsonl", edits)
    write_jsonl("final_attribution.jsonl", attribution)
    write_jsonl("advisor_comments.jsonl", sorted(comments, key=lambda c: c["inserted_at"]))
    for path, text in final.items():
        write_text(os.path.join(OUT, "final", path), text)
    for v, files in snapshots.items():
        for path, text in files.items():
            write_text(os.path.join(OUT, "overleaf_snapshots", f"v{v:04d}", path), text)

    if S.mode == "root":
        rows, convs = parse_chats()
    else:
        rows, convs, n = bundle_sources(projects)
        notes += n
        with open(os.path.join(OUT, "parse_notes.txt"), "w") as f:
            f.write("\n".join(notes) + "\n")
        print("\n".join(notes))
    write_jsonl("chat_messages.jsonl", rows)
    with open(os.path.join(OUT, "chat_conversations.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(dict.fromkeys(k for c in convs for k in c)) or ["source"])
        w.writeheader()
        w.writerows(sorted(convs, key=lambda c: c["created_at"]))

    print(f"edits={len(edits)} snapshots={len(snapshots)} attribution_spans={len(attribution)} "
          f"comments={len(comments)} chat_messages={len(rows)} conversations={len(convs)} "
          f"paper_related={sum(c['paper_related'] for c in convs)}")


if __name__ == "__main__":
    main()
