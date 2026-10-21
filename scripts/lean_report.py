#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""ClaudeLean report: where agentic work spends tokens, measured from your Claude Code transcripts.

Input side: characters of tool output (Read, Bash, Grep, ...) that entered the main conversation,
and the biggest reads. Output side: output tokens per request, prose words per reply, and Write
calls that rewrote an existing file mostly unchanged. Prints only; changes nothing.

  lean_report.py [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--projects DIR] [--top N] [--json]
"""
import argparse
import glob
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lean_hook import home_dir, is_prompt, load_config, long_path, measure, rewrite_note  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else 0


def result_chars(c):
    if isinstance(c, str):
        return len(c)
    if isinstance(c, list):
        return sum(len(b.get("text", "")) for b in c if isinstance(b, dict) and b.get("type") == "text")
    return 0


def scan(paths, since="", until="", cfg=None):
    cfg = cfg or load_config()
    out, words = [], []
    tool_chars, tool_calls = Counter(), Counter()
    reads, whole_reads, rewrites, rewrite_chars, edits = [], 0, 0, 0, 0
    read_keys, repeat_reads = set(), 0
    # Global, not per file: when the app moves a session's folder, its transcript is copied, uuids and all.
    seen, uses, entries = set(), {}, set()
    for p in paths:
        turn = None
        try:
            f = open(long_path(p), encoding="utf-8", errors="replace")
        except OSError:
            continue
        with f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(d, dict) or d.get("isSidechain") or d.get("uuid") in entries:
                    continue
                if d.get("uuid"):
                    entries.add(d["uuid"])
                ts = d.get("timestamp") or ""
                inside = (not since or ts >= since) and (not until or ts < until)
                if d.get("type") == "assistant":
                    m = d.get("message") or {}
                    for b in m.get("content") or []:
                        if not isinstance(b, dict):
                            continue
                        if b.get("type") == "tool_use":
                            ti = b.get("input") or {}
                            uses[b.get("id")] = (b.get("name"), ti)
                            if inside:
                                tool_calls[b.get("name")] += 1
                                whole_reads += b.get("name") == "Read" and not ti.get("limit")
                                if b.get("name") == "Read":
                                    key = (d.get("sessionId"), ti.get("file_path"), ti.get("offset"), ti.get("limit"))
                                    repeat_reads += key in read_keys
                                    read_keys.add(key)
                                edits += b.get("name") == "Edit"
                        elif b.get("type") == "text" and turn is not None:
                            turn += measure(b.get("text", ""))[0]
                    u = m.get("usage")
                    mid = m.get("id") or d.get("uuid")
                    if inside and u and mid not in seen and (u.get("input_tokens") or u.get("cache_read_input_tokens")):
                        seen.add(mid)
                        out.append(u.get("output_tokens") or 0)
                elif is_prompt(d):
                    if turn is not None:
                        words.append(turn)
                    turn = 0 if inside else None
                elif d.get("type") == "user" and inside:
                    for b in (d.get("message") or {}).get("content") or []:
                        if not isinstance(b, dict) or b.get("type") != "tool_result":
                            continue
                        name, ti = uses.get(b.get("tool_use_id"), ("?", {}))
                        n = result_chars(b.get("content"))
                        tool_chars[name] += n
                        if name == "Read":
                            reads.append((n, ti.get("file_path", "?")))
                        r = d.get("toolUseResult")
                        if name == "Write" and isinstance(r, dict) and r.get("type") == "update":
                            # the transcript keeps the file as it was before the Write
                            if rewrite_note(r.get("originalFile") or "", ti.get("content"), "", cfg):
                                rewrites += 1
                                rewrite_chars += len(ti.get("content") or "")
        if turn is not None:
            words.append(turn)
    reads.sort(reverse=True)
    return {
        "requests": len(out),
        "output_tokens": {"total": sum(out), "median": pct(out, .5), "p90": pct(out, .9)},
        "reply_prose_words": {"replies": len(words), "median": pct(words, .5), "p90": pct(words, .9),
                              "over_limit": sum(w > cfg["prose_words"] for w in words)},
        "tool_output_chars": dict(tool_chars.most_common()),
        "tool_calls": dict(tool_calls.most_common()),
        "reads": {"calls": len(reads), "whole_file": whole_reads, "repeated": repeat_reads,
                  "chars": sum(n for n, _ in reads), "biggest": reads[:10]},
        "mostly_unchanged_rewrites": {"count": rewrites, "chars": rewrite_chars},
        "edits": edits,
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--since", default="", help="first day, YYYY-MM-DD (by request time, UTC)")
    p.add_argument("--until", default="", help="day after the last, YYYY-MM-DD")
    p.add_argument("--projects", default=os.path.join(home_dir(), ".claude", "projects"))
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    r = scan(sorted(glob.glob(os.path.join(a.projects, "*", "*.jsonl"))), a.since, a.until)
    if a.json:
        print(json.dumps(r, indent=1, ensure_ascii=False))
        return 0
    k = lambda n: f"{n / 1000:.0f}K"  # noqa: E731
    o, w, rd, rw = r["output_tokens"], r["reply_prose_words"], r["reads"], r["mostly_unchanged_rewrites"]
    print(f"ClaudeLean report {a.since or 'start'} .. {a.until or 'now'}: {r['requests']} requests (main conversation)")
    print(f"Output tokens: total {k(o['total'])}, per request median {o['median']}, p90 {o['p90']}")
    print(f"Prose per reply (words): median {w['median']}, p90 {w['p90']}, over the limit {w['over_limit']} of {w['replies']}")
    total = sum(r["tool_output_chars"].values()) or 1
    print("Tool output that entered the context (chars, ~4 per token):")
    for name, n in list(r["tool_output_chars"].items())[:8]:
        print(f"  {name:<12} {k(n):>6}  {100 * n / total:4.0f}%  ({r['tool_calls'].get(name, 0)} calls)")
    print(f"Reads: {rd['calls']}, without a limit {rd['whole_file']}, same file and range again in a session "
          f"{rd['repeated']} (some follow edits), {k(rd['chars'])} chars in total. Biggest:")
    for n, path in rd["biggest"][:a.top]:
        print(f"  {k(n):>6}  {path}")
    print(f"Writes that repeated most of an existing file: {rw['count']} ({k(rw['chars'])} chars); Edit calls: {r['edits']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
