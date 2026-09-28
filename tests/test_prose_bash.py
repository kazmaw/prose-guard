"""prose_guard.py の Bash 経路（実行前に控え、実行後に差分だけ検査）の e2e テスト。

Bash での書き換えは Write / Edit の PostToolUse を通らないため、
python や heredoc で spec・plan を直すと検査が丸ごと抜けていた。
"""

import os
import subprocess
import unittest

from helpers import CLEAN_DOC, LONG, HookCase


class BashCase(HookCase):
    def pr_body(self, text):
        p = self.tmp / "pr-body-1.md"
        p.write_text(text, encoding="utf-8")
        return p

    def spec(self, text):
        p = self.notes / "team" / "specs" / "2026-09-25-x-design.md"
        p.write_text(text, encoding="utf-8")
        return p

    def snapshots(self, root=None):
        d = root or self.snap
        return list(d.glob("*.json")) if d.exists() else []


class TestBashDiffLint(BashCase):
    def test_changed_line_with_violation_blocks(self):
        p = self.pr_body(CLEAN_DOC)
        cmd = "python3 -c \"import pathlib;p=pathlib.Path('%s');" \
              "p.write_text(p.read_text().replace('短い項目です。','%s'))\"" % (p, LONG)
        post = self.run_bash(cmd)
        self.assertEqual(post.returncode, 2, post.stderr)
        self.assertIn("fat_bullet", post.stderr)
        self.assertIn("L3", post.stderr)

    def test_existing_violation_not_reported(self):
        p = self.pr_body("# 見出し\n\n- %s\n- 短い項目です。\n" % LONG)
        cmd = "sed -i.bak 's/短い項目です。/別の短い項目です。/' '%s'" % p
        post = self.run_bash(cmd)
        self.assertEqual(post.returncode, 0, post.stderr)

    def test_new_file_is_checked_in_full(self):
        p = self.tmp / "pr-body-new.md"
        cmd = "printf '%%s\\n' '- %s' > '%s'" % (LONG, p)
        post = self.run_bash(cmd)
        self.assertEqual(post.returncode, 2, post.stderr)

    def test_change_inside_code_block_ignored(self):
        p = self.pr_body("# 見出し\n\n```\nx = 1\n```\n")
        cmd = "sed -i.bak 's/x = 1/- %s/' '%s'" % (LONG, p)
        post = self.run_bash(cmd)
        self.assertEqual(post.returncode, 0, post.stderr)

    def test_spec_path_with_escaped_space(self):
        """新規ファイルは名前で探せないので、エスケープした空白を解釈できないと拾えない。"""
        p = self.notes / "team" / "specs" / "2026-09-25-new-design.md"
        escaped = str(p).replace(" ", "\\ ")
        cmd = "printf '%%s\\n' '- %s' > %s" % (LONG, escaped)
        post = self.run_bash(cmd)
        self.assertEqual(post.returncode, 2, post.stderr)

    def test_spec_path_by_basename(self):
        """パスを組み立てていても、specs/<ファイル名>.md が書いてあれば watch_dirs から探す。"""
        p = self.spec(CLEAN_DOC)
        cmd = ("python3 -c \"import os,pathlib;"
               "p=pathlib.Path(os.environ['NOTES_ROOT'])/'team'/'specs/%s';"
               "p.write_text(p.read_text().replace('短い項目です。','%s'))\"" % (p.name, LONG))
        post = self.run_bash(cmd)
        self.assertEqual(post.returncode, 2, post.stderr)


class TestBashWatchDirs(BashCase):
    """specs/・plans/ を含まないノートも、watch_dirs のパスが書いてあれば拾う。"""

    def test_quoted_absolute_path_to_note(self):
        post = self.run_bash("printf '%%s\\n' '- %s' > '%s'" % (LONG, self.notes / "memo.md"))
        self.assertEqual(post.returncode, 2, post.stderr)

    def test_tilde_path_with_escaped_space(self):
        post = self.run_bash("printf '%%s\\n' '- %s' > ~/my\\ notes/memo.md" % LONG)
        self.assertEqual(post.returncode, 2, post.stderr)

    def test_note_ignored_when_watch_dirs_empty(self):
        self.configure(watch_dirs=[])
        post = self.run_bash("printf '%%s\\n' '- %s' > '%s'" % (LONG, self.notes / "memo.md"))
        self.assertEqual(post.returncode, 0, post.stderr)
        self.assertEqual(self.snapshots(), [])

    def test_relative_write_when_cwd_is_watch_dir(self):
        """コマンド文字列に watch_dirs のパスが出ていなくても、cwd が watch_dir 配下なら拾う。"""
        post = self.run_bash("printf '%%s\\n' '- %s' > memo.md" % LONG, cwd=self.notes)
        self.assertEqual(post.returncode, 2, post.stderr)


class TestBashNoop(BashCase):
    def test_unrelated_command_leaves_no_snapshot(self):
        post = self.run_bash("echo hello > '%s'" % (self.tmp / "notes.md"))
        self.assertEqual(post.returncode, 0, post.stderr)
        self.assertEqual(self.snapshots(), [])

    def test_read_only_command_passes(self):
        p = self.pr_body("# 見出し\n\n- %s\n" % LONG)
        post = self.run_bash("cat '%s' > /dev/null" % p)
        self.assertEqual(post.returncode, 0, post.stderr)

    def test_post_without_snapshot_passes(self):
        p = self.pr_body("- %s\n" % LONG)
        post = self.bash_hook("PostToolUse", "cat '%s'" % p, tool_use_id="toolu_missing")
        self.assertEqual(post.returncode, 0, post.stderr)

    def test_snapshot_removed_after_post(self):
        p = self.pr_body(CLEAN_DOC)
        self.run_bash("sed -i.bak 's/短い/とても短い/' '%s'" % p)
        self.assertEqual(self.snapshots(), [])


class TestBashAttribution(BashCase):
    """差分に出ても、このコマンドで Claude が書いた行でなければ差し戻さない。"""

    def test_moved_file_not_reported(self):
        src = self.pr_body("# 見出し\n\n- %s\n" % LONG)
        dst = self.tmp / "pr-body-moved.md"
        post = self.run_bash("mv '%s' '%s'" % (src, dst))
        self.assertEqual(post.returncode, 0, post.stderr)

    def test_fetched_body_not_reported(self):
        remote = self.tmp / "remote.txt"
        remote.write_text("- %s\n" % LONG, encoding="utf-8")
        post = self.run_bash("cat '%s' > '%s'" % (remote, self.tmp / "pr-body-fetched.md"))
        self.assertEqual(post.returncode, 0, post.stderr)

    def test_concurrent_write_not_blamed(self):
        p = self.pr_body(CLEAN_DOC)
        cmd = "wc -l '%s'" % p
        self.assertEqual(self.bash_hook("PreToolUse", cmd).returncode, 0)
        p.write_text(CLEAN_DOC + "- %s\n" % LONG, encoding="utf-8")  # 別の主体が書き込む
        post = self.bash_hook("PostToolUse", cmd)
        self.assertEqual(post.returncode, 0, post.stderr)

    def test_moved_existing_line_not_reported(self):
        body = "# 見出し\n\n- %s\n\n## 後ろ\n\n" % LONG + "".join(
            "- 項目%d です。\n" % i for i in range(8))
        p = self.pr_body(body)
        cmd = ("python3 -c \"import pathlib;p=pathlib.Path('%s');L=p.read_text().split(chr(10));"
               "x=L.pop(2);L.insert(len(L)-1,x);p.write_text(chr(10).join(L))\"" % p)
        post = self.run_bash(cmd)
        self.assertEqual(post.returncode, 0, post.stderr)

    def test_moved_line_written_in_command_not_reported(self):
        """移動する行をコマンドに書いていても、実行前からあった行なら差し戻さない。"""
        body = "# 見出し\n\n- %s\n\n## 後ろ\n\n" % LONG + "".join(
            "- 項目%d です。\n" % i for i in range(8))
        p = self.pr_body(body)
        line = "- %s\\n" % LONG
        cmd = ("python3 -c \"import pathlib;p=pathlib.Path('%s');s=p.read_text();"
               "p.write_text(s.replace('%s','').replace('- 項目7 です。\\n','- 項目7 です。\\n%s'))\""
               % (p, line, line))
        post = self.run_bash(cmd)
        self.assertEqual(post.returncode, 0, post.stderr)


class TestBashCoverage(BashCase):
    def test_continuation_line_change_blocks(self):
        p = self.pr_body("# 見出し\n\n- 短い\n  継続行です。\n")
        post = self.run_bash("sed -i.bak 's/継続行です。/%s/' '%s'" % (LONG, p))
        self.assertEqual(post.returncode, 2, post.stderr)

    def test_cd_then_relative_path(self):
        (self.tmp / "sub").mkdir()
        (self.tmp / "sub" / "pr-body-rel.md").write_text(CLEAN_DOC, encoding="utf-8")
        post = self.run_bash("cd sub && sed -i.bak 's/短い項目です。/%s/' pr-body-rel.md" % LONG)
        self.assertEqual(post.returncode, 2, post.stderr)

    def test_failed_command_is_checked(self):
        """書き換えたあとにコマンドが失敗すると PostToolUse ではなく PostToolUseFailure が来る。"""
        p = self.pr_body(CLEAN_DOC)
        cmd = "sed -i.bak 's/短い項目です。/%s/' '%s'; false" % (LONG, p)
        self.assertEqual(self.bash_hook("PreToolUse", cmd).returncode, 0)
        subprocess.run(["sh", "-c", cmd], cwd=str(self.tmp), env=self.env)
        post = self.bash_hook("PostToolUseFailure", cmd)
        self.assertEqual(post.returncode, 2, post.stderr)


class TestBashRobustness(BashCase):
    def test_non_utf8_target_never_blocks(self):
        p = self.tmp / "pr-body-binary.md"
        p.write_bytes(b"\xff\xfe\x00broken")
        cmd = "cat '%s' > /dev/null" % p
        pre = self.bash_hook("PreToolUse", cmd)
        self.assertEqual((pre.returncode, pre.stdout), (0, ""), pre.stderr)
        self.assertEqual(self.bash_hook("PostToolUse", cmd).returncode, 0)
        self.assertEqual(self.snapshots(), [])

    def test_stale_snapshot_is_pruned(self):
        self.snap.mkdir()
        stale = self.snap / "old.json"
        stale.write_text("{}", encoding="utf-8")
        os.utime(stale, (0, 0))
        self.run_bash("cat '%s'" % self.pr_body(CLEAN_DOC))
        self.assertFalse(stale.exists())


class TestSnapshotDir(BashCase):
    def pre_only(self):
        r = self.bash_hook("PreToolUse", "cat '%s'" % self.pr_body(CLEAN_DOC))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_uses_plugin_data_dir(self):
        del self.env["PROSE_SNAPSHOT_DIR"]
        self.env["CLAUDE_PLUGIN_DATA"] = str(self.tmp / "data")
        self.pre_only()
        self.assertEqual(len(self.snapshots(self.tmp / "data" / "snapshots")), 1)

    def test_falls_back_to_home(self):
        del self.env["PROSE_SNAPSHOT_DIR"]
        self.pre_only()
        root = self.home / ".claude" / "prose-guard" / "snapshots"
        self.assertEqual(len(self.snapshots(root)), 1)


if __name__ == "__main__":
    unittest.main()
