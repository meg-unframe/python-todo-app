"""Googleカレンダーとの連携。Todoの期日に終日の予定を作成・更新・削除する。"""

import os
from datetime import date, timedelta
from urllib.parse import quote

API_BASE = "https://www.googleapis.com/calendar/v3"
TIMEOUT_SECONDS = 10
DONE_PREFIX = "✅ "


class CalendarError(Exception):
    """カレンダーとの通信に失敗した。"""


def build_event(todo):
    """Todoから終日の予定の内容を作る。完了済みはタイトルの先頭に ✅ を付ける。"""
    due = date.fromisoformat(todo["due_date"])
    summary = (DONE_PREFIX if todo.get("completed") else "") + todo["title"]
    return {
        "summary": summary,
        "description": todo.get("content", ""),
        "start": {"date": due.isoformat()},
        "end": {"date": (due + timedelta(days=1)).isoformat()},  # 終日の予定は翌日が終了日
        "status": "confirmed",  # 手動で削除された予定を更新したときに元に戻す
        "extendedProperties": {"private": {"todo_id": todo["id"]}},
    }


class GoogleCalendarSync:
    """サービスアカウントで、共有されたカレンダーへ予定を書き込む。"""

    def __init__(self, calendar_id, credentials=None, session=None):
        if session is None:
            from google.auth.transport.requests import AuthorizedSession

            session = AuthorizedSession(credentials)
        self._session = session
        self._events_url = f"{API_BASE}/calendars/{quote(calendar_id, safe='')}/events"

    def _request(self, method, url, body=None):
        try:
            response = self._session.request(method, url, json=body, timeout=TIMEOUT_SECONDS)
        except Exception as error:  # 通信エラー・認証エラーなど
            raise CalendarError(f"{method} に失敗しました: {type(error).__name__}") from error
        if response.status_code >= 400 and response.status_code not in (404, 410):
            raise CalendarError(f"{method} に失敗しました: HTTP {response.status_code}")
        return response

    def create(self, todo):
        response = self._request("POST", self._events_url, build_event(todo))
        if response.status_code >= 400:
            raise CalendarError(f"POST に失敗しました: HTTP {response.status_code}")
        return response.json()["id"]

    def update(self, event_id, todo):
        """予定を更新する。予定が見つからなければ作り直し、新しい予定IDを返す。"""
        url = f"{self._events_url}/{quote(event_id, safe='')}"
        response = self._request("PUT", url, build_event(todo))
        if response.status_code in (404, 410):
            return self.create(todo)
        return event_id

    def delete(self, event_id):
        # すでに削除されている（404 / 410）場合は成功とみなす
        self._request("DELETE", f"{self._events_url}/{quote(event_id, safe='')}")


def create_calendar_from_env():
    """GOOGLE_CALENDAR_ID があれば連携を有効にする。無ければ None（連携しない）。"""
    calendar_id = os.environ.get("GOOGLE_CALENDAR_ID", "").strip()
    if not calendar_id:
        return None
    from storage import load_google_credentials

    return GoogleCalendarSync(calendar_id, load_google_credentials())
