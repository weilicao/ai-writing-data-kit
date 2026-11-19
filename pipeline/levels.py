"""Labels at every level of the main paper, built from the word labels (scripts/labels.py).

Reads analysis/paper_units.jsonl (explain_edits.py). Writes to analysis/:
  level_ngrams.csv      each 6-gram that decided at least one word: its text and the event it first appeared in
  level_words.csv       every word: group, category, flags, and its source event (message / session event /
                        Overleaf version / commit / template)
  level_sentences.csv   every sentence: label, share per group and category, edit counts, AI sources
  level_paragraphs.csv  every paragraph (prose) and every caption or float (Captions and Tables)
  level_sections.csv    every section of the body, the Captions and Tables block, and the whole paper
  level_summary.csv     per level and block: units and words per label
  label_report.html     section by section and paragraph by paragraph, text colored by label
Blocks: Body (prose) and Captions and Tables (captions, table cells, figure and algorithm text).
"""
import csv
import html
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
    g, cat, st = lb.classify(tokens, removed, S)
    n = st["counted"] or 1
    cats = Counter(lb.word_label(t, S)[1] for t in tokens if lb.word_label(t, S)[0] in COUNTED)
    row = OrderedDict(id=uid, level=level, block=blk, section=section, group=g, label=cat, words=st["words"],
                      counted=st["counted"])
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

    words, sents, paras, ngrams = [], [], [], OrderedDict()
    by_section = OrderedDict()
    for pi, u in enumerate(units, 1):
        pid, blk = f"P{pi:03d}", block(u["kind"])
        starts = u["sentence_starts"] + [u["end"] + 1]
        for si, (a, b) in enumerate(zip(starts, starts[1:]), 1):
            toks = [t for t in u["tokens"] if a <= t["s"] < b]
            if not toks:
                continue
            sid = f"{pid}.S{si:02d}"
            after = u["removed_after_end"] if b > u["end"] else ()
            sents.append(unit_row(sid, "sentence", blk, u["section"], toks, removed_in(toks, after), S,
                                  flat(text[toks[0]["s"]:toks[-1]["e"]])))
            for t in toks:
                g, cat = lb.word_label(t, S)
                words.append(OrderedDict(
                    id=f"{sid}.W{len(words) + 1:05d}", block=blk, section=u["section"], paragraph=pid, sentence=sid,
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
        removed = removed_in(u["tokens"], u["removed_after_end"])
        paras.append(unit_row(pid, "paragraph" if blk == "Body" else u["kind"], blk, u["section"], u["tokens"],
                              removed, S, flat(text[u["start"]:u["end"]])))
        key = u["section"] if blk == "Body" else "Captions and Tables"
        by_section.setdefault(key, ([], [0]))
        by_section[key][0].extend(u["tokens"])
        by_section[key][1][0] += removed

    secs = [unit_row(f"T{i:02d}", "section" if name != "Captions and Tables" else "block",
                     "Body" if name != "Captions and Tables" else name, name, toks, rem[0], S)
            for i, (name, (toks, rem)) in enumerate(by_section.items(), 1)]
    body = [t for u in units if u["kind"] == "prose" for t in u["tokens"]]
    allt = [t for u in units for t in u["tokens"]]
    secs.append(unit_row("T_body", "block", "Body", "Body (all sections)", body,
                         sum(removed_in(u["tokens"], u["removed_after_end"]) for u in units if u["kind"] == "prose"), S))
    secs.append(unit_row("T_all", "paper", "All", "Whole paper", allt,
                         sum(removed_in(u["tokens"], u["removed_after_end"]) for u in units), S))

    write_csv(os.path.join(A, "level_ngrams.csv"), list(ngrams.values()))
    write_csv(os.path.join(A, "level_words.csv"), words)
    write_csv(os.path.join(A, "level_sentences.csv"), sents)
    write_csv(os.path.join(A, "level_paragraphs.csv"), paras)
    write_csv(os.path.join(A, "level_sections.csv"), secs)

    # per level and block: units and words per label (word-weighted shares leave out Excluded words only)
    summary = []
    levels = [("word", [dict(block=w["block"], group=w["group"], label=w["label"], words=1) for w in words]),
              ("sentence", sents), ("paragraph", paras),
              ("section", [s for s in secs if s["level"] == "section"])]
    for level, rows in levels:
        for b in ("Body", "Captions and Tables", "All"):
            sel = [r for r in rows if b == "All" or r["block"] == b]
            if not sel:
                continue
            n = sum(r["words"] for r in sel if r["group"] != "Excluded")
            c, k = Counter(), Counter()
            for r in sel:
                c[(r["group"], r["label"])] += r["words"]
                k[(r["group"], r["label"])] += 1
            for g in lb.GROUPS:
                gw = sum(v for (gg, _), v in c.items() if gg == g)
                if not gw:
                    continue
                for (gg, lab), v in sorted(c.items(), key=lambda x: -x[1]):
                    if gg == g:
                        summary.append(dict(level=level, block=b, group=g, label=lab, units=k[(gg, lab)], words=v,
                                            share="" if g == "Excluded" else round(v / n, 3)))
                summary.append(dict(level=level, block=b, group=g, label="(group total)",
                                    units=sum(v for (gg, _), v in k.items() if gg == g), words=gw,
                                    share="" if g == "Excluded" else round(gw / n, 3)))
    write_csv(os.path.join(A, "level_summary.csv"), summary)
    for r in summary:
        if r["label"] == "(group total)" and r["block"] != "Captions and Tables":
            print(f"{r['level']:9} {r['block']:5} {r['group']:8} units={r['units']:4} words={r['words']:5} {r['share']}")

    render(S, text, units, paras, secs, os.path.join(A, "label_report.html"))


# ---------------------------------------------------------------- report

CSS_GROUP = {"AI": "g-ai", "Mixed": "g-mix", "Human": "g-hu", "Unclear": "g-un", "Template": "g-tp", "Excluded": "g-ex"}


def word_class(t, S):
    g, cat = lb.word_label(t, S)
    if g == "AI":
        return "w-rt" if t["cat"] == "retyped" else ("w-ai" if t["cat"] == "kept" else "w-ai2")
    if g == "Human":
        return "w-me" if cat.startswith(S.me_short + ",") else "w-co"
    return {"Unclear": "w-un", "Template": "w-tp", "Excluded": "w-ex"}[g]


def bar(row):
    n = row["counted"] or 1
    parts = [(g, row[f"share_{g.lower()}"]) for g in COUNTED]
    return '<div class="bar">' + "".join(
        f'<span class="{CSS_GROUP[g]}" style="width:{v * 100:.1f}%" title="{g} {v:.0%}"></span>' for g, v in parts if v) + \
        '</div><div class="mix">' + " · ".join("%s %.0f%%" % (g, row["share_" + g.lower()] * 100) for g in COUNTED) + \
        f' of {n} words</div>' \
        if row["counted"] else '<div class="mix">no counted words</div>'


def pill(row):
    return f'<span class="pill {CSS_GROUP[row["group"]]}">{html.escape(row["group"])}: {html.escape(row["label"])}</span>'


def render(S, text, units, paras, secs, dest):
    sec_row = {s["section"]: s for s in secs}
    out, last = [], None
    for u, p in zip(units, paras):
        key = u["section"] if u["kind"] == "prose" else "Captions and Tables"
        if key != last and u["kind"] == "prose":
            s = sec_row[key]
            out.append(f'<h2>{html.escape(key)}</h2>{pill(s)}{bar(s)}<p class="cats">{html.escape(s["categories"])}</p>')
            last = key
        seg, pos = [], u["start"]
        for t in u["tokens"]:
            g, cat = lb.word_label(t, S)
            conv = settings.conv_name(t.get("ai_event") or t["event"], t["conversation"])
            tip = f"{g}: {cat}" + (f" | {conv}" if conv else "") + \
                  (f" | {settings.event_name(t['event'])}" if t["event"] else "") + \
                  (f" | 6-gram: {t['ngram']}" if t["ngram"] else "")
            seg.append(html.escape(text[pos:t["s"]]))
            seg.append(f'<span class="{word_class(t, S)}" title="{html.escape(tip)}">{html.escape(text[t["s"]:t["e"]])}</span>')
            pos = t["e"]
        seg.append(html.escape(text[pos:u["end"]]))
        kind = {"prose": "Paragraph", "caption": "Caption", "table": "Table / figure text"}[u["kind"]]
        block_html = (f'<div class="unit"><div class="head"><b>{p["id"]}</b> {kind} {pill(p)}</div>{bar(p)}'
                      f'<p class="txt">{"".join(seg)}</p>'
                      + (f'<div class="src">AI sources: {html.escape(p["ai_sources"])}</div>' if p["ai_sources"] else "")
                      + "</div>")
        if u["kind"] == "prose":
            out.append(block_html)
        else:
            sec_row.setdefault("_floats", []).append((u["section"], block_html))
    floats = sec_row.get("_floats", [])
    if floats:
        s = sec_row["Captions and Tables"]
        out.append(f'<h2>Captions and Tables</h2>{pill(s)}{bar(s)}<p class="cats">{html.escape(s["categories"])}</p>')
        for sec, b in floats:
            out.append(f'<div class="in">in {html.escape(sec)}</div>{b}')
    overview = "".join(
        f'<tr><td>{html.escape(s["section"])}</td><td>{pill(s)}</td><td class=num>{s["counted"]}</td>'
        + "".join(f'<td class=num>{s[f"share_{g.lower()}"]:.0%}</td>' for g in COUNTED) + "</tr>"
        for s in secs)
    legend = "".join(f'<li><span class="sw {c}"></span>{t}</li>' for c, t in [
        ("w-ai", "AI-generated, as written in its source"), ("w-ai2", "AI-generated, from another output"),
        ("w-rt", "AI, retyped"), ("w-me", f"{S.me_short}, typed or in a prompt"), ("w-co", "co-authors, typed"),
        ("w-un", "Unclear (paste or commit with no source)"), ("w-tp", "Template"), ("w-ex", "Excluded")])
    page = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Labels by Section</title>
<style>
:root{{--bg:#fff;--fg:#1d1d1f;--muted:#6e6e73;--line:#e5e5ea;--card:#fafafa;
--ai:#f6b25e;--ai2:#fbd9a8;--hu:#8fb8f0;--co:#c3b2f2;--un:#efe07a;--tp:#dcdcdc;--mix:#b9b9f0}}
@media (prefers-color-scheme:dark){{:root{{--bg:#161618;--fg:#ececf0;--muted:#9a9aa2;--line:#2e2e33;--card:#1d1d20;
--ai:#9a5f1c;--ai2:#5e4426;--hu:#2f5d93;--co:#4e3f8a;--un:#6e6420;--tp:#3a3a3e;--mix:#4b4b86}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,system-ui,sans-serif;margin:0 auto;max-width:1000px;padding:24px 16px}}
h1{{font-size:24px;margin:0 0 4px}} h2{{font-size:19px;margin:30px 0 6px}}
p.sub,.mix,.src,.in,.cats{{color:var(--muted);font-size:13px}} .cats{{margin:2px 0 8px}}
table{{border-collapse:collapse;width:100%;font-size:14px}} td,th{{border-bottom:1px solid var(--line);padding:4px 8px;text-align:left}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}
.bar{{display:flex;height:10px;border-radius:5px;overflow:hidden;background:var(--line);margin:6px 0 2px;max-width:420px}}
.unit{{border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin:10px 0;background:var(--card)}}
.txt{{margin:6px 0 0;white-space:pre-wrap;word-break:break-word;font:13px/1.7 ui-monospace,Menlo,monospace}}
.pill{{display:inline-block;font:600 11px/1.7 -apple-system,system-ui,sans-serif;border-radius:9px;padding:0 8px;margin-left:4px}}
.g-ai{{background:var(--ai)}} .g-mix{{background:var(--mix)}} .g-hu{{background:var(--hu)}} .g-un{{background:var(--un)}}
.g-tp,.g-ex{{background:var(--tp)}}
.w-ai{{background:var(--ai)}} .w-ai2{{background:var(--ai2)}} .w-rt{{background:var(--ai2);box-shadow:inset 0 -2px 0 var(--ai)}}
.w-me{{background:var(--hu)}} .w-co{{background:var(--co)}} .w-tp{{background:var(--tp)}} .w-ex{{opacity:.45}}
.w-un{{background:repeating-linear-gradient(135deg,var(--un) 0 4px,var(--card) 4px 8px)}}
ul.legend{{list-style:none;padding:0;display:flex;flex-wrap:wrap;gap:6px 18px;font-size:14px}}
.sw{{display:inline-block;width:14px;height:14px;border-radius:3px;vertical-align:-2px;margin-right:6px}}
</style></head><body>
<h1>Labels by Section</h1>
<p class="sub">"{html.escape(S.title)}", {html.escape(S.main_file)}. Every word is labeled by where it first appeared;
sentences, paragraphs and sections are labeled from their words (AI if AI words ≥ 50%, Mixed if 15–50%, otherwise
Human or Unclear, whichever is larger). Hover a word for its label, source event and the 6-gram that decided it.</p>
<table><tr><th>Section</th><th>Label</th><th class=num>Words</th><th class=num>AI</th><th class=num>Human</th><th class=num>Unclear</th></tr>{overview}</table>
<ul class="legend">{legend}</ul>
{"".join(out)}
</body></html>"""
    open(dest, "w").write(page)
    print(dest)


if __name__ == "__main__":
    main()
