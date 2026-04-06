from __future__ import annotations

import io
import math
import shutil
import struct
import unittest
import wave
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import settings
from app.schemas import DownloadTaskCreate
from app.services.task_service import DownloadTaskService


def build_wav_bytes(seconds: int, freq: float) -> bytes:
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


class A2ApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
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
        self.client_cm.__exit__(None, None, None)
        for key, value in self.original_values.items():
            object.__setattr__(settings, key, value)
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)

    def test_health_endpoint(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["data"]["status"], "ok")

    def test_import_history_and_query_endpoint(self) -> None:
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

    def test_sync_endpoint_reports_missing_file(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
