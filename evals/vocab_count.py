"""AI 特有の言い回しが、Claude の回答に週ごとにどれだけ出たかを数える。

instructions.md の「AI 特有の言い回しを避ける」節に載せた語の出現を、
~/.claude/projects の会話記録から 10 万字あたりの回数で出す。

    python3 evals/vocab_count.py [--since 2026-09-01]

「黙って」「瞬間」などは普通の用法も多いので、リンタには入れずここで傾向だけ見る。
"""

import argparse
import datetime
import glob
import json
import os
import re
from collections import Counter

PATTERNS = {
    "倒す": r"側に倒",
    "潰す": r"(1|一)つずつ潰",
    "静かに": r"静かに(壊|失|落|消|捨)",
    "黙って": r"黙って(捨|消|失|落|壊|無視|通)",
    "解像度": r"解像度(が|を)(上|高)",  # 画像の解像度は数えない
    "腹落ち": r"腹落ち",
    "肌感": r"肌感",
    "瞬間": r"た瞬間",
    "示唆": r"示唆",
    "語る": r"が語(る|って)",
    "前置き": r"(結論から|正直に)言うと",
}


def texts(since):
    for f in glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")):
        if datetime.date.fromtimestamp(os.path.getmtime(f)) < since:
            continue
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                # claude -p の会話（評価や週次レポート）は、ルールの語を引用するので数えない
                if r.get("type") != "assistant" or not r.get("timestamp") or r.get("entrypoint") == "sdk-cli":
                    continue
                day = datetime.date.fromisoformat(r["timestamp"][:10])
                if day < since:
                    continue
                for c in (r.get("message") or {}).get("content") or []:
                    if isinstance(c, dict) and c.get("type") == "text":
                        yield day, c["text"]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--since", default="2026-09-01", type=datetime.date.fromisoformat)
    args = ap.parse_args()

    chars, hits = Counter(), Counter()
    words = {}
    for day, text in texts(args.since):
        week = day - datetime.timedelta(days=day.weekday())
        chars[week] += len(text)
        for name, pat in PATTERNS.items():
            n = len(re.findall(pat, text))
            hits[week] += n
            words.setdefault(week, Counter())[name] += n

    print("週の始まり  文字数     回数  10万字あたり  多い語")
    for week in sorted(chars):
        top = ", ".join("%s %d" % kv for kv in words[week].most_common(3) if kv[1])
        print("%s  %9d  %4d  %12.1f  %s" % (week, chars[week], hits[week],
                                            hits[week] * 1e5 / chars[week], top))


if __name__ == "__main__":
    main()
