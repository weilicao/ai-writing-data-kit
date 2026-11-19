"""Where a run reads and writes, and the settings that differ between papers.

Every script takes `--data <folder>`:
  - the repo root (default): Weili's ICML draft, read from raw/, written to parsed/ and analysis/, with the
    settings in settings_root.py (WEILI_ICML, chosen by hand in the 2026-10-22 session; not in the public kit);
  - a contributor folder such as contributors/<slug>: reads <folder>/bundle/mydata/ (a bundle made with
    collect/INSTRUCTIONS.md), writes <folder>/parsed/ and <folder>/analysis/, and derives the settings from
    the bundle's about.json and data (see derive()). <folder>/settings.json, if present, overrides any of them.

Scripts call setup() once (from their command line) and then read settings.S.
"""
import argparse
import json
import os
import re
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Weili's own paper (the repo root) uses the hand-picked settings in settings_root.py (not part of the public kit)

S = None  # the current Settings


class Settings(dict):
    __getattr__ = dict.__getitem__


def add_arg(ap):
    ap.add_argument("--data", default=ROOT,
                    help="repo root (default: raw/ -> parsed/, analysis/) or a contributor folder holding bundle/mydata/")
    return ap


def parse_args(ap=None):
    """Parse the command line (adding --data), set up the settings and return them (args in S.args)."""
    args = add_arg(ap or argparse.ArgumentParser()).parse_args()
    s = setup(args.data)
    s["args"] = args
    return s


def setup(data=None):
    global S
    data = os.path.abspath(data or ROOT)
    if os.path.samefile(data, ROOT):
        from settings_root import WEILI_ICML  # noqa: E402 (repo root only)
        s = Settings(WEILI_ICML, data=data, inp=os.path.join(ROOT, "raw"),
                     parsed=os.path.join(ROOT, "parsed"), analysis=os.path.join(ROOT, "analysis"))
    else:
        inp = os.path.join(data, "bundle", "mydata")
        if not os.path.isdir(inp):
            raise SystemExit(f"{data}: no bundle/mydata/ folder")
        s = Settings(derive(inp), data=data, inp=inp,
                     parsed=os.path.join(data, "parsed"), analysis=os.path.join(data, "analysis"))
        override = os.path.join(data, "settings.json")
        if os.path.exists(override):
            s.update(json.load(open(override)))
    S = s
    return s


def current():
    return S or setup()


# ---------------------------------------------------------------- settings for a bundle

COMMENT_DEF = re.compile(r"\\(?:re)?newcommand\*?\{?\\([A-Za-z]+)\}?\s*\[1\]\s*\{[^\n]{0,120}?"
                         r"(?:color|todo|marginpar|\\textbf\{\[)")


def display_name(name):
    """'jane doe' -> 'Jane Doe'; usernames ('jdoe') stay as they are."""
    return name.title() if " " in name and name == name.lower() else name


def derive(inp):
    import readers  # noqa: E402 (readers imports this module)
    about = json.load(open(os.path.join(inp, "about.json")))
    projects = readers.overleaf_projects(inp)
    if not projects:
        raise SystemExit(f"{inp}: no overleaf_<id>.json")

    # the contributor's Overleaf name, from contributor.overleaf_id
    me_id = (about.get("contributor") or {}).get("overleaf_id") or \
        next((p["raw"].get("contributor_overleaf_id") for p in projects if p["raw"].get("contributor_overleaf_id")), None)
    names = {}
    for p in projects:
        names.update(p["user_names"])
    me = names.get(me_id) or (about.get("contributor") or {}).get("name") or "contributor"
    me_short = display_name(me).split()[0]
    me_key = re.sub(r"[^a-z0-9]+", "-", me_short.lower())

    # main project: the one with the most text edits; main file: the largest .tex with \begin{document}
    main = max(projects, key=lambda p: (p["text_edits"], p["first_ts"]))
    finals = readers.final_texts(main)
    docs = sorted((p for p, t in finals.items() if p.endswith(".tex") and "\\begin{document}" in t),
                  key=lambda p: -len(finals[p]))
    main_file = docs[0] if docs else max((p for p in finals if p.endswith(".tex")), key=lambda p: len(finals[p]))
    files = [main_file] + [f for f in readers.inputs(finals, main_file) if f != main_file]

    # comment macros: about.json's list plus one-argument colored macros defined in the paper; each is
    # credited to the person its name matches (initials, first or last name), else to whoever inserted most
    macros = {m.lstrip("\\") for m in (about.get("paper") or {}).get("comment_macros") or []}
    for p in projects:
        macros |= set(COMMENT_DEF.findall("".join(readers.final_texts(p).values()) + readers.inserted_text(p)))
    listed = {m.lstrip("\\") for m in (about.get("paper") or {}).get("comment_macros") or []}
    defs = "\n".join("".join(readers.final_texts(p).values()) + readers.inserted_text(p) for p in projects)
    people = sorted(set(names.values()))
    inserters = readers.macro_inserters(projects, macros)
    comment_macros = {}
    for m in sorted(macros):
        # the name the macro prints, e.g. \newcommand\jd[1]{\todo{{\bf Jane}: #1}} -> Jane
        d = re.search(r"\\(?:re)?newcommand\*?\{?\\%s\}?[^\n]*" % re.escape(m), defs)
        shown = re.search(r"(?:\\bf|\\textbf\{)\s*([A-Z][A-Za-z]+)", d.group()) if d else None
        hit = [n for n in people if (shown.group(1).lower() if shown else m.lower()) in name_keys(n)]
        if len(hit) == 1:
            comment_macros[m] = display_name(hit[0])
        elif shown:
            comment_macros[m] = shown.group(1)  # a name not among the Overleaf users
        elif m in listed and d and "[1]" in d.group() and inserters.get(m):
            comment_macros[m] = display_name(inserters[m].most_common(1)[0][0])
        # else: names nobody and is not a one-argument macro from about.json (e.g. a table marker): not a comment
    return {
        "mode": "bundle",
        "title": (about.get("paper") or {}).get("title") or main["project_name"],
        "me": me, "me_key": me_key, "me_short": me_short,
        "comment_macros": comment_macros,
        "files": files, "main_file": main_file, "main_project": main["project_id"],
        "author_names": {n: display_name(n) for n in people if display_name(n) != n},
        # text pushed from a Git clone (GitHub sync, Overleaf's Git bridge) with no earlier match is left out,
        # as GitHub-sync text is for Weili's paper; settings.json can change this
        "excluded": ["github-sync", "git-bridge"],
        # text first seen in a Git commit with an AI co-author trailer (and in no earlier session): "unclear"
        # (decided 2026-11-12) or "ai"
        "trailer_commits": "unclear",
        "v0_at_zero": False,
        "reply_sources": None,  # all AI sources
        "about": about,
    }


# ---------------------------------------------------------------- sources the contributor allowed, redaction

# about.json -> "sources": {name: true | false | {"use": bool, ...}}; a source missing from it is allowed
SOURCES = ["overleaf", "claude.ai", "chatgpt", "claude-code", "codex", "cursor", "copilot", "gemini-cli",
           "other-agents", "git"]
AGENT_TOOL_SOURCE = {"claude-code": "claude-code", "codex": "codex", "cursor": "cursor", "vscode-copilot": "copilot",
                     "copilot": "copilot", "gemini-cli": "gemini-cli"}


def source_choices():
    """{source: True/False} from about.json (all True for the repo root or when not recorded)."""
    about = current().get("about") or {}
    got = about.get("sources") or {}
    out = {}
    for s in SOURCES:
        v = got.get(s, True)
        out[s] = bool(v.get("use", True)) if isinstance(v, dict) else bool(v)
    return out


def allowed(source):
    return source_choices().get(source, source_choices().get("other-agents", True))


def agent_source(tool_folder):
    """agent_logs/<tool>/ -> the source name in the checklist."""
    return AGENT_TOOL_SOURCE.get(tool_folder, "other-agents")


_CONV_NAMES = {}


def conv_name(event, title):
    """A conversation's title, or with "redact": true (the kit's local analysis) a neutral name such as
    "claude.ai conversation 12" (numbered by first message time), so shared results never hold chat titles.
    `event` is "<source>:<conversation id>:<message id>"."""
    S = current()
    if not S.get("redact") or not event:
        return title
    if not _CONV_NAMES:
        import csv
        rows = list(csv.DictReader(open(os.path.join(S.parsed, "chat_conversations.csv"))))
        rows.sort(key=lambda r: (r.get("created_at") or "", r["id"]))
        n = Counter()
        for r in rows:
            n[r["source"]] += 1
            _CONV_NAMES[(r["source"], r["id"])] = f"{r['source']} conversation {n[r['source']]}"
    src, rest = event.split(":", 1)
    conv = rest.rsplit(":", 1)[0]
    return _CONV_NAMES.get((src, conv), f"{src} conversation")


def event_name(event):
    """An event id as shared: unchanged for Overleaf and template events; with "redact", a chat or session message
    becomes "<neutral conversation name>#<8 hex digits of its id's SHA-1>", since ids of agent sessions hold file
    paths. analysis/conversation_key.csv (local only, written by make_results.py) maps names back."""
    if not current().get("redact") or not event or event.startswith(("overleaf:", "template:")):
        return event
    import hashlib
    return f"{conv_name(event, '')}#{hashlib.sha1(event.encode()).hexdigest()[:8]}"


def name_keys(name):
    parts = [w for w in re.split(r"[^a-z]+", name.lower()) if w]
    keys = set(parts) | {"".join(parts)}
    if len(parts) >= 2:
        keys |= {parts[0][0] + parts[-1][0], "".join(w[0] for w in parts)}
    return keys
