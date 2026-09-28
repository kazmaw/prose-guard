"""prose_guard.py の I/O がロケールに依存しないことのテスト。

C ロケール（PYTHONUTF8 も無効）だと、stdin をテキストモードのまま読むと
UTF-8 の多バイト文字がバイト単位で別々の文字として解釈され、
実際は短い文章でも長さの上限を誤って超えてしまう。

60 字なのは、この環境で実測した最小の再現字数のため。50 字だと
バイト単位に化けても緩和後の上限 160 字にわずかに届かず、
直す前のコードでも block しなかった（fat_bullet の緩和倍率 1.6 との兼ね合い）。
"""

import json
import subprocess
import sys
import unittest

from helpers import GUARD, HookCase


class LocaleTest(HookCase):
    def test_clean_japanese_bullet_passes_under_c_locale(self):
        content = "- %s\n" % ("あ" * 60)
        path = self.tmp / "pr-body-locale.md"
        payload = {"hook_event_name": "PostToolUse", "tool_name": "Write",
                  "tool_input": {"file_path": str(path), "content": content},
                  "tool_use_id": "toolu_1", "cwd": str(self.tmp)}
        env = dict(self.env)
        env.pop("PYTHONIOENCODING", None)
        env.update(LC_ALL="C", LANG="C", PYTHONUTF8="0")
        r = subprocess.run([sys.executable, GUARD],
                           input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                           capture_output=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main()
