"""时间处理工具函数。

A-2 模块里大量逻辑都依赖时间范围判断，比如：
1. 查询某一段语音是否命中目标窗口。
2. 计算切片起止时间。
3. 解析历史文件推断出的起止时刻。

所以这里把时间字符串的解析和格式化统一封装起来，避免不同文件里
出现不同格式，最终导致查询条件和数据库记录对不上。
"""

from __future__ import annotations

from datetime import UTC, datetime


DATETIME_WITH_MS = "%Y-%m-%d %H:%M:%S.%f"
DATETIME_NO_MS = "%Y-%m-%d %H:%M:%S"


def utcnow_text() -> str:
    """返回当前 UTC 时间，格式精确到毫秒。

    这里主要用于记录处理时间 `process_time`，表示语音文件被系统写入
    或处理的时刻，而不是上游音频源本身的原始时间。
    """

    return datetime.now(UTC).strftime(DATETIME_WITH_MS)[:-3]


def parse_datetime(value: str) -> datetime:
    """把接口或数据库中的时间字符串解析为 `datetime` 对象。

    项目同时兼容两种输入格式：
    - `yyyy-MM-dd HH:mm:ss`
    - `yyyy-MM-dd HH:mm:ss.SSS`

    这样做是因为有些时间来自接口参数，只到秒；
    有些时间来自实时切片，会精确到毫秒。
    """

    try:
        if "." in value:
            return datetime.strptime(value, DATETIME_WITH_MS)
        return datetime.strptime(value, DATETIME_NO_MS)
    except ValueError as exc:
        raise ValueError(
            f"Invalid datetime '{value}'. Expected yyyy-MM-dd HH:mm:ss or yyyy-MM-dd HH:mm:ss.SSS"
        ) from exc


def format_datetime(value: datetime, with_ms: bool = True) -> str:
    """把 `datetime` 对象格式化为项目统一使用的字符串。

    默认保留毫秒，适合实时流切片；如果是历史归档文件推断时间，
    有时只保留到秒即可。
    """

    text = value.strftime(DATETIME_WITH_MS if with_ms else DATETIME_NO_MS)
    return text[:-3] if with_ms else text
