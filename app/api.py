from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.core.config import settings
from app.db import init_db
from app.schemas import (
    ApiResponse,
    DownloadTaskCreate,
    DownloadExecuteRequest,
    LiveAtcDownloadExecuteRequest,
    RealtimeAsxCreate,
    RealtimeMonitorRequest,
    RealtimeReceiveRequest,
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


def _build_voice_export_name(
    *, icao_code: str, band: str, start_time: str, end_time: str, output_format: str
) -> str:
    def sanitize(value: str) -> str:
        return value.replace(" ", "_").replace(":", "").replace("/", "-")

    return (
        f"{icao_code.upper()}_{sanitize(band)}_{sanitize(start_time)}_"
        f"{sanitize(end_time)}.{output_format}"
    )


def _compose_voice_export(payload: VoiceSliceRequest) -> FileResponse:
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

    media_type = "audio/wav" if payload.outputFormat == "wav" else "audio/mpeg"
    filename = _build_voice_export_name(
        icao_code=payload.icaoCode,
        band=payload.band,
        start_time=payload.startTime,
        end_time=payload.endTime,
        output_format=payload.outputFormat,
    )
    return FileResponse(
        path=output_path,
        filename=filename,
        media_type=media_type,
        background=BackgroundTask(output_path.unlink, missing_ok=True),
    )


def _write_upload_to_temp(file: UploadFile, raw: bytes) -> Path:
    original_name = Path(file.filename or "upload.bin").name
    temp_dir = settings.temp_root / uuid4().hex
    temp_path = temp_dir / original_name
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path.write_bytes(raw)
    return temp_path


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


@app.post("/api/a2/tasks/realtime/from-asx")
async def create_realtime_task_from_asx(
    taskName: str = Form(...),
    icaoCode: str = Form(...),
    band: str = Form(...),
    segmentSeconds: int = Form(60),
    preferredRef: int = Form(0),
    file: UploadFile = File(...),
) -> ApiResponse:
    payload = RealtimeAsxCreate(
        task_name=taskName,
        icao_code=icaoCode,
        band=band,
        segment_seconds=segmentSeconds,
        preferred_ref=preferredRef,
    )
    content = await file.read()
    try:
        result = realtime_service.create_task_from_asx(
            task_name=payload.task_name,
            icao_code=payload.icao_code,
            band=payload.band,
            content=content,
            preferred_ref=payload.preferred_ref,
            segment_seconds=payload.segment_seconds,
            filename=file.filename,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(data=result, count=1)


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


@app.post("/api/a2/tasks/realtime/start-receive")
def start_realtime_receive(payload: RealtimeReceiveRequest) -> ApiResponse:
    try:
        realtime_runtime.start_receive(payload.task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(data=realtime_runtime.get_state(payload.task_id), count=1)


@app.post("/api/a2/tasks/realtime/{task_id}/stop-receive")
def stop_realtime_receive(task_id: int) -> ApiResponse:
    realtime_runtime.stop_receive(task_id)
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


@app.post("/api/a2/tasks/download/liveatc/execute")
def execute_liveatc_download(payload: LiveAtcDownloadExecuteRequest) -> ApiResponse:
    try:
        result = download_service.execute_liveatc_download(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(data=result, count=1)


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


@app.get("/api/a2/voice/export")
def export_voice_get(
    startTime: str = Query(...),
    endTime: str = Query(...),
    icaoCode: str = Query(...),
    band: str = Query(...),
    outputFormat: str = Query("wav"),
) -> FileResponse:
    payload = VoiceSliceRequest(
        startTime=startTime,
        endTime=endTime,
        icaoCode=icaoCode,
        band=band,
        outputFormat=outputFormat,
    )
    return _compose_voice_export(payload)


@app.post("/api/a2/voice/slice")
def slice_voice(payload: VoiceSliceRequest) -> FileResponse:
    return _compose_voice_export(payload)


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
    temp_path = _write_upload_to_temp(file, raw)
    try:
        record = realtime_service.ingest_file_segment(
            file_path=temp_path,
            icao_code=icaoCode,
            band=band,
            original_time=originalTime,
            start_at=startAt,
            end_at=endAt,
        )
    finally:
        temp_path.unlink(missing_ok=True)
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
    temp_path = _write_upload_to_temp(file, raw)
    try:
        record = download_service.ingest_downloaded_file(
            task_id=taskId,
            source_file=temp_path,
            icao_code=icaoCode,
            band=band,
            start_at=startAt,
            end_at=endAt,
            original_time=originalTime,
        )
    finally:
        temp_path.unlink(missing_ok=True)
    return ApiResponse(data=record, count=1)


@app.post("/api/a2/voice/import/history/liveatc")
async def import_liveatc_history_file(
    taskId: int | None = Query(None),
    file: UploadFile = File(...),
) -> ApiResponse:
    raw = await file.read()
    temp_path = _write_upload_to_temp(file, raw)
    try:
        record = download_service.import_liveatc_archive_file(
            source_file=temp_path,
            task_id=taskId,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        temp_path.unlink(missing_ok=True)
    return ApiResponse(data=record, count=1)


@app.get("/api/a2/voice/file/{unique_id}")
def get_voice_file(unique_id: str) -> FileResponse:
    row = query_service.repository.get_voice_by_unique_id(unique_id)
    if not row:
        raise HTTPException(status_code=404, detail="file not found")
    file_path = Path(row["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="voice file missing on disk")
    return FileResponse(path=file_path, filename=row["file_name"])


@app.post("/api/a2/sync/run")
def run_metadata_sync() -> ApiResponse:
    result = metadata_sync.run_once()
    return ApiResponse(data=result, count=1)
