"""prose_lint の判定テスト。

実行: python3 -m unittest discover -s tests

閾値そのものより「誤検知しないこと」を厚く見る。誤検知が出るとリンタが
無視されるようになり、強制力が黙って失われるため。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

import prose_lint as pl  # noqa: E402


def rules(text):
    return [i.rule for i in pl.lint(text)]


class DetectTest(unittest.TestCase):
    """検出すべきものを取りこぼさない。"""

    def test_long_line(self):
        self.assertIn("long_line", rules("あ" * 150))

    def test_fat_bullet(self):
        self.assertIn("fat_bullet", rules("- " + "あ" * 120))

    def test_multi_sentence_bullet(self):
        self.assertIn("multi_sentence_bullet", rules("- 短い。短い。短い。"))

    def test_two_sentences_allowed(self):
        self.assertNotIn("multi_sentence_bullet", rules("- 短い。短い。"))

    def test_arrow_reason(self):
        self.assertIn("arrow_reason", rules("- **判断する** ← 理由がここにある。"))

    def test_inline_labels(self):
        self.assertIn("inline_labels", rules("**前提**: あれ。**判断**: これ。"))

    def test_single_label_allowed(self):
        self.assertNotIn("inline_labels", rules("**前提**: あれである。"))

    def test_long_paragraph(self):
        self.assertIn("long_paragraph", rules("あ。\nい。\nう。\nえ。\n"))

    def test_wrapped_bullet_counts_as_one_item(self):
        text = "- " + "あ" * 60 + "\n  " + "い" * 60 + "\n"
        self.assertIn("fat_bullet", rules(text))


class FalsePositiveTest(unittest.TestCase):
    """誤検知しない。ここが崩れるとリンタごと無視される。"""

    def test_code_block_ignored(self):
        self.assertEqual(rules("```\n" + "x" * 300 + "\n```\n"), [])

    def test_table_row_ignored(self):
        self.assertEqual(rules("| " + "あ" * 200 + " | " + "い" * 200 + " |"), [])

    def test_quote_ignored(self):
        self.assertEqual(rules("> " + "あ" * 200), [])

    def test_frontmatter_ignored(self):
        self.assertEqual(rules("---\ndescription: " + "あ" * 200 + "\n---\n"), [])

    def test_url_line_ignored(self):
        self.assertEqual(rules("詳細は https://example.com/" + "a" * 200 + " を見る"), [])

    def test_heading_length_ignored(self):
        self.assertEqual(rules("## " + "あ" * 200), [])

    def test_ascii_line_relaxed(self):
        self.assertIn("long_line", rules("あ" * 150))
        self.assertNotIn("long_line", rules("word " * 30))

    def test_inline_code_not_counted(self):
        self.assertEqual(rules("短い説明 `" + "x" * 200 + "` である。"), [])

    def test_clean_document(self):
        text = (
            "## 見出し\n\n"
            "結論を先に書く。\n\n"
            "- **判断した内容**\n"
            "  - 理由を 1 行 1 主張で書く。\n"
            "  - もう 1 つの理由。\n"
        )
        self.assertEqual(rules(text), [])


class ReportTest(unittest.TestCase):
    """hook が Claude に返す本文が壊れない。"""

    def test_count_block(self):
        self.assertGreaterEqual(pl.count(pl.lint("- " + "あ" * 120), "block"), 1)

    def test_empty_report(self):
        self.assertEqual(pl.format_report([], "x"), "")

    def test_report_has_line_and_hint(self):
        rep = pl.format_report(pl.lint("- **A** ← 理由。"), "sample.md")
        self.assertIn("sample.md", rep)
        self.assertIn("arrow_reason", rep)
        self.assertIn("L1", rep)
