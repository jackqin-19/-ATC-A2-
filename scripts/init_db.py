"""数据库初始化脚本。

这个脚本用于命令行场景下快速创建 SQLite 数据库和表结构，
方便第一次运行项目时手工执行。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    # 让脚本从仓库根目录运行时也能正确导入 `app` 包。
    sys.path.insert(0, str(ROOT))

from app.db import init_db


if __name__ == "__main__":
    init_db()
    print("database initialized")
