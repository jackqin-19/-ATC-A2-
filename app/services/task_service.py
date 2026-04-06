from __future__ import annotations

import math
import random
import socket
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from app.core.config import settings
from app.core.time_utils import parse_datetime, utcnow_text
from app.repositories import TaskRepository, VoiceRepository
from app.schemas import DownloadTaskCreate, DownloadExecuteRequest, RealtimeTaskCreate
from app.services.storage_service import StorageService


class RealtimeTaskService:
    def __init__(
        self,
        task_repo: TaskRepository | None = None,
        voice_repo: VoiceRepository | None = None,
        storage_service: StorageService | None = None,
    ) -> None:
        self.task_repo = task_repo or TaskRepository()
        self.voice_repo = voice_repo or VoiceRepository()
        self.storage_service = storage_service or StorageService()

    def create_task(self, payload: RealtimeTaskCreate) -> int:
        return self.task_repo.create_realtime_task(payload)

    def list_tasks(self) -> list[dict]:
        return self.task_repo.list_realtime_tasks()

    def test_connection(self, host: str, port: int, timeout: int = 5) -> dict:
        with socket.create_connection((host, port), timeout=timeout):
            return {"status": "success", "message": "connection ok"}

    def ingest_file_segment(
        self,
        *,
        file_path: Path,
        icao_code: str,
        band: str,
        original_time: str,
        start_at: str,
        end_at: str,
    ) -> dict:
        unique_id = f"{icao_code.upper()}_{parse_datetime(original_time).strftime('%Y%m%d%H%M%S%f')[:-3]}_{random.randint(100, 999)}"
        record = self.storage_service.write_audio_bytes(
            unique_id=unique_id,
            icao_code=icao_code,
            band=band,
            start_at=start_at,
            end_at=end_at,
            original_time=original_time,
            process_time=utcnow_text(),
            data_type="S",
            extension=file_path.suffix or ".wav",
            content=file_path.read_bytes(),
        )
        self.voice_repo.insert_voice_record(record)
        return record.model_dump()


class DownloadTaskService:
    def __init__(
        self,
        task_repo: TaskRepository | None = None,
        voice_repo: VoiceRepository | None = None,
        storage_service: StorageService | None = None,
    ) -> None:
        self.task_repo = task_repo or TaskRepository()
        self.voice_repo = voice_repo or VoiceRepository()
        self.storage_service = storage_service or StorageService()

    def create_task(self, payload: DownloadTaskCreate) -> int:
        return self.task_repo.create_download_task(payload)

    def list_tasks(self) -> list[dict]:
        return self.task_repo.list_download_tasks()

    def ingest_downloaded_file(
        self,
        *,
        task_id: int,
        source_file: Path,
        icao_code: str,
        band: str,
        start_at: str,
        end_at: str,
        original_time: str | None = None,
    ) -> dict:
        original = original_time or start_at
        unique_id = (
            f"{icao_code.upper()}_{parse_datetime(original).strftime('%Y%m%d%H%M%S%f')[:-3]}_{task_id}_{uuid.uuid4().hex[:6]}"
        )
        record = self.storage_service.write_audio_bytes(
            unique_id=unique_id,
            icao_code=icao_code,
            band=band,
            start_at=start_at,
            end_at=end_at,
            original_time=original,
            process_time=utcnow_text(),
            data_type="H",
            extension=source_file.suffix or ".wav",
            content=source_file.read_bytes(),
        )
        self.voice_repo.insert_voice_record(record)
        self.task_repo.update_download_progress(task_id, 100.0, 0, 1)
        return record.model_dump()

    def execute_http_download(self, payload: DownloadExecuteRequest) -> dict:
        task = self.task_repo.get_download_task(payload.task_id)
        if not task:
            raise ValueError(f"download task {payload.task_id} not found")

        tmp_dir = settings.temp_root / "downloads"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(payload.source_url).suffix or ".bin"
        partial_path = tmp_dir / f"task_{payload.task_id}{ext}.part"
        final_path = tmp_dir / f"task_{payload.task_id}{ext}"

        downloaded = partial_path.stat().st_size if partial_path.exists() else 0
        headers = {}
        if downloaded > 0:
            headers["Range"] = f"bytes={downloaded}-"

        request = urllib.request.Request(payload.source_url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response, partial_path.open("ab") as file_obj:
                total_length = self._resolve_total_length(response, downloaded)
                self.task_repo.update_download_progress(payload.task_id, self._calc_progress(downloaded, total_length), downloaded, 2)
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    file_obj.write(chunk)
                    downloaded += len(chunk)
                    self.task_repo.update_download_progress(
                        payload.task_id,
                        self._calc_progress(downloaded, total_length),
                        downloaded,
                        2,
                    )
                    if payload.speed_limit_kbps > 0:
                        bytes_per_sec = payload.speed_limit_kbps * 1024
                        time.sleep(len(chunk) / bytes_per_sec)
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and partial_path.exists():
                downloaded = partial_path.stat().st_size
            else:
                self.task_repo.update_download_progress(payload.task_id, 0, downloaded, -1)
                raise ValueError(f"download failed: {exc}") from exc
        except OSError as exc:
            self.task_repo.update_download_progress(payload.task_id, 0, downloaded, -1)
            raise ValueError(f"download failed: {exc}") from exc

        partial_path.replace(final_path)
        return self.ingest_downloaded_file(
            task_id=payload.task_id,
            source_file=final_path,
            icao_code=payload.icao_code,
            band=payload.band,
            start_at=payload.start_time,
            end_at=payload.end_time,
            original_time=payload.original_time,
        )

    @staticmethod
    def _resolve_total_length(response, downloaded: int) -> int:
        content_range = response.headers.get("Content-Range")
        if content_range and "/" in content_range:
            return int(content_range.rsplit("/", 1)[1])
        content_length = response.headers.get("Content-Length")
        if content_length:
            return downloaded + int(content_length)
        return downloaded

    @staticmethod
    def _calc_progress(downloaded: int, total: int) -> float:
        if total <= 0:
            return 0
        return round(min(100.0, downloaded * 100 / total), 2)
