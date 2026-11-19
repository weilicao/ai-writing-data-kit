"""Main paper only: say what every sentence is, and show how AI text was changed on its way in.

For each paragraph (and figure/table caption) of example_paper.tex, words are labeled with
match_provenance. Each sentence with Claude text is aligned word by word against the Claude reply
most of its AI words came from:
  kept     Claude's word survives
  removed  Claude's word is gone: changed when pasting (never in Overleaf in that context) or later in Overleaf
  added    word not in that reply, credited to whoever first wrote it (Weili / advisor / another reply)
  echoed   a word someone wrote before the reply (Weili's typing or prompt, an advisor, a paste) that Claude
           repeated unchanged; not an edit, and it keeps its original author
  retyped  a word Weili put in herself, in a sentence that closely follows an earlier Claude sentence
           (see retyped.py), so it was read in a Claude reply and retyped rather than written fresh

Outputs in ../analysis:
  paper_sentences.csv   every sentence: section, class, AI share, writer, source chat
  paper_edits.csv       every change to Claude text: removed/added/replaced words, who, when, tags
  paper_units.jsonl     per paragraph: tokens with categories and removals, for rendering
"""
import csv
import difflib
import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import match_provenance as mp  # noqa: E402
import labels as lb  # noqa: E402
import retyped as rt  # noqa: E402
import settings  # noqa: E402

K = 6
UNUSED_CHUNK = 30  # a removed run this long is a part of the reply that was never used, not an edit
UNUSED = "unused part of reply"
UNCLEAR = "Unclear: pasted by "
PAPER_COPY = "Unclear: from a paper copy"  # first seen in a paper version pasted into a chat, edited outside Overleaf
AI_NAMES = {"ai:claude": "Claude", "ai:chatgpt": "ChatGPT", "ai:claude-code": "Claude Code", "ai:codex": "Codex",
            "ai:cursor": "Cursor", "ai:agent-commit": "AI agent (Git commit)"}
EXCLUDED_NAMES = {"github-sync": "GitHub sync", "git-bridge": "Git bridge"}


def me():
    return settings.current().me_key


def own_labels():
    """Labels that are provably the contributor's own writing (Weili's, for her paper)."""
    return (f"human:{me()}:typed", f"human:{me()}:chat-prompt")
INTENSIFIERS = {"significantly", "substantially", "dramatically", "remarkably", "notably", "crucially", "fundamentally",
                "consistently", "highly", "particularly", "strongly", "clearly", "effectively", "comprehensive",
                "novel", "robust", "key", "critical", "crucial", "powerful", "superior", "striking", "unprecedented"}
SENT_SPLIT = re.compile(r"(?<!\be\.g)(?<!\bi\.e)(?<!\bvs)(?<!\bet al)(?<!\bFig)(?<!\bSec)(?<!\bEq)(?<=[.!?])\s+(?=[A-Z\\])")


def who(label):
    short = settings.current().me_short
    if label.startswith("ai:"):
        return AI_NAMES.get(label, label[3:])
    if label == f"human:{me()}:pasted":
        return UNCLEAR + short  # a paste into Overleaf matching no chat
    if label == f"human:{me()}:chat-attachment":
        return PAPER_COPY
    if label.startswith(f"human:{me()}"):
        return short
    if label.startswith("advisor:"):
        # an advisor's paste matches no chat we have: could be AI or their own notes
        name = label.split(":")[1]
        return UNCLEAR + name if label.endswith(":pasted") else name
    if label.startswith("git:"):
        return "Unclear: Git commit by " + label[4:]  # committed without an AI trailer, matches no session
    if label == "git-ai":
        return "Unclear: AI-trailer commit"  # committed with an AI trailer, in no collected session
    return dict(EXCLUDED_NAMES, template="Template").get(label, label)


def event_id(meta):
    """Where a word's deciding 6-gram first appeared: a chat message or session event, an Overleaf version, a
    commit, or the template."""
    if "message_id" in meta:
        return f"{meta.get('source', 'chat')}:{meta.get('conversation_id', '')}:{meta['message_id']}"
    if "toV" in meta:
        project = meta.get("project", settings.current().get("main_project"))
        return f"overleaf:{project + ':' if project else ''}v{meta['toV']}"
    if "file" in meta:
        return f"template:{meta['project'] + ':' if meta.get('project') else ''}{meta['file']}"
    return ""


def norm(words):
    return " " + " ".join(words) + " "


def overleaf_index():
    S = settings.current()
    ins, dels = [], []
    for e in mp.load_jsonl("overleaf_edits.jsonl"):
        if e["file"] != S.main_file or e.get("project", S.get("main_project")) != S.get("main_project"):
            continue
        row = (e["span_end_ts"], who((f"human:{me()}" if e["author"] == S.me else
                                      "github-sync" if e["origin"] == "github" else
                                      "git-bridge" if e["origin"] == "git-bridge" else
                                      "advisor:" + S.author_names.get(e["author"], e["author"]))),
               norm([w for w, _, _ in mp.tokenize(e["text"])]), e["start"])
        (ins if e["op"] == "insert" else dels).append(row)
    return ins, dels


def find_event(events, words, after):
    """Earliest Overleaf event after `after` whose text contains `words` as a run."""
    needle = norm(words)
    hits = [e for e in events if e[0] >= after - 60 and needle in e[2]]
    return min(hits, key=lambda e: e[0]) if hits else None


def align_sentence(js, words, cat, labs, msgs, ins_events, del_events, removals, surface):
    """Align one sentence with the Claude reply most of its AI words came from.

    Marks kept tokens in `cat`, records removed reply text in `removals`, returns the edit rows.
    `surface(j1, j2)` gives the final LaTeX text spanning unit tokens j1..j2.
    """
    ai_msgs = Counter(labs[j][1].get("message_id") for j in js if labs[j][0].startswith("ai:"))
    if not ai_msgs:
        return {"reply": None, "paste": None, "edits": []}
    reply = msgs[ai_msgs.most_common(1)[0][0]]
    reply_ts = mp.to_ts(reply["created_at"])
    rtoks = mp.tokenize(reply["text"])
    rwords = [w for w, _, _ in rtoks]
    swords = [words[j] for j in js]
    blocks = [b for b in difflib.SequenceMatcher(None, rwords, swords, autojunk=False).get_matching_blocks()
              if b.size >= 3]
    if not blocks:
        return {"reply": reply, "paste": None, "edits": []}
    lo, hi = blocks[0].a, blocks[-1].a + blocks[-1].size
    ops = difflib.SequenceMatcher(None, rwords[lo:hi], swords, autojunk=False).get_opcodes()
    kept = [b for op, a1, a2, b1, b2 in ops if op == "equal" for b in range(b1, b2)]
    for b in kept:
        # Claude's own words that survive, or earlier words (anyone's) that Claude repeated
        if cat[js[b]]["cat"] in ("ai", "human"):
            cat[js[b]]["cat"] = "kept" if cat[js[b]]["cat"] == "ai" else "echoed"
    run = [swords[b] for b in kept[:8]]
    hit = find_event(ins_events, run, reply_ts) if len(run) >= 4 else None
    paste = {"by": hit[1], "at": hit[3], "ts": hit[0]} if hit else None

    edits = []
    for op, a1, a2, b1, b2 in ops:
        if op == "equal":
            continue
        gone = rwords[lo + a1:lo + a2]
        human = [b for b in range(b1, b2) if cat[js[b]]["cat"] == "human"]
        other_ai = [b for b in range(b1, b2) if cat[js[b]]["cat"] == "ai"]
        human_by = Counter(cat[js[b]]["who"] for b in human)
        status = ""
        if gone:
            if len(gone) >= UNUSED_CHUNK and not human:
                status = UNUSED
            else:
                ctx = rwords[max(lo + a1 - 2, 0):lo + a1] + gone + rwords[lo + a2:lo + a2 + 2]
                in_overleaf = find_event(ins_events, ctx, reply_ts)
                if not in_overleaf:
                    status = "changed when pasting"
                elif human:
                    top = human_by.most_common(1)[0][0]
                    if top.startswith(UNCLEAR):
                        top = top[len(UNCLEAR):] + " (pasted, source unknown)"
                    status = f"replaced in Overleaf by {top}"
                else:
                    d = find_event(del_events, gone, in_overleaf[0])
                    status = f"deleted in Overleaf by {d[1]}" if d else "deleted in Overleaf (who unknown)"
            s0, e0 = rtoks[lo + a1][1], rtoks[lo + a2 - 1][2]
            removals.setdefault(js[b1] if b1 < len(js) else js[-1] + 1, []).append(
                {"text": reply["text"][s0:e0], "status": status, "words": len(gone)})
        if status == UNUSED or (not gone and not human):
            continue
        if gone and human:
            kind = "replaced"
        elif gone:
            kind = "swapped for other Claude text" if other_ai else "removed"
        else:
            kind = "added"
        added = [swords[b] for b in human]
        gone_text = reply["text"][rtoks[lo + a1][1]:rtoks[lo + a2 - 1][2]] if gone else ""
        added_text = surface(js[human[0]], js[human[-1]]) if human else ""
        tags = []
        if any(re.search(r"\d", w) for w in gone + added):
            tags.append("numbers")
        if (set(gone) - set(added)) & INTENSIFIERS:
            tags.append("dropped intensifier")
        if (set(added) - set(gone)) & INTENSIFIERS:
            tags.append("added intensifier")
        edits.append({"type": kind, "ai_text": gone_text, "human_text": added_text,
                      "other_claude_text": " ".join(swords[b] for b in other_ai),
                      "ai_words": len(gone), "human_words": len(added),
                      "edited_by": ", ".join(f"{k} {v}" for k, v in human_by.items()),
                      "removal": status, "tags": "; ".join(tags)})
    return {"reply": reply, "paste": paste, "edits": edits}


def units(text, kind):
    """Paragraphs of prose between \\begin{abstract} and \\appendix, plus each caption and each float's other
    text (table cells, figure and algorithm text: kind "table")."""
    start, end = text.find("\\begin{abstract}"), text.find("\\appendix")
    out = []
    for m in re.finditer(r"(?s)(?:[^\n]|\n(?!\s*\n))+", text):
        # a paragraph match can start at the newline before \begin{abstract}, so compare its end
        if m.end() <= start or m.start() >= end:
            continue
        for k in ("prose", "caption", "table"):
            chars = [i for i in range(max(m.start(), start), m.end()) if kind[i] == ("float" if k == "table" else k)]
            if not chars:
                continue
            # split a paragraph that holds several captions into one unit per caption
            run = [chars[0]]
            for i in chars[1:]:
                if i != run[-1] + 1 and k != "prose":
                    out.append((k, run[0], run[-1] + 1))
                    run = []
                run.append(i)
            out.append((k, run[0], run[-1] + 1))
    return out


def main():
    S = settings.parse_args()
    events = mp.build_events()
    text, toks, labels = mp.label_file(S.main_file, mp.make_origin_cache(events, K), events, K)
    kind = mp.regions(text)
    secs = mp.section_index(text)
    msgs = {m["message_id"]: m for m in mp.load_jsonl("chat_messages.jsonl")}
    ins_events, del_events = overleaf_index()
    replies = rt.reply_sentences(mp.load_jsonl("chat_messages.jsonl"))
    reply_times = [r[0] for r in replies]

    def section_at(pos):
        prior = [name for p, name in secs if p <= pos]
        return prior[-1] if prior else ""

    sent_rows, edit_rows, unit_rows = [], [], []
    for u_kind, u_start, u_end in units(text, kind):
        idx = [i for i, (_, s, _) in enumerate(toks) if u_start <= s < u_end and kind[s] == ("float" if u_kind == "table" else u_kind)]
        if not idx:
            continue
        words = [toks[i][0] for i in idx]
        labs = [labels[i] for i in idx]
        # per-token category; removals keyed by the unit token index they precede
        cat = [{"cat": "ai" if l[0].startswith("ai:") else "human", "who": who(l[0]), "label": l[0],
                "conversation": l[1].get("conversation", ""), "ts": l[1].get("ts"),
                "first_seen": l[1].get("conversation") or (f"Overleaf v{l[1]['toV']}" if "toV" in l[1] else ""),
                "event": event_id(l[1]),
                "ngram": " ".join(w for w, _, _ in toks[l[1]["ngram_start"]:l[1]["ngram_start"] + K])
                if "ngram_start" in l[1] else ""}
               for l in labs]
        removals, unit_sources = {}, Counter()

        unit_text = text[u_start:u_end]
        bounds = [u_start] + ([u_start + m.end() for m in SENT_SPLIT.finditer(unit_text)] if u_kind != "table" else []) + [u_end]
        for s_start, s_end in zip(bounds, bounds[1:]):
            js = [j for j, i in enumerate(idx) if s_start <= toks[i][1] < s_end]
            if not js:
                continue
            # words Weili put in herself that follow an earlier Claude sentence count as retyped Claude text
            mine = [j for j in js if cat[j]["cat"] == "human" and cat[j]["label"].startswith(f"human:{me()}")]
            match = None
            if len(mine) >= 4:
                # the reply must predate most of her words (median time), so a Claude polish of her
                # own earlier draft is never taken as its source
                times = sorted(cat[j]["ts"] or 0 for j in mine)
                match = rt.best_match([words[j] for j in js], times[len(times) // 2], replies, reply_times)
            if match:
                # content words found in the Claude sentence, plus function words, which carry no authorship;
                # Weili's other content words stay hers (her changes to the Claude sentence)
                reply_ts = mp.to_ts(match["message"]["created_at"])
                for j in mine:
                    if (cat[j]["ts"] or 0) > reply_ts and (words[j] in rt.STOP or rt.stem(words[j]) in match["reply_set"]):
                        cat[j].update(cat="retyped", conversation=match["message"]["title"],
                                      ai_event=event_id(dict(match["message"], conversation_id=match["message"]["conversation_id"])))

            sent_edits = align_sentence(js, words, cat, labs, msgs, ins_events, del_events, removals,
                                        lambda j1, j2: text[toks[idx[j1]][1]:toks[idx[j2]][2]])
            reply = sent_edits["reply"]
            if reply:
                unit_sources[reply["title"]] += len(js)
            for row in sent_edits["edits"]:
                edit_rows.append({"section": section_at(s_start), "sentence_start": s_start, **row,
                                  "conversation": reply["title"], "reply_at": reply["created_at"],
                                  "pasted_at": sent_edits["paste"]["at"] if sent_edits["paste"] else ""})

            ai = sum(cat[j]["cat"] in ("kept", "ai") for j in js)
            retyped = sum(cat[j]["cat"] == "retyped" for j in js)
            prompt = sum(lb.own(cat[j], S) for j in js)
            humans = Counter(cat[j]["who"] for j in js if cat[j]["cat"] == "human")
            removed = sum(r["words"] for j in js for r in removals.get(j, []) if r["status"] != UNUSED)
            share = ai / len(js)
            group, cls, _ = lb.classify([cat[j] for j in js], removed, S)
            sent_rows.append({
                "section": section_at(s_start), "unit": u_kind, "start": s_start, "group": group, "class": cls,
                "words": len(js), "ai_share": round(share, 3), "retyped_share": round(retyped / len(js), 3),
                "your_prompt_share": round(prompt / len(js), 3),
                "ai_words_removed": removed,
                "human_writers": ", ".join(f"{k} {v}" for k, v in humans.most_common()),
                "source_chat": reply["title"] if reply else "",
                "reply_at": reply["created_at"] if reply else "",
                "pasted": f"{sent_edits['paste']['by']} {sent_edits['paste']['at'][:16]}" if sent_edits["paste"] else "",
                "text": re.sub(r"\s+", " ", text[s_start:s_end]).strip(),
                "retyped_from_chat": match["message"]["title"] if match else "",
                "retyped_from_sentence": match["reply_text"] if match else "",
                "retyped_overlap": f"{match['containment']} words, {match['order']} order" if match else "",
            })

        unit_rows.append({"section": section_at(u_start), "kind": u_kind, "start": u_start, "end": u_end,
                          "sources": [t for t, _ in unit_sources.most_common()],
                          "classes": [r["class"] for r in sent_rows if u_start <= r["start"] < u_end],
                          "sentence_starts": [r["start"] for r in sent_rows if u_start <= r["start"] < u_end],
                          "tokens": [{"s": toks[i][1], "e": toks[i][2], **cat[j], "removed_before": removals.get(j, [])}
                                     for j, i in enumerate(idx)],
                          "removed_after_end": removals.get(len(idx), [])})

    out = S.analysis
    with open(os.path.join(out, "paper_sentences.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(sent_rows[0].keys()))
        w.writeheader()
        w.writerows(sent_rows)
    with open(os.path.join(out, "paper_edits.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(edit_rows[0].keys()))
        w.writeheader()
        w.writerows(edit_rows)
    with open(os.path.join(out, "paper_units.jsonl"), "w") as f:
        for r in unit_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    words_by = Counter()
    for r in sent_rows:
        words_by[r["class"]] += r["words"]
    n = sum(words_by.values())
    print(f"units={len(unit_rows)} sentences={len(sent_rows)} edits={len(edit_rows)} words={n}")
    for c, v in words_by.most_common():
        print(f"  {c:32} {v:5} words {v / n:.1%}  ({sum(r['class'] == c for r in sent_rows)} sentences)")
    print("edit types:", Counter(e["type"] for e in edit_rows))
    print("removal:", Counter(e["removal"] for e in edit_rows if e["ai_words"]))

if __name__ == "__main__":
    main()
