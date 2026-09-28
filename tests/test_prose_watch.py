"""prose_guard.is_watched のテスト。"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

import prose_guard as g  # noqa: E402

DIGEST = "*/maps/取り込みダイジェスト.md"


class IsWatchedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.notes = self.tmp / "notes"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cfg(self, **values):
        values.setdefault("watch_dirs", [str(self.notes)])
        values.setdefault("skip_globs", [DIGEST])
        path = self.tmp / "prose-guard.json"
        path.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")
        return g.load_config(str(path))

    def watched(self, rel, **values):
        return g.is_watched(str(self.notes / rel), self.cfg(**values))

    def test_note_is_watched(self):
        self.assertTrue(self.watched("area/notes/何かのノート.md"))

    def test_ingest_digest_is_skipped(self):
        # 1エントリ1行を仕様にしている一覧ノート
        self.assertFalse(self.watched("area/maps/取り込みダイジェスト.md"))

    def test_other_map_is_still_watched(self):
        self.assertTrue(self.watched("area/maps/Practice MoC.md"))

    def test_nested_dot_dir_is_skipped(self):
        self.assertFalse(self.watched("area/.obsidian/workspace.md"))

    def test_top_dot_dir_is_skipped(self):
        self.assertFalse(self.watched(".trash/x.md"))

    def test_non_markdown_is_not_watched(self):
        self.assertFalse(self.watched("area/a.txt"))

    def test_outside_watch_dirs_is_not_watched(self):
        self.assertFalse(g.is_watched(str(self.tmp / "other" / "a.md"), self.cfg()))

    def test_pr_body_is_watched_without_config(self):
        cfg = self.cfg(watch_dirs=[], skip_globs=[])
        self.assertTrue(g.is_watched(str(self.tmp / "other" / "pr-body-1.md"), cfg))
        self.assertTrue(g.is_watched(str(self.tmp / "other" / "pr_body.md"), cfg))

    def test_empty_watch_dirs_ignores_notes(self):
        self.assertFalse(self.watched("area/a.md", watch_dirs=[]))

    def test_trailing_slash_watch_dir(self):
        self.assertTrue(self.watched("area/a.md", watch_dirs=[str(self.notes) + "/"]))

    def test_sibling_prefix_not_watched(self):
        path = str(self.tmp / "notes-archive" / "a.md")
        self.assertFalse(g.is_watched(path, self.cfg()))

    def test_empty_string_does_not_watch_cwd(self):
        self.assertFalse(g.is_watched("zz-relative-note.md", self.cfg(watch_dirs=[""])))

    def test_skip_globs_win_over_pr_body(self):
        cfg = self.cfg(skip_globs=["*/drafts/*"])
        self.assertFalse(g.is_watched(str(self.tmp / "drafts" / "pr-body-1.md"), cfg))


if __name__ == "__main__":
    unittest.main()
