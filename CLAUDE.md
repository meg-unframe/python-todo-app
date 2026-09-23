# Life & Work TODO

## 目的
仕事（サロン・AI事業）と生活（家庭など）のやることを1か所で管理する、スマホでも使いやすい日本語のTODO Webアプリ。
データはGoogleスプレッドシートに保存し、サーバーへ公開できる構成にする。

## 要件

### 必須要件
- やることを新規登録できる
- 登録したやることを編集できる
- タイトル・内容・期日の3項目を設定できる
- データはGoogleスプレッドシートに保存する
- 登録済みTodoを一覧ページで確認できる
- サーバーへ公開できる構成にする

### 追加機能
- カテゴリ（サロン / AI・事業 / 家庭 / その他）
- 優先度（高 / 中 / 低）
- 完了／未完了の切り替え
- Todoの削除（確認画面あり）
- 期日順の表示（期日なしは末尾）
- 期限切れ・今日が期日の表示
- スマホでも見やすい日本語画面（レスポンシブ）

### スプレッドシートの列（1行目がヘッダー）
`id, title, content, due_date, category, priority, completed, created_at, updated_at`

- `id`: UUID（32桁の16進文字列）
- `due_date`: `YYYY-MM-DD`（空欄可）
- `category`: `salon` / `ai_business` / `home` / `other`
- `priority`: `high` / `medium` / `low`
- `completed`: `TRUE` / `FALSE`
- `created_at` / `updated_at`: `YYYY-MM-DD HH:MM:SS`（APP_TIMEZONE の時刻）

## 技術構成
- Python 3.11+ / Flask / Jinja2 / HTML / CSS
- gspread / google-auth（サービスアカウント認証）
- gunicorn（本番サーバー）
- pytest（テスト）

## ファイル構成
```
.
├── CLAUDE.md             # このファイル（開発メモ）
├── app.py                # Flaskアプリ本体（ルーティング・入力チェック・CSRF対策）
├── storage.py            # 保存先（Googleスプレッドシート / メモリ）の実装
├── templates/
│   ├── base.html         # 共通レイアウト
│   ├── index.html        # 一覧ページ
│   ├── form.html         # 新規登録・編集フォーム
│   ├── confirm_delete.html # 削除確認
│   └── error.html        # エラー表示
├── static/style.css      # スタイル（スマホ対応）
├── tests/test_app.py     # 自動テスト（メモリ保存で実行）
├── requirements.txt      # 本番用依存パッケージ
├── requirements-dev.txt  # 開発・テスト用依存パッケージ
├── Procfile              # 公開用起動コマンド（gunicorn）
├── .env.example          # 環境変数の見本（秘密情報なし）
├── .gitignore            # .env / credentials.json などを除外
└── .claude/settings.json # Claude Code の権限設定
```

## セキュリティ
- 認証情報・秘密鍵をコードに直接書かない。すべて環境変数から読む。
- `.env` と `credentials.json`（およびサービスアカウント鍵JSON）は `.gitignore` 済み。絶対にコミット・pushしない。
- `.env.example` にはダミー値のみを書く。
- 本番では `SECRET_KEY` を必ず長いランダム値にする（未設定時は起動エラー）。
- フォームのPOSTにはCSRFトークンを付与して検証する。
- スプレッドシートへの書き込みは `RAW` で行い、`=` で始まる入力が数式として実行されないようにする。
- 完了切替・削除はPOSTのみ受け付ける。
- Claude Code は `.env` や鍵ファイルを読まない・`git push` しない（`.claude/settings.json` で拒否）。

## 環境変数
| 名前 | 説明 |
|---|---|
| `SECRET_KEY` | Flaskのセッション署名キー（必須） |
| `STORAGE_BACKEND` | `sheets`（既定）または `memory`（動作確認用・再起動で消える） |
| `SPREADSHEET_ID` | 保存先スプレッドシートのID |
| `WORKSHEET_NAME` | シート名（既定: `todos`。無ければ自動作成） |
| `GOOGLE_APPLICATION_CREDENTIALS` | サービスアカウント鍵JSONのファイルパス（ローカル向け） |
| `GOOGLE_CREDENTIALS_JSON` | 鍵JSONの中身そのもの（ファイルを置けないサーバー向け） |
| `APP_TIMEZONE` | 期限判定のタイムゾーン（既定: `Asia/Tokyo`） |

## 開発・起動方法
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # 値を編集する
flask --app app run --debug
```
Google未設定で画面だけ確認したい場合は `.env` で `STORAGE_BACKEND=memory` にする。

## テスト方法
```bash
python -m pytest -v
```
テストはメモリ保存で実行するため、Googleの認証情報は不要。
登録・一覧・編集・完了切替・削除・入力チェック・期限切れ表示・期日順・CSRFを確認する。

## 公開方法（例: Render）
1. GitHubへpush（`.env` と鍵JSONが含まれていないことを `git status` で確認）
2. Renderで「New Web Service」→ リポジトリを選択
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `gunicorn app:app`（Procfileと同じ）
5. Environment に `SECRET_KEY`, `SPREADSHEET_ID`, `GOOGLE_CREDENTIALS_JSON`（鍵JSONの中身）などを設定

Railway・Heroku系など Procfile 対応のサービスでも同様に公開できる。
