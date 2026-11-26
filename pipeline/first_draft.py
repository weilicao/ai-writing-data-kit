"""First drafts: follow each final sentence back to the earliest version that is recognizably the same text.

Versions are the distinct sentences ever seen, each at its earliest time:
  - Overleaf: every sentence of every .tex file after every update of every project (and at v0), so text that
    was moved, rewritten in steps or carried into a later project is followed through every state;
  - every chat or session message: prompts and attached documents (the contributor's), AI outputs, commits.
From the final sentence, step to the most similar earlier version that is "recognizably the same" (ties: the
earlier one), and repeat until no earlier version qualifies (WikiWho-style chaining; docs/prior_work.md). The last
version reached is the first draft.

"Recognizably the same" (x = the later version, y = an earlier candidate; containment of x in y, METER's direction):
  T1 strict   word 3-gram containment C3(x<-y) >= 0.8
  T2 medium   C3(x<-y) >= 0.5
  T3 loose    T2, or the retyped rule (retyped.py): >= 5 shared content words, >= 50% of x's content words in y,
              content-word order ratio >= 0.35
Sentences with fewer than 4 words are not traced (their first draft is themselves).

The first draft is labeled from its words, like a final unit (scripts/labels.py), but with three outcomes:
  AI if its AI words >= 50%, else Human if Human >= Unclear, else Unclear (Template / Excluded when it has only those).
Words of an Overleaf or commit version take the 6-gram origin of match_provenance.py (earliest event); a message
version takes the message's own label (AI output -> AI, prompt -> Human, attached document -> Unclear, commit ->
its Git label).

Writes analysis/first_drafts.csv (one row per final sentence and threshold, with the chain). The transitions
(first-draft group x final sentence label) are written by sentence_labels.py, which also uses the first draft at
labels.DIRECTION_THRESHOLD to tell "AI, human-edited" from "Human, AI-edited".
"""
import csv
import difflib
import os
import re
import sys
from collections import Counter, OrderedDict, defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import labels as lb  # noqa: E402
import match_provenance as mp  # noqa: E402
import readers  # noqa: E402
import retyped as rt  # noqa: E402
import settings  # noqa: E402

THRESHOLDS = OrderedDict([("T1", 0.8), ("T2", 0.5), ("T3", 0.5)])
MIN_WORDS = 4
SENT_SPLIT = re.compile(r"(?<!\be\.g)(?<!\bi\.e)(?<!\bvs)(?<!\bet al)(?<!\bFig)(?<!\bSec)(?<!\bEq)(?<=[.!?])\s+(?=[A-Z\\])"
                        r"|\n\s*\n|\\\\")
COMMENT = re.compile(r"(?m)(?<!\\)%.*$")
TEX = re.compile(r"\.tex$", re.I)
FD_GROUPS = ["AI", "Human", "Unclear", "Template", "Excluded"]


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else ""


def grams3(w):
    return {tuple(w[i:i + 3]) for i in range(len(w) - 2)} or ({tuple(w)} if w else set())


def strip_comments(text):
    """Drop LaTeX comments and co-author comment macros (\\sz{...}, with balanced braces)."""
    text = COMMENT.sub("", text)
    if not settings.current().comment_macros:
        return text
    out, pos = [], 0
    for m in mp.comment_macro().finditer(text):
        if m.start() < pos:
            continue
        depth, i = 1, m.end()
        while i < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        out.append(text[pos:m.start()])
        pos = i
    return "".join(out) + text[pos:]


def split(text):
    """Sentences of a text as word lists (comments and comment macros dropped, LaTeX masked)."""
    out = []
    for part in SENT_SPLIT.split(strip_comments(text)):
        w = [x for x, _, _ in mp.tokenize(part)]
        if len(w) >= 3:
            out.append(w)
    return out


class Versions:
    """Distinct sentences, each at its earliest time, with an inverted index on 3-grams and content words."""

    def __init__(self):
        self.key = {}        # tuple(words) -> id
        self.rows = []       # [words, time, source, kind, label, event]
        self.g3 = defaultdict(list)
        self.cw = defaultdict(list)

    def add(self, words, t, kind, label, event):
        k = tuple(words)
        i = self.key.get(k)
        if i is not None:
            if t < self.rows[i]["t"]:
                self.rows[i].update(t=t, kind=kind, label=label, event=event)
            return
        i = self.key[k] = len(self.rows)
        self.rows.append({"w": list(words), "t": t, "kind": kind, "label": label, "event": event,
                          "g3": grams3(words), "c": rt.content(words)})
        for g in self.rows[i]["g3"]:
            self.g3[g].append(i)
        for c in set(self.rows[i]["c"]):
            self.cw[c].append(i)


def build_versions(S):
    V = Versions()
    for p in readers.overleaf_projects(S.inp):
        after, base, added = readers.replay(p)
        tag = "" if S.mode == "root" else p["project_id"] + ":"
        t0 = 0.0 if S.v0_at_zero else p["first_ts"] / 1000
        for path, text in base.items():
            if TEX.search(path):
                for w in split(text):
                    V.add(w, t0, "overleaf", "template", f"template:{tag}{path}")
        seen_par = set()
        for up, files in zip(p["updates"], after):
            m = up["meta"]
            label = mp.overleaf_label(m.get("origin"), m.get("users"), 0, 1, p["user_names"])
            for path in up["diffs"]:
                if not TEX.search(path):
                    continue
                if path in files:
                    paras = files[path].split("\n\n")
                else:  # a file deleted before the export: only its inserted text is known
                    paras = ["".join(s.get("i", "") for s in up["diffs"][path])]
                for para in paras:
                    if para in seen_par:
                        continue
                    seen_par.add(para)
                    for w in split(para):
                        V.add(w, m["end_ts"] / 1000, "overleaf", label, f"overleaf:{tag}v{up['toV']}")
        for i, path, content in added:
            m = p["updates"][i]["meta"]
            label = mp.overleaf_label(m.get("origin"), m.get("users"), len(content), (m["end_ts"] - m["start_ts"]) / 1000,
                                      p["user_names"])
            for w in split(content):
                V.add(w, m["end_ts"] / 1000, "overleaf", label, f"overleaf:{tag}v{p['updates'][i]['toV']}")
    me = S.me_key
    for m in mp.load_jsonl("chat_messages.jsonl"):
        if not m["created_at"] or (not m.get("paper_related", True) and S.get("relevance_filter", True)):
            continue
        t = mp.to_ts(m["created_at"])
        ev = f"{m['source']}:{m['conversation_id']}:{m['message_id']}"
        if m["role"] == "user":
            for w in split(m["text"]):
                V.add(w, t, "prompt", f"human:{me}:chat-prompt", ev)
            for a in m["attachments"]:
                for w in split(a["content"] or ""):
                    V.add(w, t, "attachment", f"human:{me}:chat-attachment", ev)
        else:
            label = m.get("label") or ("ai:claude" if m["source"] == "claude.ai" else "ai:chatgpt")
            for w in split(m["text"]):
                V.add(w, t, "message", label, ev)
    return V


def similar(x, y, level):
    """Containment of x in y (3-grams), or the retyped rule for T3; returns a score or 0."""
    c3 = len(x["g3"] & y["g3"]) / len(x["g3"])
    if c3 >= THRESHOLDS[level]:
        return c3
    if level == "T3":
        xs, ys = set(x["c"]), set(y["c"])
        shared = len(xs & ys)
        if shared >= rt.MIN_SHARED and xs and shared / len(xs) >= rt.MIN_CONTAINMENT:
            order = difflib.SequenceMatcher(None, x["c"], y["c"], autojunk=False).ratio()
            if order >= rt.MIN_ORDER:
                return shared / len(xs) * 0.999  # ranks below an equal 3-gram containment
    return 0


def trace(V, start, level):
    """Chain of (version id, score) from the final sentence back to its first draft."""
    x = {"w": start, "g3": grams3(start), "c": rt.content(start), "t": float("inf")}
    chain = []
    while True:
        cand = Counter()
        for g in x["g3"]:
            for i in V.g3.get(g, ()):
                cand[i] += 1
        if level == "T3":
            for c in set(x["c"]):
                for i in V.cw.get(c, ()):
                    cand[i] += 0
        best = None
        for i in cand:
            y = V.rows[i]
            if y["t"] >= x["t"] or (chain and i == chain[-1][0]):
                continue
            if level != "T3" and cand[i] / len(x["g3"]) < THRESHOLDS[level]:
                continue
            s = similar(x, y, level)
            if s and (best is None or (s, -y["t"]) > (best[1], -V.rows[best[0]]["t"])):
                best = (i, s)
        if best is None:
            return chain
        chain.append(best)
        x = V.rows[best[0]]


def fd_group(labels_, S):
    g = Counter(lb.word_label({"cat": "ai" if l.startswith("ai:") else "human", "label": l}, S)[0] for l in labels_)
    n = g["AI"] + g["Human"] + g["Unclear"]
    if not n:
        return "Template" if g["Template"] else "Excluded"
    if g["AI"] / n >= 0.5:
        return "AI"
    return "Human" if g["Human"] >= g["Unclear"] else "Unclear"


def main():
    S = settings.parse_args()
    A = S.analysis
    sents = list(csv.DictReader(open(os.path.join(A, "level_sentences.csv"))))
    words = defaultdict(list)
    for w in csv.DictReader(open(os.path.join(A, "level_words.csv"))):
        words[w["sentence"]].append(w)
    V = build_versions(S)

    traced = {}
    for level in THRESHOLDS:
        for s in sents:
            ws = [x for x, _, _ in mp.tokenize(" ".join(w["word"] for w in words[s["id"]]))]
            traced[(s["id"], level)] = trace(V, ws, level) if len(ws) >= MIN_WORDS else None

    # label each first draft from its words: 6-gram origins for Overleaf / commit versions
    events = mp.build_events()
    cache = mp.make_origin_cache(events, mp_k())
    ends = {c[-1][0] for c in traced.values() if c}
    wanted = set()
    for i in ends:
        w = V.rows[i]["w"]
        wanted |= {tuple(w[j:j + mp_k()]) for j in range(len(w) - mp_k() + 1)}
    origin = cache(wanted)

    def version_group(i):
        r = V.rows[i]
        if r["kind"] != "overleaf" and not r["label"].startswith("git"):
            return fd_group([r["label"]], S), r["label"]
        w, k = r["w"], mp_k()
        labs = []
        for j in range(len(w)):
            c = [origin[tuple(w[a:a + k])] for a in range(max(0, j - k + 1), min(j, len(w) - k) + 1)
                 if tuple(w[a:a + k]) in origin]
            labs.append(min(c, key=lambda o: o[0])[1] if c else r["label"])
        return fd_group(labs, S), Counter(labs).most_common(1)[0][0]

    rows = []
    for s in sents:
        for level in THRESHOLDS:
            chain = traced[(s["id"], level)]
            if chain:
                first, steps = V.rows[chain[-1][0]], chain
                fg, flabel = version_group(chain[-1][0])
            else:
                # too short to trace, or nothing earlier qualifies: the sentence's own word labels
                first, steps, flabel = None, [], "untraced"
                fg = fd_group([w_label_to_prov(w, S) for w in words[s["id"]]], S)
            rows.append(OrderedDict(
                sentence=s["id"], block=s["block"], section=s["section"], words=s["words"], counted=s["counted"],
                threshold=level, first_draft_group=fg,
                first_draft_label=flabel, first_draft_event=settings.event_name(first["event"]) if first else "",
                first_draft_time=iso(first["t"]) if first else "", first_draft_kind=first["kind"] if first else "",
                steps=len(steps), chain=" <- ".join(f"{settings.event_name(V.rows[i]['event'])}@{sc:.2f}" for i, sc in steps),
                # with "redact" (shared results) the first draft is not quoted: it is chat or Overleaf-history text
                first_draft_text=(" ".join(first["w"]) if first and not S.get("redact")
                                  else f"[{first['kind']} text, not shared]" if first else ""),
                final_text=s["text"]))
    with open(os.path.join(A, "first_drafts.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    for level in THRESHOLDS:
        sel = [r for r in rows if r["threshold"] == level and r["block"] == "Body"]
        n = sum(int(r["counted"]) for r in sel) or 1
        fd = Counter()
        for r in sel:
            fd[r["first_draft_group"]] += int(r["counted"])
        print(level, "Body first drafts:", {g: round(v / n, 3) for g, v in fd.most_common()})


def mp_k():
    return 6


def w_label_to_prov(w, S):
    """A level_words.csv row back to a provenance-like label for fd_group (AI / human / unclear / template)."""
    g = w["group"]
    return {"AI": "ai:x", "Human": f"human:{S.me_key}:typed", "Unclear": f"human:{S.me_key}:pasted",
            "Template": "template"}.get(g, (S.excluded or ["github-sync"])[0])


if __name__ == "__main__":
    main()
