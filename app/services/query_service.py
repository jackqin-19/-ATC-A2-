from __future__ import annotations

from app.repositories import VoiceRepository
from app.schemas import VoiceQueryRequest


class QueryService:
    def __init__(self, repository: VoiceRepository | None = None) -> None:
        self.repository = repository or VoiceRepository()

    def query_voice(self, payload: VoiceQueryRequest) -> tuple[int, list[dict]]:
        total, rows = self.repository.query_voice_records(
            start_time=payload.startTime,
            end_time=payload.endTime,
            icao_code=payload.icaoCode.upper() if payload.icaoCode else None,
            band=payload.band,
            page_num=payload.pageNum,
            page_size=payload.pageSize,
        )
        enriched: list[dict] = []
        for row in rows:
            track_ids = self.repository.find_tracks(row["icao_code"], row["start_at"], row["end_at"])
            for track_id in track_ids:
                self.repository.upsert_voice_track_rel(row["unique_id"], track_id)
            row["trackIds"] = track_ids
            row["downloadUrl"] = f"/api/a2/voice/file/{row['unique_id']}"
            enriched.append(row)
        return total, enriched
