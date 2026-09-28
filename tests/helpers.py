"""テスト共通の土台。hook を別プロセスで起動し、HOME と設定を一時ディレクトリに閉じ込める。"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / "hooks"
GUARD = str(HOOKS / "prose_guard.py")

LONG = "あ" * 120  # fat_bullet の上限 100 字を超える
BAD = "- %s\n" % LONG
GOOD = "- 短い項目です。\n- もう1つの項目です。\n"
CLEAN_DOC = "# 見出し\n\n" + GOOD

POST_TOOLS = [
    "clickup_create_task_comment",
    "clickup_create_document_page",
    "clickup_update_task",
    "slack_send_message",
]


class HookCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.notes = self.home / "my notes"  # 空白入りのパスも常に通す
        (self.notes / "team" / "specs").mkdir(parents=True)
        self.snap = self.tmp / "snap"
        self.config_path = self.tmp / "prose-guard.json"
        self.configure(watch_dirs=[str(self.notes)], post_tools=POST_TOOLS,
                       replacing_tools=["clickup_update_task"])
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("PROSE_", "CLAUDE_PLUGIN_"))}
        self.env.update(HOME=str(self.home), PROSE_GUARD_CONFIG=str(self.config_path),
                        PROSE_SNAPSHOT_DIR=str(self.snap), NOTES_ROOT=str(self.notes))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def configure(self, **values):
        self.config_path.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")

    def disable(self):
        flag = self.home / ".claude" / ".prose-guard-off"
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()

    def hook(self, payload, script=GUARD):
        payload.setdefault("tool_use_id", "toolu_1")
        payload.setdefault("cwd", str(self.tmp))
        return subprocess.run([sys.executable, script], input=json.dumps(payload),
                              capture_output=True, text=True, env=self.env)

    def write_tool(self, path, content):
        return self.hook({"hook_event_name": "PostToolUse", "tool_name": "Write",
                          "tool_input": {"file_path": str(path), "content": content}})

    def mcp(self, tool, tool_input):
        return self.hook({"hook_event_name": "PreToolUse", "tool_name": tool,
                          "tool_input": tool_input})

    def gh(self, command):
        return self.bash_hook("PreToolUse", command)

    def bash_hook(self, event, command, tool_use_id="toolu_1"):
        return self.hook({"hook_event_name": event, "tool_name": "Bash",
                          "tool_input": {"command": command}, "tool_use_id": tool_use_id})

    def run_bash(self, command, tool_use_id="toolu_1"):
        """実際の Bash と同じ順序で、実行前 hook → コマンド → 実行後 hook を回す。"""
        pre = self.bash_hook("PreToolUse", command, tool_use_id)
        self.assertEqual(pre.returncode, 0, pre.stderr)
        self.assertEqual(pre.stdout, "", "PreToolUse は stdout に何も出さない")
        subprocess.run(["sh", "-c", command], check=True, cwd=str(self.tmp), env=self.env)
        return self.bash_hook("PostToolUse", command, tool_use_id)
