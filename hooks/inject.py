#!/usr/bin/env python3
"""SessionStart で instructions.md を注入する hook。

呼ばれなくても効くように、startup / resume / clear / compact のたびに全文を入れる。
hook のバグで作業を止めない。読めないときは stderr に出して exit 0 にする。
"""

import json
import os
import sys
from pathlib import Path

INSTRUCTIONS = Path(__file__).resolve().parent.parent / "instructions.md"
DISABLE_FLAG = os.path.expanduser("~/.claude/.prose-guard-off")


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
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # hook のバグで作業を止めない
        sys.stderr.write("[prose-guard] internal error (ignored): %s\n" % exc)
        sys.exit(0)
