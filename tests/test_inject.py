"""inject.py（SessionStart の指示注入）と run.sh（Python の解決）のテスト。"""

import json
import shutil
import subprocess
import sys
import unittest

from helpers import HOOKS, ROOT, HookCase

INJECT = str(HOOKS / "inject.py")
RUN = str(HOOKS / "run.sh")


class InjectTest(HookCase):
    def inject(self, script=INJECT):
        return subprocess.run([sys.executable, script], input="{}",
                              capture_output=True, text=True, env=self.env)

    def test_emits_instructions_as_additional_context(self):
        r = self.inject()
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "SessionStart")
        expected = (ROOT / "instructions.md").read_text(encoding="utf-8")
        self.assertEqual(out["additionalContext"], expected)

    def test_flag_disables_injection(self):
        self.disable()
        r = self.inject()
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_missing_instructions_warns_and_exits_zero(self):
        lone = self.tmp / "plugin" / "hooks"
        lone.mkdir(parents=True)
        shutil.copy(INJECT, str(lone / "inject.py"))
        r = self.inject(str(lone / "inject.py"))
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertIn("instructions.md", r.stderr)


class RunShTest(HookCase):
    def run_sh(self, script, stdin):
        return subprocess.run(["sh", RUN, script], input=stdin,
                              capture_output=True, text=True, env=self.env)

    def test_launches_inject(self):
        r = self.run_sh(INJECT, "{}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("hookSpecificOutput", json.loads(r.stdout))

    def test_guard_with_empty_stdin_is_silent(self):
        r = self.run_sh(str(HOOKS / "prose_guard.py"), "")
        self.assertEqual((r.returncode, r.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()
