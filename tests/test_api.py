"""接口层测试。

这组测试通过 FastAPI TestClient 直接请求接口，
验证 HTTP 路由、参数解析、文件导出和集成接口行为是否正常。
"""

from __future__ import annotations

import io
import math
import sqlite3
import shutil
import struct
import subprocess
import threading
import time
import unittest
import wave
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import settings
from app.schemas import DownloadTaskCreate
from app.services.task_service import DownloadTaskService


def build_wav_bytes(seconds: int, freq: float) -> bytes:
    """生成测试用 WAV 二进制内容。"""

    sample_rate = 8000
    frames: list[bytes] = []
    for i in range(sample_rate * seconds):
        value = int(12000 * math.sin(2 * math.pi * freq * i / sample_rate))
        frames.append(struct.pack("<h", value))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"".join(frames))
    return buffer.getvalue()


def build_mp3_bytes(seconds: int) -> bytes:
    """借助 ffmpeg 生成测试用 MP3 二进制内容。"""

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise unittest.SkipTest("ffmpeg not available")
    temp_root = Path.cwd() / "test_artifacts" / "mp3_bytes"
    temp_root.mkdir(parents=True, exist_ok=True)
    output_path = temp_root / f"fixture_{seconds}.mp3"
    try:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=8000:cl=mono",
                "-t",
                str(seconds),
                "-q:a",
                "9",
                "-acodec",
                "libmp3lame",
                str(output_path),
            ],
            check=True,
                capture_output=True,
            )
        return output_path.read_bytes()
    finally:
        if output_path.exists():
            output_path.unlink()


class StreamingFixtureHandler(BaseHTTPRequestHandler):
    """为接口测试提供本地模拟流和归档文件的 HTTP 处理器。"""

    server_version = "A2TestHTTP/1.0"

    def do_GET(self) -> None:  # noqa: N802
        """根据测试路径返回 ASX、实时流或历史归档文件。"""

        if self.path == "/live.asx":
            body = self.server.asx_body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "video/x-ms-asf")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/stream.mp3":
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.end_headers()
            for chunk in self.server.stream_chunks:
                self.wfile.write(chunk)
                self.wfile.flush()
                time.sleep(self.server.chunk_delay)
            return

        if self.path == self.server.archive_path:
            body = self.server.archive_bytes
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        """关闭默认日志输出，避免测试时控制台噪声过多。"""

        return


class StreamingFixtureServer(ThreadingHTTPServer):
    """带自定义测试数据字段的本地 HTTP 测试服务器。"""

    asx_body: str
    stream_chunks: list[bytes]
    chunk_delay: float
    archive_path: str
    archive_bytes: bytes


class A2ApiTestCase(unittest.TestCase):
    """验证 API 层主要接口行为的测试集合。"""

    def setUp(self) -> None:
        """为每个接口测试创建独立的应用环境和测试客户端。"""

        self.root = Path.cwd() / "test_artifacts" / f"api_{self._testMethodName}"
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)
        self.original_values = {
            "workspace_root": settings.workspace_root,
            "data_root": settings.data_root,
            "db_path": settings.db_path,
            "temp_root": settings.temp_root,
            "sync_interval_seconds": settings.sync_interval_seconds,
        }
        object.__setattr__(settings, "workspace_root", self.root)
        object.__setattr__(settings, "data_root", self.root / "data")
        object.__setattr__(settings, "db_path", self.root / "db" / "a2.sqlite3")
        object.__setattr__(settings, "temp_root", self.root / "temp")
        object.__setattr__(settings, "sync_interval_seconds", 1)

        from app.api import app

        self.client_cm = TestClient(app)
        self.client = self.client_cm.__enter__()

    def tearDown(self) -> None:
        """关闭测试客户端并清理临时测试目录。"""

        self.client_cm.__exit__(None, None, None)
        for key, value in self.original_values.items():
            object.__setattr__(settings, key, value)
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)

    def start_stream_server(self) -> tuple[StreamingFixtureServer, threading.Thread]:
        """启动一个本地 HTTP 服务，模拟实时流和历史归档下载源。"""

        server = StreamingFixtureServer(("127.0.0.1", 0), StreamingFixtureHandler)
        server.stream_chunks = [b"MP3DATA" * 256 for _ in range(4)]
        server.chunk_delay = 0.6
        server.archive_path = "/VHHH9-Del-Gnd-Twr-Dir-Apr-14-2026-0000Z.mp3"
        server.archive_bytes = build_mp3_bytes(2)
        port = server.server_address[1]
        server.asx_body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<asx version=\"3.0\"><entry>"
            f"<ref href=\"http://127.0.0.1:{port}/stream.mp3\" />"
            "</entry></asx>"
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    def test_health_endpoint(self) -> None:
        """验证健康检查接口可用。"""

        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["data"]["status"], "ok")

    def test_import_history_and_query_endpoint(self) -> None:
        """验证历史导入接口和时间范围查询接口可以串起来工作。"""

        task_response = self.client.post(
            "/api/a2/tasks/download",
            json={
                "task_name": "api-history-task",
                "icao_code": "ZBAA",
                "band": "tower",
                "start_time": "2026-04-06 10:00:00",
                "end_time": "2026-04-06 10:00:05",
            },
        )
        self.assertEqual(task_response.status_code, 200)
        task_id = task_response.json()["data"]["taskId"]

        import_response = self.client.post(
            (
                f"/api/a2/voice/import/history?taskId={task_id}&icaoCode=ZBAA&band=tower"
                "&startAt=2026-04-06%2010:00:00&endAt=2026-04-06%2010:00:05"
                "&originalTime=2026-04-06%2010:00:00"
            ),
            files={"file": ("segment.wav", build_wav_bytes(5, 440.0), "audio/wav")},
        )
        self.assertEqual(import_response.status_code, 200)

        query_response = self.client.get(
            "/api/a2/voice/query",
            params={
                "startTime": "2026-04-06 10:00:01",
                "endTime": "2026-04-06 10:00:04",
                "icaoCode": "ZBAA",
                "band": "tower",
                "pageNum": 1,
                "pageSize": 10,
            },
        )
        self.assertEqual(query_response.status_code, 200)
        payload = query_response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["data"][0]["icao_code"], "ZBAA")

    def test_slice_endpoint_returns_wav_content(self) -> None:
        """验证切片接口返回的音频内容时长正确。"""

        service = DownloadTaskService()
        task_id = service.create_task(
            DownloadTaskCreate(
                task_name="slice-api-task",
                icao_code="ZBAA",
                band="tower",
                start_time="2026-04-06 10:00:00",
                end_time="2026-04-06 10:00:10",
            )
        )

        fixture_1 = self.root / "slice_1.wav"
        fixture_2 = self.root / "slice_2.wav"
        fixture_1.write_bytes(build_wav_bytes(5, 440.0))
        fixture_2.write_bytes(build_wav_bytes(5, 660.0))
        service.ingest_downloaded_file(
            task_id=task_id,
            source_file=fixture_1,
            icao_code="ZBAA",
            band="tower",
            start_at="2026-04-06 10:00:00",
            end_at="2026-04-06 10:00:05",
            original_time="2026-04-06 10:00:00",
        )
        service.ingest_downloaded_file(
            task_id=task_id,
            source_file=fixture_2,
            icao_code="ZBAA",
            band="tower",
            start_at="2026-04-06 10:00:05",
            end_at="2026-04-06 10:00:10",
            original_time="2026-04-06 10:00:05",
        )

        response = self.client.post(
            "/api/a2/voice/slice",
            json={
                "startTime": "2026-04-06 10:00:02",
                "endTime": "2026-04-06 10:00:08",
                "icaoCode": "ZBAA",
                "band": "tower",
                "outputFormat": "wav",
            },
        )
        self.assertEqual(response.status_code, 200)
        with wave.open(io.BytesIO(response.content), "rb") as wav_file:
            duration = wav_file.getnframes() / wav_file.getframerate()
        self.assertAlmostEqual(duration, 6.0, places=1)

    def test_export_endpoint_returns_wav_content_and_cleans_temp_slice(self) -> None:
        """验证导出接口会返回文件，并在结束后清理临时切片。"""

        service = DownloadTaskService()
        task_id = service.create_task(
            DownloadTaskCreate(
                task_name="export-api-task",
                icao_code="ZBAA",
                band="tower",
                start_time="2026-04-06 10:00:00",
                end_time="2026-04-06 10:00:10",
            )
        )

        fixture_1 = self.root / "export_1.wav"
        fixture_2 = self.root / "export_2.wav"
        fixture_1.write_bytes(build_wav_bytes(5, 440.0))
        fixture_2.write_bytes(build_wav_bytes(5, 660.0))
        service.ingest_downloaded_file(
            task_id=task_id,
            source_file=fixture_1,
            icao_code="ZBAA",
            band="tower",
            start_at="2026-04-06 10:00:00",
            end_at="2026-04-06 10:00:05",
            original_time="2026-04-06 10:00:00",
        )
        service.ingest_downloaded_file(
            task_id=task_id,
            source_file=fixture_2,
            icao_code="ZBAA",
            band="tower",
            start_at="2026-04-06 10:00:05",
            end_at="2026-04-06 10:00:10",
            original_time="2026-04-06 10:00:05",
        )

        response = self.client.get(
            "/api/a2/voice/export",
            params={
                "startTime": "2026-04-06 10:00:02",
                "endTime": "2026-04-06 10:00:08",
                "icaoCode": "ZBAA",
                "band": "tower",
                "outputFormat": "wav",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("ZBAA_tower_2026-04-06_100002_2026-04-06_100008.wav", response.headers["content-disposition"])
        with wave.open(io.BytesIO(response.content), "rb") as wav_file:
            duration = wav_file.getnframes() / wav_file.getframerate()
        self.assertAlmostEqual(duration, 6.0, places=1)
        temp_files = list(settings.temp_root.rglob("slice_*"))
        self.assertEqual(temp_files, [])

    def test_sync_endpoint_reports_missing_file(self) -> None:
        """验证同步接口能报告缺失文件数量。"""

        service = DownloadTaskService()
        task_id = service.create_task(
            DownloadTaskCreate(
                task_name="sync-api-task",
                icao_code="ZGGG",
                band="tower",
                start_time="2026-04-06 12:00:00",
                end_time="2026-04-06 12:00:02",
            )
        )
        fixture = self.root / "sync.wav"
        fixture.write_bytes(build_wav_bytes(2, 500.0))
        record = service.ingest_downloaded_file(
            task_id=task_id,
            source_file=fixture,
            icao_code="ZGGG",
            band="tower",
            start_at="2026-04-06 12:00:00",
            end_at="2026-04-06 12:00:02",
            original_time="2026-04-06 12:00:00",
        )
        Path(record["file_path"]).unlink()

        response = self.client.post("/api/a2/sync/run")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["missing"], 1)

    def test_file_endpoint_reports_missing_physical_file(self) -> None:
        """验证数据库有记录但文件丢失时，下载接口返回 404。"""

        service = DownloadTaskService()
        task_id = service.create_task(
            DownloadTaskCreate(
                task_name="missing-file-api-task",
                icao_code="ZGGG",
                band="tower",
                start_time="2026-04-06 12:00:00",
                end_time="2026-04-06 12:00:02",
            )
        )
        fixture = self.root / "missing.wav"
        fixture.write_bytes(build_wav_bytes(2, 500.0))
        record = service.ingest_downloaded_file(
            task_id=task_id,
            source_file=fixture,
            icao_code="ZGGG",
            band="tower",
            start_at="2026-04-06 12:00:00",
            end_at="2026-04-06 12:00:02",
            original_time="2026-04-06 12:00:00",
        )
        Path(record["file_path"]).unlink()

        response = self.client.get(f"/api/a2/voice/file/{record['unique_id']}")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "voice file missing on disk")

    def test_import_history_endpoint_cleans_temp_upload(self) -> None:
        """验证上传导入接口处理完后会清理临时文件。"""

        task_response = self.client.post(
            "/api/a2/tasks/download",
            json={
                "task_name": "cleanup-history-task",
                "icao_code": "ZBAA",
                "band": "tower",
                "start_time": "2026-04-06 10:00:00",
                "end_time": "2026-04-06 10:00:05",
            },
        )
        self.assertEqual(task_response.status_code, 200)
        task_id = task_response.json()["data"]["taskId"]

        response = self.client.post(
            (
                f"/api/a2/voice/import/history?taskId={task_id}&icaoCode=ZBAA&band=tower"
                "&startAt=2026-04-06%2010:00:00&endAt=2026-04-06%2010:00:05"
                "&originalTime=2026-04-06%2010:00:00"
            ),
            files={"file": ("segment.wav", build_wav_bytes(5, 440.0), "audio/wav")},
        )
        self.assertEqual(response.status_code, 200)
        temp_entries = list(settings.temp_root.rglob("*"))
        self.assertEqual([entry for entry in temp_entries if entry.is_file()], [])

    def test_import_liveatc_history_file_without_manual_metadata(self) -> None:
        """验证只上传 LiveATC 文件也能自动推断元数据。"""

        response = self.client.post(
            "/api/a2/voice/import/history/liveatc",
            files={
                "file": (
                    "VHHH9-Del-Gnd-Twr-Dir-Apr-14-2026-0000Z.mp3",
                    build_mp3_bytes(2),
                    "audio/mpeg",
                )
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()["data"]
        self.assertEqual(payload["icao_code"], "VHHH")
        self.assertEqual(payload["band"], "del-gnd-twr-dir")
        self.assertEqual(payload["start_at"], "2026-04-14 00:00:00")
        self.assertEqual(payload["end_at"], "2026-04-14 00:00:02")

    def test_execute_liveatc_download_persists_file_and_supports_time_range_query(self) -> None:
        """验证 LiveATC 下载、入库、查询和文件回传整条链路。"""

        from unittest.mock import patch

        fixture = self.root / "VHHH9-Del-Gnd-Twr-Dir-Apr-14-2026-0000Z.mp3"
        fixture.write_bytes(build_mp3_bytes(2))

        with patch("app.services.liveatc_downloader.ArchiveDownloader.run", return_value=fixture):
            response = self.client.post(
                "/api/a2/tasks/download/liveatc/execute",
                json={
                    "source_url": "https://www.liveatc.net/archive.php?m=vhhh5",
                    "date": "20260414",
                    "time": "0000-0030Z",
                },
            )
        self.assertEqual(response.status_code, 200)

        payload = response.json()["data"]
        record = payload["record"]
        stored_path = Path(record["file_path"])
        self.assertTrue(stored_path.exists())
        self.assertIn(str(self.root / "data" / "VHHH" / "del-gnd-twr-dir" / "2026-04-14"), str(stored_path))
        self.assertEqual(record["start_at"], "2026-04-14 00:00:00")
        self.assertEqual(record["end_at"], "2026-04-14 00:00:02")

        with sqlite3.connect(settings.db_path) as conn:
            row = conn.execute(
                "SELECT progress, status, start_time, end_time FROM a2_task_download_cfg WHERE task_id = ?",
                (payload["taskId"],),
            ).fetchone()
        self.assertEqual(row[0], 100.0)
        self.assertEqual(row[1], 1)
        self.assertEqual(row[2], "2026-04-14 00:00:00")
        self.assertEqual(row[3], "2026-04-14 00:00:02")

        query_response = self.client.get(
            "/api/a2/voice/query",
            params={
                "startTime": "2026-04-14 00:00:01",
                "endTime": "2026-04-14 00:00:03",
                "icaoCode": "VHHH",
                "band": "del-gnd-twr-dir",
                "pageNum": 1,
                "pageSize": 10,
            },
        )
        self.assertEqual(query_response.status_code, 200)
        query_payload = query_response.json()
        self.assertEqual(query_payload["count"], 1)
        self.assertEqual(query_payload["data"][0]["downloadUrl"], f"/api/a2/voice/file/{record['unique_id']}")

        file_response = self.client.get(query_payload["data"][0]["downloadUrl"])
        self.assertEqual(file_response.status_code, 200)
        self.assertGreater(len(file_response.content), 0)
        download_dir = settings.temp_root / "downloads"
        remaining = [entry for entry in download_dir.rglob("*") if entry.is_file()] if download_dir.exists() else []
        self.assertEqual(remaining, [])

    def test_import_liveatc_history_file_truncates_to_first_30_minutes(self) -> None:
        """验证超长 LiveATC 导入会被裁成前 30 分钟。"""

        response = self.client.post(
            "/api/a2/voice/import/history/liveatc",
            files={
                "file": (
                    "VHHH9-Del-Gnd-Twr-Dir-Apr-14-2026-0000Z.mp3",
                    build_mp3_bytes(1805),
                    "audio/mpeg",
                )
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()["data"]
        self.assertEqual(payload["start_at"], "2026-04-14 00:00:00")
        self.assertEqual(payload["end_at"], "2026-04-14 00:30:00")
        stored_duration = DownloadTaskService._probe_audio_duration_seconds(Path(payload["file_path"]))
        self.assertIsNotNone(stored_duration)
        self.assertLessEqual(stored_duration, 1800)
        self.assertGreaterEqual(stored_duration, 1798)

    def test_sync_endpoint_repairs_stale_metadata(self) -> None:
        """验证同步接口可以修复过期的文件大小和校验信息。"""

        response = self.client.post(
            "/api/a2/voice/import/history/liveatc",
            files={
                "file": (
                    "VHHH9-Del-Gnd-Twr-Dir-Apr-14-2026-0000Z.mp3",
                    build_mp3_bytes(2),
                    "audio/mpeg",
                )
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()["data"]

        with sqlite3.connect(settings.db_path) as conn:
            conn.execute(
                """
                UPDATE a2_voice_info
                SET file_size = ?, checksum = ?, valid_status = ?
                WHERE unique_id = ?
                """,
                (1, "stale-checksum", "invalid", payload["unique_id"]),
            )
            conn.commit()

        sync_response = self.client.post("/api/a2/sync/run")
        self.assertEqual(sync_response.status_code, 200)
        sync_payload = sync_response.json()["data"]
        self.assertEqual(sync_payload["missing"], 0)
        self.assertGreaterEqual(sync_payload["updated"], 1)

        with sqlite3.connect(settings.db_path) as conn:
            row = conn.execute(
                "SELECT file_size, checksum, valid_status FROM a2_voice_info WHERE unique_id = ?",
                (payload["unique_id"],),
            ).fetchone()
        self.assertEqual(row[0], Path(payload["file_path"]).stat().st_size)
        self.assertNotEqual(row[1], "stale-checksum")
        self.assertEqual(row[2], "valid")

    def test_create_task_from_asx_and_receive_stream_segments(self) -> None:
        """验证从 ASX 创建实时任务后，可以真正接收并落盘多个片段。"""

        server, thread = self.start_stream_server()
        try:
            create_response = self.client.post(
                "/api/a2/tasks/realtime/from-asx",
                data={
                    "taskName": "api-live-task",
                    "icaoCode": "ZBAA",
                    "band": "tower",
                    "segmentSeconds": 1,
                    "preferredRef": 0,
                },
                files={"file": ("live.asx", server.asx_body.encode("utf-8"), "video/x-ms-asf")},
            )
            self.assertEqual(create_response.status_code, 200)
            task_id = create_response.json()["data"]["taskId"]

            start_response = self.client.post(
                "/api/a2/tasks/realtime/start-receive",
                json={"task_id": task_id},
            )
            self.assertEqual(start_response.status_code, 200)

            deadline = time.time() + 8
            state_payload = {}
            while time.time() < deadline:
                state_response = self.client.get(f"/api/a2/tasks/realtime/{task_id}/state")
                self.assertEqual(state_response.status_code, 200)
                state_payload = state_response.json()["data"]
                if state_payload["segmentsSaved"] >= 2 and not state_payload["receiving"]:
                    break
                time.sleep(0.2)

            self.client.post(f"/api/a2/tasks/realtime/{task_id}/stop-receive")
            self.assertGreaterEqual(state_payload.get("segmentsSaved", 0), 2)

            start_time = (datetime.now(UTC) - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
            end_time = (datetime.now(UTC) + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
            query_response = self.client.get(
                "/api/a2/voice/query",
                params={
                    "startTime": start_time,
                    "endTime": end_time,
                    "icaoCode": "ZBAA",
                    "band": "tower",
                    "pageNum": 1,
                    "pageSize": 20,
                },
            )
            self.assertEqual(query_response.status_code, 200)
            payload = query_response.json()
            self.assertGreaterEqual(payload["count"], 2)
            self.assertTrue(all(row["data_type"] == "S" for row in payload["data"]))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_start_realtime_receive_reports_missing_task(self) -> None:
        """验证启动不存在的实时任务时会返回明确错误。"""

        response = self.client.post(
            "/api/a2/tasks/realtime/start-receive",
            json={"task_id": 999},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "realtime task 999 not found")

    def test_integration_audio_endpoint_supports_time_range_query(self) -> None:
        """验证集成语音查询接口支持按时间范围过滤。"""

        task_response = self.client.post(
            "/api/a2/tasks/download",
            json={
                "task_name": "integration-audio-task",
                "icao_code": "ZBAA",
                "band": "tower",
                "start_time": "2026-04-06 10:00:00",
                "end_time": "2026-04-06 10:00:05",
            },
        )
        self.assertEqual(task_response.status_code, 200)
        task_id = task_response.json()["data"]["taskId"]

        import_response = self.client.post(
            (
                f"/api/a2/voice/import/history?taskId={task_id}&icaoCode=ZBAA&band=tower"
                "&startAt=2026-04-06%2010:00:00&endAt=2026-04-06%2010:00:05"
                "&originalTime=2026-04-06%2010:00:00"
            ),
            files={"file": ("segment.wav", build_wav_bytes(5, 440.0), "audio/wav")},
        )
        self.assertEqual(import_response.status_code, 200)
        unique_id = import_response.json()["data"]["unique_id"]

        response = self.client.get(
            "/api/v1/integration/audio",
            params={
                "icao_code": "ZBAA",
                "band": "tower",
                "start_time": "2026-04-06 10:00:01",
                "end_time": "2026-04-06 10:00:04",
                "page": 1,
                "page_size": 10,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["data"][0]["unique_id"], unique_id)

    def test_integration_realtime_task_endpoints_support_upsert_and_filtering(self) -> None:
        """验证集成实时任务接口支持新增、更新和过滤。"""

        create_response = self.client.post(
            "/api/v1/integration/a2/realtime-tasks",
            json={
                "task_name": "integration-live-task",
                "source_url": "http://127.0.0.1/live.mp3",
                "protocol": "HTTP_STREAM",
                "timeout": 20,
                "heart_beat": 8,
                "icao_code": "ZBAA",
                "band": "tower",
                "status": 0,
                "segment_seconds": 30,
                "stream_format": "mp3",
            },
        )
        self.assertEqual(create_response.status_code, 200)
        created = create_response.json()["data"]
        self.assertEqual(created["icao_code"], "ZBAA")
        self.assertEqual(created["band"], "tower")

        list_response = self.client.get(
            "/api/v1/integration/a2/realtime-tasks",
            params={"icao_code": "ZBAA", "band": "tower", "page": 1, "page_size": 10},
        )
        self.assertEqual(list_response.status_code, 200)
        list_payload = list_response.json()
        self.assertEqual(list_payload["count"], 1)

        update_response = self.client.post(
            "/api/v1/integration/a2/realtime-tasks",
            json={
                "task_id": created["task_id"],
                "task_name": "integration-live-task-updated",
                "source_url": "http://127.0.0.1/live.mp3",
                "protocol": "HTTP_STREAM",
                "timeout": 25,
                "heart_beat": 9,
                "icao_code": "ZBAA",
                "band": "tower",
                "status": 1,
                "segment_seconds": 45,
                "stream_format": "mp3",
            },
        )
        self.assertEqual(update_response.status_code, 200)
        updated = update_response.json()["data"]
        self.assertEqual(updated["task_name"], "integration-live-task-updated")
        self.assertEqual(updated["status"], 1)

    def test_integration_download_task_endpoints_support_upsert_and_filtering(self) -> None:
        """验证集成下载任务接口支持新增、更新和过滤。"""

        create_response = self.client.post(
            "/api/v1/integration/a2/download-tasks",
            json={
                "task_name": "integration-download-task",
                "icao_code": "VHHH",
                "band": "tower",
                "start_time": "2026-04-14 00:00:00",
                "end_time": "2026-04-14 00:30:00",
                "speed_limit": 0,
                "exec_type": 1,
                "status": 0,
            },
        )
        self.assertEqual(create_response.status_code, 200)
        created = create_response.json()["data"]
        self.assertEqual(created["icao_code"], "VHHH")

        list_response = self.client.get(
            "/api/v1/integration/a2/download-tasks",
            params={"icao_code": "VHHH", "band": "tower", "page": 1, "page_size": 10},
        )
        self.assertEqual(list_response.status_code, 200)
        list_payload = list_response.json()
        self.assertEqual(list_payload["count"], 1)

        update_response = self.client.post(
            "/api/v1/integration/a2/download-tasks",
            json={
                "task_id": created["task_id"],
                "task_name": "integration-download-task-updated",
                "icao_code": "VHHH",
                "band": "tower",
                "start_time": "2026-04-14 00:00:00",
                "end_time": "2026-04-14 00:20:00",
                "speed_limit": 128,
                "exec_type": 1,
                "status": 1,
            },
        )
        self.assertEqual(update_response.status_code, 200)
        updated = update_response.json()["data"]
        self.assertEqual(updated["task_name"], "integration-download-task-updated")
        self.assertEqual(updated["status"], 1)

    def test_integration_a2_system_config_endpoints(self) -> None:
        """验证系统配置读取和更新接口可正常工作。"""

        get_response = self.client.get("/api/v1/integration/a2/system-config")
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(get_response.json()["data"]["max_download_task"], 3)

        update_response = self.client.put(
            "/api/v1/integration/a2/system-config",
            json={
                "storage_root": "/atc/a2/custom/",
                "slice_rule": "10min/200MB",
                "max_download_task": 5,
                "max_realtime_conn": 7,
                "api_timeout": 12,
                "sync_interval": 9,
            },
        )
        self.assertEqual(update_response.status_code, 200)
        payload = update_response.json()["data"]
        self.assertEqual(payload["storage_root"], "/atc/a2/custom/")
        self.assertEqual(payload["max_download_task"], 5)


if __name__ == "__main__":
    unittest.main()
