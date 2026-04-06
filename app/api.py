from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from app.core.config import settings
from app.db import init_db
from app.schemas import (
    ApiResponse,
    DownloadTaskCreate,
    DownloadExecuteRequest,
    RealtimeMonitorRequest,
    RealtimeTaskCreate,
    VoiceQueryRequest,
    VoiceSliceRequest,
)
from app.services.audio_service import AudioService
from app.services.query_service import QueryService
from app.services.runtime_service import RealtimeConnectionManager
from app.services.sync_service import MetadataSyncService
from app.services.task_service import DownloadTaskService, RealtimeTaskService

query_service = QueryService()
audio_service = AudioService()
realtime_service = RealtimeTaskService()
download_service = DownloadTaskService()
realtime_runtime = RealtimeConnectionManager()
metadata_sync = MetadataSyncService()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    metadata_sync.start()
    try:
        yield
    finally:
        metadata_sync.stop()


app = FastAPI(title="ATC A-2 Voice Module", version="1.0.0", lifespan=lifespan)


@app.get("/health")
def health() -> ApiResponse:
    return ApiResponse(data={"status": "ok"}, count=1)


@app.post("/api/a2/tasks/realtime")
def create_realtime_task(payload: RealtimeTaskCreate) -> ApiResponse:
    task_id = realtime_service.create_task(payload)
    return ApiResponse(data={"taskId": task_id}, count=1)


@app.get("/api/a2/tasks/realtime")
def list_realtime_tasks() -> ApiResponse:
    rows = realtime_service.list_tasks()
    return ApiResponse(data=rows, count=len(rows))


@app.post("/api/a2/tasks/realtime/start-monitor")
def start_realtime_monitor(payload: RealtimeMonitorRequest) -> ApiResponse:
    realtime_runtime.start_monitor(
        task_id=payload.task_id,
        heartbeat_payload=payload.heartbeat_payload,
        heartbeat_expect=payload.heartbeat_expect,
    )
    return ApiResponse(data=realtime_runtime.get_state(payload.task_id), count=1)


@app.post("/api/a2/tasks/realtime/{task_id}/stop-monitor")
def stop_realtime_monitor(task_id: int) -> ApiResponse:
    realtime_runtime.stop_monitor(task_id)
    return ApiResponse(data=realtime_runtime.get_state(task_id), count=1)


@app.get("/api/a2/tasks/realtime/{task_id}/state")
def get_realtime_monitor_state(task_id: int) -> ApiResponse:
    return ApiResponse(data=realtime_runtime.get_state(task_id), count=1)


@app.get("/api/a2/tasks/realtime/test-connection")
def test_realtime_connection(
    host: str = Query(...),
    port: int = Query(...),
    timeout: int = Query(5),
) -> ApiResponse:
    try:
        result = realtime_service.test_connection(host, port, timeout)
        return ApiResponse(data=result, count=1)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/a2/tasks/download")
def create_download_task(payload: DownloadTaskCreate) -> ApiResponse:
    task_id = download_service.create_task(payload)
    return ApiResponse(data={"taskId": task_id}, count=1)


@app.get("/api/a2/tasks/download")
def list_download_tasks() -> ApiResponse:
    rows = download_service.list_tasks()
    return ApiResponse(data=rows, count=len(rows))


@app.post("/api/a2/tasks/download/execute")
def execute_download_task(payload: DownloadExecuteRequest) -> ApiResponse:
    try:
        record = download_service.execute_http_download(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(data=record, count=1)


@app.post("/api/a2/voice/query")
def query_voice_post(payload: VoiceQueryRequest) -> ApiResponse:
    total, rows = query_service.query_voice(payload)
    return ApiResponse(data=rows, count=total)


@app.get("/api/a2/voice/query")
def query_voice_get(
    startTime: str = Query(...),
    endTime: str = Query(...),
    icaoCode: str | None = Query(None),
    band: str | None = Query(None),
    pageNum: int = Query(1),
    pageSize: int = Query(10),
) -> ApiResponse:
    payload = VoiceQueryRequest(
        startTime=startTime,
        endTime=endTime,
        icaoCode=icaoCode,
        band=band,
        pageNum=pageNum,
        pageSize=pageSize,
    )
    total, rows = query_service.query_voice(payload)
    return ApiResponse(data=rows, count=total)


@app.post("/api/a2/voice/slice")
def slice_voice(payload: VoiceSliceRequest) -> FileResponse:
    segments = query_service.repository.query_overlapping_segments(
        payload.startTime,
        payload.endTime,
        payload.icaoCode.upper(),
        payload.band,
    )
    try:
        output_path = audio_service.compose_time_range_audio(
            segments=segments,
            query_start=payload.startTime,
            query_end=payload.endTime,
            output_format=payload.outputFormat,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(path=output_path, filename=output_path.name)


@app.post("/api/a2/voice/import/realtime")
async def import_realtime_segment(
    icaoCode: str = Query(...),
    band: str = Query(...),
    originalTime: str = Query(...),
    startAt: str = Query(...),
    endAt: str = Query(...),
    file: UploadFile = File(...),
) -> ApiResponse:
    raw = await file.read()
    temp_path = settings.temp_root / file.filename
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path.write_bytes(raw)
    record = realtime_service.ingest_file_segment(
        file_path=temp_path,
        icao_code=icaoCode,
        band=band,
        original_time=originalTime,
        start_at=startAt,
        end_at=endAt,
    )
    return ApiResponse(data=record, count=1)


@app.post("/api/a2/voice/import/history")
async def import_history_segment(
    taskId: int = Query(...),
    icaoCode: str = Query(...),
    band: str = Query(...),
    startAt: str = Query(...),
    endAt: str = Query(...),
    originalTime: str | None = Query(None),
    file: UploadFile = File(...),
) -> ApiResponse:
    raw = await file.read()
    temp_path = settings.temp_root / file.filename
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path.write_bytes(raw)
    record = download_service.ingest_downloaded_file(
        task_id=taskId,
        source_file=temp_path,
        icao_code=icaoCode,
        band=band,
        start_at=startAt,
        end_at=endAt,
        original_time=originalTime,
    )
    return ApiResponse(data=record, count=1)


@app.get("/api/a2/voice/file/{unique_id}")
def get_voice_file(unique_id: str) -> FileResponse:
    row = query_service.repository.get_voice_by_unique_id(unique_id)
    if not row:
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(path=row["file_path"], filename=row["file_name"])


@app.post("/api/a2/sync/run")
def run_metadata_sync() -> ApiResponse:
    result = metadata_sync.run_once()
    return ApiResponse(data=result, count=1)
