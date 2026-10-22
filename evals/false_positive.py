"""Stop の差し戻しのうち、元のままで読めた箇所（誤検知）の割合を見積もる。

~/.claude/projects の会話記録から、差し戻された回答と書き直した回答を取り出す。
差し戻された箇所ごとに、元のままで読みにくかったかと、書き直しで良くなったかを Claude に判定させる。

    python3 evals/false_positive.py [--since 2026-10-15] [--n 50]

2026-10-22 に利用者の手判定 20 件と照らし、17 件で一致した。
"""

import argparse
import collections
import datetime
import glob
import json
import os
import random
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
import prose_lint as pl  # noqa: E402

JUDGE = """Claude の回答が、文章リンタに「読みにくい」と差し戻されました。リンタは行やバレットの字数と文の数だけで判定します。
下の「差し戻された箇所」それぞれについて、人が読んで判定してください。

- hard: 元のままで、日本語の読み手にとって実際に読みにくかったか（true/false）。
  - 識別子・パス・数値の列挙で長いだけ、短い文が3つ並んだだけで読むのに困らない、などは false。
- rewrite: 書き直し後の対応する箇所は、元より読みやすいか（better / same / worse）。
- why: 判定理由を1文で。

JSON だけを出力してください: {"items": [{"id": 1, "hard": true, "rewrite": "better", "why": "..."}, ...]}

<original>
%s
</original>

<flagged>
%s
</flagged>

<rewrite>
%s
</rewrite>"""


def send_backs(since):
    """(差し戻された回答, 書き直した回答) を返す。"""
    out = []
    for f in glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")):
        if datetime.date.fromtimestamp(os.path.getmtime(f)) < since:
            continue
        last, pending = None, None
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                m = r.get("message") or {}
                if r.get("entrypoint") == "sdk-cli":
                    continue  # claude -p の会話は、利用者が読む回答ではない
                if r.get("type") == "assistant":
                    for c in m.get("content") or []:
                        if isinstance(c, dict) and c.get("type") == "text" and c["text"].strip():
                            if pending:
                                out.append((pending, c["text"]))
                                pending = None
                            last = c["text"]
                elif r.get("type") == "user" and isinstance(m.get("content"), str):
                    day = datetime.date.fromisoformat((r.get("timestamp") or "0001-01-01")[:10])
                    if day >= since and last and m["content"].startswith("Stop hook feedback:") \
                            and "直前の回答" in m["content"]:
                        pending = last
    return out


def _span(lines, i):
    """i 行目から、折り返しの継続行までをつなげて返す。バレットは複数行にまたがることがある。"""
    j = i + 1
    while j < len(lines) and lines[j].strip() and not pl.BULLET_RE.match(lines[j]) \
            and not pl.HEADING_RE.match(lines[j]):
        j += 1
    return "\n".join(lines[i:j])


def judge(orig, rewrite):
    lines = orig.split("\n")
    flagged = [{"id": n, "rule": x.rule, "text": _span(lines, x.line - 1)}
               for n, x in enumerate((x for x in pl.lint(orig) if x.severity == "block"), 1)]
    if not flagged:
        return []  # 判定ルールが変わり、今の規則では差し戻されない
    body = "\n".join("[%d] (%s) %s" % (f["id"], f["rule"], f["text"]) for f in flagged)
    r = subprocess.run(
        ["claude", "-p", "--model", "opus", "--settings", '{"disableAllHooks": true}',
         "--output-format", "json"],
        input=JUDGE % (orig, body, rewrite), capture_output=True, text=True, cwd="/tmp")
    if r.returncode:
        raise RuntimeError("claude -p が失敗した: %s" % (r.stdout + r.stderr)[-500:])
    raw = json.loads(r.stdout)["result"]
    verdict = {x["id"]: x for x in json.loads(raw[raw.index("{"):raw.rindex("}") + 1])["items"]}
    return [dict(f, **verdict[f["id"]]) for f in flagged if f["id"] in verdict]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--since", type=datetime.date.fromisoformat,
                    default=datetime.date.today() - datetime.timedelta(days=7))
    ap.add_argument("--n", type=int, default=50, help="判定する差し戻しの件数")
    ap.add_argument("--out", help="判定の全件を書き出す JSON")
    args = ap.parse_args()

    cases = send_backs(args.since)
    pick = random.Random(0).sample(cases, min(args.n, len(cases)))
    with ThreadPoolExecutor(5) as ex:
        judged = list(ex.map(lambda c: judge(*c), pick))
    items = [x for case in judged for x in case]
    if args.out:
        Path(args.out).write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")

    print("期間: %s 以降 / 差し戻し %d 件のうち %d 件を判定（今の規則で block が出たのは %d 件）"
          % (args.since, len(cases), len(pick), sum(1 for c in judged if c)))
    print()
    print("| ルール | 差し戻した箇所 | 誤検知 | 割合 |")
    print("| --- | --- | --- | --- |")
    by = collections.defaultdict(list)
    for x in items:
        by[x["rule"]].append(x)
    for rule in sorted(by):
        fp = sum(1 for x in by[rule] if not x["hard"])
        print("| `%s` | %d | %d | %d%% |" % (rule, len(by[rule]), fp, 100 * fp / len(by[rule])))
    if items:
        fp = sum(1 for x in items if not x["hard"])
        print("| 合計 | %d | %d | %d%% |" % (len(items), fp, 100 * fp / len(items)))
    print()
    print("書き直しの結果（元が読めた箇所）:", dict(collections.Counter(x["rewrite"] for x in items if not x["hard"])))
    print()
    print("誤検知の判定理由（最大 10 件）:")
    for x in [x for x in items if not x["hard"]][:10]:
        print("- (%s) %s" % (x["rule"], x["why"]))


if __name__ == "__main__":
    main()
