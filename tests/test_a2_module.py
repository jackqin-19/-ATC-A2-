from __future__ import annotations

import math
import shutil
import sqlite3
import struct
import unittest
import wave
from pathlib import Path

from app.core.config import settings
from app.db import init_db
from app.repositories import VoiceRepository
from app.schemas import DownloadExecuteRequest, DownloadTaskCreate, VoiceQueryRequest
from app.services.audio_service import AudioService
from app.services.query_service import QueryService
from app.services.sync_service import MetadataSyncService
from app.services.task_service import DownloadTaskService


def build_wav(path: Path, seconds: int, freq: float) -> None:
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


class A2ModuleTestCase(unittest.TestCase):
    def setUp(self) -> None:
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
        for key, value in self.original_values.items():
            object.__setattr__(settings, key, value)
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)

    def test_query_voice_returns_overlapping_segments_and_track_ids(self) -> None:
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

        with sqlite3.connect(settings.db_path) as conn:
            conn.execute(
                """
                INSERT INTO adsb_tracks (
                    track_id, callsign, location, altitude, ground_speed, heading, timestamp, icao_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("track-1", "CCA123", "POINT(0 0)", 1000, 200, 90, "2026-04-06 10:00:03", "ZBAA"),
            )
            conn.commit()

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
        self.assertIn("track-1", rows[0]["trackIds"])

    def test_audio_service_composes_cross_segment_wav(self) -> None:
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

    def test_metadata_sync_marks_missing_files(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
