import os
import sys

# テスト用の環境変数（実際の秘密情報は使わない）
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["STORAGE_BACKEND"] = "memory"
os.environ.setdefault("APP_TIMEZONE", "Asia/Tokyo")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
