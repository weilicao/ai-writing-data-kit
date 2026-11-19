"""Run the whole analysis on a contributor's own machine and build the folder they share.

    python3 -I pipeline/analyze.py mydata [--work aiw_work]

Reads mydata/ (the collected bundle; it never leaves the machine), works in aiw_work/ (default: next to mydata),
and writes aiw_work/results/ and aiw_work/results.zip: the only things to upload. Runs, in order: the bundle
check, parse, provenance, sentences, word breakdown, levels, first drafts, relevance, and make_results (with
conversation titles redacted and a leak check). Standard library only; Python >= 3.9.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STEPS = ["parse_raw.py", "match_provenance.py", "explain_edits.py", "word_breakdown.py", "levels.py",
         "first_draft.py", "check_relevance.py", "make_results.py"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mydata")
    ap.add_argument("--work")
    a = ap.parse_args()
    mydata = os.path.abspath(a.mydata)
    if not os.path.exists(os.path.join(mydata, "about.json")):
        raise SystemExit(f"{mydata}: no about.json")
    work = os.path.abspath(a.work or os.path.join(os.path.dirname(mydata), "aiw_work"))
    bundle = os.path.join(work, "bundle")
    os.makedirs(bundle, exist_ok=True)
    link = os.path.join(bundle, "mydata")
    if os.path.islink(link):
        os.remove(link)
    elif os.path.isdir(link):
        shutil.rmtree(link)  # a copy from an earlier run: copy again, mydata/ may have changed
    try:
        os.symlink(mydata, link, target_is_directory=True)
    except OSError:  # e.g. Windows without symlink rights
        shutil.copytree(mydata, link)
    for d in ("parsed", "analysis"):
        os.makedirs(os.path.join(work, d), exist_ok=True)
    settings_path = os.path.join(work, "settings.json")
    s = json.load(open(settings_path)) if os.path.exists(settings_path) else {}
    s["redact"] = True  # shared results never hold conversation titles
    json.dump(s, open(settings_path, "w"), indent=1)

    py = [sys.executable, "-X", "utf8", "-I"]  # UTF-8 files on every platform (Windows defaults to cp1252)
    checker = next(p for p in (os.path.join(HERE, "bundle.py"), os.path.join(HERE, "kit_reference", "bundle.py"))
                   if os.path.exists(p))
    check = subprocess.run(py + [checker, "check", mydata], capture_output=True, text=True)
    open(os.path.join(work, "CHECK_REPORT.txt"), "w").write(check.stdout + check.stderr)
    print(f"== bundle check: {'problems found, see' if check.returncode else 'OK,'} {os.path.join(work, 'CHECK_REPORT.txt')}")
    if check.returncode:
        raise SystemExit("Fix the problems the bundle check lists (INSTRUCTIONS.md section 10), then run again.")
    for step in STEPS:
        print(f"== {step}", flush=True)
        r = subprocess.run(py + [os.path.join(HERE, step), "--data", work])
        if r.returncode:
            raise SystemExit(f"{step} failed (exit {r.returncode}); nothing to upload yet")
    print(f"\nShare only: {os.path.join(work, 'results.zip')} (contents in {os.path.join(work, 'results')})")


if __name__ == "__main__":
    main()
