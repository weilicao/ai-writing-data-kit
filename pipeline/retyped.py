"""Find paper sentences Weili typed that closely follow a Claude sentence she had already been sent.

Exact 6-word matching misses text read in a Claude reply and retyped with small changes. Here a typed
sentence is compared, by content words (stopwords dropped, crude stemming), with every Claude reply
sentence sent before the typing. A reply sentence is skipped when it mostly repeats Weili's own earlier
prompt text in that chat, so text she wrote and Claude echoed is never credited to Claude.
"""
import difflib
import re
from bisect import bisect_left

import match_provenance as mp
import settings

STOP = set("""a an the of to in on for with by as at from into over under about between through and or but nor so
yet if then than that this these those it its we our us they their them he she his her is are was were be been being
has have had do does did can could may might will would should shall must not no also such which who whom whose what
when where while how why all any each both either more most other some only own same very just even well via per
using use used based""".split())
MIN_SHARED = 5        # shared content words
MIN_CONTAINMENT = 0.5  # share of the paper sentence's content words found in the reply sentence
MIN_ORDER = 0.35      # difflib ratio over the two content-word sequences
MIN_ORIGINAL = 0.5    # share of the reply sentence's 3-word sequences not in Weili's earlier prompts in that chat
REPLY_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def stem(w):
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf) and not w.endswith("ss"):
            return w[:-len(suf)]
    return w


def content(words):
    return [stem(w) for w in words if w not in STOP and (len(w) > 2 or w.isdigit())]


def is_prose(raw):
    """Skip table rows, markdown headings/tables and short outline lines; keep sentences."""
    raw = raw.strip()
    if raw.count("&") >= 2 or raw.startswith(("#", "|")):
        return False
    return len(raw.split()) >= 12 or raw.endswith((".", "!", "?"))


def reply_sentences(messages):
    """Claude reply sentences as (time, content list, set, raw text, message), oldest first."""
    by_conv = {}
    for m in messages:
        by_conv.setdefault(m["conversation_id"], []).append(m)
    out = []
    sources = settings.current().reply_sources  # None = every AI source
    for conv in by_conv.values():
        conv.sort(key=lambda m: m["created_at"])
        seen = set()  # 3-word sequences of Weili's prompts and attachments so far in this chat
        for m in conv:
            if m["role"] == "user":
                for t in [m["text"]] + [a["content"] for a in m["attachments"]]:
                    w = [x for x, _, _ in mp.tokenize(t)]
                    seen |= {tuple(w[i:i + 3]) for i in range(len(w) - 2)}
                continue
            if m["role"] != "assistant" or (sources is not None and m["source"] not in sources):
                continue
            for raw in REPLY_SPLIT.split(m["text"]):
                if not is_prose(raw):
                    continue
                w = [x for x, _, _ in mp.tokenize(raw)]
                c = content(w)
                grams = [tuple(w[i:i + 3]) for i in range(len(w) - 2)]
                if len(c) < MIN_SHARED or not grams:
                    continue
                if sum(g not in seen for g in grams) / len(grams) < MIN_ORIGINAL:
                    continue
                out.append((mp.to_ts(m["created_at"]), c, set(c), raw.strip(), m))
    out.sort(key=lambda r: r[0])
    return out


def best_match(sentence_words, before_ts, replies, reply_times):
    """Best Claude reply sentence sent before `before_ts`, or None if nothing is close enough."""
    c = content(sentence_words)
    cs = set(c)
    if len(cs) < MIN_SHARED:
        return None
    best = None
    for t, rc, rs, raw, m in replies[:bisect_left(reply_times, before_ts)]:
        shared = len(cs & rs)
        if shared < MIN_SHARED or shared / len(cs) < MIN_CONTAINMENT:
            continue
        order = difflib.SequenceMatcher(None, c, rc, autojunk=False).ratio()
        if order < MIN_ORDER:
            continue
        score = (shared / len(cs), order)
        if best is None or score > best["score"]:
            best = {"score": score, "shared": shared, "containment": round(shared / len(cs), 3),
                    "order": round(order, 3), "reply_text": raw, "message": m, "reply_set": rs}
    return best
