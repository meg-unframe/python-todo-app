"""Life & Work TODO — Flaskアプリ本体。"""

import calendar
import hashlib
import os
import secrets
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix

from calendar_sync import create_calendar_from_env
from notifier import build_message, create_notifier_from_env
from storage import create_repository_from_env

load_dotenv()

CATEGORIES = {
    "salon": "サロン",
    "ai_business": "AI・事業",
    "home": "家庭",
    "other": "その他",
}
PRIORITIES = {"high": "高", "medium": "中", "low": "低"}
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
REPEATS = {"none": "なし", "daily": "毎日", "weekly": "毎週", "monthly": "毎月"}

TITLE_MAX = 100
CONTENT_MAX = 2000

# ログイン失敗が続いたときの一時ロック（総当たり対策）
LOGIN_MAX_FAILURES = 5
LOGIN_LOCK_SECONDS = 15 * 60

# 通知用エンドポイントのトークンの最低文字数（短いと総当たりで当てられるため）
NOTIFY_TOKEN_MIN = 32
NOTIFY_PATH = "/tasks/notify-due"


CALENDAR_WARNING = "Googleカレンダーへの反映に失敗しました（Todoは保存済みです）。"

# create_app(calendar=..., notifier=...) を省略したときは、環境変数から連携の設定を読む
_FROM_ENV = object()


def _env_flag(name, default):
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes")


def create_app(repository=None, config=None, calendar=_FROM_ENV, notifier=_FROM_ENV):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", ""),
        APP_PASSWORD=os.environ.get("APP_PASSWORD", ""),
        APP_TIMEZONE=os.environ.get("APP_TIMEZONE", "Asia/Tokyo"),
        NOTIFY_TOKEN=os.environ.get("NOTIFY_TOKEN", "").strip(),
        APP_BASE_URL=os.environ.get("APP_BASE_URL", "").strip(),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Render上（HTTPS）では自動でSecure属性を付ける
        SESSION_COOKIE_SECURE=_env_flag(
            "SESSION_COOKIE_SECURE", "1" if os.environ.get("RENDER") else "0"
        ),
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        MAX_CONTENT_LENGTH=64 * 1024,
    )
    if config:
        app.config.update(config)

    if not app.config["SECRET_KEY"] or app.config["SECRET_KEY"] == "change-me":
        raise RuntimeError(
            "SECRET_KEY が未設定です。.env または環境変数に長いランダム値を設定してください。"
        )
    if len(app.config["APP_PASSWORD"]) < 8:
        raise RuntimeError(
            "APP_PASSWORD が未設定か短すぎます。.env または環境変数に8文字以上のパスワードを設定してください。"
        )

    # Renderなどのプロキシ経由でも、利用者のIPアドレスとHTTPSを正しく扱う
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    app.extensions["login_failures"] = {}
    app.extensions["login_lock"] = threading.Lock()

    # 保存先は最初のリクエスト時に接続する（起動時にGoogleへ接続しないため）
    app.extensions["todo_repository"] = repository
    app.extensions["calendar_sync"] = calendar
    app.extensions["notifier"] = notifier

    register_routes(app)
    return app


# ---------- 共通処理 ----------


def get_repo():
    repo = current_app.extensions.get("todo_repository")
    if repo is None:
        repo = create_repository_from_env()
        current_app.extensions["todo_repository"] = repo
    return repo


def get_calendar():
    """カレンダー連携を返す。GOOGLE_CALENDAR_ID が未設定なら None（連携しない）。"""
    calendar_sync = current_app.extensions.get("calendar_sync")
    if calendar_sync is _FROM_ENV:
        calendar_sync = create_calendar_from_env()
        current_app.extensions["calendar_sync"] = calendar_sync
    return calendar_sync


def get_notifier():
    """LINE通知を返す。LINEのトークンか送信先が未設定なら None（通知しない）。"""
    notifier = current_app.extensions.get("notifier")
    if notifier is _FROM_ENV:
        notifier = create_notifier_from_env()
        current_app.extensions["notifier"] = notifier
    return notifier


def notify_token_matches(header_value):
    """Authorization: Bearer <NOTIFY_TOKEN> を一定時間で比較する。未設定・短すぎる場合は常に拒否。"""
    expected = current_app.config["NOTIFY_TOKEN"]
    scheme, _, sent = (header_value or "").partition(" ")
    if len(expected) < NOTIFY_TOKEN_MIN or scheme.lower() != "bearer":
        return False
    return secrets.compare_digest(
        hashlib.sha256(sent.strip().encode()).digest(), hashlib.sha256(expected.encode()).digest()
    )


def select_due_notifications(todos, today):
    """通知するTodoを (期限切れ, 今日まで, 明日まで) に分ける。

    完了済み・期日なし・今日すでに通知したTodoは含めない。
    """
    tomorrow = today + timedelta(days=1)
    overdue, due_today, due_tomorrow = [], [], []
    targets = sorted(
        (t for t in todos if t["due"] and not t["completed"]
         and t.get("notified_on") != today.isoformat()),
        key=sort_key_due,
    )
    for todo in targets:
        if todo["due"] < today:
            overdue.append(todo)
        elif todo["due"] == today:
            due_today.append(todo)
        elif todo["due"] == tomorrow:
            due_tomorrow.append(todo)
    return overdue, due_today, due_tomorrow


def sync_calendar(todo, create_missing=True):
    """Todoの期日に合わせて予定を作成・更新・削除し、予定IDを todo に入れる。

    Todoの保存を優先するため、失敗しても例外は出さず False を返す。
    """
    try:
        calendar_sync = get_calendar()
        if calendar_sync is None:
            return True
        event_id = todo.get("calendar_event_id") or ""
        if parse_date(todo.get("due_date")) is None:
            if event_id:
                calendar_sync.delete(event_id)
                todo["calendar_event_id"] = ""
        elif event_id:
            todo["calendar_event_id"] = calendar_sync.update(event_id, todo)
        elif create_missing:
            todo["calendar_event_id"] = calendar_sync.create(todo)
        return True
    except Exception as error:
        current_app.logger.warning("カレンダー連携に失敗しました: %s", error)
        return False


def delete_calendar_event(todo):
    """削除したTodoの予定を消す。失敗しても例外は出さず False を返す。"""
    event_id = todo.get("calendar_event_id") or ""
    try:
        calendar_sync = get_calendar()
        if calendar_sync is not None and event_id:
            calendar_sync.delete(event_id)
        return True
    except Exception as error:
        current_app.logger.warning("カレンダーの予定の削除に失敗しました: %s", error)
        return False


def now_local():
    return datetime.now(ZoneInfo(current_app.config["APP_TIMEZONE"]))


def today_local():
    return now_local().date()


def parse_date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def password_matches(entered):
    # 長さの違いから推測されないよう、ハッシュ同士を一定時間で比較する
    expected = hashlib.sha256(current_app.config["APP_PASSWORD"].encode()).digest()
    return secrets.compare_digest(hashlib.sha256(entered.encode()).digest(), expected)


def login_locked_seconds(ip):
    with current_app.extensions["login_lock"]:
        count, locked_until = current_app.extensions["login_failures"].get(ip, (0, 0))
    return max(0, int(locked_until - time.time()))


def record_login_failure(ip):
    with current_app.extensions["login_lock"]:
        failures = current_app.extensions["login_failures"]
        count, _ = failures.get(ip, (0, 0))
        count += 1
        locked_until = time.time() + LOGIN_LOCK_SECONDS if count >= LOGIN_MAX_FAILURES else 0
        failures[ip] = (0 if locked_until else count, locked_until)


def clear_login_failures(ip):
    with current_app.extensions["login_lock"]:
        current_app.extensions["login_failures"].pop(ip, None)


def safe_local_path(value):
    """同じサイト内のパスだけを許可する（外部URLへのリダイレクト防止）。"""
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return None


def validate_form(form):
    """フォーム入力を検証し、(値の辞書, エラーの一覧) を返す。"""
    data = {
        "title": form.get("title", "").strip(),
        "content": form.get("content", "").strip(),
        "due_date": form.get("due_date", "").strip(),
        "category": form.get("category", "other"),
        "priority": form.get("priority", "medium"),
        "repeat": form.get("repeat", "none"),
    }
    errors = []
    if not data["title"]:
        errors.append("タイトルを入力してください。")
    elif len(data["title"]) > TITLE_MAX:
        errors.append(f"タイトルは{TITLE_MAX}文字以内で入力してください。")
    if len(data["content"]) > CONTENT_MAX:
        errors.append(f"内容は{CONTENT_MAX}文字以内で入力してください。")
    if data["due_date"] and parse_date(data["due_date"]) is None:
        errors.append("期日の形式が正しくありません。")
    if data["category"] not in CATEGORIES:
        errors.append("カテゴリを選択してください。")
    if data["priority"] not in PRIORITIES:
        errors.append("優先度を選択してください。")
    if data["repeat"] not in REPEATS:
        errors.append("繰り返しを選択してください。")
    elif data["repeat"] != "none" and not data["due_date"]:
        errors.append("繰り返しにする場合は期日を入力してください。")
    return data, errors


def add_months(value, months, day):
    """months か月後の day 日を返す。その日が無い月は月末にする（1/31 → 2/28）。"""
    year, month = divmod(value.month - 1 + months, 12)
    year += value.year
    month += 1
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def next_due_date(due, repeat, today, anchor_day=None):
    """繰り返しの次回の期日を返す。遅れて完了した場合は今日以降まで進める。

    anchor_day: 毎月の基準日。月末に丸めた後も元の日（31日など）へ戻せるようにする。
    """
    if repeat in ("daily", "weekly"):
        step = 1 if repeat == "daily" else 7
        candidate = due + timedelta(days=step)
        if candidate < today:
            behind = (today - candidate).days
            candidate += timedelta(days=-(-behind // step) * step)
        return candidate
    if repeat == "monthly":
        day = anchor_day or due.day
        months = 1
        candidate = add_months(due, months, day)
        while candidate < today:
            months += 1
            candidate = add_months(due, months, day)
        return candidate
    return None


def create_next_occurrence(repo, todo, today, stamp):
    """繰り返しTodoの次の回を作る。すでに後の回があれば作らない（重複防止）。

    戻り値は (次の回のTodo または None, カレンダーへ反映できたか)。
    """
    due = parse_date(todo.get("due_date"))
    if todo.get("repeat") not in REPEATS or todo["repeat"] == "none" or due is None:
        return None, True
    series_id = todo["series_id"]
    series_dues = [
        d
        for t in repo.list()
        if t.get("series_id") == series_id
        for d in [parse_date(t.get("due_date"))]
        if d
    ]
    if any(d > due for d in series_dues):
        return None, True
    anchor_day = min(series_dues).day if series_dues else due.day
    next_todo = {
        "id": uuid.uuid4().hex,
        "title": todo["title"],
        "content": todo.get("content", ""),
        "due_date": next_due_date(due, todo["repeat"], today, anchor_day).isoformat(),
        "category": todo.get("category", "other"),
        "priority": todo.get("priority", "medium"),
        "completed": False,
        "created_at": stamp,
        "updated_at": stamp,
        "repeat": todo["repeat"],
        "series_id": series_id,
        "calendar_event_id": "",
        "notified_on": "",
    }
    calendar_ok = sync_calendar(next_todo)
    repo.add(next_todo)
    return next_todo, calendar_ok


def decorate(todo, today):
    """表示用の情報（期限切れ・残り日数など）を追加する。"""
    due = parse_date(todo.get("due_date"))
    todo["due"] = due
    todo["days_left"] = (due - today).days if due else None
    todo["is_overdue"] = bool(due and due < today and not todo["completed"])
    todo["is_due_today"] = bool(due and due == today and not todo["completed"])
    todo["category_label"] = CATEGORIES.get(todo.get("category"), "その他")
    todo["priority_label"] = PRIORITIES.get(todo.get("priority"), "中")
    repeat = todo.get("repeat", "none")
    todo["repeat_label"] = REPEATS[repeat] if repeat in REPEATS and repeat != "none" else ""
    return todo


def apply_series_id(todo):
    """繰り返しありなら系列IDを持たせ、なしなら外す。"""
    if todo.get("repeat", "none") == "none":
        todo["series_id"] = ""
    elif not todo.get("series_id"):
        todo["series_id"] = uuid.uuid4().hex
    return todo


def sort_key_due(todo):
    # 未完了 → 完了、期日の早い順（期日なしは最後）、優先度の高い順
    return (
        todo["completed"],
        todo["due"] is None,
        todo["due"] or date.max,
        PRIORITY_ORDER.get(todo.get("priority"), 1),
        todo.get("created_at", ""),
    )


def sort_key_priority(todo):
    return (
        todo["completed"],
        PRIORITY_ORDER.get(todo.get("priority"), 1),
        todo["due"] is None,
        todo["due"] or date.max,
    )


# ---------- ルーティング ----------


def register_routes(app):
    @app.context_processor
    def inject_globals():
        return {
            "csrf_token": csrf_token,
            "CATEGORIES": CATEGORIES,
            "PRIORITIES": PRIORITIES,
            "REPEATS": REPEATS,
        }

    @app.before_request
    def check_csrf():
        # 通知用エンドポイントは外部（cron）から呼ぶため、CSRFではなくトークンで認証する
        if request.method == "POST" and request.endpoint != "notify_due":
            sent = request.form.get("csrf_token", "")
            expected = session.get("csrf_token", "")
            if not expected or not secrets.compare_digest(sent, expected):
                abort(400, description="フォームの有効期限が切れました。画面を再読み込みしてやり直してください。")

    @app.before_request
    def require_login():
        # 通知用URLは GET などでも 405 を返せるよう、パスで判定してログイン不要にする
        if request.endpoint in ("login", "static") or request.path == NOTIFY_PATH:
            return None
        if session.get("logged_in"):
            return None
        return redirect(url_for("login", next=request.full_path.rstrip("?")))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if session.get("logged_in"):
            return redirect(url_for("index"))
        next_url = safe_local_path(request.values.get("next", "")) or url_for("index")
        error = None
        status = 200
        if request.method == "POST":
            ip = request.remote_addr or "unknown"
            wait = login_locked_seconds(ip)
            if wait:
                error = f"ログインの失敗が続いたため、一時的にロックしています。約{wait // 60 + 1}分後にお試しください。"
                status = 429
            elif password_matches(request.form.get("password", "")):
                clear_login_failures(ip)
                token = session.get("csrf_token")
                session.clear()  # セッション固定化対策
                session["csrf_token"] = token or secrets.token_urlsafe(32)
                session["logged_in"] = True
                session.permanent = True
                return redirect(next_url)
            else:
                record_login_failure(ip)
                error = "パスワードが正しくありません。"
                status = 401
        return render_template("login.html", error=error, next_url=next_url), status

    @app.post("/logout")
    def logout():
        session.clear()
        flash("ログアウトしました。", "success")
        return redirect(url_for("login"))

    @app.get("/")
    def index():
        today = today_local()
        all_todos = [decorate(t, today) for t in get_repo().list()]
        all_open = [t for t in all_todos if not t["completed"]]
        summary = {
            "open": len(all_open),
            "overdue": sum(1 for t in all_open if t["is_overdue"]),
            "today": sum(1 for t in all_open if t["is_due_today"]),
        }

        todos = all_todos
        category = request.args.get("category", "")
        status = request.args.get("status", "open")
        sort = request.args.get("sort", "due")

        if category in CATEGORIES:
            todos = [t for t in todos if t.get("category") == category]
        if status == "open":
            todos = [t for t in todos if not t["completed"]]
        elif status == "done":
            todos = [t for t in todos if t["completed"]]
        else:
            status = "all"

        if sort == "priority":
            todos.sort(key=sort_key_priority)
        elif sort == "created":
            todos.sort(key=lambda t: t.get("created_at", ""), reverse=True)
        else:
            sort = "due"
            todos.sort(key=sort_key_due)

        return render_template(
            "index.html",
            todos=todos,
            summary=summary,
            filters={"category": category, "status": status, "sort": sort},
            today=today,
        )

    @app.route("/todos/new", methods=["GET", "POST"])
    def create():
        if request.method == "POST":
            data, errors = validate_form(request.form)
            if errors:
                return render_template("form.html", todo=data, errors=errors, mode="new"), 400
            stamp = now_local().strftime("%Y-%m-%d %H:%M:%S")
            todo = {
                "id": uuid.uuid4().hex,
                **data,
                "completed": False,
                "created_at": stamp,
                "updated_at": stamp,
                "calendar_event_id": "",
                "notified_on": "",
            }
            apply_series_id(todo)
            calendar_ok = sync_calendar(todo)
            get_repo().add(todo)
            flash(f"「{todo['title']}」を登録しました。", "success")
            if not calendar_ok:
                flash(CALENDAR_WARNING, "warning")
            return redirect(url_for("index"))

        blank = {
            "title": "",
            "content": "",
            "due_date": "",
            "category": "other",
            "priority": "medium",
            "repeat": "none",
        }
        return render_template("form.html", todo=blank, errors=[], mode="new")

    @app.route("/todos/<todo_id>/edit", methods=["GET", "POST"])
    def edit(todo_id):
        repo = get_repo()
        todo = repo.get(todo_id)
        if todo is None:
            abort(404)

        if request.method == "POST":
            data, errors = validate_form(request.form)
            if errors:
                return (
                    render_template("form.html", todo={**todo, **data}, errors=errors, mode="edit"),
                    400,
                )
            todo.update(data)
            apply_series_id(todo)
            todo["updated_at"] = now_local().strftime("%Y-%m-%d %H:%M:%S")
            calendar_ok = sync_calendar(todo)
            if not repo.update(todo):
                abort(404)
            flash(f"「{todo['title']}」を更新しました。", "success")
            if not calendar_ok:
                flash(CALENDAR_WARNING, "warning")
            return redirect(url_for("index"))

        return render_template("form.html", todo=todo, errors=[], mode="edit")

    @app.post("/todos/<todo_id>/toggle")
    def toggle(todo_id):
        repo = get_repo()
        todo = repo.get(todo_id)
        if todo is None:
            abort(404)
        todo["completed"] = not todo["completed"]
        stamp = now_local().strftime("%Y-%m-%d %H:%M:%S")
        todo["updated_at"] = stamp
        apply_series_id(todo)
        # 予定があればタイトルの ✅ を付け外しする（予定が無い古いTodoには作らない）
        calendar_ok = sync_calendar(todo, create_missing=False)
        repo.update(todo)
        message = "完了にしました。" if todo["completed"] else "未完了に戻しました。"
        # 繰り返しTodoを完了にしたら次の回を作る（未完了に戻しても次の回は消さない）
        next_todo = None
        if todo["completed"]:
            next_todo, next_calendar_ok = create_next_occurrence(repo, todo, today_local(), stamp)
            calendar_ok = calendar_ok and next_calendar_ok
        if next_todo:
            due = parse_date(next_todo["due_date"])
            message += f"次回（{due.month}月{due.day}日）のTodoを作成しました。"
        flash(f"「{todo['title']}」を{message}", "success")
        if not calendar_ok:
            flash(CALENDAR_WARNING, "warning")
        return redirect(safe_next_url())

    @app.route("/todos/<todo_id>/delete", methods=["GET", "POST"])
    def delete(todo_id):
        repo = get_repo()
        todo = repo.get(todo_id)
        if todo is None:
            abort(404)
        if request.method == "POST":
            repo.delete(todo_id)
            flash(f"「{todo['title']}」を削除しました。", "success")
            if not delete_calendar_event(todo):
                flash("Googleカレンダーの予定を削除できませんでした。カレンダーから手動で削除してください。", "warning")
            return redirect(url_for("index"))
        return render_template("confirm_delete.html", todo=decorate(todo, today_local()))

    @app.post(NOTIFY_PATH)
    def notify_due():
        """期限切れ・今日まで・明日までの未完了TodoをLINEへ通知する（cron-job.org から毎朝呼ぶ）。"""
        if not notify_token_matches(request.headers.get("Authorization")):
            return jsonify(status="unauthorized"), 401
        notifier = get_notifier()
        if notifier is None:
            return jsonify(status="not_configured"), 503

        repo = get_repo()
        today = today_local()
        todos = [decorate(t, today) for t in repo.list()]
        overdue, due_today, due_tomorrow = select_due_notifications(todos, today)
        targets = overdue + due_today + due_tomorrow
        if not targets:
            return jsonify(status="no_targets", count=0)

        message = build_message(
            overdue, due_today, due_tomorrow, today, current_app.config["APP_BASE_URL"]
        )
        try:
            notifier.push(message)
        except Exception as error:
            current_app.logger.warning("LINE通知に失敗しました: %s", error)
            return jsonify(status="line_error"), 502

        # 同じ日に2回呼ばれても重複して送らないよう、通知した日を記録する
        try:
            for todo in targets:
                saved = repo.get(todo["id"])
                if saved:
                    saved["notified_on"] = today.isoformat()
                    repo.update(saved)
        except Exception as error:  # 送信は済んでいるので成功として返す
            current_app.logger.warning("通知日の記録に失敗しました: %s", error)
        return jsonify(
            status="sent",
            count=len(targets),
            overdue=len(overdue),
            today=len(due_today),
            tomorrow=len(due_tomorrow),
        )

    @app.errorhandler(400)
    def bad_request(error):
        return render_template("error.html", code=400, message=error.description), 400

    @app.errorhandler(404)
    def not_found(error):
        return render_template("error.html", code=404, message="Todoが見つかりませんでした。"), 404

    @app.errorhandler(500)
    def server_error(error):
        return (
            render_template(
                "error.html",
                code=500,
                message="エラーが発生しました。時間をおいて再度お試しください。",
            ),
            500,
        )


def safe_next_url():
    """一覧の絞り込み状態を保ったまま戻る。外部URLへは飛ばさない。"""
    return safe_local_path(request.form.get("next", "")) or url_for("index")


app = create_app()
