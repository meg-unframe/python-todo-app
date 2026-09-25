import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import create_app, next_due_date
from calendar_sync import CalendarError, GoogleCalendarSync, create_calendar_from_env
from notifier import TEXT_MAX, LineNotifier, NotifyError, build_message, create_notifier_from_env
from storage import COLUMNS, MemoryTodoRepository, SheetsTodoRepository


@pytest.fixture
def repo():
    return MemoryTodoRepository()


PASSWORD = "test-password-123"


def login(client, password=PASSWORD, next_url=None):
    html = client.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
    data = {"password": password, "csrf_token": token}
    if next_url is not None:
        data["next"] = next_url
    return client.post("/login", data=data)


@pytest.fixture
def app(repo):
    return create_app(repository=repo, config={"TESTING": True})


@pytest.fixture
def anon_client(app):
    return app.test_client()


@pytest.fixture
def client(app):
    c = app.test_client()
    assert login(c).status_code == 302
    return c


def today():
    return datetime.now(ZoneInfo("Asia/Tokyo")).date()


def get_csrf(client, path="/todos/new"):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def create_todo(client, **overrides):
    data = {
        "title": "予約表の確認",
        "content": "来週分をチェック",
        "due_date": (today() + timedelta(days=3)).isoformat(),
        "category": "salon",
        "priority": "high",
        "csrf_token": get_csrf(client, "/todos/new"),
    }
    data.update(overrides)
    return client.post("/todos/new", data=data, follow_redirects=True)


# ---------- 登録・一覧 ----------


def test_index_empty(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "表示するTodoはありません" in res.get_data(as_text=True)


def test_create_and_list(client, repo):
    res = create_todo(client)
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    assert "を登録しました" in html
    assert "予約表の確認" in html
    assert "来週分をチェック" in html
    assert "サロン" in html
    assert "優先度：高" in html
    assert "あと3日" in html

    saved = repo.list()
    assert len(saved) == 1
    todo = saved[0]
    assert set(COLUMNS) <= set(todo)
    assert todo["completed"] is False
    assert todo["created_at"] == todo["updated_at"]


def test_create_without_due_date(client, repo):
    res = create_todo(client, due_date="")
    assert res.status_code == 200
    assert "期日：なし" in res.get_data(as_text=True)
    assert repo.list()[0]["due_date"] == ""


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"title": "  "}, "タイトルを入力してください"),
        ({"title": "あ" * 101}, "100文字以内"),
        ({"content": "a" * 2001}, "2000文字以内"),
        ({"due_date": "2026-13-40"}, "期日の形式"),
        ({"category": "unknown"}, "カテゴリを選択"),
        ({"priority": "urgent"}, "優先度を選択"),
        ({"repeat": "yearly"}, "繰り返しを選択"),
        ({"repeat": "weekly", "due_date": ""}, "繰り返しにする場合は期日を入力"),
    ],
)
def test_create_validation(client, repo, overrides, message):
    res = create_todo(client, **overrides)
    assert res.status_code == 400
    assert message in res.get_data(as_text=True)
    assert repo.list() == []


def test_html_is_escaped(client):
    res = create_todo(client, title="<script>alert(1)</script>")
    html = res.get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# ---------- 編集 ----------


def test_edit(client, repo):
    create_todo(client)
    todo_id = repo.list()[0]["id"]

    res = client.get(f"/todos/{todo_id}/edit")
    assert res.status_code == 200
    assert 'value="予約表の確認"' in res.get_data(as_text=True)

    res = client.post(
        f"/todos/{todo_id}/edit",
        data={
            "title": "確定申告の準備",
            "content": "領収書を整理",
            "due_date": "2026-12-01",
            "category": "ai_business",
            "priority": "low",
            "csrf_token": get_csrf(client),
        },
        follow_redirects=True,
    )
    assert res.status_code == 200
    assert "を更新しました" in res.get_data(as_text=True)

    todo = repo.get(todo_id)
    assert todo["title"] == "確定申告の準備"
    assert todo["content"] == "領収書を整理"
    assert todo["due_date"] == "2026-12-01"
    assert todo["category"] == "ai_business"
    assert todo["priority"] == "low"


def test_edit_validation_keeps_data(client, repo):
    create_todo(client)
    todo_id = repo.list()[0]["id"]
    res = client.post(
        f"/todos/{todo_id}/edit",
        data={"title": "", "category": "home", "priority": "low", "csrf_token": get_csrf(client)},
    )
    assert res.status_code == 400
    assert repo.get(todo_id)["title"] == "予約表の確認"


def test_edit_not_found(client):
    assert client.get("/todos/nope/edit").status_code == 404


# ---------- 完了切替 ----------


def test_toggle_complete(client, repo):
    create_todo(client)
    todo_id = repo.list()[0]["id"]

    res = client.post(
        f"/todos/{todo_id}/toggle",
        data={"csrf_token": get_csrf(client), "next": "/?status=all"},
    )
    assert res.status_code == 302
    assert res.headers["Location"].endswith("/?status=all")
    assert repo.get(todo_id)["completed"] is True

    # 未完了フィルタ（初期表示）には出ず、完了フィルタに出る
    card = '<h2 class="todo-title">予約表の確認</h2>'
    assert card not in client.get("/").get_data(as_text=True)
    assert card in client.get("/?status=done").get_data(as_text=True)

    client.post(f"/todos/{todo_id}/toggle", data={"csrf_token": get_csrf(client)})
    assert repo.get(todo_id)["completed"] is False


def test_toggle_rejects_external_redirect(client, repo):
    create_todo(client)
    todo_id = repo.list()[0]["id"]
    res = client.post(
        f"/todos/{todo_id}/toggle",
        data={"csrf_token": get_csrf(client), "next": "//evil.example.com/"},
    )
    assert res.headers["Location"] == "/"


def test_toggle_requires_post(client, repo):
    create_todo(client)
    todo_id = repo.list()[0]["id"]
    assert client.get(f"/todos/{todo_id}/toggle").status_code == 405


# ---------- 削除 ----------


def test_delete(client, repo):
    create_todo(client)
    todo_id = repo.list()[0]["id"]

    res = client.get(f"/todos/{todo_id}/delete")
    assert res.status_code == 200
    assert "削除しますか" in res.get_data(as_text=True)
    assert repo.get(todo_id) is not None  # GETでは消えない

    res = client.post(
        f"/todos/{todo_id}/delete", data={"csrf_token": get_csrf(client)}, follow_redirects=True
    )
    assert "を削除しました" in res.get_data(as_text=True)
    assert repo.get(todo_id) is None


def test_delete_not_found(client):
    token = get_csrf(client)
    assert client.post("/todos/nope/delete", data={"csrf_token": token}).status_code == 404


# ---------- CSRF ----------


def test_post_without_csrf_is_rejected(client, repo):
    client.get("/")
    res = client.post("/todos/new", data={"title": "x", "category": "home", "priority": "low"})
    assert res.status_code == 400
    assert repo.list() == []


# ---------- 期日順・期限切れ・絞り込み ----------


def test_sorted_by_due_date_and_overdue(client):
    t = today()
    create_todo(client, title="来月のタスク", due_date=(t + timedelta(days=30)).isoformat())
    create_todo(client, title="期日なしタスク", due_date="")
    create_todo(client, title="昨日のタスク", due_date=(t - timedelta(days=1)).isoformat())
    create_todo(client, title="今日のタスク", due_date=t.isoformat())

    html = client.get("/").get_data(as_text=True)
    order = [html.index(name) for name in ["昨日のタスク", "今日のタスク", "来月のタスク", "期日なしタスク"]]
    assert order == sorted(order)

    assert "期限切れ" in html
    assert "1日超過" in html
    assert "（今日）" in html
    assert 'summary-item is-overdue' in html


def test_completed_task_is_not_overdue(client, repo):
    create_todo(client, title="済んだタスク", due_date=(today() - timedelta(days=5)).isoformat())
    todo_id = repo.list()[0]["id"]
    client.post(f"/todos/{todo_id}/toggle", data={"csrf_token": get_csrf(client)})
    html = client.get("/?status=all").get_data(as_text=True)
    assert "済んだタスク" in html
    assert "badge-overdue" not in html


def test_filter_by_category(client):
    create_todo(client, title="サロンの仕事", category="salon")
    create_todo(client, title="家の用事", category="home")
    html = client.get("/?category=home").get_data(as_text=True)
    assert "家の用事" in html
    assert "サロンの仕事" not in html


# ---------- 設定 ----------


def test_missing_secret_key_raises(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "")
    with pytest.raises(RuntimeError):
        create_app(repository=MemoryTodoRepository())


@pytest.mark.parametrize("password", ["", "short"])
def test_missing_or_short_app_password_raises(monkeypatch, password):
    monkeypatch.setenv("APP_PASSWORD", password)
    with pytest.raises(RuntimeError):
        create_app(repository=MemoryTodoRepository())


# ---------- ログイン ----------


@pytest.mark.parametrize("path", ["/", "/todos/new", "/todos/abc/edit", "/todos/abc/delete"])
def test_pages_require_login(anon_client, path):
    res = anon_client.get(path)
    assert res.status_code == 302
    assert "/login" in res.headers["Location"]


def test_post_requires_login(anon_client, repo):
    login_page = anon_client.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', login_page).group(1)
    res = anon_client.post(
        "/todos/new",
        data={"title": "x", "category": "home", "priority": "low", "csrf_token": token},
    )
    assert res.status_code == 302
    assert repo.list() == []


def test_static_is_public(anon_client):
    assert anon_client.get("/static/style.css").status_code == 200


def test_login_success_redirects_to_next(anon_client):
    res = login(anon_client, next_url="/?status=all")
    assert res.status_code == 302
    assert res.headers["Location"].endswith("/?status=all")
    assert anon_client.get("/").status_code == 200


def test_login_rejects_external_next(anon_client):
    res = login(anon_client, next_url="//evil.example.com/")
    assert res.headers["Location"] == "/"


def test_login_wrong_password(anon_client):
    res = login(anon_client, password="wrong-password")
    assert res.status_code == 401
    assert "パスワードが正しくありません" in res.get_data(as_text=True)
    assert anon_client.get("/").status_code == 302


def test_login_lockout_after_failures(anon_client):
    for _ in range(5):
        login(anon_client, password="wrong-password")
    res = login(anon_client)  # 正しいパスワードでもロック中は入れない
    assert res.status_code == 429
    assert "一時的にロック" in res.get_data(as_text=True)
    assert anon_client.get("/").status_code == 302


def test_logout(client):
    token = get_csrf(client)
    res = client.post("/logout", data={"csrf_token": token})
    assert res.status_code == 302
    assert client.get("/").status_code == 302


def test_session_cookie_flags(anon_client):
    res = anon_client.get("/login")
    cookie = res.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


# ---------- スプレッドシート保存（Google APIの代わりに偽のシートを使用） ----------


class FakeWorksheet:
    def __init__(self, rows=None, col_count=26):
        self.rows = rows if rows is not None else []
        self.input_options = []
        self.col_count = col_count

    def add_cols(self, n):
        self.col_count += n

    def row_values(self, n):
        return list(self.rows[n - 1]) if n <= len(self.rows) else []

    def col_values(self, n):
        return [r[n - 1] if len(r) >= n else "" for r in self.rows]

    def get_all_values(self):
        return [list(r) for r in self.rows]

    def append_row(self, values, value_input_option=None, table_range=None):
        self.input_options.append(value_input_option)
        self.rows.append(list(values))

    def update(self, range_name, values, value_input_option=None):
        self.input_options.append(value_input_option)
        m = re.match(r"A(\d+)", range_name)
        row = int(m.group(1))
        while len(self.rows) < row:
            self.rows.append([])
        self.rows[row - 1] = list(values[0])

    def delete_rows(self, n):
        del self.rows[n - 1]


def make_sheets_repo(ws):
    repo = SheetsTodoRepository.__new__(SheetsTodoRepository)
    repo._ws = ws
    repo._ensure_header()
    return repo


def test_sheets_repository_crud():
    ws = FakeWorksheet()
    repo = make_sheets_repo(ws)
    assert ws.rows[0] == COLUMNS

    todo = {
        "id": "abc123",
        "title": "=IMPORTXML(\"http://example.com\")",
        "content": "",
        "due_date": "2026-10-01",
        "category": "home",
        "priority": "medium",
        "completed": False,
        "created_at": "2026-09-23 10:00:00",
        "updated_at": "2026-09-23 10:00:00",
    }
    repo.add(todo)
    assert ws.rows[1][6] == "FALSE"
    assert repo.get("abc123")["title"].startswith("=IMPORTXML")
    assert repo.list()[0]["completed"] is False

    todo["completed"] = True
    assert repo.update(todo) is True
    assert ws.rows[1][6] == "TRUE"
    assert repo.get("abc123")["completed"] is True

    # 数式として解釈されないよう、すべてRAWで書き込む
    assert set(ws.input_options) == {"RAW"}

    assert repo.update({**todo, "id": "missing"}) is False
    assert repo.delete("abc123") is True
    assert repo.list() == []
    assert repo.delete("abc123") is False


def test_sheets_repository_rejects_unexpected_header():
    ws = FakeWorksheet(rows=[["name", "memo"]])
    with pytest.raises(RuntimeError):
        make_sheets_repo(ws)


def test_secure_cookie_on_render(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    app = create_app(repository=MemoryTodoRepository())
    assert app.config["SESSION_COOKIE_SECURE"] is True


# ---------- 繰り返しTodo ----------

OLD_COLUMNS = COLUMNS[:9]  # 繰り返し機能を追加する前のヘッダー


@pytest.mark.parametrize(
    "due, repeat, today_, anchor, expected",
    [
        ("2030-05-10", "daily", "2030-05-10", None, "2030-05-11"),
        ("2030-05-10", "weekly", "2030-05-10", None, "2030-05-17"),
        ("2030-05-10", "monthly", "2030-05-10", None, "2030-06-10"),
        ("2030-12-15", "monthly", "2030-12-15", None, "2031-01-15"),
        # その日が無い月は月末、翌月は基準日（31日）に戻る
        ("2030-01-31", "monthly", "2030-01-31", None, "2030-02-28"),
        ("2032-01-31", "monthly", "2032-01-31", None, "2032-02-29"),
        ("2030-02-28", "monthly", "2030-02-28", 31, "2030-03-31"),
        # 遅れて完了したら今日以降の最初の回まで進める（曜日・日付は保つ）
        ("2030-05-01", "daily", "2030-05-10", None, "2030-05-10"),
        ("2030-05-06", "weekly", "2030-05-21", None, "2030-05-27"),
        ("2030-01-15", "monthly", "2030-04-20", None, "2030-05-15"),
    ],
)
def test_next_due_date(due, repeat, today_, anchor, expected):
    result = next_due_date(date.fromisoformat(due), repeat, date.fromisoformat(today_), anchor)
    assert result == date.fromisoformat(expected)


def toggle(client, todo_id):
    return client.post(
        f"/todos/{todo_id}/toggle", data={"csrf_token": get_csrf(client)}, follow_redirects=True
    )


def test_create_repeat_todo(client, repo):
    html = create_todo(client, repeat="weekly").get_data(as_text=True)
    assert "🔁 毎週" in html
    todo = repo.list()[0]
    assert todo["repeat"] == "weekly"
    assert re.fullmatch(r"[0-9a-f]{32}", todo["series_id"])


def test_complete_repeat_creates_next_once(client, repo):
    due = today() + timedelta(days=3)
    create_todo(client, repeat="weekly", due_date=due.isoformat())
    first = repo.list()[0]

    html = toggle(client, first["id"]).get_data(as_text=True)
    next_due = due + timedelta(days=7)
    assert f"次回（{next_due.month}月{next_due.day}日）のTodoを作成しました" in html

    todos = repo.list()
    assert len(todos) == 2
    new = next(t for t in todos if t["id"] != first["id"])
    assert new["due_date"] == next_due.isoformat()
    assert new["completed"] is False
    for key in ("title", "content", "category", "priority", "repeat", "series_id"):
        assert new[key] == first[key]

    # 未完了に戻しても次の回は残り、もう一度完了にしても増えない
    toggle(client, first["id"])
    assert len(repo.list()) == 2
    html = toggle(client, first["id"]).get_data(as_text=True)
    assert "次回" not in html
    assert len(repo.list()) == 2


def test_complete_late_repeat_rolls_forward(client, repo):
    create_todo(client, repeat="daily", due_date=(today() - timedelta(days=5)).isoformat())
    toggle(client, repo.list()[0]["id"])
    new = next(t for t in repo.list() if not t["completed"])
    assert new["due_date"] == today().isoformat()


def test_monthly_series_keeps_anchor_day(client, repo):
    stamp = "2026-09-25 10:00:00"
    repo.add({
        "id": "m1", "title": "家賃の振込", "content": "", "due_date": "2030-01-31",
        "category": "home", "priority": "high", "completed": False,
        "created_at": stamp, "updated_at": stamp, "repeat": "monthly", "series_id": "s" * 32,
    })
    toggle(client, "m1")
    feb = next(t for t in repo.list() if not t["completed"])
    assert feb["due_date"] == "2030-02-28"
    toggle(client, feb["id"])
    mar = next(t for t in repo.list() if not t["completed"])
    assert mar["due_date"] == "2030-03-31"


def test_complete_normal_todo_does_not_repeat(client, repo):
    create_todo(client)
    html = toggle(client, repo.list()[0]["id"]).get_data(as_text=True)
    assert "次回" not in html
    assert len(repo.list()) == 1


def test_edit_repeat_sets_and_clears_series(client, repo):
    create_todo(client)
    todo = repo.list()[0]
    assert todo["series_id"] == ""
    form = {k: todo[k] for k in ("title", "content", "due_date", "category", "priority")}

    client.post(
        f"/todos/{todo['id']}/edit",
        data={**form, "repeat": "monthly", "csrf_token": get_csrf(client, f"/todos/{todo['id']}/edit")},
    )
    edited = repo.get(todo["id"])
    assert edited["repeat"] == "monthly"
    assert re.fullmatch(r"[0-9a-f]{32}", edited["series_id"])
    assert 'value="monthly" selected' in client.get(f"/todos/{todo['id']}/edit").get_data(as_text=True)

    client.post(
        f"/todos/{todo['id']}/edit",
        data={**form, "repeat": "none", "csrf_token": get_csrf(client, f"/todos/{todo['id']}/edit")},
    )
    assert repo.get(todo["id"])["repeat"] == "none"
    assert repo.get(todo["id"])["series_id"] == ""


def test_sheets_old_header_is_extended_without_touching_data():
    old_row = ["old1", "既存のTodo", "メモ", "2026-10-01", "salon", "high", "FALSE",
               "2026-09-01 09:00:00", "2026-09-01 09:00:00"]
    ws = FakeWorksheet(rows=[list(OLD_COLUMNS), list(old_row)], col_count=9)
    repo = make_sheets_repo(ws)

    assert ws.rows[0] == COLUMNS
    assert ws.col_count >= len(COLUMNS)
    assert ws.rows[1] == old_row  # データ行は書き換えない
    todo = repo.get("old1")
    assert todo["title"] == "既存のTodo"
    assert todo["repeat"] == "none"
    assert todo["series_id"] == ""

    # 既存Todoを繰り返しにして保存すると、新しい列にも書き込まれる
    todo.update(repeat="weekly", series_id="a" * 32)
    assert repo.update(todo) is True
    assert ws.rows[1][len(OLD_COLUMNS):len(OLD_COLUMNS) + 2] == ["weekly", "a" * 32]
    assert set(ws.input_options) == {"RAW"}


def test_sheets_unknown_extra_header_is_rejected():
    ws = FakeWorksheet(rows=[list(OLD_COLUMNS) + ["memo"]])
    with pytest.raises(RuntimeError):
        make_sheets_repo(ws)


def test_sheets_header_from_repeat_version_is_extended():
    # 繰り返し機能までの版（11列）のシートにも、カレンダー用の列だけを書き足す
    header = COLUMNS[: COLUMNS.index("calendar_event_id")]
    row = ["r1", "週次の売上確認", "", "2026-10-01", "salon", "high", "FALSE",
           "2026-09-01 09:00:00", "2026-09-01 09:00:00", "weekly", "b" * 32]
    ws = FakeWorksheet(rows=[list(header), list(row)], col_count=11)
    repo = make_sheets_repo(ws)
    assert ws.rows[0] == COLUMNS
    assert ws.rows[1] == row
    assert repo.get("r1")["calendar_event_id"] == ""


# ---------- Googleカレンダー連携 ----------


class FakeCalendar:
    """GoogleCalendarSync の代わり。予定を辞書に記録する。"""

    def __init__(self):
        self.events = {}
        self.calls = []
        self.fail = False
        self._next = 0

    def _check(self, name):
        self.calls.append(name)
        if self.fail:
            raise CalendarError("テスト用の失敗")

    def create(self, todo):
        self._check("create")
        self._next += 1
        event_id = f"ev{self._next}"
        self.events[event_id] = {"summary": ("✅ " if todo["completed"] else "") + todo["title"],
                                 "date": todo["due_date"]}
        return event_id

    def update(self, event_id, todo):
        self._check("update")
        self.events[event_id] = {"summary": ("✅ " if todo["completed"] else "") + todo["title"],
                                 "date": todo["due_date"]}
        return event_id

    def delete(self, event_id):
        self._check("delete")
        self.events.pop(event_id, None)


@pytest.fixture
def cal():
    return FakeCalendar()


@pytest.fixture
def cal_client(repo, cal):
    c = create_app(repository=repo, config={"TESTING": True}, calendar=cal).test_client()
    assert login(c).status_code == 302
    return c


def edit_todo(client, todo, **overrides):
    path = f"/todos/{todo['id']}/edit"
    data = {k: todo.get(k, "") for k in ("title", "content", "due_date", "category", "priority")}
    data.update(repeat=todo.get("repeat", "none"), csrf_token=get_csrf(client, path))
    data.update(overrides)
    return client.post(path, data=data, follow_redirects=True)


def test_calendar_event_created_on_create(cal_client, repo, cal):
    html = create_todo(cal_client).get_data(as_text=True)
    todo = repo.list()[0]
    assert todo["calendar_event_id"] == "ev1"
    assert cal.events["ev1"] == {"summary": "予約表の確認", "date": todo["due_date"]}
    assert "📅 カレンダー" in html
    assert "Googleカレンダーへの反映に失敗" not in html


def test_calendar_not_used_without_due_date(cal_client, repo, cal):
    create_todo(cal_client, due_date="")
    assert cal.calls == []
    assert repo.list()[0]["calendar_event_id"] == ""


def test_calendar_event_follows_edit(cal_client, repo, cal):
    create_todo(cal_client)
    todo = repo.list()[0]
    new_due = (today() + timedelta(days=10)).isoformat()

    edit_todo(cal_client, todo, title="予約表の再確認", due_date=new_due)
    assert cal.events["ev1"] == {"summary": "予約表の再確認", "date": new_due}

    # 期日を消すと予定も消える
    edit_todo(cal_client, repo.get(todo["id"]), due_date="")
    assert cal.events == {}
    assert repo.get(todo["id"])["calendar_event_id"] == ""

    # 期日を戻すと予定を作り直す
    edit_todo(cal_client, repo.get(todo["id"]), due_date=new_due)
    assert repo.get(todo["id"])["calendar_event_id"] == "ev2"


def test_calendar_created_when_editing_old_todo(cal_client, repo, cal):
    stamp = "2026-09-01 09:00:00"
    repo.add({"id": "old1", "title": "既存のTodo", "content": "", "due_date": "2030-01-10",
              "category": "home", "priority": "low", "completed": False,
              "created_at": stamp, "updated_at": stamp, "repeat": "none", "series_id": "",
              "calendar_event_id": ""})
    edit_todo(cal_client, repo.get("old1"))
    assert repo.get("old1")["calendar_event_id"] == "ev1"


def test_calendar_marks_done_on_toggle(cal_client, repo, cal):
    create_todo(cal_client)
    todo_id = repo.list()[0]["id"]
    toggle(cal_client, todo_id)
    assert cal.events["ev1"]["summary"] == "✅ 予約表の確認"
    toggle(cal_client, todo_id)
    assert cal.events["ev1"]["summary"] == "予約表の確認"


def test_toggle_does_not_create_event_for_old_todo(cal_client, repo, cal):
    stamp = "2026-09-01 09:00:00"
    repo.add({"id": "old1", "title": "既存のTodo", "content": "", "due_date": "2030-01-10",
              "category": "home", "priority": "low", "completed": False,
              "created_at": stamp, "updated_at": stamp})
    toggle(cal_client, "old1")
    assert cal.calls == []


def test_calendar_event_for_next_occurrence(cal_client, repo, cal):
    create_todo(cal_client, repeat="weekly")
    first = repo.list()[0]
    toggle(cal_client, first["id"])
    new = next(t for t in repo.list() if t["id"] != first["id"])
    assert new["calendar_event_id"] == "ev2"
    assert cal.events["ev2"] == {"summary": "予約表の確認", "date": new["due_date"]}
    assert cal.events["ev1"]["summary"] == "✅ 予約表の確認"


def test_calendar_event_deleted_with_todo(cal_client, repo, cal):
    create_todo(cal_client)
    todo_id = repo.list()[0]["id"]
    path = f"/todos/{todo_id}/delete"
    assert "Googleカレンダーの予定も削除されます" in cal_client.get(path).get_data(as_text=True)
    cal_client.post(path, data={"csrf_token": get_csrf(cal_client, path)})
    assert repo.list() == []
    assert cal.events == {}


def test_calendar_failure_keeps_todo(cal_client, repo, cal):
    cal.fail = True
    html = create_todo(cal_client).get_data(as_text=True)
    assert "を登録しました" in html
    assert "Googleカレンダーへの反映に失敗しました" in html
    todo = repo.list()[0]
    assert todo["calendar_event_id"] == ""

    html = edit_todo(cal_client, todo, title="変更後").get_data(as_text=True)
    assert repo.get(todo["id"])["title"] == "変更後"
    assert "Googleカレンダーへの反映に失敗しました" in html


def test_calendar_failure_on_delete_still_deletes_todo(cal_client, repo, cal):
    create_todo(cal_client)
    todo_id = repo.list()[0]["id"]
    cal.fail = True
    path = f"/todos/{todo_id}/delete"
    html = cal_client.post(
        path, data={"csrf_token": get_csrf(cal_client, path)}, follow_redirects=True
    ).get_data(as_text=True)
    assert repo.list() == []
    assert "カレンダーから手動で削除してください" in html


def test_calendar_disabled_without_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CALENDAR_ID", "")
    assert create_calendar_from_env() is None


# ---------- GoogleCalendarSync（Google APIの代わりに偽の通信を使用） ----------


class FakeResponse:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, json=None, timeout=None):
        self.requests.append({"method": method, "url": url, "json": json, "timeout": timeout})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


SAMPLE_TODO = {"id": "t1", "title": "予約表の確認", "content": "来週分", "due_date": "2030-01-31",
               "completed": False}


def test_google_calendar_create_event():
    session = FakeSession(FakeResponse(200, {"id": "abc"}))
    sync = GoogleCalendarSync("team#home@group.calendar.google.com", session=session)
    assert sync.create(SAMPLE_TODO) == "abc"

    req = session.requests[0]
    assert req["method"] == "POST"
    # カレンダーIDの記号はURL用に変換する
    assert "/calendars/team%23home%40group.calendar.google.com/events" in req["url"]
    assert req["timeout"]
    body = req["json"]
    assert body["summary"] == "予約表の確認"
    assert body["description"] == "来週分"
    assert body["start"] == {"date": "2030-01-31"}
    assert body["end"] == {"date": "2030-02-01"}  # 終日の予定は翌日が終了日
    assert body["extendedProperties"]["private"]["todo_id"] == "t1"


def test_google_calendar_update_marks_done_and_recreates_missing():
    session = FakeSession(FakeResponse(200, {"id": "abc"}))
    sync = GoogleCalendarSync("cal", session=session)
    assert sync.update("abc", {**SAMPLE_TODO, "completed": True}) == "abc"
    assert session.requests[0]["method"] == "PUT"
    assert session.requests[0]["url"].endswith("/events/abc")
    assert session.requests[0]["json"]["summary"] == "✅ 予約表の確認"
    assert session.requests[0]["json"]["status"] == "confirmed"

    # 予定が消されていたら作り直す
    session = FakeSession(FakeResponse(404), FakeResponse(200, {"id": "new"}))
    sync = GoogleCalendarSync("cal", session=session)
    assert sync.update("gone", SAMPLE_TODO) == "new"
    assert [r["method"] for r in session.requests] == ["PUT", "POST"]


def test_google_calendar_delete_ignores_missing_event():
    for status in (204, 404, 410):
        session = FakeSession(FakeResponse(status))
        GoogleCalendarSync("cal", session=session).delete("abc")
        assert session.requests[0]["method"] == "DELETE"


@pytest.mark.parametrize("response", [FakeResponse(403), FakeResponse(500), ConnectionError("down")])
def test_google_calendar_errors_raise_calendar_error(response):
    sync = GoogleCalendarSync("cal", session=FakeSession(response))
    with pytest.raises(CalendarError):
        sync.create(SAMPLE_TODO)


# ---------- LINE通知 ----------

NOTIFY_TOKEN = "n" * 40
NOTIFY_HEADERS = {"Authorization": f"Bearer {NOTIFY_TOKEN}"}


class FakeNotifier:
    """LineNotifier の代わり。送った本文を記録する。"""

    def __init__(self):
        self.sent = []
        self.fail = False

    def push(self, text):
        if self.fail:
            raise NotifyError("テスト用の失敗")
        self.sent.append(text)


@pytest.fixture
def line():
    return FakeNotifier()


@pytest.fixture
def notify_app(repo, line):
    return create_app(
        repository=repo,
        config={"TESTING": True, "NOTIFY_TOKEN": NOTIFY_TOKEN, "APP_BASE_URL": "https://todo.example"},
        calendar=None,
        notifier=line,
    )


def add_todo(repo, todo_id, title, due, completed=False, **extra):
    stamp = "2026-09-01 09:00:00"
    repo.add({"id": todo_id, "title": title, "content": "", "category": "salon",
              "priority": "medium", "completed": completed, "created_at": stamp,
              "updated_at": stamp, "due_date": due.isoformat() if due else "", **extra})


def test_notify_sends_today_tomorrow_and_overdue(notify_app, repo, line):
    t = today()
    add_todo(repo, "a", "期限切れの請求書", t - timedelta(days=2))
    add_todo(repo, "b", "今日の予約確認", t)
    add_todo(repo, "c", "明日の仕入れ", t + timedelta(days=1))
    add_todo(repo, "d", "来週の準備", t + timedelta(days=5))
    add_todo(repo, "e", "完了済みの期限切れ", t - timedelta(days=1), completed=True)
    add_todo(repo, "f", "完了済みの今日", t, completed=True)
    add_todo(repo, "g", "期日なし", None)

    res = notify_app.test_client().post("/tasks/notify-due", headers=NOTIFY_HEADERS)
    assert res.status_code == 200
    assert res.get_json() == {"status": "sent", "count": 3, "overdue": 1, "today": 1, "tomorrow": 1}

    assert len(line.sent) == 1
    text = line.sent[0]
    assert f"（{t.month}月{t.day}日）" in text
    overdue_day = t - timedelta(days=2)
    assert "⚠️ 期限切れ 1件" in text
    assert f"・期限切れの請求書（{overdue_day.month}/{overdue_day.day}・サロン）" in text
    assert "📅 今日まで 1件\n・今日の予約確認（サロン）" in text
    assert "🔜 明日まで 1件\n・明日の仕入れ（サロン）" in text
    assert text.endswith("https://todo.example")
    for title in ("来週の準備", "完了済み", "期日なし"):
        assert title not in text

    # 通知したTodoだけ通知日を記録し、同じ日にもう一度呼ばれても送らない
    assert {t_["id"] for t_ in repo.list() if t_.get("notified_on")} == {"a", "b", "c"}
    res = notify_app.test_client().post("/tasks/notify-due", headers=NOTIFY_HEADERS)
    assert res.get_json()["status"] == "no_targets"
    assert len(line.sent) == 1


def test_notify_overdue_again_next_day(notify_app, repo, line):
    yesterday = (today() - timedelta(days=1)).isoformat()
    add_todo(repo, "a", "期限切れの請求書", today() - timedelta(days=3), notified_on=yesterday)
    res = notify_app.test_client().post("/tasks/notify-due", headers=NOTIFY_HEADERS)
    assert res.get_json()["count"] == 1
    assert "期限切れの請求書" in line.sent[0]


def test_notify_no_targets_sends_nothing(notify_app, repo, line):
    add_todo(repo, "d", "来週の準備", today() + timedelta(days=5))
    add_todo(repo, "e", "完了済み", today(), completed=True)
    res = notify_app.test_client().post("/tasks/notify-due", headers=NOTIFY_HEADERS)
    assert res.status_code == 200
    assert res.get_json() == {"status": "no_targets", "count": 0}
    assert line.sent == []


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong-token"},
        {"Authorization": f"Bearer {NOTIFY_TOKEN}x"},
        {"Authorization": NOTIFY_TOKEN},
        {"Authorization": f"Basic {NOTIFY_TOKEN}"},
    ],
)
def test_notify_rejects_bad_token(notify_app, repo, line, headers):
    add_todo(repo, "b", "今日の予約確認", today())
    res = notify_app.test_client().post("/tasks/notify-due", headers=headers)
    assert res.status_code == 401
    assert res.get_json() == {"status": "unauthorized"}
    assert line.sent == []
    assert "notified_on" not in repo.get("b")


@pytest.mark.parametrize("token", ["", "short-token"])
def test_notify_disabled_without_long_token(repo, line, token):
    app = create_app(repository=repo, config={"TESTING": True, "NOTIFY_TOKEN": token},
                     calendar=None, notifier=line)
    add_todo(repo, "b", "今日の予約確認", today())
    res = app.test_client().post("/tasks/notify-due", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401
    assert line.sent == []


def test_notify_requires_post(notify_app):
    res = notify_app.test_client().get("/tasks/notify-due", headers=NOTIFY_HEADERS)
    assert res.status_code == 405


def test_notify_line_failure(notify_app, repo, line):
    add_todo(repo, "b", "今日の予約確認", today())
    line.fail = True
    res = notify_app.test_client().post("/tasks/notify-due", headers=NOTIFY_HEADERS)
    assert res.status_code == 502
    assert res.get_json() == {"status": "line_error"}
    # 送れなかったので通知日は記録せず、次回また送る
    assert "notified_on" not in repo.get("b")


def test_notify_not_configured(repo):
    app = create_app(repository=repo, config={"TESTING": True, "NOTIFY_TOKEN": NOTIFY_TOKEN},
                     calendar=None, notifier=None)
    res = app.test_client().post("/tasks/notify-due", headers=NOTIFY_HEADERS)
    assert res.status_code == 503


def test_notifier_disabled_without_env(monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "")
    monkeypatch.setenv("LINE_USER_ID", "")
    assert create_notifier_from_env() is None


def test_notify_other_pages_still_require_login(notify_app):
    res = notify_app.test_client().get("/", headers=NOTIFY_HEADERS)
    assert res.status_code == 302
    assert "/login" in res.headers["Location"]


def test_notify_does_not_break_repeat_and_calendar(repo, line, cal):
    app = create_app(repository=repo, config={"TESTING": True, "NOTIFY_TOKEN": NOTIFY_TOKEN},
                     calendar=cal, notifier=line)
    c = app.test_client()
    assert login(c).status_code == 302
    create_todo(c, repeat="weekly", due_date=today().isoformat())
    res = c.post("/tasks/notify-due", headers=NOTIFY_HEADERS)
    assert res.get_json()["today"] == 1
    todo = repo.list()[0]
    assert todo["calendar_event_id"] == "ev1"
    assert cal.calls == ["create"]  # 通知ではカレンダーを変更しない

    toggle(c, todo["id"])
    new = next(t for t in repo.list() if t["id"] != todo["id"])
    assert new["due_date"] == (today() + timedelta(days=7)).isoformat()
    assert new.get("notified_on", "") == ""


def test_build_message_is_truncated():
    t = today()
    many = [{"title": "あ" * 90, "due": t, "category_label": "サロン"} for _ in range(100)]
    text = build_message([], many, [], t)
    assert len(text) <= TEXT_MAX
    assert text.endswith("（以下省略）")


# ---------- LineNotifier（LINE APIの代わりに偽の通信を使用） ----------


class FakeLineSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_line_notifier_push():
    session = FakeLineSession(FakeResponse(200))
    LineNotifier("dummy-access-token", "U0123", session=session).push("こんにちは")
    call = session.calls[0]
    assert call["url"] == "https://api.line.me/v2/bot/message/push"
    assert call["headers"]["Authorization"] == "Bearer dummy-access-token"
    assert re.fullmatch(r"[0-9a-f-]{36}", call["headers"]["X-Line-Retry-Key"])
    assert call["json"] == {"to": "U0123", "messages": [{"type": "text", "text": "こんにちは"}]}
    assert call["timeout"]


@pytest.mark.parametrize("response", [FakeResponse(400), FakeResponse(401), FakeResponse(500),
                                      ConnectionError("down")])
def test_line_notifier_errors(response):
    notifier = LineNotifier("dummy-access-token", "U0123", session=FakeLineSession(response))
    with pytest.raises(NotifyError) as info:
        notifier.push("こんにちは")
    assert "dummy-access-token" not in str(info.value)  # エラー文にトークンを含めない
