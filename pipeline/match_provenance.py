"""Label every word of the final first draft with where it first came from.

Idea: a k-word shingle's origin is the earliest event in which it appears. Events are
  - the ICML template (v0),
  - each Overleaf insertion (Weili / an advisor / GitHub sync), at the span's end time, credited with
    every k-word sequence in the updated file that touches the inserted words,
  - each chat message: Weili's prompts and pasted attachments (human), Claude/ChatGPT replies
    including files Claude wrote (AI).
A word takes the origin of the earliest shingle covering it, so a sentence Weili wrote and Claude
only polished keeps Weili's words as human and only the new words as AI. Words with no shingle
seen anywhere fall back to Overleaf's own author for that character.

Outputs go to ../analysis:
  spans.jsonl        final text as runs of same-origin words (file, char offsets, label, source)
  paragraphs.csv     per paragraph: kind, section, share of words by label, best-matching chat
  sections.csv       per section share of prose words by label
  sensitivity.csv    overall shares for k = 4, 6, 8, 10
"""
import argparse
import bisect
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import readers  # noqa: E402
import settings  # noqa: E402

# drop arguments that are keys rather than prose, then command names
KEY_ARGS = re.compile(r"\\(?:cite[pt]?|citep|citet|ref|eqref|autoref|cref|Cref|label|url|includegraphics|input|"
                      r"bibliography\w*|usepackage|documentclass)\*?\s*(?:\[[^\]]*\])*\{[^}]*\}")
# environment names, placement and column specs: \begin{table*}[t], \begin{tabular}{lcc}, \end{figure}
ENV = re.compile(r"\\(?:begin|end)\s*\{[^}]*\}(?:\s*\[[^\]]*\])?(?:\s*\{[^{}]*\})?")
COMMAND = re.compile(r"\\[A-Za-z]+\*?")
WORD = re.compile(r"[A-Za-z]+|\d+")
FLOAT_ENVS = ("table", "table*", "figure", "figure*", "algorithm", "algorithm*")
BOX_ENVS = ("promptbox", "tcolorbox")  # prompt templates and agent traces, not prose
PASTE_MIN_CHARS = 150
TYPING_MAX_CPS = 15  # ~180 wpm, beyond fast typing
# Per-paper settings (scripts/settings.py): the files labeled, the comment macros, display names, and the
# labels left out of all shares (for Weili's paper: GitHub-sync text, coding-agent text pushed via GitHub sync).


def S():
    return settings.current()


def excluded():
    return set(S().excluded)


def comment_macro():
    return re.compile(r"\\(%s)\{" % "|".join(map(re.escape, sorted(S().comment_macros, key=len, reverse=True))))


def tokenize(text):
    """Return [(word, start, end)] with LaTeX keys and command names masked out."""
    masked = ENV.sub(lambda m: " " * len(m.group()), text)
    masked = KEY_ARGS.sub(lambda m: " " * len(m.group()), masked)
    masked = COMMAND.sub(lambda m: " " * len(m.group()), masked)
    return [(m.group().lower(), m.start(), m.end()) for m in WORD.finditer(masked)]


def to_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def load_jsonl(name):
    return [json.loads(l) for l in open(os.path.join(S().parsed, name))]


def overleaf_label(origin, users, chars, secs, names=None):
    # a long span that arrived faster than anyone types was pasted from somewhere
    how = "pasted" if chars >= PASTE_MIN_CHARS and chars / max(secs, 1e-3) > TYPING_MAX_CPS else "typed"
    if (origin or {}).get("kind") == "github":
        return "github-sync"  # written with a coding agent; excluded from shares for now
    if (origin or {}).get("kind") == "git-bridge":
        return "git-bridge"  # pushed from a Git clone through Overleaf's Git bridge
    author = readers.who({"users": users}, None, names) if users else ""
    if author.startswith(S().me):
        return f"human:{S().me_key}:" + how
    return f"advisor:{S().author_names.get(author, author)}:{how}"


def overleaf_events():
    """One event per inserted span, holding the whole file after that update plus which words the span touches.

    Seeing the inserted words in their new context credits an in-place edit (a word swapped inside an
    existing sentence) with the new word sequences it creates. Every Overleaf project of the paper is
    included, so text that first appears in a later project is credited to its earliest earlier source.
    A project's v0 ("template") is at time 0 for Weili's paper and at the project's creation otherwise; a
    file added later with content but no diff (an upload, a Git-bridge push) is an event at that update.
    """
    events = []
    for p in readers.overleaf_projects(S().inp):
        events += project_events(p)
    return events


def project_events(o):
    after, texts, added = readers.replay(o)
    tag = {} if S().mode == "root" else {"project": o["project_id"]}
    t0 = 0.0 if S().v0_at_zero else o["first_ts"] / 1000
    events = [(t0, "template", dict({"file": p}, **tag), [w for w, _, _ in tokenize(t)], None) for p, t in texts.items()]
    for i, path, content in added:
        m = o["updates"][i]["meta"]
        label = overleaf_label(m.get("origin"), m.get("users"), len(content), (m["end_ts"] - m["start_ts"]) / 1000,
                               o["user_names"])
        events.append((m["end_ts"] / 1000, label, dict({"toV": o["updates"][i]["toV"], "file": path, "added": True}, **tag),
                       [w for w, _, _ in tokenize(content)], None))
    for up, files in zip(o["updates"], after):
        for path, segs in up["diffs"].items():
            if path not in files:
                # a file deleted before the export: its text was never exported, so each insertion
                # is credited with the word sequences inside the inserted text only
                for s in segs:
                    if "i" in s:
                        m = s.get("meta") or up["meta"]
                        label = overleaf_label(up["meta"].get("origin"), m.get("users"), len(s["i"]),
                                               (m["end_ts"] - m["start_ts"]) / 1000, o["user_names"])
                        events.append((m["end_ts"] / 1000, label, dict({"toV": up["toV"], "file": path}, **tag),
                                       [w for w, _, _ in tokenize(s["i"])], None))
                continue
            toks = tokenize(files[path])
            words, ends, starts = [w for w, _, _ in toks], [e for _, _, e in toks], [s for _, s, _ in toks]
            pos = 0
            for s in segs:
                if "u" in s:
                    pos += s["u"]
                elif "i" in s:
                    a, b = pos, pos + len(s["i"])
                    pos = b
                    touched = set(range(bisect.bisect_right(ends, a), bisect.bisect_left(starts, b)))
                    if not touched:
                        continue
                    m = s.get("meta") or up["meta"]
                    label = overleaf_label(up["meta"].get("origin"), m.get("users"), len(s["i"]),
                                           (m["end_ts"] - m["start_ts"]) / 1000, o["user_names"])
                    events.append((m["end_ts"] / 1000, label, dict({"toV": up["toV"], "file": path}, **tag), words, touched))
    return events


def build_events():
    """Time-ordered (time, label, meta, words, touched) events; touched=None means every word is new."""
    events = overleaf_events()
    me = S().me_key
    for m in load_jsonl("chat_messages.jsonl"):
        if not m["created_at"]:
            continue
        if not m.get("paper_related", True) and S().get("relevance_filter", True):
            continue  # a conversation or session that is not about the paper (hand flag, or the relatedness rule)
        meta = {"source": m["source"], "conversation": m["title"], "conversation_id": m["conversation_id"],
                "message_id": m["message_id"]}
        t = to_ts(m["created_at"])
        if m["role"] == "user":
            events.append((t, f"human:{me}:chat-prompt", meta, [w for w, _, _ in tokenize(m["text"])], None))
            for a in m["attachments"]:
                events.append((t, f"human:{me}:chat-attachment", dict(meta, attachment=True),
                               [w for w, _, _ in tokenize(a["content"] or "")], None))
        else:
            label = m.get("label") or ("ai:claude" if m["source"] == "claude.ai" else "ai:chatgpt")
            events.append((t, label, meta, [w for w, _, _ in tokenize(m["text"])], None))
    events.sort(key=lambda e: e[0])
    return events


def first_origins(events, wanted, k):
    """For each shingle in `wanted`, the (time, label, meta) of its earliest event."""
    origin = {}
    for t, label, meta, words, touched in events:
        if touched is None:
            starts = range(len(words) - k + 1)
        else:
            starts = sorted({i for j in touched for i in range(max(0, j - k + 1), min(j, len(words) - k) + 1)})
        for i in starts:
            sh = tuple(words[i:i + k])
            if sh in wanted and sh not in origin:
                origin[sh] = (t, label, meta)
    return origin


def regions(text):
    """Char-level kind for the final text: preamble, box, float, caption, reviewer-comment, latex-comment, prose."""
    kind = ["prose"] * len(text)
    doc = text.find("\\begin{document}")
    for i in range(0, doc if doc > 0 else 0):
        kind[i] = "preamble"
    for envs, name in ((BOX_ENVS, "box"), (FLOAT_ENVS, "float")):
        for env in envs:
            for m in re.finditer(r"\\begin\{%s\}.*?\\end\{%s\}" % (re.escape(env), re.escape(env)), text, re.S):
                for i in range(m.start(), m.end()):
                    kind[i] = name
    for m in re.finditer(r"\\caption\{", text):
        if kind[m.start()] != "float":
            continue
        depth, i = 1, m.end()
        while i < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        for j in range(m.end(), i - 1):
            kind[j] = "caption"
    for m in comment_macro().finditer(text):
        depth, i = 1, m.end()
        while i < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        for j in range(m.start(), i):
            kind[j] = "reviewer-comment"
    for m in re.finditer(r"(?m)(?<!\\)%.*$", text):
        for i in range(m.start(), m.end()):
            kind[i] = "latex-comment"
    return kind


def overleaf_author_at(path):
    """Fallback: Overleaf's own author of each character of the final file."""
    spans = [s for s in load_jsonl("final_attribution.jsonl") if s["file"] == path]
    starts = [s["offset"] for s in spans]

    def at(pos):
        s = spans[bisect.bisect_right(starts, pos) - 1]
        a = s["author"]
        if a.startswith("v0"):
            return "template"
        if a in ("github", "git-bridge"):
            return "github-sync" if a == "github" else a
        if a == S().me:
            return f"human:{S().me_key}:unmatched"
        return f"advisor:{S().author_names.get(a, a)}:unmatched"
    return at


def label_file(path, origin_cache, events, k):
    text = open(os.path.join(S().parsed, "final", path)).read()
    toks = tokenize(text)
    words = [w for w, _, _ in toks]
    shingles = [tuple(words[i:i + k]) for i in range(len(words) - k + 1)]
    origin = origin_cache(set(shingles))
    fallback = overleaf_author_at(path)
    labels = []
    for i in range(len(toks)):
        # the earliest shingle covering word i decides it; on equal times the leftmost shingle wins
        cands = [(origin[shingles[j]], j) for j in range(max(0, i - k + 1), min(i, len(shingles) - 1) + 1)
                 if shingles[j] in origin]
        if cands:
            (t, label, meta), j = min(cands, key=lambda c: (c[0][0], c[1]))
            labels.append((label, dict(meta, ts=t, ngram_start=j), "shingle"))
        else:
            labels.append((fallback(toks[i][1]), {}, "fallback"))
    return text, toks, labels


def section_index(text):
    marks = [(m.start(), m.group(1), m.group(2)) for m in
             re.finditer(r"(?m)^\\(section\*?|subsection\*?|begin\{abstract\})\{?([^}\n]*)\}?", text)]
    return [(pos, ("Abstract" if "abstract" in cmd else name.strip())) for pos, cmd, name in marks]


NON_PAPER = (".bib", ".sty", ".cls", ".bst")
_STOP = {}


def stop_grams(k):
    """k-grams that are never credited to anyone (added 2026-11-19, part 4 of that session's log):
      - in a .bib/.sty/.cls/.bst file of any project (venue names, URLs, template macros);
      - in a conversation or session that is not about the paper (phrasing that also occurs elsewhere).
    A word covered only by such k-grams falls back to its other k-grams, else to Overleaf's own author.
    (A rule dropping k-grams with fewer than 2 content words was tried and rejected: a paper's own example
    sentences can consist almost only of function words.)"""
    if k in _STOP:
        return _STOP[k]
    stop = set()
    if S().get("relevance_filter", True):
        def add(text):
            w = [x for x, _, _ in tokenize(text)]
            stop.update(tuple(w[i:i + k]) for i in range(len(w) - k + 1))
        for p in readers.overleaf_projects(S().inp):
            for path, text in list(readers.final_texts(p).items()) + list(readers.first_texts(p).items()):
                if path.endswith(NON_PAPER):
                    add(text)
        for m in load_jsonl("chat_messages.jsonl"):
            if not m.get("paper_related", True):
                add(m["text"])
                for a in m["attachments"]:
                    add(a["content"] or "")
    _STOP[k] = stop
    return stop


def make_origin_cache(events, k):
    cache = {}
    stop = stop_grams(k)

    def origin_cache(wanted):
        wanted = {sh for sh in wanted if sh not in stop}
        missing = wanted - cache.keys()
        if missing:
            found = first_origins(events, missing, k)
            for sh in missing:
                cache[sh] = found.get(sh)
        return {sh: cache[sh] for sh in wanted if cache[sh] is not None}
    return origin_cache


def run(k, write):
    events = build_events()
    origin_cache = make_origin_cache(events, k)

    totals = Counter()
    span_rows, para_rows, sec_counts = [], [], defaultdict(Counter)
    EXCLUDED = excluded()
    me = S().me_key
    for path in S().files:
        text, toks, labels = label_file(path, origin_cache, events, k)
        kind = regions(text)
        secs = section_index(text)
        sec_pos = [p for p, _ in secs]

        def section_at(pos):
            i = bisect.bisect_right(sec_pos, pos) - 1
            return secs[i][1] if i >= 0 else ("Preamble" if path == S().main_file else "")

        for (w, s, e), (label, meta, how) in zip(toks, labels):
            if kind[s] == "prose" and label not in EXCLUDED:
                totals[label] += 1
                sec_counts[(path, section_at(s))][label] += 1

        # runs of same label + same source message
        run_start = 0
        for i in range(1, len(toks) + 1):
            if i == len(toks) or labels[i][0] != labels[run_start][0] or \
                    labels[i][1].get("message_id") != labels[run_start][1].get("message_id") or \
                    kind[toks[i][1]] != kind[toks[run_start][1]]:
                s, e = toks[run_start][1], toks[i - 1][2]
                label, meta, how = labels[run_start]
                span_rows.append({"file": path, "start": s, "end": e, "words": i - run_start, "kind": kind[s],
                                  "section": section_at(s), "label": label, "how": how,
                                  "source": {a: b for a, b in meta.items() if a != "ngram_start"},
                                  "text": text[s:e]})
                run_start = i

        # paragraphs = blocks separated by blank lines
        for m in re.finditer(r"(?s)(?:[^\n]|\n(?!\s*\n))+", text):
            idx = [i for i, (w, s, e) in enumerate(toks) if m.start() <= s < m.end()]
            if not idx:
                continue
            kinds = Counter(kind[toks[i][1]] for i in idx)
            pkind = kinds.most_common(1)[0][0]
            c = Counter(labels[i][0] for i in idx if kind[toks[i][1]] == pkind and labels[i][0] not in EXCLUDED)
            n = sum(c.values()) or 1
            ai_src = Counter(labels[i][1].get("conversation") for i in idx if labels[i][0].startswith("ai:"))
            para_rows.append({
                "file": path, "start": m.start(), "end": m.end(), "section": section_at(m.start()), "kind": pkind,
                "words": len(idx),
                "ai": round(sum(v for l, v in c.items() if l.startswith("ai:")) / n, 3),
                f"human_{me}": round(sum(v for l, v in c.items() if l.startswith(f"human:{me}")) / n, 3),
                "advisor": round(sum(v for l, v in c.items() if l.startswith("advisor:")) / n, 3),
                "pasted_unmatched": round(sum(v for l, v in c.items() if l.endswith(":pasted")) / n, 3),
                "template": round(c["template"] / n, 3),
                "top_ai_conversation": ai_src.most_common(1)[0][0] if ai_src else "",
                "preview": re.sub(r"\s+", " ", text[m.start():m.end()])[:160],
            })

    OUT = S().analysis
    if write:
        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, "spans.jsonl"), "w") as f:
            for r in span_rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        with open(os.path.join(OUT, "paragraphs.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(para_rows[0].keys()))
            w.writeheader()
            w.writerows(para_rows)
        labels_all = sorted({l for c in sec_counts.values() for l in c})
        with open(os.path.join(OUT, "sections.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["file", "section", "prose_words"] + labels_all)
            for (path, sec), c in sec_counts.items():
                n = sum(c.values())
                w.writerow([path, sec, n] + [round(c[l] / n, 3) for l in labels_all])
    return totals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=6)
    ap.add_argument("--sensitivity", action="store_true", help="also report overall shares for k=4,6,8,10")
    args = settings.parse_args(ap).args
    totals = run(args.k, write=True)
    n = sum(totals.values())
    print(f"k={args.k} prose words={n}", {l: round(v / n, 3) for l, v in totals.most_common()})
    if args.sensitivity:
        rows = []
        for k in (4, 6, 8, 10):
            t = totals if k == args.k else run(k, write=False)
            n = sum(t.values())
            rows.append({"k": k, "prose_words": n, **{l: round(v / n, 3) for l, v in t.items()}})
            print(rows[-1])
        keys = sorted({key for r in rows for key in r}, key=lambda x: (x not in ("k", "prose_words"), x))
        with open(os.path.join(S().analysis, "sensitivity.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys, restval=0)
            w.writeheader()
            w.writerows(rows)


if __name__ == "__main__":
    main()
