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
- Googleカレンダー連携（`GOOGLE_CALENDAR_ID` を設定したときだけ。期日に終日の予定を作る。詳細は下記）
- LINE通知（cron-job.org から毎朝8時に呼び出し、期限切れ・今日・明日が期日の未完了TodoをLINEへ送る。詳細は下記）

### スプレッドシートの列（1行目がヘッダー）
`id, title, content, due_date, category, priority, completed, created_at, updated_at, repeat, series_id, calendar_event_id, notified_on`

- `id`: UUID（32桁の16進文字列）
- `due_date`: `YYYY-MM-DD`（空欄可）
- `category`: `salon` / `ai_business` / `home` / `other`
- `priority`: `high` / `medium` / `low`
- `completed`: `TRUE` / `FALSE`
- `created_at` / `updated_at`: `YYYY-MM-DD HH:MM:SS`（APP_TIMEZONE の時刻）
- `repeat`: `none` / `daily` / `weekly` / `monthly`（空欄は `none` 扱い）
- `series_id`: 繰り返しの系列ID（32桁の16進文字列。繰り返しなしは空欄）
- `calendar_event_id`: Googleカレンダーの予定ID（未連携は空欄）
- `notified_on`: 最後にLINEで通知した日（`YYYY-MM-DD`。未通知は空欄）
- 列は必ず末尾に追加する（既存の列の順番は変えない）。1行目が旧バージョンのヘッダー（`COLUMNS` の先頭部分）なら、起動後の最初のアクセスで足りない列名だけ自動で書き足す。データ行は書き換えない。

### 繰り返しTodoの仕様
- 繰り返しにする場合は期日が必須。
- 完了にしたときに次の回を1件作る（タイトル・内容・カテゴリ・優先度・繰り返し・`series_id` を引き継ぐ）。
- 次の期日: 毎日 +1日、毎週 +7日、毎月は翌月の同じ日（無い日は月末。基準日は系列で最も早い期日の「日」なので 1/31 → 2/28 → 3/31）。
- 遅れて完了した場合は、今日以降の最初の回まで進める。
- 同じ系列に後の期日のTodoがすでにあれば作らない（完了↔未完了を繰り返しても増えない）。未完了に戻しても次の回は消さない。
- 編集で繰り返しを「なし」にすると `series_id` を外す。

### Googleカレンダー連携の仕様（`calendar_sync.py`）
- 期日があるTodoだけ、期日に終日の予定を作る（タイトル＝Todoのタイトル、説明＝内容）。
- 編集: 予定があれば更新（手動で消されていたら作り直す）、期日を消したら予定を削除、予定が無ければ作る。
- 完了切替: 予定があればタイトル先頭の「✅」を付け外しする（予定の無い古いTodoには作らない）。
- 繰り返しの次の回にも予定を作る。Todoを削除したら予定も削除する。
- Todoの保存を優先する。カレンダーで失敗してもTodoは保存し、画面に警告を出す（通信は10秒で打ち切る）。
- 過去のTodoの一括登録はしない（編集して保存すると連携される）。

### LINE通知の仕様（`notifier.py`・`POST /tasks/notify-due`）
- LINE Messaging API の push メッセージで、自分（`LINE_USER_ID`）へ1日1通にまとめて送る（LINE Notify は終了済みのため使わない）。
- 対象: 未完了で、期限切れ・今日が期日・明日が期日のTodo。完了済み・期日なし・明後日以降は送らない。
- 対象が0件なら送信せず `200 {"status": "no_targets"}` を返す。
- 送信に成功したTodoの `notified_on` に今日の日付を入れ、同じ日に再度呼ばれても重複して送らない（期限切れは翌日また送る）。
- 認証: `Authorization: Bearer <NOTIFY_TOKEN>`（32文字以上。未設定・短い場合は常に401）。このURLだけログインとCSRFの対象外。GETは405。
- 応答: 送信 `200 sent` ／ 認証失敗 `401` ／ LINE未設定 `503` ／ LINE API失敗 `502`（通知日は記録しない）。
- メッセージは5000文字を超えたら省略する。エラーのログにはトークンを出さない。

## 技術構成
- Python 3.11+ / Flask / Jinja2 / HTML / CSS
- gspread / google-auth（サービスアカウント認証）
- Google Calendar API（google-auth の AuthorizedSession + requests で REST を呼ぶ）
- LINE Messaging API（requests で push メッセージを送る）
- cron-job.org（毎朝の通知の呼び出し）
- gunicorn（本番サーバー）
- pytest（テスト）

## ファイル構成
```
.
├── CLAUDE.md             # このファイル（開発メモ）
├── app.py                # Flaskアプリ本体（ルーティング・入力チェック・CSRF対策）
├── storage.py            # 保存先（Googleスプレッドシート / メモリ）の実装
├── calendar_sync.py      # Googleカレンダー連携（予定の作成・更新・削除）
├── notifier.py           # LINE通知（Messaging API の push、本文の組み立て）
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
- `LINE_CHANNEL_ACCESS_TOKEN`・`LINE_USER_ID`・`NOTIFY_TOKEN` は環境変数だけに置く（cron-job.org にはヘッダーとして `NOTIFY_TOKEN` だけを登録する）。
- カレンダーの権限は `calendar.events`（予定の読み書き）だけ。サービスアカウントには連携用のカレンダーだけを共有する。
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
| `GOOGLE_CALENDAR_ID` | 連携するGoogleカレンダーのID（空欄なら連携しない） |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Messaging API のチャネルアクセストークン（秘密） |
| `LINE_USER_ID` | 通知を送る自分のLINEユーザーID（秘密扱い） |
| `NOTIFY_TOKEN` | 通知用URLの認証トークン（秘密・32文字以上） |
| `APP_BASE_URL` | 通知メッセージに付けるアプリのURL（任意） |
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
登録・一覧・編集・完了切替・削除・入力チェック・期限切れ表示・期日順・CSRF・ログイン（ロック、ログアウト、外部URLへのリダイレクト防止）・繰り返し（次回日付の計算、次の回の作成と重複防止）・旧ヘッダーのシートの自動拡張・Googleカレンダー連携（偽のカレンダー／偽の通信で、作成・更新・削除・✅・失敗時の動き）を確認する。
LINE通知（対象の選び方・完了済みの除外・対象なし・重複防止・認証失敗・GET拒否・LINE API失敗・未設定・文字数上限）も確認する。
テストでは `GOOGLE_CALENDAR_ID`・LINEの変数・`NOTIFY_TOKEN` を空にするため、本物のカレンダーやLINEには接続しない。

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
   - カレンダー連携を使う場合は `GOOGLE_CALENDAR_ID`。事前に Google Cloud で Calendar API を有効にし、Googleカレンダーの「設定と共有」でサービスアカウントのメールアドレスを「予定の変更」権限で追加する。カレンダーIDは同じ画面の「カレンダーの統合」にある。
   - LINE通知を使う場合は `LINE_CHANNEL_ACCESS_TOKEN`・`LINE_USER_ID`・`NOTIFY_TOKEN`（ローカルとは別の値）・`APP_BASE_URL`。
     LINE Developers で Messaging API チャネルを作り、公式アカウントを自分のLINEで友だち追加しておく。
   - cron-job.org でジョブを作る: URL `https://<Renderのドメイン>/tasks/notify-due`、メソッド POST、ヘッダー `Authorization: Bearer <NOTIFY_TOKEN>`、毎日 8:00、タイムゾーン Asia/Tokyo（UTCで設定する場合は前日23:00）。Render無料プランはスリープからの復帰に時間がかかるため、7:55 に `GET /login` を呼ぶ起こし用ジョブを置く。8:05 に同じ通知ジョブをもう1つ置いてもよい（`notified_on` があるので二重には届かない）。
6. デプロイ後、ログイン → 登録 → 編集 → 完了切替 → 削除を確認する（カレンダー連携中は予定の作成・変更・削除も確認する）。
7. 列を追加するバージョンをデプロイする前に、スプレッドシートを「ファイル → コピーを作成」でバックアップする。
