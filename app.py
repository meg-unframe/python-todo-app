"""Life & Work TODO — Flaskアプリ本体。"""

import os
import secrets
import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

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

TITLE_MAX = 100
CONTENT_MAX = 2000


def create_app(repository=None, config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", ""),
        APP_TIMEZONE=os.environ.get("APP_TIMEZONE", "Asia/Tokyo"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=64 * 1024,
    )
    if config:
        app.config.update(config)

    if not app.config["SECRET_KEY"] or app.config["SECRET_KEY"] == "change-me":
        raise RuntimeError(
            "SECRET_KEY が未設定です。.env または環境変数に長いランダム値を設定してください。"
        )

    # 保存先は最初のリクエスト時に接続する（起動時にGoogleへ接続しないため）
    app.extensions["todo_repository"] = repository

    register_routes(app)
    return app


# ---------- 共通処理 ----------


def get_repo():
    repo = current_app.extensions.get("todo_repository")
    if repo is None:
        repo = create_repository_from_env()
        current_app.extensions["todo_repository"] = repo
    return repo


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


def validate_form(form):
    """フォーム入力を検証し、(値の辞書, エラーの一覧) を返す。"""
    data = {
        "title": form.get("title", "").strip(),
        "content": form.get("content", "").strip(),
        "due_date": form.get("due_date", "").strip(),
        "category": form.get("category", "other"),
        "priority": form.get("priority", "medium"),
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
    return data, errors


def decorate(todo, today):
    """表示用の情報（期限切れ・残り日数など）を追加する。"""
    due = parse_date(todo.get("due_date"))
    todo["due"] = due
    todo["days_left"] = (due - today).days if due else None
    todo["is_overdue"] = bool(due and due < today and not todo["completed"])
    todo["is_due_today"] = bool(due and due == today and not todo["completed"])
    todo["category_label"] = CATEGORIES.get(todo.get("category"), "その他")
    todo["priority_label"] = PRIORITIES.get(todo.get("priority"), "中")
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
        }

    @app.before_request
    def check_csrf():
        if request.method == "POST":
            sent = request.form.get("csrf_token", "")
            expected = session.get("csrf_token", "")
            if not expected or not secrets.compare_digest(sent, expected):
                abort(400, description="フォームの有効期限が切れました。画面を再読み込みしてやり直してください。")

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
            }
            get_repo().add(todo)
            flash(f"「{todo['title']}」を登録しました。", "success")
            return redirect(url_for("index"))

        blank = {"title": "", "content": "", "due_date": "", "category": "other", "priority": "medium"}
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
            todo["updated_at"] = now_local().strftime("%Y-%m-%d %H:%M:%S")
            if not repo.update(todo):
                abort(404)
            flash(f"「{todo['title']}」を更新しました。", "success")
            return redirect(url_for("index"))

        return render_template("form.html", todo=todo, errors=[], mode="edit")

    @app.post("/todos/<todo_id>/toggle")
    def toggle(todo_id):
        repo = get_repo()
        todo = repo.get(todo_id)
        if todo is None:
            abort(404)
        todo["completed"] = not todo["completed"]
        todo["updated_at"] = now_local().strftime("%Y-%m-%d %H:%M:%S")
        repo.update(todo)
        message = "完了にしました。" if todo["completed"] else "未完了に戻しました。"
        flash(f"「{todo['title']}」を{message}", "success")
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
            return redirect(url_for("index"))
        return render_template("confirm_delete.html", todo=decorate(todo, today_local()))

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
    next_url = request.form.get("next", "")
    if next_url.startswith("/") and not next_url.startswith("//") and "\\" not in next_url:
        return next_url
    return url_for("index")


app = create_app()
