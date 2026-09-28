#!/usr/bin/env python3
"""日本語ドキュメント・チャット出力の読みやすさリンタ。

instructions.md の文章ルールを、形容詞ではなく数値で判定する。

なぜ機械で測るのか:
- 「読みやすく」「簡潔に」は自己採点だと必ず満点になる。実測すると spec の平均
  行長は 98 字で、文章ルールの目安 60〜80 字を常に超えていた。
- 指示は呼ばれなければ効かないが、後処理は迂回できない。

閾値は 2026-08-28 に既存の spec 5 本・plan 2 本・過去 transcript 6 本へ当てて
決めた。誤検知ゼロ、1 ファイルあたり block 10 件前後になる水準。
"""

import re

BULLET_RE = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+(.*)$")
HEADING_RE = re.compile(r"^#{1,6}\s")
TABLE_RE = re.compile(r"^\s*\|")
FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
QUOTE_RE = re.compile(r"^\s*>")
LABEL_RE = re.compile(r"\*\*[^*]+\*\*\s*[:：]")
URL_RE = re.compile(r"https?://")
JP_RE = re.compile(r"[ぁ-んァ-ヶ一-龥]")

# 日本語が薄い行（英語の定型文・パス列挙）は同じ字数でも情報量が少ないので緩める
ASCII_RELAX = 1.6
ASCII_JP_RATIO = 0.3

THRESHOLDS = {
    "long_line_warn": 100,
    "long_line_block": 140,
    "fat_bullet": 100,
    "bullet_sentences": 3,
    "paragraph_lines": 4,
}

RULE_HINTS = {
    "long_line": "文を切る（目安 80 字）",
    "fat_bullet": "子バレットに割る（上限 100 字）",
    "multi_sentence_bullet": "1 行 1 主張に割る",
    "arrow_reason": "`←` をやめ、理由を子バレットに落とす",
    "inline_labels": "ラベルは改行して縦に並べる",
    "long_paragraph": "2〜3 文で区切って空行を入れる",
}

class Issue(object):
    __slots__ = ("line", "rule", "severity", "message", "excerpt")

    def __init__(self, line, rule, severity, message, excerpt=""):
        self.line = line
        self.rule = rule
        self.severity = severity
        self.message = message
        self.excerpt = excerpt

    def __repr__(self):
        return "Issue(L%d, %s, %s)" % (self.line, self.rule, self.severity)


def _visible(s):
    """字数を数える前に、読み手の負荷にならない装飾を落とす。"""
    s = re.sub(r"`[^`]*`", "CODE", s)
    s = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"\*\*|__|~~|\*|_", "", s)
    return s.strip()


def _limit(base, text):
    jp = len(JP_RE.findall(text))
    if text and jp / float(len(text)) < ASCII_JP_RATIO:
        return int(base * ASCII_RELAX)
    return base


def _sentences(s):
    return [x for x in re.split(r"[。！？]", s) if x.strip()]


def _skip(line):
    st = line.strip()
    if not st:
        return True
    return bool(TABLE_RE.match(line) or QUOTE_RE.match(line) or HEADING_RE.match(line))


def lint(text):
    """markdown/プレーンテキストを検査して Issue のリストを返す。"""
    lines = text.split("\n")
    issues = []
    in_fence = False
    in_fm = False
    para = 0
    para_start = 0

    i = 0
    while i < len(lines):
        raw = lines[i]

        if i == 0 and raw.strip() == "---":
            in_fm = True
            i += 1
            continue
        if in_fm:
            if raw.strip() == "---":
                in_fm = False
            i += 1
            continue

        if FENCE_RE.match(raw):
            in_fence = not in_fence
            i += 1
            continue
        if in_fence:
            i += 1
            continue

        is_bullet = bool(BULLET_RE.match(raw))
        is_prose = bool(raw.strip()) and not is_bullet and not _skip(raw)

        if is_prose:
            if para == 0:
                para_start = i + 1
            para += 1
        else:
            if para >= THRESHOLDS["paragraph_lines"]:
                issues.append(Issue(para_start, "long_paragraph", "warn",
                                    "散文が %d 行続いている" % para))
            para = 0

        if _skip(raw):
            i += 1
            continue

        if is_bullet:
            i = _lint_bullet(lines, i, issues)
            continue

        _lint_line(raw, i + 1, issues)
        i += 1

    if para >= THRESHOLDS["paragraph_lines"]:
        issues.append(Issue(para_start, "long_paragraph", "warn",
                            "散文が %d 行続いている" % para))

    issues.sort(key=lambda x: (x.line, x.rule))
    return issues


def _lint_bullet(lines, i, issues):
    """バレット 1 項目を、折り返しの継続行まで含めて検査する。"""
    m = BULLET_RE.match(lines[i])
    body = m.group(2)
    j = i + 1
    while j < len(lines):
        nxt = lines[j]
        if not nxt.strip() or BULLET_RE.match(nxt) or HEADING_RE.match(nxt) \
           or FENCE_RE.match(nxt) or TABLE_RE.match(nxt):
            break
        body += nxt.strip()
        j += 1

    lineno = i + 1
    plain = _visible(body)
    if not URL_RE.search(body):
        limit = _limit(THRESHOLDS["fat_bullet"], plain)
        if len(plain) > limit:
            issues.append(Issue(lineno, "fat_bullet", "block",
                                "1 バレット %d 字" % len(plain), body[:45]))

    n = len(_sentences(plain))
    if n >= THRESHOLDS["bullet_sentences"]:
        issues.append(Issue(lineno, "multi_sentence_bullet", "block",
                            "1 バレットに %d 文" % n, body[:45]))

    if "←" in plain or "⇐" in plain:
        issues.append(Issue(lineno, "arrow_reason", "block",
                            "`←` で理由を接続している", body[:45]))

    _lint_labels(body, lineno, issues)
    return j


def _lint_line(raw, lineno, issues):
    st = raw.strip()
    if URL_RE.search(st):
        return
    plain = _visible(st)
    n = len(plain)
    block = _limit(THRESHOLDS["long_line_block"], plain)
    warn = _limit(THRESHOLDS["long_line_warn"], plain)
    if n > block:
        issues.append(Issue(lineno, "long_line", "block", "1 行 %d 字" % n, st[:45]))
    elif n > warn:
        issues.append(Issue(lineno, "long_line", "warn", "1 行 %d 字" % n, st[:45]))
    _lint_labels(st, lineno, issues)


def _lint_labels(text, lineno, issues):
    if len(LABEL_RE.findall(text)) >= 2:
        issues.append(Issue(lineno, "inline_labels", "block",
                            "ラベル付き記述を 1 行に詰めている", text[:45]))


def count(issues, severity):
    return sum(1 for x in issues if x.severity == severity)


def format_report(issues, title, limit=8):
    """hook が Claude に返す本文。何を直すかまで書く。"""
    if not issues:
        return ""
    nb, nw = count(issues, "block"), count(issues, "warn")
    out = ["%s — 読みやすさ違反 block %d 件 / warn %d 件" % (title, nb, nw)]
    shown = [x for x in issues if x.severity == "block"][:limit]
    if len(shown) < limit:
        shown += [x for x in issues if x.severity == "warn"][:limit - len(shown)]
    for x in sorted(shown, key=lambda v: v.line):
        out.append("  L%-4d %-22s %s — %s"
                   % (x.line, x.rule, x.message, RULE_HINTS.get(x.rule, "")))
        if x.excerpt:
            out.append("        | " + x.excerpt[:45])
    rest = len(issues) - len(shown)
    if rest > 0:
        out.append("  （ほか %d 件）" % rest)
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    rc = 0
    for path in sys.argv[1:]:
        with open(path, encoding="utf-8") as f:
            issues = lint(f.read())
        rep = format_report(issues, path, limit=40)
        if rep:
            print(rep)
            print()
        if count(issues, "block"):
            rc = 1
    sys.exit(rc)
