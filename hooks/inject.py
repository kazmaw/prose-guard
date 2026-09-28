#!/usr/bin/env python3
"""SessionStart で instructions.md を注入する hook。

呼ばれなくても効くように、startup / resume / clear / compact のたびに全文を入れる。
hook のバグで作業を止めない。読めないときは stderr に出して exit 0 にする。
"""

import contextlib
import io
import json
import os
import sys
from pathlib import Path

# prose_guard の import で sys.stdout が sys.stderr に差し替わるので、
# 実物の stdout をここで確保しておく。
_real_stdout = sys.__stdout__

INSTRUCTIONS = Path(__file__).resolve().parent.parent / "instructions.md"
DISABLE_FLAG = os.path.expanduser("~/.claude/.prose-guard-off")
HERE = Path(__file__).resolve().parent


def _config_warnings():
    """load_config が stderr に出す警告を拾う。失敗しても注入は止めない。"""
    try:
        sys.path.insert(0, str(HERE))
        import prose_guard  # noqa: E402
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            prose_guard.load_config()
        return buf.getvalue()
    except Exception as exc:  # 設定の警告表示は付加機能。失敗しても注入は続ける
        sys.stderr.write("[prose-guard] 設定の警告を確認できなかった: %s\n" % exc)
        return ""


def main():
    if os.path.exists(DISABLE_FLAG):
        return 0
    try:
        text = INSTRUCTIONS.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write("[prose-guard] %s を読めないので注入しない: %s\n" % (INSTRUCTIONS, exc))
        return 0
    payload = {"hookSpecificOutput": {"hookEventName": "SessionStart",
                                      "additionalContext": text}}
    warnings = _config_warnings()
    if warnings:
        sys.stderr.write(warnings)
        payload["systemMessage"] = warnings
    _real_stdout.write(json.dumps(payload) + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # hook のバグで作業を止めない
        sys.stderr.write("[prose-guard] internal error (ignored): %s\n" % exc)
        sys.exit(0)
