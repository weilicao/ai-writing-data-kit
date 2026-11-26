"""Word and 6-gram labels of the main paper, and the word statistics of every sentence (scripts/labels.py).

Reads analysis/paper_units.jsonl (explain_edits.py). Writes to analysis/:
  level_ngrams.csv      each 6-gram that decided at least one word: its text and the event it first appeared in
  level_words.csv       every word: group, category, flags, and its source event (message / session event /
                        Overleaf version / commit / template)
  level_sentences.csv   every sentence: share per group and category, edit counts, AI sources; its label is added
                        by sentence_labels.py (after first_draft.py, since the label needs the first draft)
Blocks: Body (prose) and Captions and Tables (captions, table cells, figure and algorithm text).
"""
import csv
import json
import os
import sys
from collections import Counter, OrderedDict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import labels as lb  # noqa: E402
import settings  # noqa: E402

UNUSED = "unused part of reply"
COUNTED = ("AI", "Human", "Unclear")


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else ""


def block(kind):
    return "Body" if kind == "prose" else "Captions and Tables"


def removed_in(tokens, after_end=()):
    return sum(r["words"] for t in tokens for r in t["removed_before"] if r["status"] != UNUSED) + \
        sum(r["words"] for r in after_end if r["status"] != UNUSED)


def sources(tokens, S):
    """AI source events of a unit, most words first: 'event (conversation): n'."""
    c = Counter((t.get("ai_event") or t["event"], t["conversation"]) for t in tokens
                if lb.word_label(t, S)[0] == "AI" and (t.get("ai_event") or t["event"]))
    return "; ".join(f"{settings.event_name(e)}" + ("" if settings.current().get("redact") else f" ({conv})") + f": {n}"
                     for (e, conv), n in c.most_common())


def unit_row(uid, level, blk, section, tokens, removed, S, text=""):
    _, _, st = lb.classify(tokens, removed, S)
    n = st["counted"] or 1
    cats = Counter(lb.word_label(t, S)[1] for t in tokens if lb.word_label(t, S)[0] in COUNTED)
    row = OrderedDict(id=uid, level=level, block=blk, section=section, words=st["words"], counted=st["counted"])
    for grp in COUNTED:
        row[f"share_{grp.lower()}"] = round(st[grp] / n, 3)
    row.update(template=st["Template"], excluded=st["Excluded"], ai_as_written=st.get("ai_as_written", 0),
               retyped=st.get("retyped", 0), changed_human=st.get("changed_human", 0),
               changed_unclear=st.get("changed_unclear", 0), ai_words_removed=removed,
               own_words_repeated_by_ai=st.get("own_repeated", 0),
               categories="; ".join(f"{c} {v / n:.3f}" for c, v in cats.most_common()),
               ai_sources=sources(tokens, S))
    if text:
        row["text"] = text
    return row


def sentence_tokens(units):
    """(sentence id, paragraph id, block, section, tokens, tokens' unit) for every sentence, in order."""
    for pi, u in enumerate(units, 1):
        pid, blk = f"P{pi:03d}", block(u["kind"])
        starts = u["sentence_starts"] + [u["end"] + 1]
        for si, (a, b) in enumerate(zip(starts, starts[1:]), 1):
            toks = [t for t in u["tokens"] if a <= t["s"] < b]
            if toks:
                yield f"{pid}.S{si:02d}", pid, blk, u["section"], toks, (u if b > u["end"] else None)


def write_csv(path, rows):
    keys = list(OrderedDict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, restval="")
        w.writeheader()
        w.writerows(rows)


def main():
    S = settings.parse_args()
    A = S.analysis
    text = open(os.path.join(S.parsed, "final", S.main_file)).read()
    units = [json.loads(l) for l in open(os.path.join(A, "paper_units.jsonl"))]
    flat = lambda s: " ".join(s.split())  # noqa: E731

    words, sents, ngrams = [], [], OrderedDict()
    for sid, pid, blk, section, toks, last in sentence_tokens(units):
        after = last["removed_after_end"] if last else ()
        sents.append(unit_row(sid, "sentence", blk, section, toks, removed_in(toks, after), S,
                              flat(text[toks[0]["s"]:toks[-1]["e"]])))
        for t in toks:
            g, cat = lb.word_label(t, S)
            words.append(OrderedDict(
                id=f"{sid}.W{len(words) + 1:05d}", block=blk, section=section, paragraph=pid, sentence=sid,
                start=t["s"], end=t["e"], word=text[t["s"]:t["e"]], group=g, label=cat,
                flags=";".join(lb.flags(t, S)), source=t["who"],
                conversation=settings.conv_name(t.get("ai_event") or t["event"], t["conversation"]),
                event=settings.event_name(t["event"]), event_time=iso(t["ts"]), ngram=t["ngram"],
                ai_event=settings.event_name(t.get("ai_event") or (t["event"] if g == "AI" else ""))))
            if t["ngram"]:
                k = (t["ngram"], t["event"])
                r = ngrams.setdefault(k, OrderedDict(ngram=t["ngram"], event=settings.event_name(t["event"]),
                                                     event_time=iso(t["ts"]),
                                                     source=t["who"], group=g,
                                                     conversation=settings.conv_name(t["event"], t["conversation"]),
                                                     label=cat, first_word=f"{sid}.W{len(words):05d}", words=0))
                r["words"] += 1

    write_csv(os.path.join(A, "level_ngrams.csv"), list(ngrams.values()))
    write_csv(os.path.join(A, "level_words.csv"), words)
    write_csv(os.path.join(A, "level_sentences.csv"), sents)
    for old in ("level_paragraphs.csv", "level_sections.csv"):  # paragraph and section labels: left for later
        if os.path.exists(os.path.join(A, old)):
            os.remove(os.path.join(A, old))
    print(f"{len(words)} words, {len(sents)} sentences, {len(ngrams)} deciding 6-grams")


if __name__ == "__main__":
    main()
