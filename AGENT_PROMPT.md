# Prompt for your coding agent

Paste everything below the line into your coding agent (Claude Code, Codex, Cursor...), started in this
folder, on the same computer as the browser you use for Overleaf; or just tell it to get the kit from
https://github.com/weilicao/ai-writing-data-kit and follow this file. The agent asks you two things, then runs on
its own, and shows you the result before anything is uploaded.

(For the agent: if you were pointed to the GitHub page, first get the kit: `git clone
https://github.com/weilicao/ai-writing-data-kit`, or download and unzip
https://github.com/weilicao/ai-writing-data-kit/releases/latest/download/collection_kit_v1.zip, and work in that
folder. Then follow everything below the line.)

---

I'm contributing data to a research study on how AI is used to write papers. This folder is the study's
collection kit (`README.md` explains each step). Build the data bundle with as little of my time as possible,
under these rules.

**Rules (always)**

1. **Privacy.** The bundle holds my private chats, my co-authors' edits and an unpublished paper. Don't upload,
   paste or send any of it anywhere except: the kit's scripts reading overleaf.com, claude.ai and chatgpt.com
   through my logged-in browser, and the final upload to the study's Duke Box link (top of `README.md`) after
   I approve it. Don't read chat or paper contents beyond what a step needs (structure and counts, not text),
   and don't quote them back to me.
2. **Never type a password or log in for me.** If a site is logged out or a session expires, stop that step and
   tell me.
3. **Run the kit's scripts unchanged.** Pass settings through `window.KIT_CONFIG`.
4. **Change the bundle only through `bundle.py`.** Don't edit the exported files by hand.
5. **Work things out yourself** instead of asking me: the Overleaf projects, the writing period, the co-authors,
   the files, the AI tools used. They all come from the exports.

**A. Ask me only this, in one message**

1. The paper's title, as it appears in Overleaf. (If several of my papers could match, I may give the Overleaf
   link instead.)
2. "Do you agree to share your AI chats from the period you wrote this paper, and the paper's Overleaf history
   (which includes your co-authors' names, edits and comments)? And may short excerpts be quoted in
   publications?" Record the answers in `about.json` → `consent`. If the first answer is no, stop.

At the same time, check `python3 --version` (3.9 or newer) and whether you can control my browser (e.g. Claude
in Chrome). Open overleaf.com, claude.ai and chatgpt.com, and look at whether each shows my account or a login
page; if one is logged out, ask me to log in now. If you can't control a browser, tell me and I'll paste the
scripts myself as `README.md` describes; you do everything else.

After that I may leave. Don't ask me anything else until C unless something is blocked.

**B. Unattended run**

1. `python3 bundle.py init mydata --title "<title>"`, then set the consent answers in `mydata/about.json`.
2. **Overleaf:** on any overleaf.com page run `window.KIT_CONFIG = {title: "<title>"}` (or `{projects: ["<id>"]}`
   if I gave a link), then the whole of `overleaf_export.js`. It finds every project whose name matches the
   title (drafts, copies, later versions) and saves one `overleaf_export.json`. It can take several minutes: if
   your tool call returns first, poll `window.KIT_STATUS` (`state` is `running`, `done` or `error`, with a
   `summary`) every 30 seconds, and never start a script while one is running.
3. `python3 bundle.py fill mydata`. It reads the export and fills in the rest of `about.json`, including the
   window, and prints the `window.KIT_CONFIG` line for the chat exports.
4. **claude.ai:** on https://claude.ai run that `window.KIT_CONFIG` line, then the whole of `claude_ai_export.js`;
   poll the same way. **ChatGPT:** the same on https://chatgpt.com with `chatgpt_export.js`. A site where I have
   no account or no chats is fine: note it and go on. If I'm logged in to another AI chat (Gemini, Copilot,
   Perplexity...), the kit can't export it yet: write its name in `about.json` → `optional.ai_tools_used_for`
   and mention it in the summary.
5. `python3 bundle.py auto mydata`. It collects the downloads, copies my Claude Code / Codex sessions that
   contain the paper's text or title, keeps the chats related to the paper, checks and packs.
   - If an export failed (e.g. missing diffs), run that export once more, then `auto` again.
   - If it reports possible secrets, note the file names (never the secret) for me.
   - Anything else under PROBLEMS: leave it for me with a one-line explanation.

**C. Finish with me**

Tell me the bundle is ready, in a few lines: the projects found, the window, how many chats were kept and
removed, how many agent sessions were included, and any problems. Point me to `mydata_REVIEW.txt` (every chat
kept or removed, with its title) in case I want to look. Apply what I ask through
`auto.chat_filter.always_keep_ids` / `always_drop_ids` in `about.json` and run `auto` again. When I say it's
fine, upload `mydata.zip` to the Duke Box link, or tell me to drag it there if you can't attach files. Then
say what was uploaded and when.
