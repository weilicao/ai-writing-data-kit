# Instructions for the contributor's coding agent

You are helping your user contribute to a research study on how researchers use AI to write papers. You will
collect, **on the user's own machine(s)**, everything about how one paper of theirs was written (its Overleaf
history, their AI chats about it, their coding-agent sessions that worked on it, the Git history of its files),
from the sources the user allows; run the analysis in `pipeline/` there; and upload **only the results** when the
user approves.

**What is uploaded:** `aiw_work/results.zip`, made by `pipeline/analyze.py`: the final text of the paper with a
label for every word, sentence, paragraph and section; per-label statistics; the first-draft and relevance
reports; `about.json`; the check report. **What never leaves the user's machines:** the chats, the sessions, the
Overleaf history and the Git history (`mydata/`, `aiw_work/bundle`, `aiw_work/parsed`, `aiw_work/analysis`).

Work out everything you can yourself. Ask the user only for what is listed in step 1.

Study contact: Weili Cao, weili.cao@duke.edu. Upload link (Duke Box File Request; only the study team can see
uploads): https://duke.app.box.com/f/b381de52ca74476dbe81feb0e857347f

## Rules

1. **Keep the data out of your context.** The data is the user's private chats, their co-authors' edits and an
   unpublished paper. Anything you read or print goes to your model provider, so do the collecting, filtering
   and checking **with code that writes to files**, and look only at structure, counts, ids and dates. Don't print
   or read chat text, paper text, or session contents, and never print access tokens or cookies.
2. **The data stays on the user's machines.** Read from the sites below only through the user's logged-in
   browser, and move files only between the user's own machines (e.g. from their laptop to a server they use, see
   section 6a). The only upload is `results.zip` to the Box link above, after the user approves it. Never upload
   `mydata/` or anything else.
3. **Never type a password or log in for the user.** If a site is logged out, ask them to log in.
4. **When in doubt, keep.** If you can't tell whether something is about the paper, keep it and say so in the
   summary; the user reviews the list before anything is sent.
5. **Record gaps.** Anything you couldn't collect (deleted sessions, a site you couldn't reach, a tool with no
   export) goes into `about.json` → `gaps`, so missing data isn't mistaken for "not used".

## 1. Ask the user, in one message

1. The paper's title (or its Overleaf link).
2. Which coding agents they used while writing it (Claude Code, Codex, Cursor, Copilot, Gemini CLI, Windsurf,
   Cline, Aider...). Assume they used at least one.
3. **Which sources the analysis may use.** Show this checklist and let them untick any source (default: all):

   | Source | What it covers |
   |---|---|
   | `overleaf` | edit history of the paper's Overleaf projects (required: without it there is nothing to analyse) |
   | `claude.ai` | claude.ai chats and Projects |
   | `chatgpt` | ChatGPT chats and Codex cloud tasks |
   | `claude-code` | Claude Code sessions (terminal, IDE, desktop app, and Claude Code on the web) |
   | `codex` | Codex CLI sessions |
   | `cursor` | Cursor chats |
   | `copilot` | VS Code Copilot Chat |
   | `gemini-cli` | Gemini CLI |
   | `other-agents` | Windsurf, Cline, Roo Code, Continue, Aider and others |
   | `git` | Git history of the paper's files (GitHub sync, local repos) |

   Don't collect a source they untick. Tell them: text that came from a left-out AI source can only be labeled
   Human or Unclear, never AI.
4. **Where agents ran.** Which machines (laptop, servers, clusters, cloud VMs). Before asking, run
   `python3 -I pipeline/find_sessions.py` and show the hints it found, so they only confirm (section 6a); run it
   again with `--since <window start>` once the window is known (section 3). Say on which machine you are running
   and whether it can reach their browser.
5. Consent: *"Do you agree to let the analysis use your AI chats, coding-agent sessions and the paper's Overleaf
   and Git history on your computer, and to share the results: the paper's final text with a label for every part,
   statistics, and the names of co-authors who edited it? Your chats, sessions and histories are not shared. May
   short excerpts of the paper be quoted in publications?"* If they don't agree, stop.

Record the checklist in `about.json` → `sources` (section 8). Also ask them to log in, in Chrome, to Overleaf,
claude.ai, ChatGPT and any other AI they allowed, if they haven't. You need a way to run JavaScript in their logged-in Chrome tabs (e.g. Claude in Chrome); if you don't
have one, write each browser step as a script they paste into the console (Cmd+Option+J / Ctrl+Shift+J), and
tell them exactly where.

## 2. The local bundle

Create the folder `mydata/` **inside the kit folder** (the clone of this repository), on the machine where the
analysis will run; it stays there. `pipeline/analyze.py` later writes `aiw_work/` beside it. Use exactly this
layout. Formats are fixed because `pipeline/` reads them. Leave out the files of sources the user
unticked.

```
mydata/
  about.json                      what you worked out, the answers, consent, gaps (section 8)
  overleaf_<project id>.json      one per Overleaf project (section 3)
  claude_ai_<org uuid>.json       one per claude.ai account with chats (section 4)
  chatgpt.json                    ChatGPT chats and Codex cloud tasks (section 5)
  agent_logs/<tool>/<machine>/... coding-agent session files, copied unchanged (section 6); <tool> is one of
                                  claude-code, codex, codex-archived, cursor, copilot, gemini-cli, windsurf,
                                  cline, roo-code, continue, aider
  git_<owner>__<repo>.json        history of the paper's files in a Git repo (section 7)
  other/                          anything else that helps (other AI tools' exports, notes)
  CHECK_REPORT.txt                counts and names only, no content (section 10)

pipeline/                         (part of this kit) the analysis code; standard library Python >= 3.9
```

Large data must never pass through your tool output. In the browser, build the JSON in the page and save it as
a download (`new Blob([JSON.stringify(x)])` plus a temporary `<a download>` click), one file per site, then move
it from the Downloads folder into `mydata/`. Use `fetch(url, {credentials: "include"})` from a tab on the same
site. Retry on HTTP 429/5xx with exponential backoff; for long runs, keep the progress in a page variable and
poll it, so your tool call doesn't time out.

## 3. Overleaf (in a logged-in overleaf.com tab)

1. **Find the projects.** `GET /user/projects` returns `{projects: [{_id, name, ...}]}`. Take every project whose
   name contains at least 60% of the title's words (lowercase, ignore "a an the of for and in on to with is are
   via from by at as"). This finds drafts, copies, and arXiv, camera-ready or resubmission versions. Show the user
   the list of names in the summary; if the title matches nothing, ask for the Overleaf link.
2. **History.** For each project: `GET /project/<id>/updates?min_count=50` returns `{updates, nextBeforeTimestamp}`,
   newest first; repeat with `&before=<nextBeforeTimestamp>` until it is absent. Store the updates **oldest
   first**. Each update has `fromV, toV, meta {users, start_ts, end_ts, origin}, pathnames, project_ops`.
3. **Diffs.** For every update and every path in its `pathnames`:
   `GET /project/<id>/diff?pathname=<path>&from=<fromV>&to=<toV>` returns `{diff: [...]}`, a list of segments
   `{u: text}` (unchanged), `{i: text, meta}` (inserted), `{d: text, meta}` (deleted), or an object such as
   `{binary: true}`. Store the list in `updates[k].diffs[path]`, **replacing each `u` text by its length**
   (`{u: 1234}`). Use up to 4 requests in parallel.
4. **Final text with authors.** `GET /project/<id>/filetree/diff?from=0&to=<last toV>` lists the files. For each
   file that isn't removed or binary, `GET /project/<id>/diff?pathname=<path>&from=0&to=<last toV>`; store the
   segments **with full `u` text** in `full_attributed_diff[path]`. For binary files (images, PDFs), store
   `{binary: true}` instead of a segment list.
5. **Also:** `GET /project/<id>/threads` (review comments), `/project/<id>/labels` (named versions), and
   `/project/<id>/github-sync/status` (`{enabled, repo, merge_status}`: the GitHub repo the project syncs with;
   see section 7).
6. **Remove emails:** every `users` entry becomes `{id, first_name, last_name}`: in each update's `meta`, in the
   `meta` of every diff segment (in `updates[k].diffs` and in `full_attributed_diff`), in threads and in `users`;
   and `github_sync` keeps only `{enabled, repo, merge_status}` (the status also returns the owner's email). Record the user's own Overleaf id
   (the page's `<meta name="ol-user_id">`) as `about.json` → `contributor.overleaf_id`.
7. Save each project as `overleaf_<id>.json`:
   `{source: "overleaf", project_id, project_name, exported_at, last_version, updates, full_attributed_diff,
   threads, labels, filetree, users, github_sync, warnings}`.
8. **Check** (section 10): the history must start at v0 (`updates[0].fromV == 0`) with no gaps. If it starts
   later, the project's history is hidden (Overleaf's free plan shows only the last 24 hours, and the owner's
   plan is what counts). Tell the user and record it in `gaps`.

**The writing period ("window"):** from 7 days before the first text edit to 7 days after the last, over all
the projects. A text edit is an update with at least one `i` or `d` segment, ignoring updates whose
`meta.origin.kind` is `history-resync`. Use this window for everything below.

## 4. claude.ai (in a logged-in claude.ai tab; only if `claude.ai` is allowed)

Claude Code on the web sessions are collected only if `claude-code` is allowed.

- `GET /api/organizations` lists the accounts. Use each one whose `capabilities` include `"chat"`; the others are
  API accounts without chats (record them in `gaps`).
- Conversations: `GET /api/organizations/<org>/chat_conversations?limit=100&offset=<n>`, paging until a page is
  short or repeats. Take those **active in the window**: `created_at <= until` and `updated_at >= since`.
- Each conversation in full, with edited and retried branches:
  `GET /api/organizations/<org>/chat_conversations/<uuid>?tree=True&rendering_mode=messages&render_all_tools=true`.
  Keep the listing's `project_uuid` on it.
- For each claude.ai Project those conversations belong to: `GET /api/organizations/<org>/projects/<uuid>` and
  `.../projects/<uuid>/docs` (knowledge files; drafts are often uploaded there).
- **Claude Code on the web** (once per user, not per account): `GET /v1/code/sessions?limit=50` (then
  `&resume_token=<resume_token>` until it is absent, or until a page adds no new session ids), **with the header `anthropic-version: 2023-06-01`**. For each
  session active in the window (`created_at`, `last_event_at`), get all events: `GET /v1/code/sessions/<id>/events`,
  then `?cursor=<next_cursor>` until it is absent, with the same header.
- Save `claude_ai_<org uuid>.json`: `{source: "claude.ai", org, exported_at, window, conversations: [full
  conversations], projects: {uuid: {name, description, prompt_template, docs}}, code_sessions: [session with
  events]}`.
- If this fails, the fallback is claude.ai → Settings → Privacy → Export data (emailed; no branches): put the
  conversations from the window into the same structure and note it in `gaps`.

## 5. ChatGPT (in a logged-in chatgpt.com tab; only if `chatgpt` is allowed)

- `GET /api/auth/session` returns `accessToken`; send it as `Authorization: Bearer <token>` on the calls below. Keep
  it inside the page; never print it.
- List: `GET /backend-api/conversations?offset=<n>&limit=100&order=updated`, and again with `&is_archived=true`.
  Chats inside ChatGPT Projects: `GET /backend-api/gizmos/snorlax/sidebar?conversations_per_gizmo=0` lists projects
  (`items[].gizmo.gizmo.id`); for each, `GET /backend-api/gizmos/<id>/conversations?cursor=0`, then `?cursor=<cursor>`.
- For each conversation active in the window (`create_time`, `update_time`): `GET /backend-api/conversation/<id>`
  (the full message tree). Wait about 250 ms between requests.
- **Codex cloud:** `GET /backend-api/wham/tasks/list?limit=20&task_filter=current` (and `task_filter=archived`),
  paging with `&cursor=<cursor>`; for each task active in the window, `GET /backend-api/wham/tasks/<id>`.
- Save `chatgpt.json`: `{source: "chatgpt.com", exported_at, window, conversations, codex_tasks}`.
- Fallback: ChatGPT → Settings → Data controls → Export data (emailed zip with `conversations.json`, same
  conversation format); keep the conversations from the window.

## 6. Coding-agent sessions (on every machine where an agent ran)

Look in **all** of these that the user allowed, not only the agents they named, on this computer (macOS, Linux or Windows; `~` is
the home folder):

| Tool | Where its sessions are |
|---|---|
| Claude Code (terminal, IDE, desktop app) | `~/.claude/projects/**/*.jsonl` |
| Codex CLI | `~/.codex/sessions/**`, `~/.codex/archived_sessions/**` |
| Gemini CLI | `~/.gemini/tmp/**` |
| Cursor | `<config>/Cursor/User/workspaceStorage/*/state.vscdb` and `globalStorage/state.vscdb` (SQLite; chats are JSON values in tables `ItemTable` and `cursorDiskKV`) |
| VS Code (Copilot Chat) | `<config>/Code/User/workspaceStorage/*/chatSessions/*.json`, and `state.vscdb` files there; also `Code - Insiders` |
| Windsurf | `<config>/Windsurf/User/...`, as for VS Code |
| Cline / Roo Code | `<config>/Code/User/globalStorage/saoudrizwan.claude-dev/tasks/*/` and `rooveterinaryinc.roo-cline/tasks/*/` |
| Continue | `~/.continue/sessions/` |
| Aider | `.aider.chat.history.md` in project folders (e.g. the paper's Git repos) |
| Others the user names | find where they keep history |

`<config>` is `~/Library/Application Support` (macOS), `~/.config` (Linux) or `%APPDATA%` (Windows).

- **Which files to keep:** files or database rows from the window (by the timestamps inside them, or the file's
  modification time) that are **about the paper**, using the test in section 9. Copy kept files **unchanged** to
  `agent_logs/<tool>/<machine>/<path relative to the tool's folder>`. From SQLite databases, copy the matching rows
  as JSON lines to `<database path>.related_rows.jsonl` (copy the database first; the editor may lock it). For
  `<machine>`, use `this-computer` for the local one, and a short name for each other machine.
- **Other machines:** see section 6a.
- **Deleted sessions:** Claude Code deletes session files about 30 days after their last activity by default
  (setting `cleanupPeriodDays`). If the window ended more than 30 days ago and few or no Claude Code sessions are
  left, record that in `gaps`.
- In `about.json` → `agent_sessions`, record per tool and machine: folders searched, files from the window, files
  kept.

## 6a. Servers, clusters and several machines

Sessions stay on the machine where the agent ran; browser data (Overleaf, claude.ai, ChatGPT) can only be read on
the machine with the user's browser. `python3 -I pipeline/find_sessions.py --since <window since>` lists, with
counts only: the session stores on this machine; hints of other machines (`Host` entries in `~/.ssh/config`, the
Claude desktop app's `ssh-*` folders, one per SSH connection it ran Claude Code over, and VS Code / Cursor
Remote-SSH hosts); and, on a server, traces that a laptop drove agents there (`~/.claude/remote` from the Claude
desktop app, `~/.vscode-server`, `~/.cursor-server`). For each host it prints the fetch command.

- **You run on the laptop, sessions are on a server:** for each host the user confirms, ask before connecting, then
  run the printed command, e.g.
  `ssh HOST 'cd ~ && find .claude/projects .codex/sessions .codex/archived_sessions .gemini/tmp -type f -newermt <since> 2>/dev/null | tar czf - -T -' > agent_sessions_HOST.tgz`,
  extract it to a temporary folder, filter it like local files into `agent_logs/<tool>/<HOST>/`, and delete the
  archive and the folder. If `ssh` needs a password or a second factor, give the user the command to run in their
  own terminal instead.
- **You run on the server, the browser is on the laptop:** you can't reach the browser. Give the user the browser
  steps (sections 3–5) as scripts to paste into the console of their logged-in tabs on the laptop; they save
  downloads there. Then ask them to move the downloaded files to `mydata/` on the server, on their own machines
  only: `scp ~/Downloads/<files> HOST:<path to mydata>/`, or drag them into the folder in VS Code / Cursor
  Remote-SSH, or the desktop app's file upload. Collect the server's sessions and Git repos directly.
- **You run on the server, with browser tools that drive the laptop's Chrome** (e.g. the Claude desktop app with
  Claude in Chrome): the browser steps work, but their downloads land on the laptop; move them to `mydata/` on the
  server as above.
- **Results back to the laptop:** if the analysis ran on a server, the user needs `aiw_work/results/` on the laptop
  to open `label_report.html` and to upload `results.zip`: ask them to copy it (`scp -r HOST:<kit>/aiw_work/results
  .` and `results.zip`, or download it in VS Code).
- **`ssh` from Windows PowerShell:** don't redirect binary output (`> file.tgz` corrupts it); run
  `ssh HOST "<find …> | tar czf ~/agent_sessions.tgz -T -"` and then `scp HOST:agent_sessions.tgz .`.
- **Several machines:** collect each machine's sessions into `agent_logs/<tool>/<machine>/` and bring everything
  to **one** machine of the user's (where `mydata/` is), the same way. Run the analysis there once.
- Record every machine in `about.json` → `machines` (name, how its data was fetched, or why not) and anything
  unreachable in `gaps`.

## 7. Git history of the paper's files (only if `git` is allowed)

- **GitHub sync:** for each Overleaf project whose `github_sync.enabled` is true, clone `github_sync.repo` with
  the user's own GitHub access (`gh repo clone` or `git clone`; set `GIT_TERMINAL_PROMPT=0` so it can't hang on
  a password prompt). If cloning fails, ask the user to log in to GitHub (`gh auth login`) or for a local copy.
- **Other repos holding the paper:** local Git repos whose remote is that GitHub repo, or the Overleaf Git
  bridge (`git.overleaf.com/<project id>`), or that contain the paper's `.tex` files (search the folders the
  agent sessions ran in).
- For each repo: `git log --all -p --no-color` limited to the paper's files: the Overleaf project's file paths
  plus `*.tex *.bib *.sty *.cls *.bst`. For every commit, keep sha, parents, author name, author date,
  committer name, commit date, full message, and patch. **Remove email addresses** in messages, keeping only the
  domain (e.g. `<...@anthropic.com>`): `Co-Authored-By` trailers show which commits an AI agent made. Also keep
  the list of refs. Don't copy anything else from the repo.
- Save `git_<owner>__<repo>.json`: `{source: "git", repo, collected_at, paths, refs, commits}`.

## 8. `about.json`

```json
{
  "kit_version": "instructions-2",
  "collected_by": "<your name and model>",
  "paper": {"title": "", "overleaf_projects": [{"project_id": "", "name": "", "first_edit": "", "last_edit": "",
            "text_edits": 0, "tex_files": [], "github_sync": null}], "text_files": [], "comment_macros": []},
  "consent": {"use_data_locally_and_share_results": true,
              "ok_to_quote_short_excerpts_in_publications": false},
  "contributor": {"name": "", "email": "", "overleaf_id": ""},
  "sources": {"overleaf": true, "claude.ai": true, "chatgpt": true, "claude-code": true, "codex": true,
              "cursor": true, "copilot": true, "gemini-cli": true, "other-agents": true, "git": true},
  "machines": [{"name": "this-computer", "role": "analysis runs here", "fetched": "local"}],
  "window": {"since": "YYYY-MM-DD", "until": "YYYY-MM-DD", "rule": "7 days before the first text edit to 7 days after the last"},
  "people": [{"overleaf_name": "", "inserted_chars": 0}],
  "coding_agents_used": ["as the user said"],
  "agent_sessions": {"claude-code / this-computer": {"searched": [], "files_in_window": 0, "kept": 0}},
  "git_repos": [{"repo": "", "commits": 0, "ai_marked_commits": 0}],
  "other_ai_tools": [{"tool": "", "what": "exported to other/ | could not export"}],
  "excluded": [{"file": "", "id": "", "created_at": "", "messages": 0, "reason": ""}],
  "gaps": [],
  "notes": ""
}
```

- `sources`: the user's checklist from step 1 (`false` = left out; the results say which).
- `contributor`: the Overleaf name for the user's `overleaf_id`, and their email if they gave it or the page shows
  it (`<meta name="ol-usersEmail">`).
- `people`: everyone in the Overleaf histories, with the characters they inserted.
- `comment_macros`: macros defined in the paper that look like inline comments, e.g.
  `\newcommand{\jd}[1]{\textcolor{red}{...}}`. Co-authors often comment this way, e.g. `\jd{fix this}`.

## 9. Keep only what is about the paper (chats, web sessions, Codex tasks, agent sessions)

Decide **with code**, not by reading:

- Paper text = all text inserted in any Overleaf update, plus the final text of every `.tex` file. Leave out
  `.bib`, `.sty`, `.cls` and `.bst` files: their boilerplate (venue names, URLs) also appears in unrelated chats.
  Split it into lowercase words (`[a-z0-9]+`) and take every run of 6 consecutive words.
- An item's text = what the user and the AI wrote: message text, attachments, and tool inputs and outputs. Not
  metadata, system prompts or tool and skill lists: those mention `latex` in nearly every chat.
- Keep an item if **at least 3 distinct 6-word runs** of its text also occur in the paper text, **or** it contains the paper's title (ignoring case and spacing), **or** the name
  of a synced repo, **or** it is about LaTeX or Overleaf (`latex`, `overleaf`, `.tex`, `.bib`, `\section`, `\cite`...).
- Everything else from the window is left out of the bundle. Record each left-out item in `about.json` →
  `excluded` with **only** its file, id, date, message count and the reason.
- Write a review list **next to** `mydata/`, not inside it: `mydata_REVIEW.txt`, listing every kept and left-out
  item with date, title and reason. It is for the user only.

(On the study's first paper, this rule kept 67 of the 68 chats about the paper and 5 of 15 unrelated ones.)

## 10. Checks (write `CHECK_REPORT.txt`: counts and names only)

Run `python3 -I pipeline/bundle.py check mydata`: it does all of the checks below and writes
`mydata/CHECK_REPORT.txt` (only `check` is needed from that script). Fix what it reports as problems. In short:

- **Overleaf completeness:** for each project, start from the final text (join `u` and `i` segments of
  `full_attributed_diff`) and undo the updates newest to oldest. For each segment list: unchanged `u` (a length)
  copies that many characters, `i` must match the text at that position and is skipped, `d` is put back. The
  result must equal v0 (join `u` and `d` segments). Report per file: OK or not. Also check that every path in
  every update's `pathnames` has a diff.
- **Counts:** updates, versions, dates, inserted characters per author and per origin (`editor`, `github`,
  `upload`); conversations, messages and attachments per chat file; web sessions; Codex tasks; agent session files
  per tool and machine; Git commits and how many mention an AI agent.
- **Window:** every kept chat or session is active in the window.
- **Secrets:** scan every file in `mydata/` for API keys and tokens (`sk-ant-...`, `sk-...`, `ghp_...`,
  `github_pat_...`, `AKIA...`, `xox...-`, `hf_...`, `-----BEGIN ... PRIVATE KEY-----`). Report which file and
  what kind, never the value, and ask the user whether to remove them.
- **Emails:** none left in any Overleaf `users` entry (update and segment `meta` included), threads,
  `github_sync` or Git authors (emails inside the paper's own
  files, e.g. in `.sty` headers, stay).

## 11. Analyse on this machine

```
python3 -I pipeline/analyze.py mydata          # writes aiw_work/ next to mydata/
```

(On Windows use `py -X utf8 -I` instead of `python3 -I`, here and in sections 1 and 10.) The analysis reads the
sessions of Claude Code (local and on the web), Codex and Cursor; sessions of the other tools are collected and
counted, but not yet analysed (`about.json` → `analysis` → `parse_notes` says so). Codex `.jsonl.zst` files need
the `zstd` program (`brew install zstd`, `apt install zstd`, or `winget install zstd`); without it they are skipped
and listed in the parse notes, so record that in `gaps`.

It runs the bundle check, then the analysis (parse, 6-gram provenance, word labels, first
drafts, sentence labels, relevance) and builds `aiw_work/results/` and `aiw_work/results.zip`, with conversation titles replaced by
neutral names ("claude.ai conversation 12") and no first-draft quotes. Its last step is a **leak check**: no
8-word run from a chat, a session, a commit or the Overleaf history that is not also in the final paper may
appear in any analysis file of the results. `about.json` and the check report are shared reduced (names, counts and
choices; no email, file paths or list of left-out items, only their number per reason); since you write them during
collection, often inside a session that is itself collected, overlaps there are listed in `SUMMARY.md` under "To
review": show the user those fields before they approve. If the check fails, `results.zip` is not written: don't upload anything; tell the user, and draft for them an email to
the study contact with the file names and counts from `SUMMARY.md` (no text), which they send themselves.
If the bundle check (section 10) reports problems, `analyze.py` stops; fix them first.

If a step fails on a format difference in a file you wrote, fix that file's format to match this document and run
again. Don't change `pipeline/`; if it fails anyway, record the error message (not data) in `notes`.

## 12. Review and upload

1. Tell the user, in a few lines: the Overleaf projects found, the window, the sources used and left out, how many
   chats, web sessions, Codex tasks and agent session files were kept or left out (per tool and machine), the Git
   repos, the gaps, and the headline shares from `aiw_work/results/SUMMARY.md`. Point them to `mydata_REVIEW.txt`
   and to **`aiw_work/results/`, which is exactly what will be sent**: `MANIFEST.txt` lists every file with its
   size and SHA-256, and `label_report.html` shows the paper colored by label. Change what they ask (for example
   untick a source, set it to `false` in `about.json` → `sources` and run `analyze.py` again).
2. When they approve, upload `aiw_work/results.zip` (only that file) at the Box link (it asks for their email). If
   you can't attach files in the browser, ask them to drag it there. Then tell them what was uploaded and when.
3. `mydata/` and `aiw_work/` stay on their machine. They may delete them, or keep them in case of follow-up
   questions.
