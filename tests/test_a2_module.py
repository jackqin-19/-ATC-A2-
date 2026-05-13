"""核心业务层测试。

这组测试主要验证不经过 HTTP 接口时，底层服务和仓储逻辑本身是否正确。
"""

from __future__ import annotations

import math
import shutil
import sqlite3
import struct
import subprocess
import unittest
import wave
from pathlib import Path

from app.core.config import settings
from app.db import init_db
from app.repositories import VoiceRepository
from app.schemas import DownloadExecuteRequest, DownloadTaskCreate, LiveAtcDownloadExecuteRequest, VoiceQueryRequest
from app.services.audio_service import AudioService
from app.services.query_service import QueryService
from app.services.sync_service import MetadataSyncService
from app.services.task_service import DownloadTaskService, RealtimeTaskService


def build_wav(path: Path, seconds: int, freq: float) -> None:
    """生成测试用 WAV 文件。"""

    sample_rate = 8000
    frames: list[bytes] = []
    for i in range(sample_rate * seconds):
        value = int(12000 * math.sin(2 * math.pi * freq * i / sample_rate))
        frames.append(struct.pack("<h", value))
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"".join(frames))


def build_mp3(path: Path, seconds: int) -> None:
    """借助 ffmpeg 生成测试用 MP3 文件。"""

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise unittest.SkipTest("ffmpeg not available")
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
            str(path),
        ],
        check=True,
        capture_output=True,
    )


class A2ModuleTestCase(unittest.TestCase):
    """验证 service 和 repository 层核心逻辑的测试集合。"""

    def setUp(self) -> None:
        """为每个测试准备独立的临时工作区和数据库。"""

        self.root = Path.cwd() / "test_artifacts" / self._testMethodName
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
        init_db()

    def tearDown(self) -> None:
        """测试结束后恢复全局配置并清理临时目录。"""

        for key, value in self.original_values.items():
            object.__setattr__(settings, key, value)
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)

    def test_query_voice_returns_overlapping_segments(self) -> None:
        """验证按时间范围查询时，能返回时间重叠的语音片段。"""

        service = DownloadTaskService()
        fixture_1 = self.root / "seg1.wav"
        fixture_2 = self.root / "seg2.wav"
        build_wav(fixture_1, 5, 440.0)
        build_wav(fixture_2, 5, 660.0)

        task_id = service.create_task(
            DownloadTaskCreate(
                task_name="query-demo",
                icao_code="ZBAA",
                band="tower",
                start_time="2026-04-06 10:00:00",
                end_time="2026-04-06 10:00:10",
            )
        )
        record_1 = service.ingest_downloaded_file(
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

        total, rows = QueryService().query_voice(
            VoiceQueryRequest(
                startTime="2026-04-06 10:00:02",
                endTime="2026-04-06 10:00:08",
                icaoCode="ZBAA",
                band="tower",
                pageNum=1,
                pageSize=10,
            )
        )

        self.assertEqual(total, 2)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["unique_id"], record_1["unique_id"])
        self.assertIn("downloadUrl", rows[0])

    def test_audio_service_composes_cross_segment_wav(self) -> None:
        """验证跨多个片段的 WAV 查询可以被正确拼接。"""

        service = DownloadTaskService()
        fixture_1 = self.root / "slice1.wav"
        fixture_2 = self.root / "slice2.wav"
        build_wav(fixture_1, 5, 440.0)
        build_wav(fixture_2, 5, 660.0)

        task_id = service.create_task(
            DownloadTaskCreate(
                task_name="slice-demo",
                icao_code="ZBAA",
                band="tower",
                start_time="2026-04-06 10:00:00",
                end_time="2026-04-06 10:00:10",
            )
        )
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

        segments = VoiceRepository().query_overlapping_segments(
            "2026-04-06 10:00:02",
            "2026-04-06 10:00:08",
            "ZBAA",
            "tower",
        )
        output = AudioService().compose_time_range_audio(
            segments=segments,
            query_start="2026-04-06 10:00:02",
            query_end="2026-04-06 10:00:08",
            output_format="wav",
        )

        with wave.open(str(output), "rb") as wav_file:
            duration = wav_file.getnframes() / wav_file.getframerate()
        self.assertAlmostEqual(duration, 6.0, places=1)

    def test_execute_http_download_imports_file_and_updates_progress(self) -> None:
        """验证普通下载流程会写入文件并把进度更新为完成。"""

        fixture = self.root / "history.wav"
        build_wav(fixture, 3, 550.0)
        service = DownloadTaskService()
        task_id = service.create_task(
            DownloadTaskCreate(
                task_name="download-demo",
                icao_code="ZSPD",
                band="ground",
                start_time="2026-04-06 11:00:00",
                end_time="2026-04-06 11:00:03",
            )
        )

        record = service.execute_http_download(
            DownloadExecuteRequest(
                task_id=task_id,
                source_url=fixture.resolve().as_uri(),
                icao_code="ZSPD",
                band="ground",
                start_time="2026-04-06 11:00:00",
                end_time="2026-04-06 11:00:03",
                original_time="2026-04-06 11:00:00",
            )
        )

        self.assertTrue(Path(record["file_path"]).exists())
        with sqlite3.connect(settings.db_path) as conn:
            row = conn.execute(
                "SELECT progress, status FROM a2_task_download_cfg WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        self.assertEqual(row[0], 100.0)
        self.assertEqual(row[1], 1)

    def test_parse_liveatc_archive_metadata_from_filename(self) -> None:
        """验证可以仅从 LiveATC 文件名中解析出基础元数据。"""

        metadata = DownloadTaskService().parse_liveatc_archive_metadata(
            "VHHH9-Del-Gnd-Twr-Dir-Apr-14-2026-0000Z.mp3"
        )

        self.assertEqual(metadata.icao_code, "VHHH")
        self.assertEqual(metadata.band, "del-gnd-twr-dir")
        self.assertEqual(metadata.start_at, "2026-04-14 00:00:00")
        self.assertEqual(metadata.end_at, "2026-04-14 00:00:00")

    def test_execute_liveatc_download_inferrs_metadata_from_file_name(self) -> None:
        """验证 SeleniumBase 下载完成后，元数据推断与入库流程正确。"""

        fixture = self.root / "VHHH5-App-Dep-Dir-Zone-Apr-09-2026-0630Z.mp3"
        build_mp3(fixture, 2)

        from unittest.mock import patch
        from app.services.liveatc_downloader import ArchiveDownloader

        with patch.object(ArchiveDownloader, "run", return_value=fixture):
            result = DownloadTaskService().execute_liveatc_download(
                LiveAtcDownloadExecuteRequest(
                    source_url="https://www.liveatc.net/archive.php?m=vhhh5",
                    date="20260409",
                    time="0630-0700Z",
                )
            )

        record = result["record"]
        self.assertEqual(record["icao_code"], "VHHH")
        self.assertEqual(record["band"], "app-dep-dir-zone")
        self.assertEqual(record["start_at"], "2026-04-09 06:30:00")
        self.assertEqual(record["end_at"], "2026-04-09 06:30:02")
        self.assertTrue(Path(record["file_path"]).exists())

    def test_import_liveatc_archive_limits_metadata_and_file_to_30_minutes(self) -> None:
        """验证超长 LiveATC 文件会被截断到前 30 分钟。"""

        fixture = self.root / "VHHH5-App-Dep-Dir-Zone-Apr-09-2026-0630Z.mp3"
        build_mp3(fixture, 1805)

        record = DownloadTaskService().import_liveatc_archive_file(source_file=fixture)

        self.assertEqual(record["start_at"], "2026-04-09 06:30:00")
        self.assertEqual(record["end_at"], "2026-04-09 07:00:00")
        stored_duration = DownloadTaskService._probe_audio_duration_seconds(Path(record["file_path"]))
        self.assertIsNotNone(stored_duration)
        self.assertLessEqual(stored_duration, 1800)
        self.assertGreaterEqual(stored_duration, 1798)

    def test_metadata_sync_marks_missing_files(self) -> None:
        """验证同步服务能识别数据库有记录但磁盘文件缺失的情况。"""

        fixture = self.root / "sync.wav"
        build_wav(fixture, 2, 500.0)
        service = DownloadTaskService()
        task_id = service.create_task(
            DownloadTaskCreate(
                task_name="sync-demo",
                icao_code="ZGGG",
                band="tower",
                start_time="2026-04-06 12:00:00",
                end_time="2026-04-06 12:00:02",
            )
        )
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

        result = MetadataSyncService().run_once()
        refreshed = VoiceRepository().get_voice_by_unique_id(record["unique_id"])

        self.assertEqual(result["missing"], 1)
        self.assertIsNotNone(refreshed)
        self.assertEqual(refreshed["valid_status"], "missing")

    def test_create_realtime_task_from_asx_extracts_stream_url(self) -> None:
        """验证 ASX 解析后可以创建出带真实流地址的实时任务。"""

        result = RealtimeTaskService().create_task_from_asx(
            task_name="live-stream-demo",
            icao_code="ZBAA",
            band="tower",
            content=(
                b'<?xml version="1.0" encoding="UTF-8"?>'
                b"<asx version=\"3.0\"><entry><ref href=\"http://127.0.0.1/live.mp3\" />"
                b"</entry></asx>"
            ),
            segment_seconds=15,
        )

        rows = RealtimeTaskService().list_tasks()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_url"], "http://127.0.0.1/live.mp3")
        self.assertEqual(rows[0]["segment_seconds"], 15)
        self.assertEqual(result["streamUrl"], "http://127.0.0.1/live.mp3")
        self.assertEqual(result["refs"], ["http://127.0.0.1/live.mp3"])


if __name__ == "__main__":
    unittest.main()
