import os
import sys

# テスト用の環境変数（実際の秘密情報は使わない）
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["APP_PASSWORD"] = "test-password-123"
os.environ["STORAGE_BACKEND"] = "memory"
os.environ.setdefault("APP_TIMEZONE", "Asia/Tokyo")
# .env にカレンダーIDがあっても、テストでは本物のカレンダーへ接続しない
os.environ["GOOGLE_CALENDAR_ID"] = ""

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
