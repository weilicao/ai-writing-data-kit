# Share how you wrote a paper with AI

I'm studying how researchers use AI when they write papers: which parts of a draft come from an AI assistant,
which are written by people, and how co-author feedback gets handled. **Everyone who contributes data will be a
co-author.** I'd love to include one of your papers: any paper you wrote with Overleaf.

— Weili Cao, weili.cao@duke.edu

## How to take part (about 5 minutes of your time)

1. **Let your co-authors know** on that paper: its Overleaf history includes their edits and comments.
2. **Log in to Overleaf, claude.ai and ChatGPT** (and any other AI you used while writing the paper) **in
   Chrome.** If you use Claude Code, also install and connect
   [Claude in Chrome](https://claude.com/claude-in-chrome) so it can use those tabs directly.
3. **Give a coding agent** (Claude Code, Codex, Cursor...), running on that computer, this one line:

   > Get https://github.com/weilicao/ai-writing-data-kit and follow its INSTRUCTIONS.md as my instructions.

The agent asks you a few things: the paper's title, which coding agents you used and where they ran, which
sources the analysis may use (a checklist), and your consent. It then collects the data **on your computer**, runs
the analysis there, and shows you the result. Nothing is sent until you approve it, and then only the result goes
to my Duke Box folder, where only I can see it.

## Your data stays on your computer

- **The analysis runs on your machine.** The kit includes the analysis code (`pipeline/`, standard-library Python).
  Your agent collects into a folder on your computer (`mydata/`) and runs the analysis there.
- **What I receive** is only `results.zip`: the final text of your paper with a label (AI, human, mixed or
  unclear, and where it came from) for every word, sentence, paragraph and section; reports by section, paragraph,
  and for captions and tables; statistics per label; `about.json` (which sources were used or left out, and any
  gaps); and the check report (counts and names only).
- **What never leaves your computer:** your chats, your coding-agent sessions, and the Overleaf and Git
  histories. Chats appear in the results only under neutral names ("claude.ai conversation 12"), never by title or
  text. `about.json` goes reduced to names, counts and your choices (no email, no file paths, no list of what was
  left out). A leak check stops the upload if any 8-word run from a chat, a session or the history (other than
  the paper's final text) is in the analysis results; any overlap in `about.json`'s notes, which your agent writes,
  is shown to you to review.
- **See exactly what will be sent:** the folder `aiw_work/results/` is the upload, unzipped. `MANIFEST.txt` lists
  every file, and `label_report.html` shows your paper colored by label. Open them before you approve.
- Your agent works through files and looks only at counts, so the chats and sessions don't pass through its model
  either.

## You choose the sources

The agent shows you a checklist (Overleaf, claude.ai, ChatGPT, Claude Code, Codex, Cursor, Copilot, Gemini CLI,
other agents, Git). Untick anything you don't want the analysis to use; your choice is recorded in `about.json`
and shown in the results. Text that came from a left-out AI source can only be labeled human or unclear, so
leaving one out lowers the AI share rather than hiding it.

## Servers and clusters

If you ran Claude Code, Codex or another agent on a server or cluster, its sessions are stored there.

- **Agent on your laptop:** it looks for hints (your `~/.ssh/config`, the Claude desktop app's SSH connections,
  VS Code Remote-SSH) and, after asking you, fetches the session folders over `ssh`, or gives you the command to
  run.
- **Agent on the server:** it can't reach your browser, so it gives you short scripts to paste into the Overleaf,
  claude.ai and ChatGPT tabs on your laptop, and you move the downloaded files to the server (`scp`, or drag them
  into VS Code). The analysis then runs on the server.
- **Several machines:** everything is brought to one of your machines and analysed there. Data moves only between
  your own machines.

If your agent can't control your browser (e.g. Claude Code with Claude in Chrome can), it tells you what to paste
into the browser console instead.

## What is collected (on your computer)

Everything about how **this one paper** was written, from the weeks you wrote it, from the sources you allow:

- **Overleaf:** the full edit history of every project whose name matches the title (drafts, copies, arXiv,
  camera-ready, resubmissions), with who changed what and when. Co-authors' emails are removed.
- **AI chats about the paper:** claude.ai (including Projects and Claude Code on the web) and ChatGPT (including
  Codex cloud tasks).
- **Coding-agent sessions about the paper:** Claude Code, Codex, Cursor, Copilot, Gemini CLI and others, on your
  computer and on any server you used.
- **Git history of the paper's files**, if the project is synced with GitHub or you edited it in a Git repo.

Chats and sessions are kept only if they are about the paper: they share text with it, mention its title, or are
about its LaTeX. Your agent decides this with code, not by reading them. Anything else from those weeks is left
out; only its date and size are recorded. You see the full list before anything is sent, and none of this
collected data is sent: only the analysis result is.

## Questions

- **Free Overleaf plan?** It shows only the last 24 hours of history, and the project owner's plan is what counts.
  Your agent will notice; let me know if it happens.
- **Deleted chats, or old Claude Code sessions?** Claude Code deletes sessions after about 30 days by default.
  Whatever is gone is noted as a gap, which is still useful to know.
- **Something failed?** `mydata/CHECK_REPORT.txt` contains only counts and names, so you can send it to me safely.
- **Can I keep the data?** Yes: `mydata/` and `aiw_work/` stay with you. Keep them if you're happy to answer
  follow-up questions, or delete them.

[INSTRUCTIONS.md](INSTRUCTIONS.md) is what your agent follows, and [pipeline/](pipeline/) is the analysis it runs,
if you want to see exactly what they do.
