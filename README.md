# ClaudeLean

ClaudeLean is a [Claude Code](https://code.claude.com) skill that cuts the tokens agentic coding wastes on both sides of the conversation.

- **Input.** While solving a problem, Claude runs Read, Grep, ls and shell commands. Every file and log it pulls in stays in the context and is re-sent with every later request. Reading twenty whole files to find one bug fills the context with hundreds of thousands of tokens.
- **Output.** Output tokens cost about five times as much as input tokens. Long explanations of why a solution was chosen, unrequested alternatives, walkthroughs of how the code works, visible self-correction, and reprinted imports and unchanged classes all spend them on text you did not need.

ClaudeLean gives Claude rules for both sides, and three small hooks that enforce or remind where rules alone slip.

## What it does

| Part | What happens |
|---|---|
| Rules (`SKILL.md`) | Locate with Grep before reading and read ranges, not whole files. Follow the call chain from the error, not twenty files. Don't re-read unchanged files. Cap command output; give wide searches to a subagent. Lead with the result plus at most 3 short lines. Explain, compare or walk through only when asked. Keep self-correction internal. Use Edit for existing files and show diffs in chat; never reprint unchanged imports, types or classes. Your explicit request always wins. |
| Read guard (`PreToolUse`, `Read`) | Blocks a whole-file read of a text file over 400 lines that has no `limit`. The message tells Claude to Grep for the part it needs and read that range, or to call Read again with an explicit limit if it really needs everything. Images, PDFs, notebooks and binaries pass. |
| Write note (`PreToolUse`, `Write`) | When a Write repeats most of an existing file (60%+ of its lines, files of 80+ lines), Claude gets a note to use Edit next time. The write still happens: blocking it would only spend the tokens twice. |
| Reply note (`UserPromptSubmit`) | If Claude's last reply had more than 250 words of prose or printed more than 80 lines of code in chat, it gets a one-line reminder with the next prompt. It stays silent when you asked why or for an explanation (neden, açıkla, anlat, why, explain…). |
| `lean_report.py` | From your transcripts: tool output that entered the context, per tool; whole-file and repeated reads, and the biggest ones; output tokens per request; prose words per reply; Writes that repeated most of an existing file. It only prints; it changes nothing. |

The hooks print nothing when there is nothing to say, so they add no tokens then.

## Install

```bash
git clone https://github.com/omer-faruk-g/ClaudeLean ~/.claude/skills/claudelean
```

Then add the hooks to `~/.claude/settings.json`. Merge them with any hooks you already have. If `python` is not on your PATH, use `python3` or the interpreter's full path; if `~` is not expanded on your system, write the script's full path.

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Read|Write", "hooks": [ { "type": "command", "command": "python ~/.claude/skills/claudelean/scripts/lean_hook.py pre", "timeout": 5 } ] }
    ],
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command", "command": "python ~/.claude/skills/claudelean/scripts/lean_hook.py prompt", "timeout": 5 } ] }
    ]
  }
}
```

Optional settings in `~/.claude/claudelean.json` (defaults shown):

```json
{ "read_max_lines": 400, "write_min_lines": 80, "write_same_pct": 60, "prose_words": 250, "code_lines": 80 }
```

Measure before and after:

```bash
python ~/.claude/skills/claudelean/scripts/lean_report.py --since 2026-10-01
```

Tests: `python ~/.claude/skills/claudelean/tests/test_claudelean.py` (16 tests, stdlib only).

## Example (one user's transcripts, 1,208 requests over 11 days)

- Tool output that entered the context: Bash 47%, Read 40%, everything else 13%.
- 129 reads: 23 without a limit, 7 repeated the same file and range in a session, and the biggest single read was 43K characters (~11K tokens).
- Prose per reply: median 52 words, but p90 619. 29 of 91 replies were over 250 words.
- Writes that repeated most of an existing file: 0 (against 294 Edit calls). For this user that rule changes nothing; the report shows where your own waste is.

Run `lean_report.py` on your own transcripts; your numbers will differ.

## Limits

- The Read guard costs one extra round trip when Claude really needs the whole file.
- Bash output, the largest share above, is not trimmed. The rules ask for capped output; a dedicated output filter is the place to enforce that.
- The reply note arrives one turn late: it cannot shorten a reply that is already written. The output rules are instructions, and Claude can still be verbose. Thinking tokens are not affected.
- On Windows each hook call adds about 0.3 s, mostly Python start-up: once per prompt, and once per Read or Write call.
- 400 lines, 250 words and 80 code lines are reasonable defaults, not measured optima.
- `lean_report.py` reads transcript fields (`isMeta`, `toolUseResult.originalFile`, …) that are observed, not documented, and may change.
- Nothing is changed in your settings without you: the hooks are added by hand.

## Background

From the Claude Code docs:

- [Manage costs effectively](https://code.claude.com/docs/en/costs): token cost grows with context size, and everything Claude reads stays in the context.
- [Hooks](https://code.claude.com/docs/en/hooks): `PreToolUse` can deny a tool call with a reason Claude sees, or add `additionalContext` next to the tool result; `UserPromptSubmit` can add `additionalContext`; the Read tool's input has `file_path`, `offset` and `limit`.

## Türkçe özet

ClaudeLean, ajan modunda boşa giden tokeni iki yönden keser:

- **Girdi:** Claude bir hatayı çözerken dosya okur, grep yapar, komut çalıştırır. Okunan her dosya ve log bağlamda kalır ve sonraki her istekte yeniden gönderilir. ClaudeLean önce Grep ile yeri bulup yalnızca o aralığı okutur. 400 satırdan büyük bir dosyanın tamamının `limit` olmadan okunmasını hook ile engeller.
- **Çıktı:** Çıktı tokeni girdinin yaklaşık 5 katı fiyatlı. Sorulmadıkça "neden bu çözüm", alternatifler, kodun nasıl çalıştığı anlatılmaz ve kendi kendini düzeltme yazıya dökülmez. Değişmeyen import'lar ve sınıflar yeniden yazılmaz; mevcut dosyalar Edit ile değiştirilir, sohbette yalnızca diff gösterilir.
- Uzun bir yanıttan sonra (250+ kelime düz yazı ya da 80+ satır kod) bir sonraki mesajda Claude'a tek satırlık bir hatırlatma gider. "Neden", "açıkla" gibi sorularda sessiz kalır.
- `lean_report.py` gerçek kullanımını ölçer: hangi aracın bağlama ne kadar çıktı soktuğunu, en büyük ve tekrarlanan okumaları, istek başına çıktı tokenini ve yanıt uzunluklarını gösterir.

Kurulum için yukarıdaki `git clone` komutunu çalıştır ve hook'ları `~/.claude/settings.json`'a ekle.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
