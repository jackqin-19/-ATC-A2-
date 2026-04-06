from __future__ import annotations

import hashlib
from pathlib import Path

from app.core.config import settings
from app.core.time_utils import utcnow_text
from app.schemas import VoiceRecord


class StorageService:
    def build_storage_path(self, icao_code: str, band: str, start_at: str, file_name: str) -> Path:
        date_dir = start_at[:10]
        return settings.data_root / icao_code.upper() / band / date_dir / file_name

    def write_audio_bytes(
        self,
        *,
        unique_id: str,
        icao_code: str,
        band: str,
        start_at: str,
        end_at: str,
        original_time: str,
        process_time: str | None,
        data_type: str,
        extension: str,
        content: bytes,
    ) -> VoiceRecord:
        file_name = f"{unique_id}_{data_type}.{extension.lstrip('.')}"
        path = self.build_storage_path(icao_code, band, start_at, file_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        checksum = hashlib.sha256(content).hexdigest()
        return VoiceRecord(
            unique_id=unique_id,
            icao_code=icao_code.upper(),
            band=band,
            original_time=original_time,
            process_time=process_time or utcnow_text(),
            file_path=str(path.resolve()),
            file_name=file_name,
            file_size=path.stat().st_size,
            data_type=data_type,
            start_at=start_at,
            end_at=end_at,
            checksum=checksum,
        )
