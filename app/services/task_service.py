"""任务业务服务。

这一层负责把“任务配置”和“音频落库”串起来：
1. `RealtimeTaskService` 处理实时任务的创建和手工导入。
2. `DownloadTaskService` 处理历史下载、LiveATC 元数据推断、限速、续传和入库。
"""

from __future__ import annotations

import random
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from app.core.config import settings
from app.core.time_utils import format_datetime, parse_datetime, utcnow_text
from app.repositories import TaskRepository, VoiceRepository
from app.schemas import (
    DownloadTaskCreate,
    DownloadExecuteRequest,
    LiveAtcDownloadExecuteRequest,
    RealtimeTaskCreate,
)
from app.services.exception import ATCDownloadError
from app.services.liveatc_downloader import ArchiveDownloader
from app.services.runtime_service import AsxStreamResolver
from app.services.storage_service import StorageService


@dataclass(frozen=True)
class LiveAtcArchiveMetadata:
    """从 LiveATC 归档文件名和音频时长中解析出的业务元数据。"""

    icao_code: str
    band: str
    original_time: str
    start_at: str
    end_at: str
    file_name: str


class RealtimeTaskService:
    def __init__(
        self,
        task_repo: TaskRepository | None = None,
        voice_repo: VoiceRepository | None = None,
        storage_service: StorageService | None = None,
        resolver: AsxStreamResolver | None = None,
    ) -> None:
        """实时任务的业务入口，负责创建任务和导入实时片段。"""

        self.task_repo = task_repo or TaskRepository()
        self.voice_repo = voice_repo or VoiceRepository()
        self.storage_service = storage_service or StorageService()
        self.resolver = resolver or AsxStreamResolver()

    def create_task(self, payload: RealtimeTaskCreate) -> int:
        """创建实时任务配置。"""

        return self.task_repo.create_realtime_task(payload)

    def list_tasks(self) -> list[dict]:
        """列出全部实时任务。"""

        return self.task_repo.list_realtime_tasks()

    def create_task_from_asx(
        self,
        *,
        task_name: str,
        icao_code: str,
        band: str,
        content: bytes,
        preferred_ref: int = 0,
        segment_seconds: int = 60,
        filename: str | None = None,
    ) -> dict:
        """从 ASX 内容中解析真实流地址并创建实时任务。"""

        refs = self.resolver.parse(content)
        if preferred_ref >= len(refs):
            raise ValueError(f"preferred_ref out of range, available refs: 0-{len(refs) - 1}")
        selected = refs[preferred_ref]
        stream_format = Path(urlparse(selected).path).suffix.lstrip(".") or self._guess_stream_format(filename)
        task_id = self.create_task(
            RealtimeTaskCreate(
                task_name=task_name,
                source_url=selected,
                protocol="HTTP_STREAM",
                timeout=30,
                heart_beat=10,
                icao_code=icao_code,
                band=band,
                segment_seconds=segment_seconds,
                stream_format=stream_format,
            )
        )
        return {"taskId": task_id, "streamUrl": selected, "refs": refs}

    def test_connection(self, host: str, port: int, timeout: int = 5) -> dict:
        """测试指定主机端口是否可连通。"""

        with socket.create_connection((host, port), timeout=timeout):
            return {"status": "success", "message": "connection ok"}

    @staticmethod
    def _guess_stream_format(filename: str | None) -> str | None:
        """根据上传文件名猜测流格式。"""

        if not filename:
            return None
        suffix = Path(filename).suffix.lower()
        if suffix == ".asx":
            return None
        return suffix.lstrip(".") or None

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
        """把手工上传的实时语音片段写入本地并入库。"""

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
    MAX_LIVEATC_ARCHIVE_SECONDS = 30 * 60

    def __init__(
        self,
        task_repo: TaskRepository | None = None,
        voice_repo: VoiceRepository | None = None,
        storage_service: StorageService | None = None,
    ) -> None:
        """历史下载任务的业务入口。"""

        self.task_repo = task_repo or TaskRepository()
        self.voice_repo = voice_repo or VoiceRepository()
        self.storage_service = storage_service or StorageService()

    def create_task(self, payload: DownloadTaskCreate) -> int:
        """创建下载任务配置。"""

        return self.task_repo.create_download_task(payload)

    def list_tasks(self) -> list[dict]:
        """列出全部下载任务。"""

        return self.task_repo.list_download_tasks()

    def create_task_from_liveatc_archive(self, source_name: str) -> tuple[int, LiveAtcArchiveMetadata]:
        """根据 LiveATC 文件名自动创建一个下载任务。"""

        metadata = self.parse_liveatc_archive_metadata(source_name)
        task_id = self.create_task(
            DownloadTaskCreate(
                task_name=f"liveatc-{Path(source_name).stem}",
                icao_code=metadata.icao_code,
                band=metadata.band,
                start_time=metadata.start_at,
                end_time=metadata.end_at,
            )
        )
        return task_id, metadata

    def import_liveatc_archive_file(
        self,
        *,
        source_file: Path,
        task_id: int | None = None,
    ) -> dict:
        """直接导入一个本地 LiveATC 归档文件。"""

        metadata = self.parse_liveatc_archive_metadata(source_file.name, source_file=source_file)
        source_file = self._limit_liveatc_archive_file(source_file)
        effective_task_id = task_id
        if effective_task_id is None:
            effective_task_id, _ = self.create_task_from_liveatc_archive(source_file.name)
        self.task_repo.update_download_task_time_range(effective_task_id, metadata.start_at, metadata.end_at)
        return self.ingest_downloaded_file(
            task_id=effective_task_id,
            source_file=source_file,
            icao_code=metadata.icao_code,
            band=metadata.band,
            start_at=metadata.start_at,
            end_at=metadata.end_at,
            original_time=metadata.original_time,
        )

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
        """把已下载完成的历史文件写入正式存储并更新任务状态。"""

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

    def execute_liveatc_download(self, payload: LiveAtcDownloadExecuteRequest) -> dict:
        """通过浏览器自动化从 LiveATC 归档页面下载语音。

        使用 SeleniumBase 绕过 Cloudflare，模拟用户在归档页面选择日期和时段后触发下载。
        """

        download_dir = settings.temp_root / "downloads"
        download_dir.mkdir(parents=True, exist_ok=True)
        downloader = ArchiveDownloader(
            url=payload.source_url,
            date=payload.date,
            time_slot=payload.time_slot,
            file_dir=download_dir,
        )
        try:
            file_path = downloader.run()
        except Exception as exc:
            raise ATCDownloadError(str(exc)) from exc

        source_file = self._limit_liveatc_archive_file(file_path)
        metadata = payload.icao_code and payload.band and self._try_manual_metadata(payload, source_file)
        if metadata is None:
            metadata = self.parse_liveatc_archive_metadata(source_file.name, source_file=source_file)
        task_id, _ = self.create_task_from_liveatc_archive(source_file.name)
        self.task_repo.update_download_task_time_range(task_id, metadata.start_at, metadata.end_at)
        record = self.ingest_downloaded_file(
            task_id=task_id,
            source_file=source_file,
            icao_code=metadata.icao_code,
            band=metadata.band,
            start_at=metadata.start_at,
            end_at=metadata.end_at,
            original_time=metadata.original_time,
        )
        try:
            source_file.unlink(missing_ok=True)
        except OSError:
            pass
        return {"taskId": task_id, "record": record, "metadata": metadata.__dict__}

    def _try_manual_metadata(
        self, payload: LiveAtcDownloadExecuteRequest, source_file: Path
    ) -> LiveAtcArchiveMetadata | None:
        if not payload.icao_code or not payload.band:
            return None
        duration = self._probe_audio_duration_seconds(source_file) or 0
        start_dt = parse_datetime(payload.date + "000000")
        end_dt = start_dt + timedelta(seconds=min(duration, self.MAX_LIVEATC_ARCHIVE_SECONDS))
        return LiveAtcArchiveMetadata(
            icao_code=payload.icao_code.upper(),
            band=payload.band,
            original_time=format_datetime(start_dt, with_ms=False),
            start_at=format_datetime(start_dt, with_ms=False),
            end_at=format_datetime(end_dt, with_ms=False),
            file_name=source_file.name,
        )

    def execute_http_download(self, payload: DownloadExecuteRequest) -> dict:
        """执行普通 HTTP 下载，支持简单断点续传和限速。

        下载完成后不会把临时文件直接暴露出去，而是统一走入库流程，
        让最终存储结构和元数据格式保持一致。
        """

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
            # 如果之前已经下载过一部分，则用 Range 从断点继续请求。
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
                        # 通过 sleep 粗略控制平均下载速率。
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
        try:
            # 对 LiveATC 长文件执行课程项目级的前 30 分钟截断规则。
            final_path = self._limit_liveatc_archive_file(final_path, source_name=Path(payload.source_url).name)
            metadata = self._resolve_download_metadata(payload, final_path)
            self.task_repo.update_download_task_time_range(payload.task_id, metadata.start_at, metadata.end_at)
            return self.ingest_downloaded_file(
                task_id=payload.task_id,
                source_file=final_path,
                icao_code=metadata.icao_code,
                band=metadata.band,
                start_at=metadata.start_at,
                end_at=metadata.end_at,
                original_time=metadata.original_time,
            )
        finally:
            final_path.unlink(missing_ok=True)

    def parse_liveatc_archive_metadata(
        self,
        source_name: str,
        *,
        source_file: Path | None = None,
    ) -> LiveAtcArchiveMetadata:
        """从 LiveATC 文件名中解析机场、频段和开始时间。

        如果同时传入真实文件，还会借助音频时长推断结束时间。
        """

        file_name = Path(source_name).name
        stem = Path(file_name).stem
        parts = stem.split("-")
        if len(parts) < 6:
            raise ValueError(f"unsupported LiveATC archive file name: {file_name}")

        source_token = parts[0]
        icao_code = self._extract_liveatc_icao(source_token)
        month_text, day_text, year_text, time_text = parts[-4:]
        time_text = time_text.removesuffix("Z")
        band_parts = parts[1:-4]
        if not band_parts:
            raise ValueError(f"unsupported LiveATC archive file name: {file_name}")

        try:
            start_dt = datetime.strptime(
                f"{year_text}-{month_text}-{day_text} {time_text}",
                "%Y-%b-%d %H%M",
            )
        except ValueError as exc:
            raise ValueError(f"unsupported LiveATC archive file name: {file_name}") from exc
        duration_seconds = self._probe_audio_duration_seconds(source_file) if source_file else None
        if source_file is not None and duration_seconds is None:
            raise ValueError(f"could not determine audio duration for archive file: {file_name}")
        if duration_seconds is None:
            duration_seconds = 0
        effective_duration_seconds = min(duration_seconds, self.MAX_LIVEATC_ARCHIVE_SECONDS)
        end_dt = start_dt + timedelta(seconds=effective_duration_seconds)

        band = "-".join(part.lower() for part in band_parts)
        start_text = format_datetime(start_dt, with_ms=False)
        return LiveAtcArchiveMetadata(
            icao_code=icao_code,
            band=band,
            original_time=start_text,
            start_at=start_text,
            end_at=format_datetime(end_dt, with_ms=False),
            file_name=file_name,
        )

    def _resolve_download_metadata(self, payload: DownloadExecuteRequest, final_path: Path) -> LiveAtcArchiveMetadata:
        """优先使用手工传入元数据，否则从文件名自动推断。"""

        manual_complete = all([payload.icao_code, payload.band, payload.start_time, payload.end_time])
        if manual_complete:
            original_time = payload.original_time or payload.start_time
            return LiveAtcArchiveMetadata(
                icao_code=str(payload.icao_code),
                band=str(payload.band),
                original_time=str(original_time),
                start_at=str(payload.start_time),
                end_at=str(payload.end_time),
                file_name=final_path.name,
            )
        source_name = Path(urlparse(payload.source_url).path).name or final_path.name
        inferred = self.parse_liveatc_archive_metadata(source_name, source_file=final_path)
        if payload.original_time:
            return LiveAtcArchiveMetadata(
                icao_code=inferred.icao_code,
                band=inferred.band,
                original_time=payload.original_time,
                start_at=inferred.start_at,
                end_at=inferred.end_at,
                file_name=inferred.file_name,
            )
        return inferred

    @staticmethod
    def _extract_liveatc_icao(source_token: str) -> str:
        """从 LiveATC 文件名首段中抽取 ICAO 机场码。"""

        letters = "".join(char for char in source_token if char.isalpha())
        if len(letters) < 4:
            raise ValueError(f"unsupported LiveATC source token: {source_token}")
        return letters[:4].upper()

    def _limit_liveatc_archive_file(self, source_file: Path, source_name: str | None = None) -> Path:
        """如果是超长 LiveATC 归档文件，只保留前 30 分钟。"""

        archive_name = source_name or source_file.name
        if not self._is_liveatc_archive_name(archive_name):
            return source_file

        duration_seconds = self._probe_audio_duration_seconds(source_file)
        if duration_seconds is None or duration_seconds <= self.MAX_LIVEATC_ARCHIVE_SECONDS:
            return source_file

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise ValueError("ffmpeg is required to truncate LiveATC archive files longer than 30 minutes")

        trimmed_path = source_file.with_name(f"{source_file.stem}_first30m{source_file.suffix}")
        try:
            subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    str(source_file),
                    "-t",
                    str(self.MAX_LIVEATC_ARCHIVE_SECONDS),
                    str(trimmed_path),
                ],
                check=True,
                capture_output=True,
            )
            trimmed_path.replace(source_file)
        except (OSError, subprocess.CalledProcessError) as exc:
            if trimmed_path.exists():
                trimmed_path.unlink(missing_ok=True)
            raise ValueError("failed to truncate LiveATC archive file to 30 minutes") from exc
        return source_file

    def _is_liveatc_archive_name(self, source_name: str) -> bool:
        """判断文件名是否符合 LiveATC 归档命名规则。"""

        try:
            self.parse_liveatc_archive_metadata(source_name)
        except ValueError:
            return False
        return True

    @staticmethod
    def _probe_audio_duration_seconds(source_file: Path | None) -> int | None:
        """调用 ffprobe 探测音频时长，返回秒数。"""

        if source_file is None or not source_file.exists():
            return None
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return None
        try:
            result = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(source_file),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError):
            return None
        try:
            return max(1, int(round(float(result.stdout.strip()))))
        except ValueError:
            return None

    @staticmethod
    def _resolve_total_length(response, downloaded: int) -> int:
        """尽量从响应头推断文件总大小，用于计算下载进度。"""

        content_range = response.headers.get("Content-Range")
        if content_range and "/" in content_range:
            return int(content_range.rsplit("/", 1)[1])
        content_length = response.headers.get("Content-Length")
        if content_length:
            return downloaded + int(content_length)
        return downloaded

    @staticmethod
    def _calc_progress(downloaded: int, total: int) -> float:
        """把已下载字节数换算为百分比进度。"""

        if total <= 0:
            return 0
        return round(min(100.0, downloaded * 100 / total), 2)
