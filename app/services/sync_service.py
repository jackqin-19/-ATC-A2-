from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path

from app.core.config import settings
from app.repositories import VoiceRepository


class MetadataSyncService:
    def __init__(self, repository: VoiceRepository | None = None) -> None:
        self.repository = repository or VoiceRepository()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="a2-metadata-sync")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    def run_once(self) -> dict[str, int]:
        records = self.repository.list_voice_records()
        missing = 0
        updated = 0
        for record in records:
            path = Path(record["file_path"])
            if not path.exists():
                self.repository.update_voice_status(record["unique_id"], valid_status="missing")
                missing += 1
                continue
            checksum = self._hash_file(path)
            size = path.stat().st_size
            status = "valid"
            if record.get("file_size") != size or record.get("checksum") != checksum or record.get("valid_status") != status:
                self.repository.update_voice_status(
                    record["unique_id"],
                    valid_status=status,
                    file_size=size,
                    checksum=checksum,
                )
                updated += 1
        return {"missing": missing, "updated": updated, "scanned": len(records)}

    def _run_loop(self) -> None:
        while not self._stop_event.wait(settings.sync_interval_seconds):
            self.run_once()

    @staticmethod
    def _hash_file(path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as file_obj:
            while True:
                chunk = file_obj.read(1024 * 256)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest()
