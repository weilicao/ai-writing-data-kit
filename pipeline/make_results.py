"""Build the folder a contributor shares: <data>/results/ (and results.zip), from the local analysis.

Only these files go in (an allowlist; nothing else from <data> is copied):
  the labeled final paper at every level   level_words.csv, level_ngrams.csv, level_sentences.csv,
                                           level_paragraphs.csv, level_sections.csv
  per-label stats                          level_summary.csv, word_breakdown.csv
  section, paragraph and float report      label_report.html
  first drafts                             first_drafts.csv, first_draft_transitions.csv, first_draft_summary.md
  relevance check                          relevance_report.md, suspicious_matches.csv
  about.json                               from the bundle, plus the sources used and left out
  CHECK_REPORT.txt                         the bundle check (counts and names only)
  SUMMARY.md, MANIFEST.txt                 written here
Chats, sessions and the Overleaf history never go in. Run the analysis with "redact": true (the kit does) so
conversation titles are replaced by neutral names and first drafts from chats are not quoted.

Leak check: every 8-word run that occurs in a chat, session or commit message but in no paper text (any Overleaf
text ever inserted, or any project's final .tex) must be absent from every results file. A hit fails the build
and is reported by file and count, never by text.
"""
import csv
import hashlib
import html
import json
import os
import re
import shutil
import sys
import zipfile
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import match_provenance as mp  # noqa: E402
import readers  # noqa: E402
import settings  # noqa: E402

FILES = ["level_words.csv", "level_ngrams.csv", "level_sentences.csv", "level_paragraphs.csv", "level_sections.csv",
         "level_summary.csv", "word_breakdown.csv", "label_report.html", "first_drafts.csv",
         "first_draft_transitions.csv", "first_draft_summary.md", "relevance_report.md", "suspicious_matches.csv"]
WORD = re.compile(r"[a-z0-9]+")
N = 8
CAVEAT = ("Text that came from a source left out of the analysis can only be labeled Human (if it was typed) or "
          "Unclear (if it was pasted); it can never be labeled AI.")


def runs(text, n=N):
    w = WORD.findall(text.lower())
    return {" ".join(w[i:i + n]) for i in range(len(w) - n + 1)}


def pieces(path):
    """The text units of a results file: CSV cells, markdown table cells and lines, HTML text nodes (no tags or
    styles), so that 8-word runs never span two fields."""
    text = open(path, encoding="utf-8", errors="replace").read()
    if path.endswith(".csv"):
        return [c for row in csv.reader(text.splitlines()) for c in row]
    if path.endswith(".html"):
        text = re.sub(r"(?s)<style.*?</style>|<script.*?</script>", " ", text)
        return [html.unescape(x) for x in re.split(r"<[^>]+>", text)]
    if path.endswith(".json"):
        out = []

        def walk(x):
            if isinstance(x, dict):
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
            elif isinstance(x, str):
                out.append(x)
        walk(json.loads(text))
        return out
    return [c for line in text.splitlines() for c in line.split("|")]


def leak_check(S, out):
    """8-word runs of private text found in the results. Private: chat, session and commit messages and every text
    ever inserted in Overleaf (the history); public: the final text of every project and its first version."""
    public, private = set(), set()
    for p in readers.overleaf_projects(S.inp):
        for u in p["updates"]:
            for segs in u["diffs"].values():
                if isinstance(segs, list):
                    private |= runs("".join(s.get("i", "") for s in segs if isinstance(s, dict)))
        for t in list(readers.final_texts(p).values()) + list(readers.first_texts(p).values()) + [p["project_name"]]:
            public |= runs(t)
            public |= runs(" ".join(w for w, _, _ in mp.tokenize(t)))  # the words as the outputs list them
    for l in open(os.path.join(S.parsed, "chat_messages.jsonl")):
        m = json.loads(l)
        for t in [m["text"]] + [a.get("content") or "" for a in m["attachments"]]:
            private |= runs(t)
    private -= public
    titles = {r["title"].strip() for r in csv.DictReader(open(os.path.join(S.parsed, "chat_conversations.csv")))
              if len((r.get("title") or "").split()) >= 3} if S.get("redact") else set()
    hits, notes = Counter(), Counter()
    for f in sorted(os.listdir(out)):
        found = sum(len(runs(x) & private) for x in pieces(os.path.join(out, f)))
        found += sum(any(t == x.strip() for x in pieces(os.path.join(out, f))) for t in titles)
        # about.json and the check report are written during collection and reviewed by the contributor: warn only
        (notes if f in ("about.json", "CHECK_REPORT.txt") else hits)[f] += found
    return {f: n for f, n in hits.items() if n}, {f: n for f, n in notes.items() if n}


def main():
    S = settings.parse_args()
    if S.mode == "root":
        raise SystemExit("make_results.py is for a bundle (--data <folder>), not the repo root")
    out = os.path.join(S.data, "results")
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)
    for f in FILES:
        src = os.path.join(S.analysis, f)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(out, f))

    about = json.load(open(os.path.join(S.inp, "about.json")))
    choices = settings.source_choices()
    about["analysis"] = {
        "sources_used": [s for s, v in choices.items() if v],
        "sources_left_out": [s for s, v in choices.items() if not v],
        "redacted": bool(S.get("redact")),
        "note": CAVEAT,
    }
    notes = os.path.join(S.parsed, "parse_notes.txt")
    if os.path.exists(notes):
        about["analysis"]["parse_notes"] = open(notes).read().splitlines()
    json.dump(about, open(os.path.join(out, "about.json"), "w"), indent=1, ensure_ascii=False)
    for name in ("CHECK_REPORT.txt",):
        for src in (os.path.join(S.data, name), os.path.join(S.inp, name)):
            if os.path.exists(src):
                shutil.copy(src, os.path.join(out, name))
                break

    # summary: headline shares and sources
    rows = list(csv.DictReader(open(os.path.join(out, "word_breakdown.csv"))))
    lines = [f"# Results: {S.title}", "",
             "What this folder holds: the final paper text with a label for every word, sentence, paragraph and "
             "section, the per-label shares, the first-draft analysis, the relevance check, about.json and the bundle "
             "check. It holds no chat, session or Overleaf-history text (see the leak check below).", "",
             "## Word labels", "", "| block | group | share |", "|---|---|---|"]
    lines += [f"| {r['block']} | {r['group']} | {float(r['share']):.1%} |" for r in rows
              if r["category"] == "(group total)" and r["share"]]
    left = about["analysis"]["sources_left_out"]
    lines += ["", "## Sources", "", "Used: " + ", ".join(about["analysis"]["sources_used"]) + ".",
              "Left out by the contributor: " + (", ".join(left) if left else "none") + "."]
    if left:
        lines.append(CAVEAT)
    if about.get("gaps"):
        lines += ["", "Gaps recorded during collection:"] + [f"- {g}" for g in about["gaps"]]
    if S.get("redact"):  # the key from neutral names back to conversations: local only, never in results/
        with open(os.path.join(S.analysis, "conversation_key.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["name", "source", "conversation_id", "title"])
            for r in csv.DictReader(open(os.path.join(S.parsed, "chat_conversations.csv"))):
                w.writerow([settings.conv_name(f"{r['source']}:{r['id']}:x", ""), r["source"], r["id"], r["title"]])
    leaks, warn = leak_check(S, out)
    lines += ["", "## Leak check", "",
              ("Passed: no 8-word run from a chat, a session, a commit or the Overleaf history that is not also in the "
               "final paper appears in these files" + (", and no conversation title does." if S.get("redact") else "."))
              if not leaks else
              "FAILED: " + ", ".join(f"{f}: {n}" for f, n in sorted(leaks.items())) + ". Do not upload; report this."]
    if warn:
        lines.append("To review (written during collection): " + ", ".join(f"{f} has {n} run(s) also found in chats"
                                                                         for f, n in sorted(warn.items())) + ".")
    open(os.path.join(out, "SUMMARY.md"), "w").write("\n".join(lines) + "\n")

    man = []
    for f in sorted(os.listdir(out)):
        b = open(os.path.join(out, f), "rb").read()
        man.append(f"{hashlib.sha256(b).hexdigest()}  {len(b):>9}  {f}")
    open(os.path.join(out, "MANIFEST.txt"), "w").write("\n".join(man) + "\n")
    z = os.path.join(S.data, "results.zip")
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(os.listdir(out)):
            zi = zipfile.ZipInfo(os.path.join("results", f), date_time=(2026, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(zi, open(os.path.join(out, f), "rb").read())
    print("\n".join(lines))
    print(f"\n{out}\n{z}")
    if leaks:
        sys.exit(1)


if __name__ == "__main__":
    main()
