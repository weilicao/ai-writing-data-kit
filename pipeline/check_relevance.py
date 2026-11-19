"""Is what we extract about the paper? Writes analysis/relevance_report.md and analysis/suspicious_matches.csv.

1. The relatedness rule (collect/INSTRUCTIONS.md section 9, parse_raw.relatedness) against hand labels: for the repo
   root, `paper_related` in parsed/chat_conversations.csv is Weili's hand flag (her off-topic lists); for a bundle,
   conversations listed in <data>/hand_labels.csv (source,id,paper_related), if present, are the reference.
2. Every AI-labeled word's deciding 6-gram and source, checked for:
     unrelated     its conversation or session fails the relatedness rule (or the hand flag says off-topic)
     stop-gram     the 6-gram has fewer than 2 content words (e.g. "as shown in table 1 the"); flag only, since
                   such text can be the paper's content (example sentences in a philosophy paper)
     boilerplate   the 6-gram is also in a .bib/.sty/.cls/.bst file
     background    the 6-gram also occurs in an unrelated conversation or session
     isolated      the AI run in its sentence is a single 6-gram (6 words) and no other word of the sentence comes
                   from that conversation or session
     out-of-window the message is more than 30 days before the first Overleaf update of any project
"""
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import match_provenance as mp  # noqa: E402
import parse_raw  # noqa: E402
import readers  # noqa: E402
import retyped as rt  # noqa: E402
import settings  # noqa: E402

K = 6
NON_PAPER = (".bib", ".sty", ".cls", ".bst")


def content_count(words):
    return len(rt.content(list(words)))


def main():
    S = settings.parse_args()
    A, P = S.analysis, S.parsed
    projects = readers.overleaf_projects(S.inp)
    msgs = mp.load_jsonl("chat_messages.jsonl")
    convs = list(csv.DictReader(open(os.path.join(P, "chat_conversations.csv"))))
    by_conv = defaultdict(list)
    for m in msgs:
        by_conv[(m["source"], m["conversation_id"])].append(m)

    # 1. the rule vs the reference labels
    test = parse_raw.relatedness(projects, S.title,
                                 [((p["raw"].get("github_sync") or {}).get("repo") or "") for p in projects])
    rule = {}
    for (src, cid), rs in by_conv.items():
        rule[(src, cid)] = test("\n".join([r["text"] for r in rs] + [a["content"] or "" for r in rs for a in r["attachments"]]))
    ref = {}
    if S.mode == "root":
        ref = {(c["source"], c["id"]): c["paper_related"] == "True" for c in convs}
    elif os.path.exists(os.path.join(S.data, "hand_labels.csv")):
        ref = {(c["source"], c["id"]): c["paper_related"] == "True"
               for c in csv.DictReader(open(os.path.join(S.data, "hand_labels.csv")))}
    conf = Counter()
    misses = []
    for k, why in rule.items():
        if k in ref:
            conf[(ref[k], bool(why))] += 1
            if ref[k] != bool(why):
                misses.append((k, ref[k], why or "no rule fired", len(by_conv[k])))
    kinds = Counter((k[0], bool(why)) for k, why in rule.items())
    reasons = Counter(re.sub(r"^\d+ ", "", why) if why else "not related" for why in rule.values())

    # 2. matches
    related = {k for k, why in rule.items() if why}
    if S.mode == "root":  # the hand flag is the truth here
        related = {k for k, v in ref.items() if v}
    unrelated_grams = defaultdict(set)
    for k, rs in by_conv.items():
        if k in related:
            continue
        for r in rs:
            for t in [r["text"]] + [a["content"] or "" for a in r["attachments"]]:
                w = [x for x, _, _ in mp.tokenize(t)]
                for i in range(len(w) - K + 1):
                    unrelated_grams[tuple(w[i:i + K])].add(k)
    boiler = set()
    first_ts = min(p["first_ts"] for p in projects) / 1000
    for p in projects:
        texts = {}
        for path, t in list(readers.final_texts(p).items()) + list(readers.first_texts(p).items()):
            if path.endswith(NON_PAPER):
                texts[path + str(len(texts))] = t
        for path, t in texts.items():
            w = [x for x, _, _ in mp.tokenize(t)]
            boiler |= {tuple(w[i:i + K]) for i in range(len(w) - K + 1)}
    conv_of_event = {}
    for m in msgs:
        ev = f"{m['source']}:{m['conversation_id']}:{m['message_id']}"
        conv_of_event[ev] = conv_of_event[settings.event_name(ev)] = (m["source"], m["conversation_id"])

    words = list(csv.DictReader(open(os.path.join(A, "level_words.csv"))))
    by_sent = defaultdict(list)
    for w in words:
        by_sent[w["sentence"]].append(w)
    flagged, flag_words = [], Counter()

    def src(w):  # the conversation or session (an agent writes one sentence over many messages)
        return conv_of_event.get(w["event"]) or w["event"]

    for sid, ws in by_sent.items():
        runs, cur = [], []
        for w in ws:
            if w["group"] == "AI" and w["ngram"] and "retyped" not in w["label"]:
                if cur and src(cur[-1]) != src(w):
                    runs.append(cur)
                    cur = []
                cur.append(w)
            elif cur:
                runs.append(cur)
                cur = []
        if cur:
            runs.append(cur)
        for run in runs:
            ev = run[0]["event"]
            grams = {tuple(w["ngram"].split()) for w in run}
            conv = conv_of_event.get(ev)
            t = mp.to_ts(run[0]["event_time"]) if run[0]["event_time"] else None
            flags = []
            if conv and conv not in related:
                flags.append("unrelated")
            if all(content_count(g) < 2 for g in grams):
                flags.append("stop-gram")
            if grams and all(g in boiler for g in grams):
                flags.append("boilerplate")
            if grams and all(unrelated_grams.get(g) for g in grams):
                flags.append("background")
            if len(run) <= K and len(grams) == 1 and sum(src(w) == src(run[0]) for w in ws) == len(run):
                flags.append("isolated")
            if t and t < first_ts - 30 * 86400:
                flags.append("out-of-window")
            if flags:
                for f in flags:
                    flag_words[f] += len(run)
                flagged.append({"sentence": sid, "section": run[0]["section"], "words": len(run), "flags": ";".join(flags),
                                "event": ev, "conversation": run[0]["conversation"],
                                "event_time": run[0]["event_time"],
                                "ngram": run[0]["ngram"], "text": " ".join(w["word"] for w in run)})
    ai_words = sum(w["group"] == "AI" for w in words)
    with open(os.path.join(A, "suspicious_matches.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["sentence", "section", "words", "flags", "event", "conversation", "event_time",
                                          "ngram", "text"])
        w.writeheader()
        w.writerows(flagged)

    out = [f"# Relevance check: {S.title}", "", "Generated by scripts/check_relevance.py.", "",
           "## Conversations and sessions kept by the relatedness rule", ""]
    if ref:
        tp, fp, fn, tn = conf[(True, True)], conf[(False, True)], conf[(True, False)], conf[(False, False)]
        out += [f"Against {'the hand flags' if S.mode == 'root' else 'hand_labels.csv'} ({len(ref)} conversations): "
                f"kept {tp} of {tp + fn} related (recall {tp / max(tp + fn, 1):.1%}), and {fp} of {fp + tn} unrelated; "
                f"precision {tp / max(tp + fp, 1):.1%}.", ""]
        out += ["| source | id | hand label | rule | messages |", "|---|---|---|---|---|"]
        out += [f"| {k[0]} | {k[1][:13]} | {'related' if r else 'unrelated'} | {why} | {n} |" for k, r, why, n in misses]
        out.append("")
    else:
        out += ["No hand labels for this data (add `hand_labels.csv` with source,id,paper_related to measure).", ""]
    out += ["Kept by source: " + ", ".join(f"{s} {sum(v for (ss, kk), v in kinds.items() if ss == s and kk)}/"
                                           f"{sum(v for (ss, _), v in kinds.items() if ss == s)}"
                                           for s in sorted({s for s, _ in kinds})),
            "Reasons: " + ", ".join(f"{r} {v}" for r, v in reasons.most_common()), "",
            "## AI matches that look suspicious", "",
            f"{ai_words} AI words. Flagged runs: {len(flagged)}; words per flag (a run can have several): " +
            ", ".join(f"{f} {v}" for f, v in flag_words.most_common()) + ".", "",
            "| sentence | words | flags | conversation | 6-gram |", "|---|---|---|---|---|"]
    for r in sorted(flagged, key=lambda r: (-len(r["flags"].split(";")), r["sentence"]))[:40]:
        out.append(f"| {r['sentence']} | {r['words']} | {r['flags']} | {r['conversation'][:40]} | {r['ngram']} |")
    open(os.path.join(A, "relevance_report.md"), "w").write("\n".join(out) + "\n")
    print("\n".join(out[:12]))
    print(f"AI words={ai_words} flagged runs={len(flagged)}", dict(flag_words))


if __name__ == "__main__":
    main()
