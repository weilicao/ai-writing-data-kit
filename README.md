# Share how you wrote a paper with AI

I'm studying how researchers use AI when they write papers: which parts of a draft come from an AI assistant,
which are written by people, and how co-author feedback gets handled. **Everyone who contributes data will be a
co-author.** I'd love to include one of your papers: any paper you wrote with Overleaf.

**What I need:** the paper's Overleaf edit history (including its other versions: drafts, copies, arXiv,
camera-ready), and your AI chats about the paper from the weeks you wrote it (claude.ai, ChatGPT, and Claude
Code or Codex sessions that worked on it).

**All you give is the paper's title and your consent.** This kit finds the paper's Overleaf projects, works out
when you wrote it and who the co-authors are, and keeps only the chats that are about the paper. Everything
runs on your own computer, through your logged-in browser; nothing is sent anywhere until you have seen what
was collected and approved it, and then it goes only to my Duke Box folder, where only I can see it.

— Weili Cao, weili.cao@duke.edu

## How to take part (about 5 minutes with a coding agent)

1. **Tell your co-authors** on that paper: the Overleaf history includes their edits and comments.
2. **Log in to Overleaf, claude.ai and ChatGPT** (and any other AI you used while writing the paper) **in
   Chrome**, so the agent can access them directly. The kit exports claude.ai and ChatGPT; for any other AI,
   the agent notes what you used.
3. **Give a coding agent** (Claude Code, Codex, Cursor...), running on the computer where that Chrome is, this
   one line:

   > Get the kit from https://github.com/weilicao/ai-writing-data-kit and follow its AGENT_PROMPT.md as my
   > instructions.

   It asks you for the title and your consent, then runs on its own if it can control your browser (e.g. Claude
   Code with Claude in Chrome); otherwise it tells you what to paste where. At the end it shows you a summary
   and uploads only when you say so.

**No coding agent?** Download [collection_kit_v1.zip](https://github.com/weilicao/ai-writing-data-kit/releases/latest/download/collection_kit_v1.zip),
unzip it and follow [Doing it by hand](#doing-it-by-hand-about-20-minutes) below (about 20 minutes).

**Uploads** go to this Duke Box link (no Box account needed; only I can see uploads):
https://duke.app.box.com/f/b381de52ca74476dbe81feb0e857347f. Please don't send the data by email or Slack.
If something fails, `mydata/CHECK_REPORT.txt` contains only counts and names, so you can send me that safely.

## What is collected

| Data | How | Notes |
|---|---|---|
| **Overleaf history** of every project whose name matches the title: drafts, copies, arXiv, camera-ready and resubmission versions | `overleaf_export.js` | every edit with who made it and when; co-authors' emails are removed |
| **claude.ai chats** from the writing period, with edited and retried replies, attached files, Project files | `claude_ai_export.js` | only chats related to the paper are kept (see below) |
| **ChatGPT chats** from the writing period, including archived chats and chats in Projects | `chatgpt_export.js` | same |
| **Claude Code and Codex sessions** from the writing period that contain the paper's text or title | `bundle.py auto` | sessions about anything else are not copied |
| `about.json`: title, consent, and what the kit worked out (projects, writing period, co-authors, files) | `bundle.py` | you fill in only the title and consent |

**The writing period** runs from a week before the first edit in any of the paper's Overleaf projects to a week
after the last. **Which chats are kept:** a chat (or agent session) goes into the bundle if it contains text
that also appears somewhere in the paper's history (at least 3 identical runs of 6 words), mentions the title,
or is about LaTeX or Overleaf. Everything else in the period is left out; only its id, date and message count
are recorded, so we know something was left out but not what. You can override any decision.

## Before you start

- **Co-authors.** The Overleaf history contains everyone's edits and comments. Let your co-authors know before
  you send it; the consent question covers this.
- **Full Overleaf history.** On the free plan Overleaf shows only the last 24 hours of history, and the script
  then gets only those. The project owner's plan is what counts. The script warns you if early history is
  hidden; if so, contact us.
- **Requirements.** A desktop browser (Chrome, Edge, Firefox or Safari) logged in to Overleaf, claude.ai and
  ChatGPT (whichever you used), and Python 3.9 or newer (`python3 --version`; on Windows use `python`).

## Doing it by hand (about 20 minutes)

All commands run in this folder. `mydata` is the bundle folder.

**1. Start the bundle with the title.**

```bash
python3 bundle.py init mydata --title "Your Paper's Title"
```

Open `mydata/about.json` and set `consent.share_chats_and_overleaf_history_including_coauthors` to `true`
(and `ok_to_quote_short_excerpts_in_publications`, if it is).

**2. Export the Overleaf history.** Open any page on overleaf.com, then the browser console: **Cmd+Option+J**
(Mac) or **Ctrl+Shift+J** (Windows/Linux) in Chrome or Edge; **Cmd+Option+K** / **Ctrl+Shift+K** in Firefox; in
Safari, first enable Settings → Advanced → "Show features for web developers", then **Cmd+Option+C**. Type this
line with your title and press Enter:

```js
window.KIT_CONFIG = {title: "Your Paper's Title"}
```

Then open `overleaf_export.js` in a text editor, copy all of it, paste it into the console and press Enter.
Chrome may ask you to type `allow pasting` first (pasting code into the console can be dangerous; this script
only reads your projects and saves a file, as you can check by reading it). Wait for `Saved
overleaf_export.json`, which lists the projects it found.

**3. Let the kit work out the writing period.**

```bash
python3 bundle.py fill mydata
```

It prints a line like `window.KIT_CONFIG = {since: "2026-01-14", until: "2026-02-13"}`.

**4. Export your chats.** On https://claude.ai, open the console, paste the line from step 3, then all of
`claude_ai_export.js`. On https://chatgpt.com, the same with `chatgpt_export.js`. Skip a site you didn't use.

**5. Build, check and pack.**

```bash
python3 bundle.py auto mydata
```

It writes `mydata.zip` and, next to it, `mydata_REVIEW.txt`: every chat kept or left out, with its title and the
reason. This file is for you and is not in the zip. To change a decision, put the chat's id in
`auto.chat_filter.always_keep_ids` or `always_drop_ids` in `about.json` and run `auto` again.

**6. Upload `mydata.zip`** with the Duke Box link at the top of this page (under "How to take part").

Other commands, if you need them: `bundle.py list-chats`, `exclude-chat` (remove a chat by hand), `agent-logs`
(sessions from another machine: run it there and copy `mydata/agent_logs/` back), `import` (ChatGPT's or
claude.ai's own Export-data zip, if a script fails), `check`, `pack`. Run `python3 bundle.py -h` for details.

## What `check` verifies

`auto` runs it and writes `mydata/CHECK_REPORT.txt` (counts and names only, no chat or paper text; you can send it
to us on its own if something goes wrong). It checks that:
- consent is given;
- each Overleaf history starts at v0, has no gaps and has every file diff, and replaying it backwards from the
  final text reproduces the first version exactly, so no edit is missing;
- chats are readable and fall in the writing period, and claude.ai chats keep their reply branches;
- no API keys, tokens or private keys are left anywhere in the bundle.

## FAQ

**The title matched the wrong projects, or missed one.** Give the project ids instead:
`window.KIT_CONFIG = {projects: ["<id>", "<id>"]}` (the id is the 24 characters after `/project/` in the URL).

**I deleted some chats, or used temporary chats.** They can't be recovered. Say so in `about.json` → `notes`.

**We used GitHub or Git sync with Overleaf, or wrote parts elsewhere (Google Docs, Word).** That shows up in the
history; if you like, say where the text came from in `about.json` → `optional.writing_outside_overleaf`.

**Can I look at what is being sent?** Yes: everything in `mydata/` is plain JSON, and the scripts are short
enough to read.

**Windows?** Use `python` instead of `python3`.
