#!/bin/sh
# prose-guard の hook 用の Python 解決。
#
# bare `python3` は PATH 依存で、pyenv shim が未インストール版を指す・direnv が
# PATH を書き換える等で exit 127 になる。PreToolUse の非ブロッキングエラーは
# 「ツールは許可」なので、強制力だけが黙って消える。それを避けるため、
# 既知の絶対パスを先に試してから PATH に落とす。
#
# stdout は hook の判定 JSON 専用なので、このスクリプトは何も出力しない。

SCRIPT="$1"
shift

for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if [ -x "$candidate" ]; then
    exec "$candidate" "$SCRIPT" "$@"
  fi
done

if command -v python3 >/dev/null 2>&1; then
  exec python3 "$SCRIPT" "$@"
fi

exec python "$SCRIPT" "$@"
