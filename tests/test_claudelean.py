# SPDX-License-Identifier: GPL-3.0-or-later
"""ClaudeLean tests (stdlib unittest). Run: python tests/test_claudelean.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, SCRIPTS)

import lean_hook  # noqa: E402
import lean_report  # noqa: E402

CFG = dict(lean_hook.DEFAULTS)
NL = chr(10)


def prompt(text, uuid, ts="2026-10-20T10:00:00Z", **flags):
    return dict({"type": "user", "uuid": uuid, "timestamp": ts, "sessionId": "s1",
                 "message": {"role": "user", "content": text}}, **flags)


def reply(text, uuid, mid=None, out=50, ts="2026-10-20T10:00:01Z"):
    return {"type": "assistant", "uuid": uuid, "timestamp": ts, "sessionId": "s1",
            "message": {"id": mid or uuid, "content": [{"type": "text", "text": text}],
                        "usage": {"input_tokens": 5, "cache_read_input_tokens": 1000, "output_tokens": out}}}


def tool_use(name, inp, tid, uuid, ts="2026-10-20T10:00:01Z"):
    return {"type": "assistant", "uuid": uuid, "timestamp": ts, "sessionId": "s1",
            "message": {"id": "m-" + uuid, "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}],
                        "usage": {"input_tokens": 5, "cache_read_input_tokens": 1000, "output_tokens": 20}}}


def tool_result(tid, content, uuid, result=None, ts="2026-10-20T10:00:02Z"):
    d = {"type": "user", "uuid": uuid, "timestamp": ts, "sessionId": "s1",
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": content}]}}
    if result:
        d["toolUseResult"] = result
    return d


class Base(unittest.TestCase):
    def setUp(self):
        # Under home, not %TEMP%: Store Python on Windows virtualizes AppData writes.
        self.home = tempfile.mkdtemp(prefix=".claudelean-test-", dir=os.path.expanduser("~"))

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def file(self, name, lines, text="line {}"):
        p = os.path.join(self.home, name)
        with open(p, "w", encoding="utf-8", newline=NL) as f:
            f.write(NL.join(text.format(i) for i in range(lines)) + NL)
        return p

    def transcript(self, entries, name="t.jsonl"):
        p = os.path.join(self.home, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline=NL) as f:
            for e in entries:
                f.write(json.dumps(e) + NL)
        return p


class TestReadGuard(Base):
    def test_small_file_passes(self):
        self.assertIsNone(lean_hook.read_check({"file_path": self.file("a.py", 300)}, CFG))

    def test_large_whole_read_is_blocked(self):
        reason = lean_hook.read_check({"file_path": self.file("big.py", 900)}, CFG)
        self.assertIn("big.py has 900 lines", reason)
        self.assertIn("Grep", reason)
        self.assertIn("explicit limit", reason)

    def test_limit_or_late_offset_passes(self):
        p = self.file("big.py", 900)
        self.assertIsNone(lean_hook.read_check({"file_path": p, "limit": 2000}, CFG))
        self.assertIsNone(lean_hook.read_check({"file_path": p, "offset": 700}, CFG))
        self.assertIsNotNone(lean_hook.read_check({"file_path": p, "offset": "x"}, CFG))

    def test_images_binary_and_missing_pass(self):
        self.assertIsNone(lean_hook.read_check({"file_path": self.file("shot.png", 900)}, CFG))
        p = os.path.join(self.home, "blob.bin")
        with open(p, "wb") as f:
            f.write(b"\0\1" + b"x\n" * 1000)
        self.assertIsNone(lean_hook.read_check({"file_path": p}, CFG))
        self.assertIsNone(lean_hook.read_check({"file_path": os.path.join(self.home, "nope.py")}, CFG))
        self.assertIsNone(lean_hook.read_check({}, CFG))

    def test_pre_event_shape(self):
        out = lean_hook.pre_event({"tool_name": "Read", "tool_input": {"file_path": self.file("b.py", 900)}}, CFG)
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertEqual(out["hookEventName"], "PreToolUse")
        self.assertIsNone(lean_hook.pre_event({"tool_name": "Bash", "tool_input": {"command": "ls"}}, CFG))


class TestWriteNote(Base):
    def test_mostly_unchanged_rewrite(self):
        p = self.file("mod.py", 200)
        new = NL.join(f"line {i}" for i in range(200)).replace("line 5" + NL, "changed" + NL)
        out = lean_hook.pre_event({"tool_name": "Write", "tool_input": {"file_path": p, "content": new}}, CFG)
        self.assertNotIn("permissionDecision", out)  # the write goes ahead
        self.assertIn("unchanged", out["additionalContext"])
        self.assertIn("Edit", out["additionalContext"])

    def test_new_small_or_real_rewrite_is_silent(self):
        self.assertIsNone(lean_hook.write_check({"file_path": os.path.join(self.home, "new.py"), "content": "x"}, CFG))
        small = self.file("small.py", 30)
        self.assertIsNone(lean_hook.write_check({"file_path": small, "content": "line 1"}, CFG))
        big = self.file("big.py", 200)
        other = NL.join(f"other {i}" for i in range(200))
        self.assertIsNone(lean_hook.write_check({"file_path": big, "content": other}, CFG))


class TestPromptNote(Base):
    def long_prose(self, n=400):
        return " ".join(["word"] * n)

    def test_long_prose_gets_a_note(self):
        tp = self.transcript([prompt("fix the bug", "u1"), reply(self.long_prose(), "a1"),
                              prompt("next thing", "u2")])  # the current prompt may already be written
        note = lean_hook.prompt_event({"transcript_path": tp, "prompt": "next thing"}, CFG)
        self.assertIn("400 words of prose", note)

    def test_asked_for_explanation_is_silent(self):
        tp = self.transcript([prompt("bunu neden böyle yaptın?", "u1"), reply(self.long_prose(), "a1")])
        self.assertIsNone(lean_hook.prompt_event({"transcript_path": tp, "prompt": "tamam"}, CFG))
        tp = self.transcript([prompt("fix it", "u1"), reply(self.long_prose(), "a1")], name="t2.jsonl")
        self.assertIsNone(lean_hook.prompt_event({"transcript_path": tp, "prompt": "explain that"}, CFG))

    def test_code_in_chat_counts_separately(self):
        code = "```python" + NL + NL.join(f"x{i} = {i}" for i in range(120)) + NL + "```"
        tp = self.transcript([prompt("fix", "u1"), reply("Done." + NL + code, "a1")])
        note = lean_hook.prompt_event({"transcript_path": tp}, CFG)
        self.assertIn("120 lines of code", note)
        self.assertNotIn("prose", note)

    def test_whole_turn_is_measured_and_short_replies_pass(self):
        half = self.long_prose(150)
        tp = self.transcript([prompt("old", "u0"), reply(self.long_prose(900), "a0"), prompt("fix", "u1"),
                              reply(half, "a1"), tool_use("Bash", {"command": "ls"}, "t1", "a2"),
                              tool_result("t1", "ok", "r1"), reply(half, "a3")])
        self.assertIn("300 words", lean_hook.prompt_event({"transcript_path": tp}, CFG))
        tp = self.transcript([prompt("fix", "u1"), reply("Fixed in a.py:12. Tests pass.", "a1")], name="t3.jsonl")
        self.assertIsNone(lean_hook.prompt_event({"transcript_path": tp}, CFG))
        self.assertIsNone(lean_hook.prompt_event({"transcript_path": os.path.join(self.home, "none")}, CFG))


class TestProcess(Base):
    def run_hook(self, mode, stdin):
        env = dict(os.environ, CLAUDELEAN_HOME=self.home)
        return subprocess.run([sys.executable, os.path.join(SCRIPTS, "lean_hook.py"), mode], input=stdin,
                              capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)

    def test_bad_input_never_blocks(self):
        r = self.run_hook("pre", "{not json")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertIn("claudelean pre", r.stderr)

    def test_json_shapes(self):
        r = self.run_hook("pre", json.dumps({"tool_name": "Read", "tool_input": {"file_path": self.file("b.py", 900)}}))
        self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        tp = self.transcript([prompt("fix", "u1"), reply(" ".join(["w"] * 400), "a1")])
        r = self.run_hook("prompt", json.dumps({"transcript_path": tp, "prompt": "go"}))
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "UserPromptSubmit")
        self.assertIn("ClaudeLean", out["additionalContext"])
        r = self.run_hook("prompt", json.dumps({"transcript_path": os.path.join(self.home, "none")}))
        self.assertEqual(r.stdout, "")

    def test_config(self):
        os.makedirs(os.path.join(self.home, ".claude"))
        with open(os.path.join(self.home, ".claude", "claudelean.json"), "w") as f:
            json.dump({"read_max_lines": 1000, "prose_words": True, "code_lines": -1, "bogus": 2}, f)
        cfg = lean_hook.load_config(self.home)
        self.assertEqual(cfg["read_max_lines"], 1000)
        self.assertEqual(cfg["prose_words"], 250)
        self.assertEqual(cfg["code_lines"], 80)
        self.assertNotIn("bogus", cfg)
        r = self.run_hook("pre", json.dumps({"tool_name": "Read", "tool_input": {"file_path": self.file("c.py", 900)}}))
        self.assertEqual(r.stdout, "")  # 900 lines is under the configured 1000


class TestReport(Base):
    def test_scan(self):
        old = NL.join(f"line {i}" for i in range(100))
        entries = [
            prompt("fix the bug", "u1"),
            tool_use("Read", {"file_path": "/a.py"}, "t1", "a1"),
            tool_result("t1", "x" * 5000, "r1"),
            tool_use("Read", {"file_path": "/a.py"}, "t2", "a2"),
            tool_result("t2", "x" * 5000, "r2"),
            tool_use("Read", {"file_path": "/b.py", "limit": 50}, "t3", "a3"),
            tool_result("t3", [{"type": "text", "text": "y" * 300}], "r3"),
            tool_use("Bash", {"command": "pytest"}, "t4", "a4"),
            tool_result("t4", "z" * 2000, "r4"),
            tool_use("Write", {"file_path": "/a.py", "content": old + NL + "new"}, "t5", "a5"),
            tool_result("t5", "ok", "r5", {"type": "update", "originalFile": old}),
            reply(" ".join(["w"] * 300), "a6"),
            prompt("thanks", "u2"),
            reply("ok", "a7"),
            prompt("old one", "u0", ts="2026-09-01T00:00:00Z"),
        ]
        a = self.transcript(entries, name=os.path.join("p", "C--x", "s1.jsonl"))
        b = os.path.join(self.home, "p", "C--moved", "s1.jsonl")
        os.makedirs(os.path.dirname(b))
        shutil.copy(a, b)  # a moved session's copied transcript must not count twice
        r = lean_report.scan(sorted([a, b]), since="2026-10-01")
        self.assertEqual(r["requests"], 7)
        self.assertEqual(r["reads"]["calls"], 3)
        self.assertEqual(r["reads"]["whole_file"], 2)
        self.assertEqual(r["reads"]["repeated"], 1)
        self.assertEqual(r["tool_output_chars"]["Read"], 10300)
        self.assertEqual(r["tool_output_chars"]["Bash"], 2000)
        self.assertEqual(r["mostly_unchanged_rewrites"]["count"], 1)
        self.assertEqual(r["reply_prose_words"]["replies"], 2)
        self.assertEqual(r["reply_prose_words"]["over_limit"], 1)

    def test_cli_runs(self):
        root = os.path.join(self.home, "projects")
        self.transcript([prompt("hi", "u1"), reply("hello", "a1")], name=os.path.join("projects", "C--x", "s.jsonl"))
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "lean_report.py"), "--projects", root],
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 requests", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=1)
