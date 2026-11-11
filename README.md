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

The agent asks you three things: the paper's title, which coding agents you used, and your consent. It then
collects the data, checks it, and shows you a summary. Nothing is sent until you approve it, and then it goes
only to my Duke Box folder, where only I can see it.

If your agent can't control your browser (e.g. Claude Code with Claude in Chrome can), it tells you what to paste
into the browser console instead.

## What is collected

Everything about how **this one paper** was written, from the weeks you wrote it:

- **Overleaf:** the full edit history of every project whose name matches the title (drafts, copies, arXiv,
  camera-ready, resubmissions), with who changed what and when. Co-authors' emails are removed.
- **AI chats about the paper:** claude.ai (including Projects and Claude Code on the web) and ChatGPT (including
  Codex cloud tasks).
- **Coding-agent sessions about the paper:** Claude Code, Codex, Cursor, Copilot, Gemini CLI and others, on your
  computer and on any server you used.
- **Git history of the paper's files**, if the project is synced with GitHub or you edited it in a Git repo.

Chats and sessions are kept only if they are about the paper: they share text with it, mention its title, or are
about its LaTeX. Your agent decides this with code, not by reading them. Anything else from those weeks is left
out; only its date and size are recorded. You see the full list before anything is sent.

## Questions

- **Free Overleaf plan?** It shows only the last 24 hours of history, and the project owner's plan is what counts.
  Your agent will notice; let me know if it happens.
- **Deleted chats, or old Claude Code sessions?** Claude Code deletes sessions after about 30 days by default.
  Whatever is gone is noted as a gap, which is still useful to know.
- **Something failed?** `mydata/CHECK_REPORT.txt` contains only counts and names, so you can send it to me safely.

[INSTRUCTIONS.md](INSTRUCTIONS.md) is what your agent follows, if you want to see exactly what it does.
