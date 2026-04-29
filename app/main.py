"""应用启动入口。

本文件只负责把 FastAPI 应用对象暴露给 `uvicorn` 加载，
真正的路由和业务逻辑都定义在 `app.api` 中。
"""

from app.api import app
