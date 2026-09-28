"""prose_guard.py の送信前検査・ノートの検査・Stop・設定と一時停止の e2e テスト。"""

import json
import unittest

from helpers import BAD, GOOD, LONG, HookCase


class TestMcpPost(HookCase):
    def test_clickup_comment_with_violation_blocks(self):
        r = self.mcp("mcp__claude_ai_ClickUp__clickup_create_task_comment",
                     {"task_id": "abc123", "comment_text": BAD})
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("fat_bullet", r.stderr)
        self.assertIn("prose_lint.py", r.stderr)

    def test_slack_clean_message_passes(self):
        r = self.mcp("mcp__claude_ai_Slack__slack_send_message",
                     {"channel_id": "C123", "message": GOOD})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_non_post_tool_is_ignored(self):
        r = self.mcp("mcp__claude_ai_ClickUp__clickup_get_task", {"task_id": LONG})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_other_server_same_tail_blocks(self):
        r = self.mcp("mcp__other_server__slack_send_message", {"message": BAD})
        self.assertEqual(r.returncode, 2, r.stderr)


class TestGhPost(HookCase):
    def test_body_flag_blocks(self):
        r = self.gh("gh pr comment 1 --body '%s'" % BAD)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_api_field_in_heredoc_blocks(self):
        cmd = ("gh api repos/o/r/pulls/1/comments/2/replies -f body=\"$(cat <<'EOF'\n"
               "%sそれは don't です。\nEOF\n)\"" % BAD)
        r = self.gh(cmd)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_body_file_stdin_heredoc_blocks(self):
        r = self.gh("gh pr comment 1 --body-file - <<'EOF'\n%sEOF" % BAD)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_flag_of_other_command_is_ignored(self):
        """`-b` を持つ別コマンドの引数を gh の本文と取り違えない。"""
        r = self.gh("git checkout -b '%s' && gh pr view 1" % LONG)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_clean_body_passes(self):
        r = self.gh("gh pr comment 1 --body '%s'" % GOOD)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestWatchedNotes(HookCase):
    def test_any_note_is_linted(self):
        r = self.write_tool(self.notes / "team" / "investigation" / "2026-09-25 調査.md", BAD)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_system_dir_is_ignored(self):
        r = self.write_tool(self.notes / ".obsidian" / "snippets" / "x.md", BAD)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestReplacingTools(HookCase):
    def transcript(self, result_text):
        """既存の本文を読んだ transcript。MCP の結果は JSON 文字列で入る。"""
        p = self.tmp / "t.jsonl"
        rec = {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_0",
             "content": [{"type": "text", "text": json.dumps({"description": result_text},
                                                            ensure_ascii=False)}]}]}}
        p.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
        return str(p)

    def update_task(self, description, transcript):
        return self.hook({"hook_event_name": "PreToolUse",
                          "tool_name": "mcp__claude_ai_ClickUp__clickup_update_task",
                          "tool_input": {"task_id": "abc123", "markdown_description": description},
                          "transcript_path": transcript})

    def test_doc_page_with_violation_blocks(self):
        r = self.mcp("mcp__claude_ai_ClickUp__clickup_create_document_page",
                     {"document_id": "d1", "name": "QA 結果", "content": BAD})
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_update_keeps_existing_violation(self):
        """他の人が書いた既存の説明文は、置き換えで送り直しても差し戻さない。"""
        r = self.update_task(BAD + GOOD, self.transcript(BAD))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_update_blocks_new_violation(self):
        r = self.update_task(GOOD + BAD, self.transcript(GOOD))
        self.assertEqual(r.returncode, 2, r.stderr)


class TestConfigDefaults(HookCase):
    def test_no_config_still_checks_pr_body_and_gh(self):
        self.config_path.unlink()
        r = self.write_tool(self.tmp / "pr-body-1.md", BAD)
        self.assertEqual(r.returncode, 2, r.stderr)
        r = self.gh("gh pr comment 1 --body '%s'" % BAD)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_no_config_skips_notes_and_mcp(self):
        self.config_path.unlink()
        self.assertEqual(self.write_tool(self.notes / "memo.md", BAD).returncode, 0)
        r = self.mcp("mcp__claude_ai_Slack__slack_send_message", {"message": BAD})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_broken_config_warns_and_keeps_defaults(self):
        self.config_path.write_text("{", encoding="utf-8")
        r = self.write_tool(self.tmp / "pr-body-1.md", BAD)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("prose-guard.json", r.stderr)

    def test_empty_post_tools_skips_mcp(self):
        self.configure(watch_dirs=[str(self.notes)], post_tools=[])
        r = self.mcp("mcp__claude_ai_ClickUp__clickup_create_task_comment", {"comment_text": BAD})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_empty_watch_dirs_skips_notes(self):
        self.configure(watch_dirs=[])
        self.assertEqual(self.write_tool(self.notes / "memo.md", BAD).returncode, 0)

    def test_tilde_watch_dir(self):
        self.configure(watch_dirs=["~/my notes"])
        self.assertEqual(self.write_tool(self.notes / "memo.md", BAD).returncode, 2)

    def test_full_tool_name_in_config(self):
        self.configure(post_tools=["mcp__claude_ai_Slack__slack_send_message"])
        r = self.mcp("mcp__claude_ai_Slack__slack_send_message", {"message": BAD})
        self.assertEqual(r.returncode, 2, r.stderr)


class TestStop(HookCase):
    def stop(self, text, active=False):
        t = self.tmp / "t.jsonl"
        rec = {"type": "assistant", "message": {"role": "assistant",
                                                "content": [{"type": "text", "text": text}]}}
        t.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
        return self.hook({"hook_event_name": "Stop", "transcript_path": str(t),
                          "stop_hook_active": active})

    def test_bad_answer_is_sent_back_without_config(self):
        self.config_path.unlink()
        r = self.stop(BAD * 2)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["decision"], "block")

    def test_second_stop_in_turn_passes(self):
        r = self.stop(BAD * 2, active=True)
        self.assertEqual((r.returncode, r.stdout), (0, ""))


class TestDisableFlag(HookCase):
    def test_flag_disables_all_checks(self):
        self.disable()
        self.assertEqual(self.write_tool(self.tmp / "pr-body-1.md", BAD).returncode, 0)
        self.assertEqual(self.gh("gh pr comment 1 --body '%s'" % BAD).returncode, 0)
        r = self.mcp("mcp__claude_ai_Slack__slack_send_message", {"message": BAD})
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
