"""元数据同步与修复服务。

数据库里记录的是“系统认为文件应该是什么样”，
而磁盘里的文件才是“真实存在的状态”。
这个服务负责定期对比两者，发现缺失或不一致时回写数据库。
"""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path

from app.core.config import settings
from app.repositories import VoiceRepository


class MetadataSyncService:
    def __init__(self, repository: VoiceRepository | None = None) -> None:
        """允许注入 Repository，便于测试和复用。"""

        self.repository = repository or VoiceRepository()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """启动后台同步线程。"""

        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="a2-metadata-sync")
        self._thread.start()

    def stop(self) -> None:
        """停止后台同步线程。"""

        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    def run_once(self) -> dict[str, int]:
        """执行一次全量扫描并修复元数据。"""

        records = self.repository.list_voice_records()
        missing = 0
        updated = 0
        for record in records:
            path = Path(record["file_path"])
            if not path.exists():
                # 数据库里有记录但磁盘上文件已丢失，标记为 missing。
                self.repository.update_voice_status(record["unique_id"], valid_status="missing")
                missing += 1
                continue
            checksum = self._hash_file(path)
            size = path.stat().st_size
            status = "valid"
            if record.get("file_size") != size or record.get("checksum") != checksum or record.get("valid_status") != status:
                # 文件还在，但元数据落后于真实状态时进行修正。
                self.repository.update_voice_status(
                    record["unique_id"],
                    valid_status=status,
                    file_size=size,
                    checksum=checksum,
                )
                updated += 1
        return {"missing": missing, "updated": updated, "scanned": len(records)}

    def _run_loop(self) -> None:
        """按配置的时间间隔循环执行同步。"""

        while not self._stop_event.wait(settings.sync_interval_seconds):
            self.run_once()

    @staticmethod
    def _hash_file(path: Path) -> str:
        """按块读取文件并计算 SHA-256，避免大文件一次性读入内存。"""

        hasher = hashlib.sha256()
        with path.open("rb") as file_obj:
            while True:
                chunk = file_obj.read(1024 * 256)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest()
