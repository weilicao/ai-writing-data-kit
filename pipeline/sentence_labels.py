"""Sentence labels (scripts/labels.py: sentence_label), after first_draft.py.

For every sentence of the main paper:
  1. its word statistics (levels.py) give the share of AI words (as written or retyped);
  2. its AI source is the span of 1-2 consecutive sentences of an AI output (chat reply or agent-session event)
     that supplied a word of it, or that is its first draft, with the smallest word-level edit distance; the edit
     operations against it (kept, replaced, added, deleted) are counted (labels.edit_ops);
  3. its first draft at labels.DIRECTION_THRESHOLD (first_drafts.csv) says who drafted it;
  4. labels.sentence_label gives AI / AI, human-edited / Human, AI-edited / Human / Unclear (or Template, Excluded).
Writes to analysis/:
  level_sentences.csv          the sentence rows of levels.py plus group, label, writer, first draft and edit counts
  level_summary.csv            per level (word, sentence) and block: units and words per label
  first_draft_transitions.csv  first-draft group x final sentence label, per threshold, overall, block and section
  first_draft_summary.md       the same as tables
  label_report.html            section by section, paragraph by paragraph, every sentence with its label and its
                               words colored by word label
"""
import csv
import html
import json
import os
import re
import sys
from collections import Counter, OrderedDict, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import labels as lb  # noqa: E402
import levels  # noqa: E402
import match_provenance as mp  # noqa: E402
import settings  # noqa: E402

SPLIT = re.compile(r"(?<=[.!?:;])\s+|\n+")
MIN_OVERLAP = 0.3  # a candidate AI span shares at least this share of the sentence's distinct words
FD_GROUPS = ["AI", "Human", "Unclear", "Template", "Excluded"]
ADDED = ("group", "label", "writer", "first_draft_group", "ai_source", "distance", "kept", "replaced", "added",
         "deleted", "longest_added")  # columns this script adds to level_sentences.csv (dropped when re-run)
THRESHOLDS = ["T1", "T2", "T3"]


def words_of(t):
    return [w for w, _, _ in mp.tokenize(t)]


def main():
    S = settings.parse_args()
    A = S.analysis
    text = open(os.path.join(S.parsed, "final", S.main_file)).read()
    units = [json.loads(l) for l in open(os.path.join(A, "paper_units.jsonl"))]
    rows = OrderedDict((r["id"], OrderedDict((k, v) for k, v in r.items() if k not in ADDED))
                       for r in csv.DictReader(open(os.path.join(A, "level_sentences.csv"))))
    fds = defaultdict(dict)
    for r in csv.DictReader(open(os.path.join(A, "first_drafts.csv"))):
        fds[r["sentence"]][r["threshold"]] = r

    # AI outputs by raw event id, and shared event names back to raw ids (first_drafts.csv holds shared names)
    ai_text, raw_of = {}, {}
    for l in open(os.path.join(S.parsed, "chat_messages.jsonl"), encoding="utf-8"):
        m = json.loads(l)
        ev = f"{m['source']}:{m['conversation_id']}:{m['message_id']}"
        raw_of[settings.event_name(ev)] = ev
        if m["role"] == "assistant":
            ai_text[ev] = m["text"]
    spans = {}

    def reply_spans(ev):
        if ev not in spans:
            ss = [x for x in (words_of(p) for p in SPLIT.split(ai_text[ev])) if x]
            spans[ev] = ss + [ss[i] + ss[i + 1] for i in range(len(ss) - 1)]
        return spans[ev]

    out, toks_of, para_of = [], {}, {}
    for sid, pid, blk, section, toks, last in levels.sentence_tokens(units):
        r = rows[sid]
        after = last["removed_after_end"] if last else ()
        _, _, st = lb.classify(toks, levels.removed_in(toks, after), S)
        fd = fds.get(sid, {}).get(lb.DIRECTION_THRESHOLD, {})
        cands = {t.get("ai_event") or t["event"] for t in toks if lb.word_label(t, S)[0] == "AI"}
        if fd.get("first_draft_group") == "AI":
            cands.add(raw_of.get(fd.get("first_draft_event", ""), ""))
        b = words_of(r["text"])
        best, bs = None, set(b)
        for ev in sorted(c for c in cands if c in ai_text):
            for a in reply_spans(ev):
                if len(bs & set(a)) < MIN_OVERLAP * len(bs):
                    continue
                e = lb.edit_ops(a, b)
                if best is None or e["distance"] < best[0]["distance"]:
                    best = (e, ev)
        edit, src = best if best else (None, "")
        label = lb.sentence_label(st, edit, fd.get("first_draft_group"))
        human = Counter(lb.person(c) for t in toks for g, c in [lb.word_label(t, S)] if g == "Human")
        row = OrderedDict(list(r.items())[:4])
        row.update(group=lb.SENTENCE_GROUP[label], label=label,
                   writer=human.most_common(1)[0][0] if human and label in ("Human", "Human, AI-edited") else "")
        row.update(list(r.items())[4:-1])
        row.update(first_draft_group=fd.get("first_draft_group", ""), ai_source=settings.event_name(src))
        row.update({k: (edit or {}).get(k, "") for k in ("distance", "kept", "replaced", "added", "deleted",
                                                          "longest_added")})
        row["text"] = r["text"]
        out.append(row)
        toks_of[sid], para_of[sid] = toks, pid
    levels.write_csv(os.path.join(A, "level_sentences.csv"), out)

    # level_summary.csv: word and sentence level, per block; shares leave out Excluded words
    words = list(csv.DictReader(open(os.path.join(A, "level_words.csv"))))
    summary = []
    for level, rs in (("word", [dict(block=w["block"], group=w["group"], label=w["label"], words=1) for w in words]),
                      ("sentence", [dict(block=x["block"], group=x["group"], label=x["label"], words=int(x["words"]))
                                    for x in out])):
        for blk in ("Body", "Captions and Tables", "All"):
            sel = [x for x in rs if blk == "All" or x["block"] == blk]
            if not sel:
                continue
            n = sum(x["words"] for x in sel if x["group"] != "Excluded") or 1
            c, k = Counter(), Counter()
            for x in sel:
                c[(x["group"], x["label"])] += x["words"]
                k[(x["group"], x["label"])] += 1
            for g in lb.GROUPS:
                gw = sum(v for (gg, _), v in c.items() if gg == g)
                if not gw:
                    continue
                for (gg, lab), v in sorted(c.items(), key=lambda x: (-x[1], x[0])):
                    if gg == g:
                        summary.append(dict(level=level, block=blk, group=g, label=lab, units=k[(gg, lab)], words=v,
                                            share="" if g == "Excluded" else round(v / n, 3)))
                summary.append(dict(level=level, block=blk, group=g, label="(group total)",
                                    units=sum(v for (gg, _), v in k.items() if gg == g), words=gw,
                                    share="" if g == "Excluded" else round(gw / n, 3)))
    levels.write_csv(os.path.join(A, "level_summary.csv"), summary)
    for x in summary:
        if x["level"] == "sentence" and x["block"] == "Body" and x["label"] != "(group total)":
            print(f"sentence Body {x['label']:18} units={x['units']:4} words={x['words']:5} {x['share']}")

    transitions(S, A, out, fds)
    render(S, text, units, out, toks_of, para_of, os.path.join(A, "label_report.html"))


def transitions(S, A, sents, fds):
    """First-draft group x final sentence label, weighted by counted words."""
    trans = []
    for level in THRESHOLDS:
        sel = [(s, fds[s["id"]][level]) for s in sents if level in fds.get(s["id"], {})]
        scopes = [("All", "", sel)] + [("Block", b, [x for x in sel if x[0]["block"] == b])
                                       for b in ("Body", "Captions and Tables")] + \
                 [("Section", sec, [x for x in sel if x[0]["section"] == sec and x[0]["block"] == "Body"])
                  for sec in OrderedDict.fromkeys(x[0]["section"] for x in sel if x[0]["block"] == "Body")]
        for scope, name, rs in scopes:
            n = sum(int(s["counted"]) for s, _ in rs) or 1
            c, k = Counter(), Counter()
            for s, f in rs:
                c[(f["first_draft_group"], s["label"])] += int(s["counted"])
                k[(f["first_draft_group"], s["label"])] += 1
            for fg in FD_GROUPS:
                for fin in lb.SENTENCE_LABELS:
                    if k[(fg, fin)]:
                        trans.append(OrderedDict(threshold=level, scope=scope, name=name, first_draft=fg, final=fin,
                                                 sentences=k[(fg, fin)], words=c[(fg, fin)],
                                                 share=round(c[(fg, fin)] / n, 3)))
    levels.write_csv(os.path.join(A, "first_draft_transitions.csv"), trans)
    finals = lb.SENTENCE_LABELS[:5]
    md = [f"# First drafts: {S.title}", "",
          "Share of counted words, by the group of each sentence's first draft (rows) and its final sentence label "
          "(columns). T1: 3-gram containment >= 0.8; T2: >= 0.5; T3: T2 or the retyped rule. The final label uses the "
          f"first draft at {lb.DIRECTION_THRESHOLD} (who drafted the sentence). Generated by scripts/sentence_labels.py.", ""]
    for name in ("Body", "Captions and Tables"):
        for level in THRESHOLDS:
            sel = [x for x in trans if x["threshold"] == level and x["scope"] == "Block" and x["name"] == name]
            if not sel:
                continue
            md += [f"## {name}, {level}", "", "| first draft \\ final | " + " | ".join(finals) + " | all |",
                   "|---|" + "---|" * (len(finals) + 1)]
            for fg in FD_GROUPS:
                tot = sum(x["share"] for x in sel if x["first_draft"] == fg)
                if tot:
                    md.append(f"| {fg} | " + " | ".join(
                        f"{v:.1%}" if v else "" for v in
                        (sum(x["share"] for x in sel if x["first_draft"] == fg and x["final"] == f) for f in finals))
                        + f" | {tot:.1%} |")
            md.append("")
    md += ["## Body, by section", "",
           "| section | " + " | ".join(f"{lv} AI first draft | {lv} AI first, now Human or Unclear" for lv in THRESHOLDS)
           + " |", "|---|" + "---|" * (2 * len(THRESHOLDS))]
    for sec in OrderedDict.fromkeys(x["name"] for x in trans if x["scope"] == "Section"):
        cells = []
        for lv in THRESHOLDS:
            sel = [x for x in trans if x["threshold"] == lv and x["scope"] == "Section" and x["name"] == sec]
            cells.append(f"{sum(x['share'] for x in sel if x['first_draft'] == 'AI'):.0%}")
            cells.append(f"{sum(x['share'] for x in sel if x['first_draft'] == 'AI' and x['final'] in ('Human', 'Unclear')):.0%}")
        md.append(f"| {sec} | " + " | ".join(cells) + " |")
    open(os.path.join(A, "first_draft_summary.md"), "w").write("\n".join(md) + "\n")


# ---------------------------------------------------------------- report

CSS = {"AI": "s-ai", "AI, human-edited": "s-aie", "Human, AI-edited": "s-hue", "Human": "s-hu", "Unclear": "s-un",
       "Template": "s-tp", "Excluded": "s-tp"}


def word_class(t, S):
    g, cat = lb.word_label(t, S)
    if g == "AI":
        return "w-rt" if t["cat"] == "retyped" else ("w-ai" if t["cat"] == "kept" else "w-ai2")
    if g == "Human":
        return "w-me" if cat.startswith(S.me_short + ",") else "w-co"
    return {"Unclear": "w-un", "Template": "w-tp", "Excluded": "w-ex"}[g]


def shares(rows):
    n = sum(int(r["words"]) for r in rows if r["label"] != "Excluded") or 1
    c = Counter()
    for r in rows:
        c[r["label"]] += int(r["words"])
    return n, c


def bar(rows):
    n, c = shares(rows)
    lab = [x for x in lb.SENTENCE_LABELS[:5] if c[x]]
    return ('<div class="bar">' + "".join(f'<span class="{CSS[x]}" style="width:{c[x] / n * 100:.1f}%" '
                                         f'title="{x} {c[x] / n:.0%}"></span>' for x in lab) + '</div><div class="mix">'
            + " · ".join(f"{x} {c[x] / n:.0%}" for x in lab) + f" of {n} words (sentence labels)</div>")


def render(S, text, units, sents, toks_of, para_of, dest):
    by_para = defaultdict(list)
    for s in sents:
        by_para[para_of[s["id"]]].append(s)
    body_secs = OrderedDict()
    for s in sents:
        if s["block"] == "Body":
            body_secs.setdefault(s["section"], []).append(s)
    out, floats, last = [], [], None
    for pi, u in enumerate(units, 1):
        pid = f"P{pi:03d}"
        if u["kind"] == "prose" and u["section"] != last:
            out.append(f'<h2>{html.escape(u["section"])}</h2>{bar(body_secs.get(u["section"], []))}')
            last = u["section"]
        lines = []
        for s in by_para.get(pid, []):
            seg, toks = [], toks_of[s["id"]]
            pos = toks[0]["s"]
            for t in toks:
                g, cat = lb.word_label(t, S)
                tip = f"{g}: {cat}" + (f" | {settings.event_name(t['event'])}" if t["event"] else "") + \
                      (f" | 6-gram: {t['ngram']}" if t["ngram"] else "")
                seg.append(html.escape(text[pos:t["s"]]))
                seg.append(f'<span class="{word_class(t, S)}" title="{html.escape(tip)}">'
                           f'{html.escape(text[t["s"]:t["e"]])}</span>')
                pos = t["e"]
            ed = (f'replaced {s["replaced"]}, added {s["added"]}, deleted {s["deleted"]} words of its AI source '
                  f'(distance {s["distance"]})') if s["distance"] != "" else "no AI source text"
            who = f' ({html.escape(s["writer"])})' if s["writer"] else ""
            lines.append(f'<div class="sent"><span class="pill {CSS[s["label"]]}" title="{html.escape(ed)}">'
                         f'{html.escape(s["label"])}{who}</span> <span class="sid">{s["id"]}</span>'
                         f'<div class="txt">{"".join(seg)}</div></div>')
        kind = {"prose": "Paragraph", "caption": "Caption", "table": "Table / figure text"}[u["kind"]]
        card = f'<div class="unit"><div class="head"><b>{pid}</b> {kind}</div>{"".join(lines)}</div>'
        (out if u["kind"] == "prose" else floats).append(card if u["kind"] == "prose"
                                                         else f'<div class="in">in {html.escape(u["section"])}</div>{card}')
    if floats:
        out.append(f'<h2>Captions and Tables</h2>{bar([s for s in sents if s["block"] != "Body"])}' + "".join(floats))
    overview = "".join(
        f'<tr><td>{html.escape(sec)}</td><td class=num>{shares(rs)[0]}</td>'
        + "".join(f'<td class=num>{shares(rs)[1][x] / shares(rs)[0]:.0%}</td>' for x in lb.SENTENCE_LABELS[:5]) + "</tr>"
        for sec, rs in list(body_secs.items()) + [("Body (all sections)", [s for s in sents if s["block"] == "Body"]),
                                                  ("Captions and Tables", [s for s in sents if s["block"] != "Body"])]
        if rs)
    legend = "".join(f'<li><span class="sw {c}"></span>{t}</li>' for c, t in [
        ("w-ai", "AI-generated, as written in its source"), ("w-ai2", "AI-generated, from another output"),
        ("w-rt", "AI, retyped"), ("w-me", f"{S.me_short}, typed or in a prompt"), ("w-co", "co-authors, typed"),
        ("w-un", "Unclear (paste or commit with no source)"), ("w-tp", "Template"), ("w-ex", "Excluded")])
    head = "".join(f"<th class=num>{x}</th>" for x in lb.SENTENCE_LABELS[:5])
    page = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Labels by Section</title>
<style>
:root{{--bg:#fff;--fg:#1d1d1f;--muted:#6e6e73;--line:#e5e5ea;--card:#fafafa;
--ai:#f6b25e;--ai2:#fbd9a8;--hu:#8fb8f0;--co:#c3b2f2;--un:#efe07a;--tp:#dcdcdc;--aie:#f0a3a3;--hue:#a8dcc0}}
@media (prefers-color-scheme:dark){{:root{{--bg:#161618;--fg:#ececf0;--muted:#9a9aa2;--line:#2e2e33;--card:#1d1d20;
--ai:#9a5f1c;--ai2:#5e4426;--hu:#2f5d93;--co:#4e3f8a;--un:#6e6420;--tp:#3a3a3e;--aie:#8a3b3b;--hue:#2f6b4c}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,system-ui,sans-serif;margin:0 auto;max-width:1000px;padding:24px 16px}}
h1{{font-size:24px;margin:0 0 4px}} h2{{font-size:19px;margin:30px 0 6px}}
p.sub,.mix,.in,.sid{{color:var(--muted);font-size:13px}}
table{{border-collapse:collapse;width:100%;font-size:14px}} td,th{{border-bottom:1px solid var(--line);padding:4px 8px;text-align:left}}
td.num,th.num{{text-align:right;font-variant-numeric:tabular-nums}}
.bar{{display:flex;height:10px;border-radius:5px;overflow:hidden;background:var(--line);margin:6px 0 2px;max-width:420px}}
.unit{{border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin:10px 0;background:var(--card)}}
.sent{{margin:8px 0}}
.txt{{margin:2px 0 0;white-space:pre-wrap;word-break:break-word;font:13px/1.7 ui-monospace,Menlo,monospace}}
.pill{{display:inline-block;font:600 11px/1.7 -apple-system,system-ui,sans-serif;border-radius:9px;padding:0 8px}}
.s-ai{{background:var(--ai)}} .s-aie{{background:var(--aie)}} .s-hue{{background:var(--hue)}} .s-hu{{background:var(--hu)}}
.s-un{{background:var(--un)}} .s-tp{{background:var(--tp)}}
.w-ai{{background:var(--ai)}} .w-ai2{{background:var(--ai2)}} .w-rt{{background:var(--ai2);box-shadow:inset 0 -2px 0 var(--ai)}}
.w-me{{background:var(--hu)}} .w-co{{background:var(--co)}} .w-tp{{background:var(--tp)}} .w-ex{{opacity:.45}}
.w-un{{background:repeating-linear-gradient(135deg,var(--un) 0 4px,var(--card) 4px 8px)}}
ul.legend{{list-style:none;padding:0;display:flex;flex-wrap:wrap;gap:6px 18px;font-size:14px}}
.sw{{display:inline-block;width:14px;height:14px;border-radius:3px;vertical-align:-2px;margin-right:6px}}
</style></head><body>
<h1>Labels by Section</h1>
<p class="sub">"{html.escape(S.title)}", {html.escape(S.main_file)}. Every sentence has one label: AI (an AI
sentence kept as written or only cut), AI, human-edited (an AI first draft with words replaced or added), Human,
AI-edited (a person's first draft with at least 15% AI words), Human or Unclear (under 15% AI words). Hover a label
for its edit counts against the AI source, and a word for its own label, source event and deciding 6-gram. Section
rows give the share of words in sentences of each label.</p>
<table><tr><th>Section</th><th class=num>Words</th>{head}</tr>{overview}</table>
<ul class="legend">{legend}</ul>
{"".join(out)}
</body></html>"""
    open(dest, "w").write(page)
    print(dest)


if __name__ == "__main__":
    main()
