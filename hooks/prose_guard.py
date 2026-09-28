#!/usr/bin/env python3
"""読みやすさを機械で落とす hook。

- PostToolUse: 検査対象の Markdown を書いたら検査する。block があれば書き直させる。
- PreToolUse(Bash) / PostToolUse(Bash) / PostToolUseFailure(Bash):
               Bash での書き換えは Write / Edit を通らないので、実行前に対象ファイルを
               控え、実行後に変わった行だけを検査する。
- PreToolUse(MCP 投稿 / gh):
               設定の post_tools に挙げた MCP ツールと gh の本文を送信前に検査し、
               block があれば止める。
- Stop:        直前のチャット回答を検査する。block が 2 件以上なら書き直させる。

どのファイルとツールを検査するかは ~/.claude/prose-guard.json で決める（load_config）。
設定が無くても、PR 本文のファイル・gh・チャット回答は検査する。

設計方針:
- **hook のバグで作業を人質に取らない。** 例外は握り潰して exit 0 にする。
- **黙って弱くならない。** 判定できない異常は stderr に必ず出す。
- **stdout は判定 JSON 専用。** 他が 1 バイトでも混ざると JSON が壊れる。

Stop の書き直しは 1 ターン 1 回だけ。stop_hook_active が立っていれば何もしない。
"""

import sys

_real_stdout = sys.stdout
sys.stdout = sys.stderr

import difflib  # noqa: E402
import fnmatch  # noqa: E402
import glob  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import shlex  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import prose_lint as pl  # noqa: E402

# チャット回答を止める下限。1 件だと発火率 61% で往復が増えすぎる（実測）。
CHAT_BLOCK_MIN = 2

# 設定が無くても検査する PR 本文のファイル。fnmatch の * は / もまたぐ。
PR_BODY_GLOBS = ("*pr-body*.md", "*pr_body*.md")

EDIT_TOOLS = {"Write", "Edit", "MultiEdit"}

DISABLE_FLAG = os.path.expanduser("~/.claude/.prose-guard-off")

CONFIG_PATH = os.environ.get("PROSE_GUARD_CONFIG") or os.path.expanduser(
    "~/.claude/prose-guard.json")
CONFIG_KEYS = ("watch_dirs", "skip_globs", "post_tools", "replacing_tools")

# 差し戻しの文面で示す判定基準の場所
CRITERIA = " 判定基準は %s にある。\n" % (HERE / "prose_lint.py")

# Bash 経路の控え。tool_use_id ごとに 1 ファイル。実行後に消す。
SNAPSHOT_DIR = os.environ.get("PROSE_SNAPSHOT_DIR") or os.path.join(
    os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.expanduser("~/.claude/prose-guard"),
    "snapshots")
SNAPSHOT_TTL = 24 * 3600  # 中断で残った控えはこの時間で掃除する

# コマンド文字列から検査対象らしきパスを拾う。
# これにも watch_dirs のパスにも掛からないコマンドは素通り。
_BASH_HINT = re.compile(r"(?:specs|plans)/|pr[-_]body")
_PATH_TOKEN = re.compile(r"(?:\\ |[^\s\"'<>|;&()=])+\.md")
_NAMED_DOC = re.compile(r"(specs|plans)/([^\s\"'<>|;&()/\\]+\.md)")
_LEADING_CD = re.compile(r"""^\s*cd\s+("[^"]+"|'[^']+'|(?:\\ |[^\s;&])+)\s*(?:&&|;)""")
# 読み込み済みとみなす行の最短の長さ。短い定型行で誤って外さないため
_SEEN_MIN_CHARS = 10
# ID や URL などの短い値は検査しない
_POST_MIN_CHARS = 20
_GH_POST = re.compile(r"\bgh\s+(?:pr|issue|api)\b")
_HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)(\w+)\1([^\n]*)\n(.*?)\n[ \t]*\2(?=\s|\)|$)", re.S)
_HEREDOC_MARK = re.compile(r"__PROSE_HEREDOC_(\d+)__")
_SHELL_SEPARATORS = {";", "&&", "||", "|", "&"}
# 行の中身がコマンドに書かれているかを見るときの照合の長さ
_AUTHORED_PROBE = 15
# これを超える行数のファイルは、差分計算を速い方式に切り替える
_LARGE_FILE_LINES = 3000


def emit(payload):
    _real_stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    _real_stdout.flush()


def _tool_tail(name):
    """MCP ツール名の最後の __ より後ろ。サーバー名は環境で変わるので末尾で照合する。"""
    return name.rsplit("__", 1)[-1]


def load_config(path=None):
    """設定ファイルを読む。読めないときも既定値（すべて空）で動かし、作業を止めない。"""
    path = path or CONFIG_PATH
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        raw = {}
    except (OSError, ValueError) as exc:
        sys.stderr.write("[prose-guard] 設定 %s を読めないので既定値で動く: %s\n" % (path, exc))
        raw = {}
    if not isinstance(raw, dict):
        sys.stderr.write("[prose-guard] 設定 %s がオブジェクトでないので既定値で動く\n" % path)
        raw = {}
    values = {}
    for key in CONFIG_KEYS:
        v = raw.get(key, [])
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            sys.stderr.write("[prose-guard] 設定の %s は文字列の配列にすること。"
                             "既定値 [] で動く\n" % key)
            v = []
        # 空文字の watch_dirs はカレントディレクトリ全体になるので捨てる
        values[key] = [x for x in v if x.strip()]
    post = {_tool_tail(t) for t in values["post_tools"]}
    return {
        "watch_dirs": [os.path.abspath(os.path.expanduser(d)) for d in values["watch_dirs"]],
        "skip_globs": [os.path.expanduser(x) for x in values["skip_globs"]],
        "post_tools": post,
        "replacing_tools": {_tool_tail(t) for t in values["replacing_tools"]} & post,
    }


def is_watched(path, cfg):
    if not path or not path.endswith(".md"):
        return False
    p = os.path.abspath(os.path.expanduser(path))
    if any(fnmatch.fnmatch(p, g) for g in cfg["skip_globs"]):
        return False
    if any(fnmatch.fnmatch(p, g) for g in PR_BODY_GLOBS):
        return True
    for root in cfg["watch_dirs"]:
        if p.startswith(root + os.sep):
            return "/." not in p[len(root):]  # .obsidian / .trash などの管理領域
    return False


def _dir_forms(d):
    """コマンドに書かれうる watch_dirs の表記。絶対パス・~ 始まり・空白のエスケープ。"""
    home = os.path.expanduser("~")
    forms = {d}
    if d == home or d.startswith(home + os.sep):
        forms.add("~" + d[len(home):])
    return forms | {f.replace(" ", "\\ ") for f in forms}


def _mentions_target(command, cfg):
    if _BASH_HINT.search(command):
        return True
    return any(f in command for d in cfg["watch_dirs"] for f in _dir_forms(d))


def bash_targets(command, cwd, cfg):
    """Bash コマンドが触りそうな検査対象ファイルの絶対パス。

    実行前に呼ぶので、まだ存在しない新規ファイルも返す。
    パスを変数や glob で組み立てたコマンドは拾えない（約 8%、実測）。
    """
    if not command or not _mentions_target(command, cfg):
        return []
    base = cwd or os.getcwd()
    m = _LEADING_CD.match(command)
    if m:
        base = os.path.join(base, os.path.expanduser(m.group(1).strip("'\"").replace("\\ ", " ")))
    found = set()
    tokens = [m.group(2) for m in re.finditer(r"(['\"])([^'\"\n]+?\.md)\1", command)]
    tokens += [m.group(0) for m in _PATH_TOKEN.finditer(command)]
    for tok in tokens:
        p = os.path.expanduser(tok.replace("\\ ", " "))
        if not os.path.isabs(p):
            p = os.path.join(base, p)
        p = os.path.normpath(p)
        if is_watched(p, cfg):
            found.add(p)
    # パスを組み立てていても specs/<名前>.md が書いてあれば、watch_dirs から実体を探す
    for kind, name in _NAMED_DOC.findall(command):
        for root in cfg["watch_dirs"]:
            for pat in ("*/%s/%s", "*/*/%s/%s"):
                for hit in glob.glob(os.path.join(glob.escape(root),
                                                  pat % (kind, glob.escape(name)))):
                    hit = os.path.normpath(hit)
                    if is_watched(hit, cfg):
                        found.add(hit)
    return sorted(found)


def _snapshot_path(tool_use_id):
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", tool_use_id or "")
    return os.path.join(SNAPSHOT_DIR, safe + ".json") if safe else ""


def _read_or_none(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def _prune_snapshots():
    now = time.time()
    for p in glob.glob(os.path.join(glob.escape(SNAPSHOT_DIR), "*.json")):
        try:
            if now - os.path.getmtime(p) > SNAPSHOT_TTL:
                os.remove(p)
        except OSError:
            pass


def _post_strings(obj):
    """MCP 投稿の入力から、本文らしき文字列を全部拾う。スキーマの項目名には依存しない。"""
    if isinstance(obj, str):
        if len(obj) >= _POST_MIN_CHARS:
            yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _post_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _post_strings(v)


def _read_body_file(path, cwd):
    p = os.path.expanduser(path)
    if not os.path.isabs(p):
        p = os.path.join(cwd or os.getcwd(), p)
    return _read_or_none(p)


def gh_bodies(command, cwd):
    """gh コマンドが投稿する本文。--body / -f body= / --body-file と heredoc を拾う。"""
    if not command or not _GH_POST.search(command):
        return []
    heredocs = []

    def stash(m):
        heredocs.append(m.group(4))
        return "<<__PROSE_HEREDOC_%d__%s" % (len(heredocs) - 1, m.group(3))

    # heredoc の本文に ' があると shlex が壊れるので、先に退避しておく
    flat = _HEREDOC.sub(stash, command)
    lexer = shlex.shlex(flat, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError as exc:
        sys.stderr.write("[prose] gh コマンドを分解できなかった: %s\n" % exc)
        return []

    def restore(value):
        m = _HEREDOC_MARK.search(value)
        return heredocs[int(m.group(1))] if m else value

    bodies = []
    in_gh = False
    for i, tok in enumerate(tokens):
        if tok in _SHELL_SEPARATORS:
            in_gh = False
            continue
        if tok == "gh":
            in_gh = True
            continue
        if not in_gh:
            continue
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if tok in ("--body", "-b") and nxt:
            bodies.append(restore(nxt))
        elif tok.startswith("--body="):
            bodies.append(restore(tok[len("--body="):]))
        elif tok in ("-f", "-F", "--field", "--raw-field") and nxt.startswith("body="):
            value = nxt[len("body="):]
            if tok in ("-F", "--field") and value.startswith("@"):
                value = "-" if value == "@-" else _read_body_file(value[1:], cwd)
            if value == "-":
                bodies.extend(heredocs)
            elif value:
                bodies.append(restore(value))
        elif tok == "--body-file" and nxt:
            if nxt == "-":
                bodies.extend(heredocs)
            else:
                bodies.append(_read_body_file(nxt, cwd) or "")
    return [b for b in bodies if b and b.strip()]


def _tool_result_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_tool_result_text(c.get("text") if isinstance(c, dict) else c)
                         for c in content)
    return ""


def _json_strings(text):
    """ツール結果が JSON なら中の文字列を全部取り出す。JSON でなければそのまま返す。"""
    try:
        obj = json.loads(text)
    except ValueError:
        return [text]
    out = []
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, str):
            out.append(cur)
        elif isinstance(cur, dict):
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return out


def seen_text(transcript_path):
    """transcript のツール結果の本文。ClickUp から読んだ既存の説明文やページがここに入る。"""
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    parts = []
    with open(transcript_path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            if '"tool_result"' not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            for b in (rec.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    parts.extend(_json_strings(_tool_result_text(b.get("content"))))
    return "\n".join(parts)


def _is_seen(line, seen):
    st = line.strip()
    return len(st) >= _SEEN_MIN_CHARS and st in seen


def _block_post(texts, title, seen=""):
    """投稿本文を検査する。block が 1 件でもあれば送信を止める。

    seen（読み込み済みの本文）に含まれる行は既存とみなし、その行だけに掛かる指摘は外す。
    """
    reports = []
    for n, text in enumerate(texts, 1):
        issues = pl.lint(text)
        if seen:
            lines = text.split("\n")
            issues = [x for x in issues
                      if not all(_is_seen(lines[i - 1], seen) for i in _issue_span(lines, x.line))]
        if pl.count(issues, "block"):
            label = title if len(texts) == 1 else "%s %d" % (title, n)
            reports.append(pl.format_report(issues, label))
    if not reports:
        return 0
    sys.stderr.write(
        "\n\n".join(reports)
        + "\n\nまだ送信していない。上の block を直した本文で、もう一度送ること。"
          " 情報は削らず、長い行と長いバレットを分割する。"
        + CRITERIA
    )
    return 2


def handle_pre_post(payload, cfg):
    name = _tool_tail(payload.get("tool_name") or "")
    seen = seen_text(payload.get("transcript_path")) if name in cfg["replacing_tools"] else ""
    return _block_post(list(_post_strings(payload.get("tool_input") or {})), name + " の本文", seen)


def handle_pre_bash(payload, cfg):
    """gh の投稿本文を送信前に検査し、通ったら書き換え対象のファイルを控える。"""
    tool_input = payload.get("tool_input") or {}
    rc = _block_post(gh_bodies(tool_input.get("command") or "", payload.get("cwd")), "gh の投稿本文")
    if rc:
        return rc
    targets = bash_targets(tool_input.get("command") or "", payload.get("cwd"), cfg)
    snap = _snapshot_path(payload.get("tool_use_id"))
    if not targets or not snap:
        return 0
    data = json.dumps({p: _read_or_none(p) for p in targets}, ensure_ascii=False)
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    _prune_snapshots()
    tmp = snap + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, snap)
    return 0


def _authored_lines(before, after, command):
    """このコマンドで Claude が書いたとみなせる after 側の行番号（1 始まり）。

    差分に出た行のうち、中身がコマンドに書かれていて、実行前のファイルには無い行だけ。
    mv / cp / gh pr view で持ってきた既存の本文、並行する別の書き込み、行の移動は
    差分に出てもコマンドに中身が無いので外れる。
    """
    a = (before or "").split("\n")
    b = after.split("\n")
    existed = set(a)
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=len(b) > _LARGE_FILE_LINES)
    authored = set()
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag not in ("replace", "insert"):
            continue
        for j in range(j1, j2):
            line = b[j]
            m = pl.BULLET_RE.match(line)
            key = (m.group(2) if m else line).strip()[:_AUTHORED_PROBE]
            if key and line not in existed and key in command:
                authored.add(j + 1)
    return authored


def _issue_span(lines, lineno):
    """指摘が覆う行番号。バレットの指摘は先頭行に付くので、継続行まで広げる。"""
    span = {lineno}
    if not pl.BULLET_RE.match(lines[lineno - 1]):
        return span
    j = lineno
    while j < len(lines):
        nxt = lines[j]
        if not nxt.strip() or pl.BULLET_RE.match(nxt) or pl.HEADING_RE.match(nxt) \
           or pl.FENCE_RE.match(nxt) or pl.TABLE_RE.match(nxt):
            break
        j += 1
        span.add(j)
    return span


def handle_post_bash(payload):
    snap = _snapshot_path(payload.get("tool_use_id"))
    raw = _read_or_none(snap) if snap else None
    if raw is None:
        return 0
    os.remove(snap)  # 中身が壊れていても控えは残さない
    _prune_snapshots()
    before_map = json.loads(raw)
    command = (payload.get("tool_input") or {}).get("command") or ""

    reports = []
    for path, before in sorted(before_map.items()):
        after = _read_or_none(path)
        if after is None or after == before:
            continue
        authored = _authored_lines(before, after, command)
        if not authored:
            continue
        # 断片を切り出すとコードブロックや表の文脈が消えるので、全文を判定して
        # Claude が書いた行に当たる指摘だけを残す
        lines = after.split("\n")
        issues = [x for x in pl.lint(after) if _issue_span(lines, x.line) & authored]
        if pl.count(issues, "block"):
            scope = "Bash で書いた箇所" if before is not None else "Bash で作った全文"
            reports.append(pl.format_report(issues, "%s（%s）" % (os.path.basename(path), scope)))
    if not reports:
        return 0
    sys.stderr.write(
        "\n\n".join(reports)
        + "\n\n上の block を直してから次に進むこと。"
          " 直すときは全文を書き直さず、該当箇所だけ触ること。"
        + CRITERIA
    )
    return 2


def last_assistant_text(transcript_path):
    """transcript の末尾から、直近の assistant テキストを 1 件返す。"""
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    rows = []
    with open(transcript_path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(line)
    for line in reversed(rows[-400:]):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("type") != "assistant":
            continue
        msg = rec.get("message") or {}
        if msg.get("role") != "assistant":
            continue
        chunks = [b.get("text") or "" for b in (msg.get("content") or [])
                  if isinstance(b, dict) and b.get("type") == "text"]
        text = "\n".join(c for c in chunks if c.strip())
        if text.strip():
            return text
    return ""


def _written_parts(tool, tool_input):
    """今回の書き込みで新しく入った本文だけを返す。

    Edit でファイル全体を検査すると、変更と無関係な既存違反が編集のたびに
    再掲される。直しようのない指摘でコンテキストを食うだけなので、
    Edit / MultiEdit は new_string に絞る。Write は全文が自分の書いたもの。
    """
    if tool == "Write":
        return [tool_input.get("content") or ""]
    if tool == "Edit":
        return [tool_input.get("new_string") or ""]
    if tool == "MultiEdit":
        return [e.get("new_string") or ""
                for e in (tool_input.get("edits") or []) if isinstance(e, dict)]
    return []


def _offset_of(haystack, needle):
    """検査した断片がファイル内で始まる行番号。見つからなければ 0。"""
    if not haystack or not needle:
        return 0
    idx = haystack.find(needle)
    if idx < 0:
        return 0
    return haystack.count("\n", 0, idx)


def handle_post_tool_use(payload, cfg):
    tool = payload.get("tool_name") or ""
    if tool not in EDIT_TOOLS:
        return 0
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0
    path = tool_input.get("file_path") or ""
    if not is_watched(path, cfg):
        return 0

    parts = [p for p in _written_parts(tool, tool_input) if p.strip()]
    if not parts:
        return 0

    try:
        with open(path, encoding="utf-8") as f:
            whole = f.read()
    except OSError as exc:
        sys.stderr.write("[prose] %s を読めなかった: %s\n" % (path, exc))
        whole = ""

    issues = []
    for part in parts:
        base = _offset_of(whole, part)
        for issue in pl.lint(part):
            issue.line += base
            issues.append(issue)
    issues.sort(key=lambda x: (x.line, x.rule))

    if not pl.count(issues, "block"):
        return 0

    scope = "書いた箇所" if tool != "Write" else "全文"
    report = pl.format_report(issues, "%s（%s）" % (os.path.basename(path), scope))
    sys.stderr.write(
        report
        + "\n\n上の block を直してから次に進むこと。"
          " 直すときは全文を書き直さず Edit で該当箇所だけ触ること。"
        + CRITERIA
    )
    return 2  # stderr が Claude にフィードバックされる


def handle_stop(payload):
    if payload.get("stop_hook_active"):
        return 0  # 書き直しは 1 ターン 1 回だけ
    text = last_assistant_text(payload.get("transcript_path"))
    if not text or len(text) < 200:
        return 0
    issues = pl.lint(text)
    if pl.count(issues, "block") < CHAT_BLOCK_MIN:
        return 0
    report = pl.format_report(issues, "直前の回答", limit=8)
    emit({
        "decision": "block",
        "reason": report + "\n\n同じ内容のまま、上の block だけ直して回答し直すこと。"
                           " 情報は削らず、長い行と長いバレットを分割する。",
    })
    return 0


def main():
    if os.path.exists(DISABLE_FLAG):
        return 0
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        return 0
    cfg = load_config()
    event = payload.get("hook_event_name") or ""
    tool = payload.get("tool_name") or ""
    is_bash = tool == "Bash"
    if event == "PreToolUse" and is_bash:
        return handle_pre_bash(payload, cfg)
    if event == "PreToolUse" and tool.startswith("mcp__") and _tool_tail(tool) in cfg["post_tools"]:
        return handle_pre_post(payload, cfg)
    # コマンドが失敗すると PostToolUse ではなく PostToolUseFailure が来る
    if event in ("PostToolUse", "PostToolUseFailure") and is_bash:
        return handle_post_bash(payload)
    if event == "PostToolUse":
        return handle_post_tool_use(payload, cfg)
    if event == "Stop":
        return handle_stop(payload)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except Exception as exc:  # hook のバグで作業を止めない
        sys.stderr.write("[prose-guard] internal error (ignored): %s\n" % exc)
        sys.exit(0)
