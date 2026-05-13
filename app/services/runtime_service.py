"""实时流运行时服务。

这一层是真正“让实时任务跑起来”的地方：
它负责解析流地址、启动后台线程、维护连接状态、持续读取音频流，
并按固定时间切片写入本地和数据库。
"""

from __future__ import annotations

import socket
import threading
import time
import uuid
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

from app.core.time_utils import format_datetime, utcnow_text
from app.repositories import TaskRepository, VoiceRepository
from app.services.storage_service import StorageService


class AsxStreamResolver:
    @staticmethod
    def parse(content: bytes, base_url: str | None = None) -> list[str]:
        """解析 ASX 内容并提取可播放的真实流地址列表。"""

        text = content.decode("utf-8", errors="ignore").strip()
        if not text:
            raise ValueError("ASX file is empty")

        refs: list[str] = []
        try:
            root = ET.fromstring(text)
            for element in root.iter():
                if element.tag.lower().endswith("ref"):
                    href = element.attrib.get("href") or element.attrib.get("HREF")
                    if href:
                        refs.append(urljoin(base_url or "", href.strip()))
        except ET.ParseError:
            # 有些 ASX 文件不是标准 XML，这里降级为按行解析。
            for line in text.splitlines():
                lowered = line.strip().lower()
                if lowered.startswith("ref") and "=" in line:
                    refs.append(urljoin(base_url or "", line.split("=", 1)[1].strip()))

        normalized: list[str] = []
        seen: set[str] = set()
        for ref in refs:
            if ref and ref not in seen:
                seen.add(ref)
                normalized.append(ref)
        if not normalized:
            raise ValueError("No playable stream URL found in ASX file")
        return normalized

    def resolve_from_url(self, source_url: str) -> list[str]:
        """直接通过 URL 下载 ASX 文件并解析。"""

        with urllib.request.urlopen(source_url, timeout=15) as response:
            content = response.read()
        return self.parse(content, base_url=source_url)


class RealtimeConnectionManager:
    def __init__(
        self,
        repository: TaskRepository | None = None,
        voice_repository: VoiceRepository | None = None,
        storage_service: StorageService | None = None,
        resolver: AsxStreamResolver | None = None,
    ) -> None:
        """维护实时任务运行线程、状态和切片落盘逻辑。"""

        self.repository = repository or TaskRepository()
        self.voice_repository = voice_repository or VoiceRepository()
        self.storage_service = storage_service or StorageService()
        self.resolver = resolver or AsxStreamResolver()
        self._threads: dict[int, threading.Thread] = {}
        self._stops: dict[int, threading.Event] = {}
        self._receive_threads: dict[int, threading.Thread] = {}
        self._receive_stops: dict[int, threading.Event] = {}
        self._receive_state: dict[int, dict[str, object]] = {}

    def start_monitor(
        self,
        *,
        task_id: int,
        heartbeat_payload: str = "PING\n",
        heartbeat_expect: str | None = None,
    ) -> None:
        """启动心跳监控线程，负责检查连接是否可用。"""

        if task_id in self._threads and self._threads[task_id].is_alive():
            return
        stop_event = threading.Event()
        self._stops[task_id] = stop_event
        thread = threading.Thread(
            target=self._run_monitor,
            kwargs={
                "task_id": task_id,
                "stop_event": stop_event,
                "heartbeat_payload": heartbeat_payload,
                "heartbeat_expect": heartbeat_expect,
            },
            daemon=True,
            name=f"a2-realtime-{task_id}",
        )
        self._threads[task_id] = thread
        thread.start()

    def stop_monitor(self, task_id: int) -> None:
        """停止心跳监控线程，并把任务状态置回空闲。"""

        event = self._stops.get(task_id)
        if event:
            event.set()
        thread = self._threads.get(task_id)
        if thread:
            thread.join(timeout=2)
        self.repository.update_realtime_status(task_id, 0)

    def get_state(self, task_id: int) -> dict[str, object]:
        """返回实时任务的运行状态快照。"""

        monitor_thread = self._threads.get(task_id)
        receive_thread = self._receive_threads.get(task_id)
        receive_state = self._receive_state.get(task_id, {})
        return {
            "taskId": task_id,
            "running": bool(monitor_thread and monitor_thread.is_alive()),
            "monitoring": bool(monitor_thread and monitor_thread.is_alive()),
            "receiving": bool(receive_thread and receive_thread.is_alive()),
            "segmentsSaved": receive_state.get("segmentsSaved", 0),
            "lastSegmentAt": receive_state.get("lastSegmentAt"),
            "lastError": receive_state.get("lastError"),
            "streamUrl": receive_state.get("streamUrl"),
        }

    def start_receive(self, task_id: int) -> None:
        """启动接收线程，真正开始拉取音频流。"""

        if task_id in self._receive_threads and self._receive_threads[task_id].is_alive():
            return
        task = self.repository.get_realtime_task(task_id)
        if not task:
            raise ValueError(f"realtime task {task_id} not found")
        if not task.get("source_url"):
            raise ValueError("source_url is required")
        stop_event = threading.Event()
        self._receive_stops[task_id] = stop_event
        self._receive_state.setdefault(
            task_id,
            {"segmentsSaved": 0, "lastSegmentAt": None, "lastError": None, "streamUrl": None},
        )
        thread = threading.Thread(
            target=self._run_receive,
            kwargs={"task": task, "stop_event": stop_event},
            daemon=True,
            name=f"a2-receive-{task_id}",
        )
        self._receive_threads[task_id] = thread
        thread.start()

    def stop_receive(self, task_id: int) -> None:
        """停止接收线程。"""

        event = self._receive_stops.get(task_id)
        if event:
            event.set()
        thread = self._receive_threads.get(task_id)
        if thread:
            thread.join(timeout=5)
        self.repository.update_realtime_status(task_id, 0)

    def _run_monitor(
        self,
        *,
        task_id: int,
        stop_event: threading.Event,
        heartbeat_payload: str,
        heartbeat_expect: str | None,
    ) -> None:
        """监控 socket 连接状态，并在失败时按退避策略重试。"""

        backoff = [10, 30, 60]
        task = self.repository.get_realtime_task(task_id)
        if not task:
            return
        missed_heartbeats = 0
        backoff_index = 0
        while not stop_event.is_set():
            try:
                self.repository.update_realtime_status(task_id, 1)
                with socket.create_connection((task["server_addr"], task["server_port"]), timeout=task["timeout"]) as conn:
                    conn.settimeout(task["heart_beat"])
                    while not stop_event.wait(task["heart_beat"]):
                        # 主动发送心跳，必要时检查对端响应内容。
                        conn.sendall(heartbeat_payload.encode("utf-8"))
                        if heartbeat_expect is not None:
                            data = conn.recv(1024).decode("utf-8", errors="ignore")
                            if heartbeat_expect not in data:
                                missed_heartbeats += 1
                            else:
                                missed_heartbeats = 0
                        else:
                            missed_heartbeats = 0
                        if missed_heartbeats >= 3:
                            raise ConnectionError("heartbeat response missing 3 times")
                if stop_event.is_set():
                    break
            except OSError:
                self.repository.update_realtime_status(task_id, 2)
                # 使用逐步增加的等待时间，避免频繁重连压垮上游。
                delay = backoff[min(backoff_index, len(backoff) - 1)]
                backoff_index = min(backoff_index + 1, len(backoff) - 1)
                if stop_event.wait(delay):
                    break
                continue
            backoff_index = 0
        self.repository.update_realtime_status(task_id, 0)

    def _run_receive(self, *, task: dict[str, object], stop_event: threading.Event) -> None:
        """循环拉取实时流，失败时自动重连。"""

        task_id = int(task["task_id"])
        backoff = [3, 10, 30]
        backoff_index = 0
        while not stop_event.is_set():
            try:
                stream_url = self._resolve_stream_url(task["source_url"])
                self._receive_state[task_id]["streamUrl"] = stream_url
                self._receive_state[task_id]["lastError"] = None
                self.repository.update_realtime_status(task_id, 1)
                extra_headers: dict[str, str] = {}
                extra_cookies: dict[str, str] = {}
                parsed = urlparse(str(task["source_url"]))
                if "liveatc.net" in (parsed.netloc or ""):
                    stream_url, extra_headers, extra_cookies = self._resolve_liveatc_stream_url(
                        str(task["source_url"])
                    )
                    self._receive_state[task_id]["streamUrl"] = stream_url
                self._record_stream(task, stream_url, stop_event, extra_headers, extra_cookies)
                if stop_event.is_set():
                    break
                backoff_index = 0
            except OSError as exc:
                self.repository.update_realtime_status(task_id, 2)
                self._receive_state[task_id]["lastError"] = str(exc)
                delay = backoff[min(backoff_index, len(backoff) - 1)]
                backoff_index = min(backoff_index + 1, len(backoff) - 1)
                if stop_event.wait(delay):
                    break
            except ValueError as exc:
                self.repository.update_realtime_status(task_id, 2)
                self._receive_state[task_id]["lastError"] = str(exc)
                break
        self.repository.update_realtime_status(task_id, 0)

    @staticmethod
    def _resolve_liveatc_stream_url(source_url: str) -> tuple[str, dict[str, str], dict[str, str]]:
        from app.services.liveatc_downloader import StreamDownloader

        sd = StreamDownloader(source_url, Path("."))
        return sd.resolve_stream_url()

    def _resolve_stream_url(self, source_url: str) -> str:
        """如果传入的是 ASX 地址，就先解析出真实流地址。"""

        parsed = urlparse(source_url)
        suffix = Path(parsed.path).suffix.lower()
        if suffix == ".asx":
            refs = self.resolver.resolve_from_url(source_url)
            return refs[0]
        return source_url

    def _record_stream(
        self,
        task: dict[str, object],
        stream_url: str,
        stop_event: threading.Event,
        extra_headers: dict[str, str] | None = None,
        extra_cookies: dict[str, str] | None = None,
    ) -> None:
        """持续读取流内容，并按固定秒数切成多个语音片段。"""

        extension = self._guess_extension(
            stream_url=stream_url,
            content_type=None,
            declared_format=task.get("stream_format"),
        )
        segment_seconds = int(task.get("segment_seconds") or 60)
        current_bytes = bytearray()
        segment_start = datetime.now(UTC)

        if extra_headers is not None and extra_headers:
            self._record_stream_via_requests(
                task, stream_url, stop_event, extension, segment_seconds,
                extra_headers, extra_cookies or {},
            )
            return

        request = urllib.request.Request(stream_url, headers={"User-Agent": "ATC-A2/1.0"})
        with urllib.request.urlopen(request, timeout=int(task.get("timeout") or 30)) as response:
            content_type = response.headers.get("Content-Type")
            extension = self._guess_extension(
                stream_url=stream_url,
                content_type=content_type,
                declared_format=task.get("stream_format"),
            )
            while not stop_event.is_set():
                if hasattr(response, "read1"):
                    chunk = response.read1(4096)
                else:
                    chunk = response.read(4096)
                if not chunk:
                    break
                current_bytes.extend(chunk)
                now = datetime.now(UTC)
                if (now - segment_start).total_seconds() >= segment_seconds:
                    self._save_segment(task, bytes(current_bytes), segment_start, now, extension)
                    current_bytes = bytearray()
                    segment_start = now
            if current_bytes:
                end_time = datetime.now(UTC)
                self._save_segment(task, bytes(current_bytes), segment_start, end_time, extension)

    def _record_stream_via_requests(
        self,
        task: dict[str, object],
        stream_url: str,
        stop_event: threading.Event,
        extension: str,
        segment_seconds: int,
        headers: dict[str, str],
        cookies: dict[str, str],
    ) -> None:
        current_bytes = bytearray()
        segment_start = datetime.now(UTC)
        response = requests.get(
            stream_url, headers=headers, cookies=cookies, stream=True, timeout=30
        )
        response.raise_for_status()
        content_type = response.headers.get("Content-Type")
        extension = self._guess_extension(
            stream_url=stream_url,
            content_type=content_type,
            declared_format=task.get("stream_format"),
        )
        for chunk in response.iter_content(chunk_size=4096):
            if stop_event.is_set():
                break
            if not chunk:
                break
            current_bytes.extend(chunk)
            now = datetime.now(UTC)
            if (now - segment_start).total_seconds() >= segment_seconds:
                self._save_segment(task, bytes(current_bytes), segment_start, now, extension)
                current_bytes = bytearray()
                segment_start = now
        if current_bytes:
            end_time = datetime.now(UTC)
            self._save_segment(task, bytes(current_bytes), segment_start, end_time, extension)

    def _save_segment(
        self,
        task: dict[str, object],
        content: bytes,
        segment_start: datetime,
        segment_end: datetime,
        extension: str,
    ) -> None:
        """把单个实时切片写入本地，并写入语音元数据表。"""

        if not content:
            return
        original_time = format_datetime(segment_start)
        record = self.storage_service.write_audio_bytes(
            unique_id=f"{task['icao_code']}_{segment_start.strftime('%Y%m%d%H%M%S%f')[:-3]}_{uuid.uuid4().hex[:6]}",
            icao_code=str(task["icao_code"]),
            band=str(task["band"]),
            start_at=original_time,
            end_at=format_datetime(segment_end),
            original_time=original_time,
            process_time=utcnow_text(),
            data_type="S",
            extension=extension,
            content=content,
        )
        try:
            self.voice_repository.insert_voice_record(record)
        except Exception:
            Path(record.file_path).unlink(missing_ok=True)
            raise
        task_state = self._receive_state.setdefault(
            int(task["task_id"]),
            {"segmentsSaved": 0, "lastSegmentAt": None, "lastError": None, "streamUrl": None},
        )
        task_state["segmentsSaved"] = int(task_state.get("segmentsSaved", 0)) + 1
        task_state["lastSegmentAt"] = record.end_at

    @staticmethod
    def _guess_extension(stream_url: str, content_type: str | None, declared_format: object) -> str:
        """尽量推断实时流片段最终应该使用的文件后缀。"""

        if isinstance(declared_format, str) and declared_format.strip():
            return f".{declared_format.strip().lstrip('.')}"
        if content_type:
            lowered = content_type.lower()
            if "mpeg" in lowered or "mp3" in lowered:
                return ".mp3"
            if "aac" in lowered:
                return ".aac"
            if "wav" in lowered or "wave" in lowered:
                return ".wav"
        suffix = Path(urlparse(stream_url).path).suffix
        return suffix or ".mp3"
