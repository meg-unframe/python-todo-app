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
- パスワードログイン（APP_PASSWORD。5回失敗で15分ロック）
- 繰り返しTodo（毎日 / 毎週 / 毎月）。完了にすると次の回を自動作成する（詳細は下記）

### スプレッドシートの列（1行目がヘッダー）
`id, title, content, due_date, category, priority, completed, created_at, updated_at, repeat, series_id`

- `id`: UUID（32桁の16進文字列）
- `due_date`: `YYYY-MM-DD`（空欄可）
- `category`: `salon` / `ai_business` / `home` / `other`
- `priority`: `high` / `medium` / `low`
- `completed`: `TRUE` / `FALSE`
- `created_at` / `updated_at`: `YYYY-MM-DD HH:MM:SS`（APP_TIMEZONE の時刻）
- `repeat`: `none` / `daily` / `weekly` / `monthly`（空欄は `none` 扱い）
- `series_id`: 繰り返しの系列ID（32桁の16進文字列。繰り返しなしは空欄）
- 列は必ず末尾に追加する（既存の列の順番は変えない）。1行目が旧バージョンのヘッダー（`COLUMNS` の先頭部分）なら、起動後の最初のアクセスで足りない列名だけ自動で書き足す。データ行は書き換えない。

### 繰り返しTodoの仕様
- 繰り返しにする場合は期日が必須。
- 完了にしたときに次の回を1件作る（タイトル・内容・カテゴリ・優先度・繰り返し・`series_id` を引き継ぐ）。
- 次の期日: 毎日 +1日、毎週 +7日、毎月は翌月の同じ日（無い日は月末。基準日は系列で最も早い期日の「日」なので 1/31 → 2/28 → 3/31）。
- 遅れて完了した場合は、今日以降の最初の回まで進める。
- 同じ系列に後の期日のTodoがすでにあれば作らない（完了↔未完了を繰り返しても増えない）。未完了に戻しても次の回は消さない。
- 編集で繰り返しを「なし」にすると `series_id` を外す。

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
│   ├── login.html        # ログイン
│   └── error.html        # エラー表示
├── static/style.css      # スタイル（スマホ対応）
├── tests/test_app.py     # 自動テスト（メモリ保存で実行）
├── requirements.txt      # 本番用依存パッケージ
├── requirements-dev.txt  # 開発・テスト用依存パッケージ
├── Procfile              # 公開用起動コマンド（gunicorn）
├── .python-version       # Render で使う Python のバージョン
├── .env.example          # 環境変数の見本（秘密情報なし）
├── .gitignore            # .env / credentials.json などを除外
└── .claude/settings.json # Claude Code の権限設定
```

## セキュリティ
- 認証情報・秘密鍵をコードに直接書かない。すべて環境変数から読む。
- `.env` と `credentials.json`（およびサービスアカウント鍵JSON）は `.gitignore` 済み。絶対にコミット・pushしない。
- `.env.example` にはダミー値のみを書く。
- 本番では `SECRET_KEY` を必ず長いランダム値にする（未設定時は起動エラー）。
- 全ページにログインが必要（`APP_PASSWORD`、8文字以上。未設定時は起動エラー）。ログイン失敗が5回続くと、そのIPを15分ロックする。
- Render上ではセッションCookieに Secure 属性を自動で付ける（`RENDER` 環境変数で判定）。
- リポジトリは GitHub で公開（public）されているため、秘密情報は絶対にコミットしない。
- フォームのPOSTにはCSRFトークンを付与して検証する。
- スプレッドシートへの書き込みは `RAW` で行い、`=` で始まる入力が数式として実行されないようにする。
- 完了切替・削除はPOSTのみ受け付ける。
- Claude Code は `.env` や鍵ファイルを読まない・`git push` しない（`.claude/settings.json` で拒否）。

## 環境変数
| 名前 | 説明 |
|---|---|
| `SECRET_KEY` | Flaskのセッション署名キー（必須） |
| `APP_PASSWORD` | ログイン用パスワード（必須・8文字以上） |
| `STORAGE_BACKEND` | `sheets`（既定）または `memory`（動作確認用・再起動で消える） |
| `SPREADSHEET_ID` | 保存先スプレッドシートのID |
| `WORKSHEET_NAME` | シート名（既定: `todos`。無ければ自動作成） |
| `GOOGLE_APPLICATION_CREDENTIALS` | サービスアカウント鍵JSONのファイルパス（ローカル向け） |
| `GOOGLE_CREDENTIALS_JSON` | 鍵JSONの中身そのもの（ファイルを置けないサーバー向け） |
| `APP_TIMEZONE` | 期限判定のタイムゾーン（既定: `Asia/Tokyo`） |
| `SESSION_COOKIE_SECURE` | `1` でCookieをHTTPS限定にする（Render上では自動で `1`） |

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
登録・一覧・編集・完了切替・削除・入力チェック・期限切れ表示・期日順・CSRF・ログイン（ロック、ログアウト、外部URLへのリダイレクト防止）・繰り返し（次回日付の計算、次の回の作成と重複防止）・旧ヘッダーのシートの自動拡張を確認する。

## 公開方法（Render）
1. GitHub（public リポジトリ `meg-unframe/python-todo-app`）へ push する。`.env` と鍵JSONが含まれていないことを `git status` で確認する。
2. Render で「New」→「Web Service」→ GitHub リポジトリを選択する。
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `gunicorn app:app`（Procfileと同じ。ポートは Render の `PORT` を gunicorn が自動で使う）
5. Environment に次を設定する。
   - `SECRET_KEY`（ローカルとは別の新しいランダム値）
   - `APP_PASSWORD`
   - `STORAGE_BACKEND=sheets`、`SPREADSHEET_ID`、`WORKSHEET_NAME=todos`、`APP_TIMEZONE=Asia/Tokyo`
   - 認証情報は Secret Files に `credentials.json` をアップロードし、`GOOGLE_APPLICATION_CREDENTIALS=/etc/secrets/credentials.json` を設定する（`GOOGLE_CREDENTIALS_JSON` に中身を入れる方法でも可）。
6. デプロイ後、ログイン → 登録 → 編集 → 完了切替 → 削除を確認する。
7. 列を追加するバージョンをデプロイする前に、スプレッドシートを「ファイル → コピーを作成」でバックアップする。
