#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""ClaudeLean hook: keeps bulky tool output out of the context and nudges verbose replies.

  lean_hook.py pre      PreToolUse (Read|Write)
                        Read:  blocks a whole-file read of a large text file (no `limit`) and says how to
                               get the part that matters: Grep -n, then Read with offset/limit
                        Write: when a rewrite of an existing file repeats most of its lines, tells Claude
                               to use Edit next time (the write itself goes ahead)
  lean_hook.py prompt   UserPromptSubmit: if Claude's last reply was long prose or printed a lot of code
                        in chat, reminds it once to lead with the result

Config (optional): ~/.claude/claudelean.json, e.g. {"read_max_lines": 400, "prose_words": 250}.
A hook must never break your session: on any error it writes to stderr and exits 0 silently.
"""
import argparse
import json
import os
import re
import sys
from collections import Counter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

B = chr(92)  # backslash, kept out of literals so the file reads the same everywhere

DEFAULTS = {
    "read_max_lines": 400,   # whole-file reads above this need an explicit `limit`
    "write_min_lines": 80,   # only check rewrites of files at least this long
    "write_same_pct": 60,    # ...that repeat at least this share of the new lines unchanged
    "prose_words": 250,      # reply prose (outside code blocks) above this gets a reminder
    "code_lines": 80,        # code printed in chat above this gets a reminder
}
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".pdf", ".ipynb"}  # Read renders these
BIG_FILE = 4 * 1024 * 1024

ASKED_TO_EXPLAIN = re.compile(r"(?i)\b(neden|niye|niçin|açıkla|anlat|detay|why|explain|describe|elaborate|walk me through)")
FENCE = re.compile(r"```.*?(?:```|\Z)", re.S)

READ_BLOCK = ("ClaudeLean: {name} has {lines} lines (~{tok}K tokens). A whole-file read stays in the context and is "
              "re-sent with every later request. Find the part you need with Grep (-n, a few context lines), then "
              "Read only that range with offset/limit. If you really need the whole file, call Read again with an "
              "explicit limit.")
WRITE_NOTE = ("ClaudeLean: this Write re-sends {same} of {total} lines of {name} unchanged (~{tok}K output tokens). "
              "To change an existing file, use Edit with only the changed lines; keep Write for new files and real "
              "rewrites.")
PROSE_NOTE = ("ClaudeLean: your last reply had {w} words of prose. Unless the user asks why or for an explanation, "
              "lead with the result (code change, finding, answer) and add at most 3 short lines: no alternatives, "
              "no walkthrough of how the code works, no recap, no visible self-correction.")
CODE_NOTE = ("ClaudeLean: your last reply printed {n} lines of code in chat. Show only the changed lines or a diff "
             "with a few lines of context; do not reprint unchanged imports, dependencies, types or classes.")


def home_dir(home=None):
    return home or os.environ.get("CLAUDELEAN_HOME") or os.path.expanduser("~")


def long_path(path):
    """Windows MAX_PATH workaround: Claude Code project dirs can exceed 260 chars."""
    ap = os.path.abspath(path)
    if os.name != "nt" or len(ap) < 250 or ap.startswith(B + B):
        return ap
    return B + B + "?" + B + ap


def load_config(home=None):
    cfg = dict(DEFAULTS)
    try:
        with open(os.path.join(home_dir(home), ".claude", "claudelean.json"), encoding="utf-8") as f:
            user = json.load(f)
    except (OSError, ValueError):
        return cfg
    if isinstance(user, dict):
        for k, v in user.items():
            if k in DEFAULTS and isinstance(v, int) and not isinstance(v, bool) and v >= 0:
                cfg[k] = v
    return cfg


def read_check(ti, cfg):
    """Deny reason for a whole-file read of a large text file, else None."""
    path = ti.get("file_path")
    if not path or ti.get("limit") or os.path.splitext(path)[1].lower() in SKIP_EXT:
        return None
    try:
        size = os.path.getsize(long_path(path))
        lines = None
        if size <= BIG_FILE:
            with open(long_path(path), "rb") as f:
                data = f.read()
            if b"\0" in data[:8192]:
                return None  # binary: let Read decide
            lines = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
    except OSError:
        return None  # missing or unreadable: let Read report it
    try:
        start = max(int(ti.get("offset") or 1), 1)
    except (TypeError, ValueError):
        start = 1
    if lines is not None and lines - start + 1 <= cfg["read_max_lines"]:
        return None
    return READ_BLOCK.format(name=os.path.basename(path), lines=lines if lines is not None else "4MB+ of",
                             tok=round(size / 4000))


def write_check(ti, cfg):
    """Note for a Write that repeats most of an existing file, else None."""
    path, new = ti.get("file_path"), ti.get("content")
    if not path or not isinstance(new, str):
        return None
    try:
        if os.path.getsize(long_path(path)) > BIG_FILE:
            return None
        with open(long_path(path), encoding="utf-8", errors="replace") as f:
            old = f.read()
    except OSError:
        return None  # a new file
    return rewrite_note(old, new, os.path.basename(path), cfg)


def rewrite_note(old, new, name, cfg):
    old_lines = [x.strip() for x in old.splitlines() if x.strip()]
    new_lines = [x.strip() for x in (new or "").splitlines() if x.strip()]
    if len(old_lines) < cfg["write_min_lines"] or not new_lines:
        return None
    same = sum((Counter(old_lines) & Counter(new_lines)).values())
    if same * 100 < cfg["write_same_pct"] * len(new_lines):
        return None
    return WRITE_NOTE.format(same=same, total=len(new_lines), name=name, tok=f"{len(new) / 4000:.1f}")


def pre_event(data, cfg):
    """PreToolUse output dict, or None to stay silent."""
    tool, ti = data.get("tool_name"), data.get("tool_input") or {}
    if tool == "Read":
        reason = read_check(ti, cfg)
        if reason:
            return {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}
    elif tool == "Write":
        note = write_check(ti, cfg)
        if note:
            return {"hookEventName": "PreToolUse", "additionalContext": note}
    return None


def is_prompt(d):
    """A message the user typed (not a tool result, injected text or compaction summary)."""
    if d.get("type") != "user" or d.get("isMeta") or d.get("isCompactSummary"):
        return False
    c = (d.get("message") or {}).get("content")
    if isinstance(c, list):
        return not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c)
    return isinstance(c, str)


def prompt_text(d):
    c = (d.get("message") or {}).get("content")
    if isinstance(c, list):
        return " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return c or ""


def measure(text):
    """(prose words outside code blocks, lines inside code blocks)."""
    code = sum(max(m.group(0).count("\n") - 1, 0) for m in FENCE.finditer(text))
    return len(FENCE.sub(" ", text).split()), code


def last_turn(transcript_path, max_bytes=2 * 1024 * 1024):
    """(Claude's text in its last turn, the prompt that started that turn) from the transcript's end."""
    try:
        f = open(long_path(transcript_path), "rb")
    except (OSError, TypeError, ValueError):
        return "", None
    with f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        raw = f.read().split(b"\n")
    if size > max_bytes:
        raw = raw[1:]  # first line may be cut
    texts, prompt = [], None
    for line in reversed(raw):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict) or d.get("isSidechain"):
            continue
        if d.get("type") == "assistant":
            for b in reversed((d.get("message") or {}).get("content") or []):
                if isinstance(b, dict) and b.get("type") == "text":
                    texts.append(b.get("text", ""))
        elif is_prompt(d) and texts:  # a prompt with no reply after it is the one being sent now
            prompt = prompt_text(d)
            break
    return "\n".join(reversed(texts)), prompt


def prompt_event(data, cfg):
    """additionalContext for UserPromptSubmit, or None."""
    text, prev = last_turn(data.get("transcript_path"))
    if not text:
        return None
    words, code = measure(text)
    asked = ASKED_TO_EXPLAIN.search(prev or "") or ASKED_TO_EXPLAIN.search(data.get("prompt") or "")
    notes = []
    if words > cfg["prose_words"] and not asked:
        notes.append(PROSE_NOTE.format(w=words))
    if code > cfg["code_lines"]:
        notes.append(CODE_NOTE.format(n=code))
    return " ".join(notes) or None


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", choices=["pre", "prompt"])
    a = p.parse_args(argv)
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace") or "{}")
        cfg = load_config()
        if a.mode == "pre":
            out = pre_event(data, cfg)
        else:
            ctx = prompt_event(data, cfg)
            out = ctx and {"hookEventName": "UserPromptSubmit", "additionalContext": ctx}
        if out:
            print(json.dumps({"hookSpecificOutput": out}, ensure_ascii=False))
    except Exception as e:  # a guard must never break the session; report and step aside
        print(f"claudelean {a.mode}: {type(e).__name__}: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
