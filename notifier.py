"""LINE通知。LINE Messaging API の push メッセージで、期限が近いTodoを知らせる。"""

import os
import uuid

PUSH_URL = "https://api.line.me/v2/bot/message/push"
TIMEOUT_SECONDS = 10
TEXT_MAX = 5000  # LINEのテキストメッセージの上限文字数


class NotifyError(Exception):
    """LINEへの送信に失敗した。"""


def build_message(overdue, due_today, due_tomorrow, today, base_url=""):
    """通知の本文を作る。各グループは期日の早い順に並べた表示用Todoのリスト。"""
    lines = [f"📋 Life & Work TODO（{today.month}月{today.day}日）"]
    groups = [
        ("⚠️ 期限切れ", overdue, True),
        ("📅 今日まで", due_today, False),
        ("🔜 明日まで", due_tomorrow, False),
    ]
    for heading, todos, show_date in groups:
        if not todos:
            continue
        lines += ["", f"{heading} {len(todos)}件"]
        for todo in todos:
            note = f"{todo['due'].month}/{todo['due'].day}・" if show_date else ""
            lines.append(f"・{todo['title']}（{note}{todo['category_label']}）")
    if base_url:
        lines += ["", base_url]
    text = "\n".join(lines)
    if len(text) > TEXT_MAX:
        text = text[: TEXT_MAX - 20].rstrip() + "\n…（以下省略）"
    return text


class LineNotifier:
    """1人のLINEユーザーへ push メッセージを送る。"""

    def __init__(self, access_token, to_user_id, session=None):
        if session is None:
            import requests

            session = requests.Session()
        self._session = session
        self._access_token = access_token
        self._to = to_user_id

    def push(self, text):
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            # 同じ送信を再試行しても二重に届かないようにする
            "X-Line-Retry-Key": str(uuid.uuid4()),
        }
        body = {"to": self._to, "messages": [{"type": "text", "text": text}]}
        try:
            response = self._session.post(
                PUSH_URL, json=body, headers=headers, timeout=TIMEOUT_SECONDS
            )
        except Exception as error:  # 通信エラーなど（トークンを含めないよう種類だけ残す）
            raise NotifyError(f"LINEへの送信に失敗しました: {type(error).__name__}") from error
        if response.status_code >= 400:
            raise NotifyError(f"LINEへの送信に失敗しました: HTTP {response.status_code}")


def create_notifier_from_env():
    """LINEのトークンと送信先があれば通知を有効にする。無ければ None（通知しない）。"""
    access_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    to_user_id = os.environ.get("LINE_USER_ID", "").strip()
    if not access_token or not to_user_id:
        return None
    return LineNotifier(access_token, to_user_id)
