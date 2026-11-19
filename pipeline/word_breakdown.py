"""Word-level breakdown of the main paper by label (scripts/labels.py), from paper_units.jsonl -> word_breakdown.csv.

One row per (block, group, category): Body (prose paragraphs), Captions and Tables (captions, table cells,
figure and algorithm text), and All. Shares leave out Excluded words.
"""
import csv
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import labels as lb  # noqa: E402
import settings  # noqa: E402

BLOCKS = ["Body", "Captions and Tables", "All"]
MEANING = {
    "AI-generated": "first appeared in this AI tool's output (chat reply, agent reply or text an agent wrote)",
    "AI, retyped": "put in by the contributor, but the sentence follows an earlier AI sentence (retyped rule)",
    "typed": "first appeared in this person's Overleaf typing (or Overleaf's author, when no 6-gram is found)",
    "in a prompt": "first appeared in a prompt the contributor sent to an AI",
    "Pasted by": "a paste (>=150 chars at >15 chars/s) matching no earlier source",
    "Paper copy": "first seen in a document attached to an AI chat, edited outside Overleaf",
    "Git commit, AI trailer": "first seen in a Git commit with an AI co-author trailer, in no collected session",
    "Git commit by": "first seen in a Git commit without an AI trailer, in no collected session",
    "Template": "the project's first version (template or initial upload)",
    "(excluded)": "pushed from a Git clone, matching no earlier source; not counted",
}


def meaning(cat):
    return next((m for k, m in MEANING.items() if k in cat), "")


def block(kind):
    return "Body" if kind == "prose" else "Captions and Tables"


def main():
    S = settings.parse_args()
    A = S.analysis
    c = Counter()
    for l in open(os.path.join(A, "paper_units.jsonl")):
        u = json.loads(l)
        for t in u["tokens"]:
            g, cat = lb.word_label(t, S)
            for b in (block(u["kind"]), "All"):
                c[(b, g, cat)] += 1
                if "repeated_by_ai" in lb.flags(t, S):
                    c[(b, "flag", "repeated_by_ai")] += 1
    order = sorted({(g, cat) for b, g, cat in c if g != "flag"},
                   key=lambda x: (lb.GROUPS.index(x[0]), -c[("All",) + x], x[1]))
    with open(os.path.join(A, "word_breakdown.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["block", "group", "category", "meaning", "words", "share"])
        for b in BLOCKS:
            n = sum(v for (bb, g, _), v in c.items() if bb == b and g not in ("Excluded", "flag"))
            groups = Counter()
            for g, cat in order:
                v = c[(b, g, cat)]
                if not v:
                    continue
                w.writerow([b, g, cat, meaning(cat), v, "" if g == "Excluded" else f"{v / n:.3f}"])
                if g != "Excluded":
                    groups[g] += v
            for g in lb.GROUPS:
                if groups[g]:
                    w.writerow([b, g, "(group total)", "", groups[g], f"{groups[g] / n:.3f}"])
            w.writerow([b, "Human", "(flag) repeated by AI", "human words an AI output later repeated unchanged",
                        c[(b, "flag", "repeated_by_ai")], f"{c[(b, 'flag', 'repeated_by_ai')] / n:.3f}"])
            print(f"{b}: counted words={n}", {g: f"{v / n:.1%}" for g, v in groups.items()})
    for g, cat in order:
        v = c[("All", g, cat)]
        print(f"  {g:8} {cat:42} {v:5}")


if __name__ == "__main__":
    main()
