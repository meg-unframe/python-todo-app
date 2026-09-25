"""Todoの保存先。Googleスプレッドシート版と、テスト・動作確認用のメモリ版がある。"""

import json
import os
import threading

COLUMNS = [
    "id",
    "title",
    "content",
    "due_date",
    "category",
    "priority",
    "completed",
    "created_at",
    "updated_at",
    # 以降は後から追加した列（既存シートのヘッダーは自動で書き足す）
    "repeat",
    "series_id",
]

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _to_sheet_value(key, value):
    if key == "completed":
        return "TRUE" if value else "FALSE"
    return "" if value is None else str(value)


def _from_sheet_row(row):
    row = list(row) + [""] * (len(COLUMNS) - len(row))
    todo = dict(zip(COLUMNS, row[: len(COLUMNS)]))
    todo["completed"] = str(todo["completed"]).strip().upper() == "TRUE"
    todo["repeat"] = todo["repeat"].strip() or "none"
    return todo


class MemoryTodoRepository:
    """プロセス内メモリに保存する（再起動で消える）。"""

    def __init__(self):
        self._todos = {}
        self._lock = threading.Lock()

    def list(self):
        with self._lock:
            return [dict(t) for t in self._todos.values()]

    def get(self, todo_id):
        with self._lock:
            todo = self._todos.get(todo_id)
            return dict(todo) if todo else None

    def add(self, todo):
        with self._lock:
            self._todos[todo["id"]] = dict(todo)

    def update(self, todo):
        with self._lock:
            if todo["id"] not in self._todos:
                return False
            self._todos[todo["id"]] = dict(todo)
            return True

    def delete(self, todo_id):
        with self._lock:
            return self._todos.pop(todo_id, None) is not None


class SheetsTodoRepository:
    """Googleスプレッドシートに保存する。1行目はヘッダー、2行目以降が1件ずつのTodo。"""

    def __init__(self, spreadsheet_id, worksheet_name, credentials):
        import gspread

        client = gspread.authorize(credentials)
        spreadsheet = client.open_by_key(spreadsheet_id)
        try:
            self._ws = spreadsheet.worksheet(worksheet_name)
        except gspread.WorksheetNotFound:
            self._ws = spreadsheet.add_worksheet(
                title=worksheet_name, rows=1000, cols=len(COLUMNS)
            )
        self._ensure_header()

    def _ensure_header(self):
        header = self._ws.row_values(1)
        if header[: len(COLUMNS)] == COLUMNS:
            return
        # 旧バージョンのヘッダー（COLUMNS の先頭部分）なら、足りない列名だけ書き足す。
        # データ行には触れないので、既存のTodoは新しい列が空欄のまま読み込まれる。
        filled = list(header)
        while filled and not filled[-1]:
            filled.pop()
        if filled != COLUMNS[: len(filled)]:
            raise RuntimeError(
                "スプレッドシートの1行目が想定と異なります。"
                f"次の列名にしてください: {', '.join(COLUMNS)}"
            )
        if self._ws.col_count < len(COLUMNS):
            self._ws.add_cols(len(COLUMNS) - self._ws.col_count)
        self._ws.update(range_name="A1", values=[COLUMNS], value_input_option="RAW")

    def _row_range(self, row_number):
        last_col = chr(ord("A") + len(COLUMNS) - 1)
        return f"A{row_number}:{last_col}{row_number}"

    def _find_row_number(self, todo_id):
        ids = self._ws.col_values(1)
        for index, value in enumerate(ids[1:], start=2):
            if value == todo_id:
                return index
        return None

    def list(self):
        rows = self._ws.get_all_values()[1:]
        return [_from_sheet_row(r) for r in rows if r and r[0]]

    def get(self, todo_id):
        row_number = self._find_row_number(todo_id)
        if row_number is None:
            return None
        return _from_sheet_row(self._ws.row_values(row_number))

    def add(self, todo):
        values = [_to_sheet_value(k, todo.get(k)) for k in COLUMNS]
        # RAW: 入力値を数式として解釈させない（数式インジェクション対策）
        self._ws.append_row(values, value_input_option="RAW", table_range="A1")

    def update(self, todo):
        row_number = self._find_row_number(todo["id"])
        if row_number is None:
            return False
        values = [_to_sheet_value(k, todo.get(k)) for k in COLUMNS]
        self._ws.update(
            range_name=self._row_range(row_number),
            values=[values],
            value_input_option="RAW",
        )
        return True

    def delete(self, todo_id):
        row_number = self._find_row_number(todo_id)
        if row_number is None:
            return False
        self._ws.delete_rows(row_number)
        return True


def load_google_credentials():
    """環境変数からサービスアカウントの認証情報を読み込む。"""
    from google.oauth2.service_account import Credentials

    raw_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "").strip()
    if raw_json:
        return Credentials.from_service_account_info(json.loads(raw_json), scopes=SCOPES)

    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if path:
        return Credentials.from_service_account_file(path, scopes=SCOPES)

    raise RuntimeError(
        "Googleの認証情報がありません。GOOGLE_CREDENTIALS_JSON または "
        "GOOGLE_APPLICATION_CREDENTIALS を設定してください。"
    )


def create_repository_from_env():
    backend = os.environ.get("STORAGE_BACKEND", "sheets").strip().lower()
    if backend == "memory":
        return MemoryTodoRepository()
    if backend != "sheets":
        raise RuntimeError(f"STORAGE_BACKEND の値が不正です: {backend}")

    spreadsheet_id = os.environ.get("SPREADSHEET_ID", "").strip()
    if not spreadsheet_id:
        raise RuntimeError("SPREADSHEET_ID を設定してください。")
    worksheet_name = os.environ.get("WORKSHEET_NAME", "todos").strip() or "todos"
    return SheetsTodoRepository(spreadsheet_id, worksheet_name, load_google_credentials())
