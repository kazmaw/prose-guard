"""プラグインの組み立て（hooks.json・マニフェスト・同梱の文章）のテスト。"""

import json
import sys
import unittest

from helpers import HOOKS, ROOT

sys.path.insert(0, str(HOOKS))

import prose_lint as pl  # noqa: E402

CMD = 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh" "${CLAUDE_PLUGIN_ROOT}/hooks/%s"'
EXPECTED = {
    "SessionStart": [("startup|resume|clear|compact", "inject.py")],
    "PreToolUse": [("Bash|mcp__.*", "prose_guard.py")],
    "PostToolUse": [("Write|Edit|MultiEdit|Bash", "prose_guard.py")],
    "PostToolUseFailure": [("Bash", "prose_guard.py")],
    "Stop": [(None, "prose_guard.py")],
}
BUNDLED_PROSE = ("instructions.md", "skills/approval-summary/SKILL.md", "README.md")


def load_json(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class HooksJsonTest(unittest.TestCase):
    def test_events_matchers_and_commands(self):
        hooks = load_json("hooks/hooks.json")["hooks"]
        actual = {ev: [(g.get("matcher"), h["type"], h["command"], h["timeout"])
                       for g in groups for h in g["hooks"]]
                  for ev, groups in hooks.items()}
        expected = {ev: [(m, "command", CMD % s, 10) for m, s in pairs]
                    for ev, pairs in EXPECTED.items()}
        self.assertEqual(actual, expected)

    def test_referenced_scripts_exist(self):
        for name in ("run.sh", "inject.py", "prose_guard.py", "prose_lint.py"):
            self.assertTrue((HOOKS / name).is_file(), name)


class ManifestTest(unittest.TestCase):
    def test_plugin_manifest(self):
        m = load_json(".claude-plugin/plugin.json")
        self.assertEqual((m["name"], m["version"], m["license"]), ("prose-guard", "0.1.0", "MIT"))

    def test_marketplace_lists_repo_root(self):
        m = load_json(".claude-plugin/marketplace.json")
        self.assertEqual(m["name"], "prose-guard")
        self.assertEqual([(p["name"], p["source"]) for p in m["plugins"]], [("prose-guard", ".")])


class BundledProseTest(unittest.TestCase):
    def test_bundled_prose_passes_linter(self):
        for rel in BUNDLED_PROSE:
            issues = pl.lint(read(rel))
            self.assertEqual(pl.count(issues, "block"), 0, pl.format_report(issues, rel))

    def test_instructions_point_to_skill(self):
        self.assertIn("prose-guard:approval-summary", read("instructions.md"))

    def test_skill_is_self_contained(self):
        text = read("skills/approval-summary/SKILL.md")
        self.assertIn("name: approval-summary", text)
        self.assertNotIn("verification-strategy", text)
        self.assertNotIn("CLAUDE.md", text)


if __name__ == "__main__":
    unittest.main()
