"""项目运行配置。

这一层不处理具体业务，而是给整个项目提供统一的运行参数来源。
之所以单独抽成一个模块，是为了避免把路径、库文件位置、切片规则、
同步周期这类“环境信息”写死在业务代码里，后续切换部署目录或测试目录时
只需要改配置，不需要改业务逻辑。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """应用运行所需的静态配置集合。

    这里使用不可变 dataclass，目的是让配置在运行期尽量保持只读，
    避免被业务逻辑无意改坏；测试里如果需要临时替换，会显式通过
    `object.__setattr__` 覆盖。
    """

    app_name: str = "ATC A-2 Voice Module"
    app_version: str = "1.0.0"
    workspace_root: Path = Path(os.getenv("A2_WORKSPACE_ROOT", Path.cwd()))
    data_root: Path = Path(os.getenv("A2_DATA_ROOT", Path.cwd() / "storage"))
    db_path: Path = Path(os.getenv("A2_DB_PATH", Path(os.getenv("A5_DB_PATH", str(Path.cwd().parent / "Alpha" / "data" / "alpha_a5.db")))))
    temp_root: Path = Path(os.getenv("A2_TEMP_ROOT", Path.cwd() / "storage" / "tmp"))
    default_slice_minutes: int = int(os.getenv("A2_SLICE_MINUTES", "5"))
    default_slice_mb: int = int(os.getenv("A2_SLICE_MB", "100"))
    sync_interval_seconds: int = int(os.getenv("A2_SYNC_INTERVAL_SECONDS", "300"))
    max_download_task: int = int(os.getenv("A2_MAX_DOWNLOAD_TASK", "3"))
    max_realtime_conn: int = int(os.getenv("A2_MAX_REALTIME_CONN", "5"))
    download_chunk_size: int = 1024 * 16
    download_timeout: int = int(os.getenv("A2_DOWNLOAD_TIMEOUT", "600"))
    stream_timeout: int = int(os.getenv("A2_STREAM_TIMEOUT", "30"))
    fresh_time: int = int(os.getenv("A2_FRESH_TIME", "10"))
    max_retry: int = int(os.getenv("A2_MAX_RETRY", "10"))
    wait_timeout: int = int(os.getenv("A2_WAIT_TIMEOUT", "60"))
    timewait: int = int(os.getenv("A2_TIMEWAIT", "1"))
    audio_loudness: float = float(os.getenv("A2_LOUDNESS", "-24.0"))
    audio_sample_rate: int = int(os.getenv("A2_SAMPLE_RATE", "16000"))
    audio_bit_depth: int = int(os.getenv("A2_BIT_DEPTH", "16"))


settings = Settings()
