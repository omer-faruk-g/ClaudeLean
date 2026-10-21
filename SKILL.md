---
name: claudelean
description: "ClaudeLean: cuts the tokens agentic coding wastes. Input: Grep before Read, read ranges instead of whole files, no re-reads, capped command output, subagents for wide searches. Output: lead with the result; no essays, alternatives, walkthroughs or visible self-correction unless asked; Edit and diffs only, never reprinted imports or unchanged code. Use on every coding, debugging and file-exploration task, when a ClaudeLean hook message appears, or when the user says claudelean, kısa, uzatma."
---

# ClaudeLean: less in, less out

Tool output stays in the context and is re-sent with every later request. Output tokens cost about five times as much as input tokens. Spend both on the result, not on bulk.

## 1. Input: what enters the context
- Locate before reading: Grep with `-n` and a few context lines, or Glob. Then Read only the range you need (offset/limit).
- One bug is not twenty files. Start at the error (stack trace, failing test, file:line) and follow the call chain; widen only when the trail ends.
- Do not re-read a file that has not changed since you read it.
- Searches across many files or unfamiliar code: propose a subagent, so only its summary enters this context.
- Command output: quiet flags, `| tail -n 40`, failures-only test runs. Never `cat` a large file or a whole log.
- When the Read guard blocks a whole-file read, do what it says. Pass an explicit `limit` only when you really need the whole file.

## 2. Output: what you write
- Lead with the result: the code change, the finding, the answer. Then at most 3 short lines: what changed, how it was verified, what is left.
- Explain why, compare alternatives or walk through how the code works only when the user asks (neden, açıkla, anlat, why, explain), and then answer just that.
- Self-correction stays internal. If you catch a mistake, fix it and report the final state; do not print the wrong version first or narrate "actually, wait".
- No recap of the plan, the question, or what you just did beyond one line.
- Change existing files with Edit, touching only the lines that change. Use Write for new files and real rewrites.
- In chat, show only the changed lines or a diff with about 3 lines of context. Never reprint unchanged imports, dependencies, type definitions, constants or classes.
- Do not add types, constants, defensive checks or boilerplate the task did not ask for.
- The user's explicit request wins: if they ask for a whole file or a detailed explanation, give it.

## 3. Hooks and measuring
- Read guard (PreToolUse): blocks whole-file reads of text files over 400 lines that have no `limit`.
- Write note (PreToolUse): when a Write repeats most of an existing file, a note says to use Edit next time. The write still happens.
- Reply note (UserPromptSubmit): after a reply with more than 250 words of prose or 80 lines of code in chat, a one-line reminder.
- `python "${CLAUDE_SKILL_DIR}/scripts/lean_report.py" --since YYYY-MM-DD` shows, from the transcripts, tool output per tool, the biggest and repeated reads, output tokens per request, prose per reply and mostly-unchanged rewrites. Report it as measured X -> Y and never promise percentages.

Change settings.json hooks only after showing a diff and getting the user's explicit approval.
