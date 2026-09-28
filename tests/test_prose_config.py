"""prose_guard.load_config のテスト。

設定の不具合で作業を止めない。読めないときは警告を出して既定値で動く。
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

import prose_guard as g  # noqa: E402

EMPTY = {"watch_dirs": [], "skip_globs": [], "post_tools": set(), "replacing_tools": set()}


class LoadConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "prose-guard.json"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def load(self, text=None, **values):
        if text is None and values:
            text = json.dumps(values)
        if text is not None:
            self.path.write_text(text, encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            cfg = g.load_config(str(self.path))
        return cfg, err.getvalue()

    def test_missing_file_uses_defaults_silently(self):
        cfg, err = self.load()
        self.assertEqual(cfg, EMPTY)
        self.assertEqual(err, "")

    def test_broken_json_warns_and_uses_defaults(self):
        cfg, err = self.load("{")
        self.assertEqual(cfg, EMPTY)
        self.assertIn("prose-guard.json", err)

    def test_top_level_not_object_warns(self):
        for text in ("[]", "null"):
            cfg, err = self.load(text)
            self.assertEqual(cfg, EMPTY, text)
            self.assertNotEqual(err, "", text)

    def test_wrong_type_resets_only_that_key(self):
        cfg, err = self.load(watch_dirs="~/notes", post_tools=["slack_send_message"])
        self.assertEqual(cfg["watch_dirs"], [])
        self.assertEqual(cfg["post_tools"], {"slack_send_message"})
        self.assertIn("watch_dirs", err)

    def test_non_string_item_resets_key(self):
        cfg, err = self.load(skip_globs=["*.md", 1])
        self.assertEqual(cfg["skip_globs"], [])
        self.assertIn("skip_globs", err)

    def test_unknown_key_is_ignored(self):
        cfg, err = self.load(unknown=1)
        self.assertEqual(cfg, EMPTY)
        self.assertEqual(err, "")

    def test_watch_dirs_are_expanded(self):
        with mock.patch.dict(os.environ, {"HOME": str(self.tmp)}):
            cfg, _ = self.load(watch_dirs=["~/notes/", "~/a/../b"])
        self.assertEqual(cfg["watch_dirs"], [str(self.tmp / "notes"), str(self.tmp / "b")])

    def test_empty_string_watch_dir_is_dropped(self):
        cfg, _ = self.load(watch_dirs=["", "  "])
        self.assertEqual(cfg["watch_dirs"], [])

    def test_watch_dir_with_surrounding_whitespace_is_stripped(self):
        with mock.patch.dict(os.environ, {"HOME": str(self.tmp)}):
            cfg, _ = self.load(watch_dirs=[" ~/notes "])
        self.assertEqual(cfg["watch_dirs"], [str(self.tmp / "notes")])

    def test_relative_watch_dir_is_dropped_with_warning(self):
        cfg, err = self.load(watch_dirs=["notes"])
        self.assertEqual(cfg["watch_dirs"], [])
        self.assertIn("watch_dirs", err)

    def test_full_mcp_name_normalized(self):
        cfg, _ = self.load(post_tools=["mcp__claude_ai_Slack__slack_send_message"],
                           replacing_tools=["mcp__x__slack_send_message"])
        self.assertEqual(cfg["post_tools"], {"slack_send_message"})
        self.assertEqual(cfg["replacing_tools"], {"slack_send_message"})

    def test_replacing_limited_to_post_tools(self):
        cfg, _ = self.load(post_tools=["clickup_update_task"],
                           replacing_tools=["clickup_update_task", "clickup_update_comment"])
        self.assertEqual(cfg["replacing_tools"], {"clickup_update_task"})


if __name__ == "__main__":
    unittest.main()
