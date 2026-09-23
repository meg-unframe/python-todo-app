import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import create_app
from storage import COLUMNS, MemoryTodoRepository, SheetsTodoRepository


@pytest.fixture
def repo():
    return MemoryTodoRepository()


@pytest.fixture
def client(repo):
    app = create_app(repository=repo, config={"TESTING": True})
    return app.test_client()


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


# ---------- スプレッドシート保存（Google APIの代わりに偽のシートを使用） ----------


class FakeWorksheet:
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else []
        self.input_options = []

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
