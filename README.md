# prose-guard

Claude Code に、読みやすい日本語の文章を書かせるプラグインです。

文章の書き方をセッションのたびに指示し、行やバレットの長さを数値で判定します。
違反した文章は、hook が差し戻して書き直させます。

## 何をするか

5つの層で文章を整えます。

| 層 | 中身 | 効き方 |
| --- | --- | --- |
| 文章スタイル | 敬体、1文1主張、段落の区切り方、穴埋めの禁止 | セッション開始時に注入 |
| 承認サマリー | spec と plan の冒頭に置く要約の項目 | スキル `prose-guard:approval-summary` |
| PR 本文 | 変更点を段落から書く、2段の箇条書き | セッション開始時に注入 |
| 出力の既定 | 簡潔さ、平易な指摘、コピペ用のコードブロック | セッション開始時に注入 |
| 文章リンタ | 行、バレット、段落の長さの判定 | hook が違反を差し戻す |

## 入れ方

```text
/plugin marketplace add kazmaw/prose-guard
/plugin install prose-guard@prose-guard
```

入れたあとの新しいセッションから効きます。

前提として Python 3 が要ります。外部ライブラリは使いません。

## 設定しなくても効くもの

- `*pr-body*.md` と `*pr_body*.md` への書き込みの検査
- `gh pr`・`gh issue`・`gh api` で送る本文の、送信前の検査
- チャット回答の検査
- 文章ルールの注入と、承認サマリーのスキル

## 設定

`~/.claude/prose-guard.json` に書きます。
ファイルが無ければ、上の「設定しなくても効くもの」だけで動きます。

```json
{
  "watch_dirs": ["~/Documents/ObsidianVault"],
  "skip_globs": ["*/maps/取り込みダイジェスト.md"],
  "post_tools": ["slack_send_message", "clickup_create_task_comment", "clickup_update_task"],
  "replacing_tools": ["clickup_update_task"]
}
```

同じ内容を `prose-guard.example.json` に置いています。

| キー | 意味 |
| --- | --- |
| `watch_dirs` | 配下の `.md` をすべて検査するディレクトリ。`~` も使えます |
| `skip_globs` | 検査しないファイルの glob。絶対パスと照合し、`*` は `/` もまたぎます |
| `post_tools` | 送信前に検査して止める MCP ツールの名前 |
| `replacing_tools` | `post_tools` のうち、本文を丸ごと置き換えるもの |

- **`watch_dirs` の除外**
  - 配下のドット始まりのフォルダ（`.obsidian` や `.trash`）は検査しません。
- **`post_tools` の書き方**
  - MCP ツール名の最後の `__` より後ろを書きます。
  - `mcp__claude_ai_Slack__slack_send_message` なら `slack_send_message` です。
- **`replacing_tools` の効き方**
  - 会話の中で読み込んだ既存の行は、検査から外します。
  - 他の人が書いた文を理由に、差し戻さないためです。

設定を読めないときは、stderr に警告を出して既定値で動きます。

ClickUp と Slack の主なツール名は次のとおりです。

| サービス | 投稿 | 本文の置き換え |
| --- | --- | --- |
| ClickUp | `clickup_create_task_comment`、`clickup_create_comment`、`clickup_send_chat_message`、`clickup_create_task`、`clickup_create_document`、`clickup_create_document_page` | `clickup_update_comment`、`clickup_update_task`、`clickup_update_document_page` |
| Slack | `slack_send_message`、`slack_send_message_draft`、`slack_schedule_message` | なし |

置き換えの列のツールは、`post_tools` と `replacing_tools` の両方に書きます。

## 判定ルール

| ルール | 条件 | 重さ |
| --- | --- | --- |
| `long_line` | 散文の1行が100字を超える | warn |
| `long_line` | 散文の1行が140字を超える | block |
| `fat_bullet` | 1つのバレットが100字を超える | block |
| `multi_sentence_bullet` | 1つのバレットに3文以上ある | block |
| `arrow_reason` | バレットで `←` を使って理由をつなぐ | block |
| `inline_labels` | `**ラベル**:` が1行に2つ以上ある | block |
| `long_paragraph` | 散文が4行以上続く | warn |

- 日本語が3割に満たない行は、上限を1.6倍に緩めます。
- コードブロック・表・引用・見出し・frontmatter は数えません。
- URL を含む行は、長さを数えません。
- インラインコードは4字として数え、リンクは表示される文字だけを数えます。

差し戻すかどうかは、経路ごとに決まっています。

| 経路 | 差し戻す条件 |
| --- | --- |
| Write と Edit | 書いた箇所に block が1件以上ある |
| Bash | コマンドで書いた行に block が1件以上ある |
| MCP の投稿と `gh` | 本文に block が1件以上ある（送信前に止めます） |
| チャット回答 | 200字以上で block が2件以上ある（1ターンに1回まで） |

## 一時停止

```sh
touch ~/.claude/.prose-guard-off   # 止める
rm ~/.claude/.prose-guard-off      # 再開する
```

止めている間は、指示の注入も検査もしません。

## 更新

```text
/plugin marketplace update prose-guard
```

そのあと `claude plugin update prose-guard@prose-guard` を実行し、新しいセッションを始めます。

## テスト

```sh
python3 -m unittest discover -s tests
```

## ライセンス

MIT です。詳しくは `LICENSE` を見てください。
