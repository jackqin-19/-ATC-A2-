from __future__ import annotations

import math
import struct
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import init_db
from app.schemas import DownloadTaskCreate
from app.services.task_service import DownloadTaskService


FIXTURES_DIR = ROOT / "storage" / "fixtures"


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


def main() -> None:
    init_db()
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    segments = [
        ("seg_1.wav", 5, 440.0, "2026-04-06 10:00:00", "2026-04-06 10:00:05"),
        ("seg_2.wav", 5, 660.0, "2026-04-06 10:00:05", "2026-04-06 10:00:10"),
        ("seg_3.wav", 5, 880.0, "2026-04-06 10:00:10", "2026-04-06 10:00:15"),
    ]

    task_service = DownloadTaskService()
    task_id = task_service.create_task(
        DownloadTaskCreate(
            task_name="demo-history-task",
            icao_code="ZBAA",
            band="tower",
            start_time="2026-04-06 10:00:00",
            end_time="2026-04-06 10:00:15",
            priority="medium",
        )
    )

    for file_name, seconds, freq, start_at, end_at in segments:
        file_path = FIXTURES_DIR / file_name
        build_wav(file_path, seconds, freq)
        task_service.ingest_downloaded_file(
            task_id=task_id,
            source_file=file_path,
            icao_code="ZBAA",
            band="tower",
            start_at=start_at,
            end_at=end_at,
            original_time=start_at,
        )

    print("demo data generated")
    print("icaoCode=ZBAA band=tower range=2026-04-06 10:00:00 -> 2026-04-06 10:00:15")
    print("suggested slice range: 2026-04-06 10:00:02 -> 2026-04-06 10:00:12")


if __name__ == "__main__":
    main()
