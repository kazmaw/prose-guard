"""差し戻しの「直し方の注意」を変えたとき、書き直しで意味がずれないかを比べる。

差し戻された文章を、今の注意（A）と候補の注意（B）でそれぞれ書き直させる。
別の呼び出しで、どちらが A かを伏せて、元の文章からの意味のずれを数えさせる。

    python3 evals/rewrite_eval.py --candidate "新しい注意"

claude -p を hook を止めて呼ぶ。1 ラウンドで、例 1 件につき 3 回呼ぶ。
同じ文言でも回ごとのずれが大きく揺れるので、A と B は必ず同じ回で比べる。
"""

import argparse
import json
import random
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "hooks"))
import prose_guard  # noqa: E402
import prose_lint as pl  # noqa: E402

ASK = "\n\n上の block だけ直して書き直すこと。"
KINDS = ("lost", "added", "shifted", "relation")

JUDGE = """次の「元の文章」と、それを読みやすく書き直した2つの版 X と Y を比べてください。
書き直しの目的は、長い文やバレットを割ることだけです。意味は変えてはいけません。
各版について、元からの意味のずれを数えてください。
- lost: 元にあった情報・ニュアンスが消えた箇所
- added: 元に無い動作主、原因、条件、数値、例が足された箇所
- shifted: 主張の比重、言い切りの強さ（断定/推量）、文の働き（評価/説明/依頼）が変わった箇所
- relation: 文を割ったせいで前後の関係（理由、対比、「AではなくB」、目的）が失われた箇所
JSON だけを出力してください: {"X": {"lost": n, "added": n, "shifted": n, "relation": n, "notes": "..."}, "Y": {...}}

<original>
%s
</original>
<X>
%s
</X>
<Y>
%s
</Y>"""


def claude(prompt, model):
    r = subprocess.run(
        ["claude", "-p", "--model", model, "--settings", '{"disableAllHooks": true}',
         "--output-format", "json"],
        input=prompt, capture_output=True, text=True, cwd="/tmp")
    if r.returncode:
        raise RuntimeError("claude -p が失敗した: %s" % (r.stdout + r.stderr)[-500:])
    return json.loads(r.stdout)["result"]


def rewrite(answer, notes, model):
    report = pl.format_report(pl.lint(answer), "直前の文章", limit=8)
    return claude(
        "あなたが書いた文章は次のとおりです。\n<text>\n" + answer + "\n</text>\n\n"
        + report + ASK + notes + "\n\n（書き直した文章の全文だけを出力してください）", model)


def judge(answer, a, b, seed, model):
    flip = random.Random(seed).random() < 0.5
    x, y = (b, a) if flip else (a, b)
    raw = claude(JUDGE % (answer, x, y), model)
    j = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
    return {"A": j["Y" if flip else "X"], "B": j["X" if flip else "Y"]}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--candidate", required=True, help="比べる注意の文言（B）")
    ap.add_argument("--base", default=prose_guard.KEEP_MEANING, help="基準の注意（A）。既定は今の KEEP_MEANING")
    ap.add_argument("--samples", default=str(HERE / "samples.json"),
                    help='[{"answer": "差し戻された文章"}] の JSON')
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--model", default="opus")
    ap.add_argument("--out", help="書き直しと判定の全件を書き出す JSON")
    args = ap.parse_args()

    answers = [s["answer"] for s in json.loads(Path(args.samples).read_text(encoding="utf-8"))]
    notes = {"A": args.base, "B": "\n" + args.candidate.strip()}

    def one(job):
        rnd, i = job
        texts = {k: rewrite(answers[i], notes[k], args.model) for k in ("A", "B")}
        return {"round": rnd, "sample": i, "texts": texts,
                "drift": judge(answers[i], texts["A"], texts["B"], rnd * 1000 + i, args.model),
                "blocks": {k: pl.count(pl.lint(t), "block") for k, t in texts.items()}}

    jobs = [(r, i) for r in range(args.rounds) for i in range(len(answers))]
    with ThreadPoolExecutor(5) as ex:
        results = list(ex.map(one, jobs))
    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    print("ラウンド  案  " + "  ".join(KINDS) + "  合計  残った block")
    for r in range(args.rounds):
        rows = [x for x in results if x["round"] == r]
        for k in ("A", "B"):
            per = [sum(x["drift"][k][c] for x in rows) for c in KINDS]
            print("%8d  %2s  %s  %4d  %d" % (r + 1, k, "  ".join("%*d" % (len(c), n) for c, n in zip(KINDS, per)),
                                           sum(per), sum(x["blocks"][k] for x in rows)))


if __name__ == "__main__":
    main()
