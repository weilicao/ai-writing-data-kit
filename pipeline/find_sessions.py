"""Where coding-agent sessions are on this machine, and hints that agents also ran on other machines.

    python3 -I find_sessions.py [--since YYYY-MM-DD] [--json out.json]

Prints counts and folder names only (no session content):
  - local session stores per tool (Claude Code, Codex, Gemini CLI, Cursor, VS Code / Copilot, Windsurf, Cline / Roo,
    Continue), with files modified since --since;
  - hints of other machines: hosts in ~/.ssh/config, `ssh-*` folders of the Claude desktop app (its SSH
    connections), VS Code Remote-SSH hosts (settings and recent workspaces), and, on a server, traces that a laptop
    connected here (~/.claude/remote from the Claude desktop app, ~/.vscode-server, ~/.cursor-server);
  - for each host, the command that fetches its session folders as an archive (run it only after asking).
"""
import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime

HOME = os.path.expanduser("~")
CONFIG = {"darwin": os.path.join(HOME, "Library", "Application Support"),
          "win32": os.environ.get("APPDATA", os.path.join(HOME, "AppData", "Roaming"))}.get(sys.platform,
                                                                                         os.path.join(HOME, ".config"))
STORES = [  # (tool, folder relative to HOME or CONFIG, glob of session files)
    ("claude-code", "~/.claude/projects", "**/*.jsonl"),
    ("codex", "~/.codex/sessions", "**/*.jsonl*"),
    ("codex", "~/.codex/archived_sessions", "**/*.jsonl*"),
    ("gemini-cli", "~/.gemini/tmp", "**/*"),
    ("cursor", "@/Cursor/User", "**/state.vscdb"),
    ("copilot", "@/Code/User/workspaceStorage", "*/chatSessions/*.json"),
    ("copilot", "@/Code - Insiders/User/workspaceStorage", "*/chatSessions/*.json"),
    ("windsurf", "@/Windsurf/User", "**/state.vscdb"),
    ("cline", "@/Code/User/globalStorage/saoudrizwan.claude-dev/tasks", "*/*.json"),
    ("roo-code", "@/Code/User/globalStorage/rooveterinaryinc.roo-cline/tasks", "*/*.json"),
    ("continue", "~/.continue/sessions", "*.json"),
]
FETCH = ("ssh {host} 'cd ~ && find .claude/projects .codex/sessions .codex/archived_sessions .gemini/tmp "
         "-type f -newermt {since} 2>/dev/null | tar czf ~/agent_sessions.tgz -T -' && "
         "scp {host}:agent_sessions.tgz agent_sessions_{name}.tgz && ssh {host} rm ~/agent_sessions.tgz")


def path_of(p):
    return os.path.join(CONFIG, p[2:]) if p.startswith("@/") else os.path.expanduser(p)


def local_stores(since_ts):
    out = []
    for tool, folder, pattern in STORES:
        base = path_of(folder)
        if not os.path.isdir(base):
            continue
        files = [f for f in glob.glob(os.path.join(base, pattern), recursive=True) if os.path.isfile(f)]
        recent = [f for f in files if os.path.getmtime(f) >= since_ts]
        out.append({"tool": tool, "folder": base, "files": len(files), "files_since": len(recent)})
    return out


def ssh_hosts():
    hosts = []
    cfg = os.path.join(HOME, ".ssh", "config")
    if os.path.exists(cfg):
        for line in open(cfg, errors="replace"):
            m = re.match(r"\s*Host\s+(.+)", line, re.I)
            if m:
                hosts += [h for h in m.group(1).split() if not any(c in h for c in "*?!")]
    return hosts


def desktop_app_hosts():
    """The Claude desktop app keeps one `ssh-<host...>` folder per SSH connection it ran Claude Code over."""
    found = []
    roots = [os.path.join(CONFIG, d) for d in ("Claude", "Claude-3p", "claude")] + [os.path.join(HOME, ".claude")]
    for r in roots:
        if os.path.isdir(r):
            for d in glob.glob(os.path.join(r, "**", "ssh-*"), recursive=True):
                if os.path.isdir(d):
                    found.append({"folder": d, "host": re.sub(r"^ssh-", "", os.path.basename(d))})
    return found


def vscode_hosts():
    hosts = set()
    for app in ("Code", "Code - Insiders", "Cursor", "Windsurf"):
        s = os.path.join(CONFIG, app, "User", "settings.json")
        if os.path.exists(s):
            hosts |= set(re.findall(r'"remote\.SSH\.remotePlatform"\s*:\s*\{([^}]*)\}', open(s, errors="replace").read()))
        storage = os.path.join(CONFIG, app, "User", "globalStorage", "storage.json")
        if os.path.exists(storage):
            hosts |= set(re.findall(r"vscode-remote://ssh-remote%2B([^/\"]+)", open(storage, errors="replace").read()))
    out = set()
    for h in hosts:
        out |= set(re.findall(r'"([^"]+)"\s*:', h)) if ":" in h else {h}
    return sorted(out)


def server_traces():
    """On a server: signs that agents were driven from a laptop over SSH (their sessions are stored here)."""
    t = []
    for d, what in (("~/.claude/remote", "Claude desktop app (remote Claude Code)"),
                    ("~/.vscode-server", "VS Code Remote-SSH"), ("~/.cursor-server", "Cursor Remote-SSH")):
        if os.path.isdir(os.path.expanduser(d)):
            t.append({"folder": os.path.expanduser(d), "what": what})
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2000-01-01")
    ap.add_argument("--json")
    a = ap.parse_args()
    since_ts = datetime.fromisoformat(a.since).timestamp()
    hosts = {}
    for h in ssh_hosts():
        hosts.setdefault(h, []).append("~/.ssh/config")
    for d in desktop_app_hosts():
        hosts.setdefault(d["host"], []).append("Claude desktop app " + os.path.basename(d["folder"]))
    for h in vscode_hosts():
        hosts.setdefault(h, []).append("VS Code / Cursor Remote-SSH")
    res = {"machine": os.uname().nodename if hasattr(os, "uname") else os.environ.get("COMPUTERNAME", ""),
           "local_stores": local_stores(since_ts), "server_traces": server_traces(),
           "other_machines": [{"host": h, "hints": v,
                               "fetch_command": FETCH.format(host=h, since=a.since, name=re.sub(r"[^\w.-]", "_", h))}
                              for h, v in sorted(hosts.items())]}
    print(f"machine: {res['machine']}")
    print("local session stores:")
    for s in res["local_stores"]:
        print(f"  {s['tool']:12} {s['files']:6} files, {s['files_since']:6} since {a.since}  {s['folder']}")
    if res["server_traces"]:
        print("this machine was used remotely:", "; ".join(f"{t['what']} ({t['folder']})" for t in res["server_traces"]))
    print("other machines:" if res["other_machines"] else "other machines: no hints found")
    for m in res["other_machines"]:
        print(f"  {m['host']}  ({', '.join(m['hints'])})\n    {m['fetch_command']}")
    if a.json:
        json.dump(res, open(a.json, "w"), indent=1)


if __name__ == "__main__":
    main()
