"""The label space: group -> category -> evidence, the same for every paper (approved 2026-11-19).

Word level (the base; every other level is built from it):
  group     AI | Human | Unclear | Template | Excluded
  category  AI-generated, <tool> / AI, retyped by <contributor> / <person>, typed / <contributor>, in a prompt /
            Pasted by <person>, no source / Paper copy / Git commit, AI trailer / Git commit by <person> /
            Template / first version / <GitHub sync | Git bridge> (excluded)
  evidence  the source event and flags: as_written / other_reply (AI words), repeated_by_ai (human words an AI
            output later repeated unchanged), fallback (no 6-gram anywhere: Overleaf's own author)

Unit level (sentence, paragraph, section, block), from the unit's word labels; shares leave out Template and
Excluded words (a unit with only those takes that group):
  AI       AI words (incl. retyped) >= 50%: verbatim / lightly edited / heavily edited / retyped
  Mixed    15% <= AI < 50%: AI + human / AI + unclear (the larger non-AI group; a tie goes to unclear)
  Human    AI < 15% and Human >= Unclear (by person: most Human words), or the contributor's own earlier words
           repeated by AI >= 50% with AI >= 15% (<contributor>'s draft, polished by AI)
  Unclear  AI < 15% and Unclear > Human (its largest Unclear category)
"""
from collections import Counter

GROUPS = ["AI", "Mixed", "Human", "Unclear", "Template", "Excluded"]
AI_NAMES = {"ai:claude": "Claude", "ai:chatgpt": "ChatGPT", "ai:claude-code": "Claude Code", "ai:codex": "Codex",
            "ai:cursor": "Cursor", "ai:agent-commit": "AI agent (Git commit)"}
EXCLUDED_NAMES = {"github-sync": "GitHub sync", "git-bridge": "Git bridge"}

AI_SHARE = 0.5     # unit is AI at or above this share of AI words
MIXED_SHARE = 0.15  # ... Mixed at or above this, below AI_SHARE
VERBATIM = 0.9     # AI as written, with nothing changed
LIGHT_EDIT = 0.15  # changed words (added + removed) per unit word, at or below = lightly edited
POLISHED = 0.5     # the contributor's own earlier words repeated by AI


def tool(label):
    return AI_NAMES.get(label, label[3:] if label.startswith("ai:") else label)


def word_label(t, S):
    """(group, category) of one token from paper_units.jsonl (its 'cat' and provenance 'label')."""
    me, short = S.me_key, S.me_short
    lab, cat = t["label"], t["cat"]
    if cat in ("kept", "ai"):
        return "AI", f"AI-generated, {tool(lab)}"
    if cat == "retyped":
        return "AI", f"AI, retyped by {short}"
    if lab in S.excluded:
        return "Excluded", f"{EXCLUDED_NAMES.get(lab, lab)} (excluded)"
    if lab == "template":
        return "Template", "Template / first version"
    if lab in (f"human:{me}:typed", f"human:{me}:unmatched"):
        return "Human", f"{short}, typed"
    if lab == f"human:{me}:chat-prompt":
        return "Human", f"{short}, in a prompt"
    if lab == f"human:{me}:pasted":
        return "Unclear", f"Pasted by {short}, no source"
    if lab == f"human:{me}:chat-attachment":
        return "Unclear", "Paper copy"
    if lab.startswith("advisor:"):
        name, how = lab.split(":")[1:3]
        return ("Unclear", f"Pasted by {name}, no source") if how == "pasted" else ("Human", f"{name}, typed")
    if lab == "git-ai":
        return "Unclear", "Git commit, AI trailer"
    if lab.startswith("git:"):
        return "Unclear", f"Git commit by {lab[4:]}"
    return "Unclear", lab


def person(category):
    """'Jane Doe, typed' -> 'Jane Doe'."""
    return category.rsplit(", ", 1)[0]


def flags(t, S):
    f = []
    if t["cat"] == "kept":
        f.append("as_written")
    elif t["cat"] == "ai":
        f.append("other_reply")
    elif t["cat"] == "echoed":
        f.append("repeated_by_ai")
    if t["label"].endswith(":unmatched"):
        f.append("fallback")
    return f


def own(t, S):
    """The contributor's own earlier words (typing or a prompt), repeated unchanged by an AI output."""
    return t["cat"] == "echoed" and t["label"] in (f"human:{S.me_key}:typed", f"human:{S.me_key}:chat-prompt")


def classify(tokens, removed, S):
    """(group, category, stats) for a unit, from its tokens and the AI words removed from it."""
    gc = [word_label(t, S) for t in tokens]
    groups = Counter(g for g, _ in gc)
    n = sum(v for g, v in groups.items() if g not in ("Template", "Excluded"))
    stats = {"words": len(tokens), "counted": n, **{g: groups[g] for g in ("AI", "Human", "Unclear", "Template", "Excluded")}}
    if not n:
        g = "Template" if groups["Template"] else "Excluded"
        return g, (gc[0][1] if g == "Excluded" and gc else "Template / first version"), stats
    ai_written = sum(t["cat"] in ("kept", "ai") for t in tokens)
    retyped = sum(t["cat"] == "retyped" for t in tokens)
    changed_h = sum(t["cat"] == "human" and g == "Human" for t, (g, _) in zip(tokens, gc))
    changed_u = sum(t["cat"] == "human" and g == "Unclear" for t, (g, _) in zip(tokens, gc))
    polished = sum(own(t, S) for t in tokens)
    share, share_rt = ai_written / n, (ai_written + retyped) / n
    stats.update(ai_as_written=ai_written, retyped=retyped, changed_human=changed_h, changed_unclear=changed_u,
                 removed=removed, own_repeated=polished)
    if polished / n >= POLISHED and share >= MIXED_SHARE:
        return "Human", f"{S.me_short}'s draft, polished by AI", stats
    if share >= VERBATIM and not (changed_h or changed_u or removed or retyped):
        return "AI", "AI, verbatim", stats
    if share >= AI_SHARE:
        light = (changed_h + changed_u + removed) / n <= LIGHT_EDIT
        return "AI", "AI, lightly edited" if light else "AI, heavily edited", stats
    if share_rt >= AI_SHARE:
        return "AI", "AI, retyped", stats
    if share_rt >= MIXED_SHARE:
        return "Mixed", "AI + human" if groups["Human"] > groups["Unclear"] else "AI + unclear", stats
    if groups["Human"] >= groups["Unclear"] and groups["Human"]:
        top = Counter(person(c) for g, c in gc if g == "Human").most_common(1)[0][0]
        return "Human", top, stats
    if groups["Unclear"]:
        return "Unclear", Counter(c for g, c in gc if g == "Unclear").most_common(1)[0][0], stats
    # only AI words below the thresholds cannot happen (share would be 1); keep a safe default
    return "Mixed", "AI + human", stats
