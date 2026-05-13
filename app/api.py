"""FastAPI 接口层。

这个文件是整个 A-2 模块的对外入口，负责把 HTTP 请求转成
具体的业务调用。它本身尽量不承载复杂业务逻辑，而是做三件事：
1. 接收和解析请求参数。
2. 调用对应的 service。
3. 把结果包装成统一响应或文件响应返回。
"""

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
    A2SystemConfigUpdateRequest,
    ApiResponse,
    DownloadTaskCreate,
    DownloadExecuteRequest,
    IntegrationAudioQueryRequest,
    IntegrationDownloadTaskQueryRequest,
    IntegrationDownloadTaskUpsertRequest,
    IntegrationRealtimeTaskQueryRequest,
    IntegrationRealtimeTaskUpsertRequest,
    LiveAtcDownloadExecuteRequest,
    RealtimeAsxCreate,
    RealtimeMonitorRequest,
    RealtimeReceiveRequest,
    RealtimeTaskCreate,
    VoiceQueryRequest,
    VoiceSliceRequest,
)
from app.services.audio_service import AudioService
from app.services.exception import ATCError
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
    """生成导出音频时返回给用户的文件名。"""

    def sanitize(value: str) -> str:
        """把时间和频段里的特殊字符替换掉，避免文件名不安全。"""

        return value.replace(" ", "_").replace(":", "").replace("/", "-")

    return (
        f"{icao_code.upper()}_{sanitize(band)}_{sanitize(start_time)}_"
        f"{sanitize(end_time)}.{output_format}"
    )


def _compose_voice_export(payload: VoiceSliceRequest) -> FileResponse:
    """根据时间范围查询、裁剪并导出语音文件。

    这是 `/voice/export` 和 `/voice/slice` 两个接口共用的核心逻辑：
    先查时间重叠片段，再调用音频服务进行裁剪和拼接，最后返回文件。
    """

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
        # 导出文件是临时生成的，响应发完后自动删除，避免临时目录堆积。
        background=BackgroundTask(output_path.unlink, missing_ok=True),
    )


def _write_upload_to_temp(file: UploadFile, raw: bytes) -> Path:
    """把上传文件先落到临时目录，便于后续统一处理。"""

    original_name = Path(file.filename or "upload.bin").name
    temp_dir = settings.temp_root / uuid4().hex
    temp_path = temp_dir / original_name
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path.write_bytes(raw)
    return temp_path


@asynccontextmanager
async def lifespan(_: FastAPI):
    """应用启动和关闭时执行的生命周期逻辑。"""

    init_db()
    # 应用启动时顺带启动元数据后台同步线程。
    metadata_sync.start()
    try:
        yield
    finally:
        metadata_sync.stop()


app = FastAPI(title="ATC A-2 Voice Module", version="1.0.0", lifespan=lifespan)


@app.get("/health")
def health() -> ApiResponse:
    """健康检查接口，用于确认服务已启动。"""

    return ApiResponse(data={"status": "ok"}, count=1)


# =========================
# 实时任务相关接口
# =========================
@app.post("/api/a2/tasks/realtime")
def create_realtime_task(payload: RealtimeTaskCreate) -> ApiResponse:
    """创建一条实时接收任务配置。"""

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
    """通过上传 ASX 文件创建实时任务。

    这个接口的意义是：用户不需要自己解析 ASX 播放列表，
    直接把文件传进来，系统自动抽取真实流地址并创建任务。
    """

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
    """列出全部实时任务。"""

    rows = realtime_service.list_tasks()
    return ApiResponse(data=rows, count=len(rows))


@app.post("/api/a2/tasks/realtime/start-monitor")
def start_realtime_monitor(payload: RealtimeMonitorRequest) -> ApiResponse:
    """启动实时任务的心跳监控线程。"""

    realtime_runtime.start_monitor(
        task_id=payload.task_id,
        heartbeat_payload=payload.heartbeat_payload,
        heartbeat_expect=payload.heartbeat_expect,
    )
    return ApiResponse(data=realtime_runtime.get_state(payload.task_id), count=1)


@app.post("/api/a2/tasks/realtime/{task_id}/stop-monitor")
def stop_realtime_monitor(task_id: int) -> ApiResponse:
    """停止实时任务的心跳监控线程。"""

    realtime_runtime.stop_monitor(task_id)
    return ApiResponse(data=realtime_runtime.get_state(task_id), count=1)


@app.post("/api/a2/tasks/realtime/start-receive")
def start_realtime_receive(payload: RealtimeReceiveRequest) -> ApiResponse:
    """启动实时流接收线程，开始真正拉取音频数据。"""

    try:
        realtime_runtime.start_receive(payload.task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(data=realtime_runtime.get_state(payload.task_id), count=1)


@app.post("/api/a2/tasks/realtime/{task_id}/stop-receive")
def stop_realtime_receive(task_id: int) -> ApiResponse:
    """停止实时流接收线程。"""

    realtime_runtime.stop_receive(task_id)
    return ApiResponse(data=realtime_runtime.get_state(task_id), count=1)


@app.get("/api/a2/tasks/realtime/{task_id}/state")
def get_realtime_monitor_state(task_id: int) -> ApiResponse:
    """查看实时任务当前运行状态。"""

    return ApiResponse(data=realtime_runtime.get_state(task_id), count=1)


@app.get("/api/a2/tasks/realtime/test-connection")
def test_realtime_connection(
    host: str = Query(...),
    port: int = Query(...),
    timeout: int = Query(5),
) -> ApiResponse:
    """测试 socket 目标地址能否建立连接。"""

    try:
        result = realtime_service.test_connection(host, port, timeout)
        return ApiResponse(data=result, count=1)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# =========================
# 历史下载任务相关接口
# =========================
@app.post("/api/a2/tasks/download")
def create_download_task(payload: DownloadTaskCreate) -> ApiResponse:
    """创建一条历史下载任务配置。"""

    task_id = download_service.create_task(payload)
    return ApiResponse(data={"taskId": task_id}, count=1)


@app.get("/api/a2/tasks/download")
def list_download_tasks() -> ApiResponse:
    """列出全部历史下载任务。"""

    rows = download_service.list_tasks()
    return ApiResponse(data=rows, count=len(rows))


@app.post("/api/a2/tasks/download/execute")
def execute_download_task(payload: DownloadExecuteRequest) -> ApiResponse:
    """执行普通历史文件下载。"""

    try:
        record = download_service.execute_http_download(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(data=record, count=1)


@app.post("/api/a2/tasks/download/liveatc/execute")
def execute_liveatc_download(payload: LiveAtcDownloadExecuteRequest) -> ApiResponse:
    """执行 LiveATC 归档下载，并自动推断元数据。"""

    try:
        result = download_service.execute_liveatc_download(payload)
    except (ATCError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(data=result, count=1)


# =========================
# 语音查询、导出与导入接口
# =========================
@app.post("/api/a2/voice/query")
def query_voice_post(payload: VoiceQueryRequest) -> ApiResponse:
    """通过 POST 方式按时间范围查询语音。"""

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
    """通过 GET 方式按时间范围查询语音。

    同时保留 GET 和 POST，是为了兼容不同的前端或集成调用方式。
    """

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
    """直接导出指定时间范围内的完整语音文件。"""

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
    """按时间范围裁剪并返回语音文件。"""

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
    """手动导入一段实时语音文件。"""

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
    """手动导入一段历史语音文件，并挂到指定下载任务下。"""

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
    """导入一个符合 LiveATC 命名规则的历史归档文件。"""

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
    """根据唯一 ID 下载原始语音文件。"""

    row = query_service.repository.get_voice_by_unique_id(unique_id)
    if not row:
        raise HTTPException(status_code=404, detail="file not found")
    file_path = Path(row["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="voice file missing on disk")
    return FileResponse(path=file_path, filename=row["file_name"])


@app.post("/api/a2/sync/run")
def run_metadata_sync() -> ApiResponse:
    """手动触发一次元数据同步修复。"""

    result = metadata_sync.run_once()
    return ApiResponse(data=result, count=1)


# =========================
# 面向外部系统的集成接口
# =========================
@app.get("/api/v1/integration/audio")
def list_integration_audio(
    unique_id: str | None = Query(None),
    icao_code: str | None = Query(None),
    band: str | None = Query(None),
    start_time: str | None = Query(None),
    end_time: str | None = Query(None),
    page: int = Query(1),
    page_size: int = Query(20),
) -> ApiResponse:
    """供外部系统按条件查询语音元数据。"""

    payload = IntegrationAudioQueryRequest(
        unique_id=unique_id,
        icao_code=icao_code,
        band=band,
        start_time=start_time,
        end_time=end_time,
        page=page,
        page_size=page_size,
    )
    total, rows = query_service.list_audio(payload)
    return ApiResponse(data=rows, count=total)


@app.get("/api/v1/integration/a2/realtime-tasks")
def list_integration_realtime_tasks(
    icao_code: str | None = Query(None),
    band: str | None = Query(None),
    status: int | None = Query(None),
    page: int = Query(1),
    page_size: int = Query(20),
) -> ApiResponse:
    """供外部系统分页查询实时任务。"""

    payload = IntegrationRealtimeTaskQueryRequest(
        icao_code=icao_code,
        band=band,
        status=status,
        page=page,
        page_size=page_size,
    )
    total, rows = realtime_service.task_repo.list_realtime_tasks_filtered(
        icao_code=payload.icao_code,
        band=payload.band,
        status=payload.status,
        page_num=payload.page,
        page_size=payload.page_size,
    )
    return ApiResponse(data=rows, count=total)


@app.post("/api/v1/integration/a2/realtime-tasks")
def upsert_integration_realtime_task(payload: IntegrationRealtimeTaskUpsertRequest) -> ApiResponse:
    """供外部系统新增或更新实时任务。"""

    task_id = realtime_service.task_repo.upsert_realtime_task(payload)
    row = realtime_service.task_repo.get_realtime_task(task_id)
    return ApiResponse(data=row, count=1)


@app.get("/api/v1/integration/a2/download-tasks")
def list_integration_download_tasks(
    icao_code: str | None = Query(None),
    band: str | None = Query(None),
    status: int | None = Query(None),
    page: int = Query(1),
    page_size: int = Query(20),
) -> ApiResponse:
    """供外部系统分页查询下载任务。"""

    payload = IntegrationDownloadTaskQueryRequest(
        icao_code=icao_code,
        band=band,
        status=status,
        page=page,
        page_size=page_size,
    )
    total, rows = download_service.task_repo.list_download_tasks_filtered(
        icao_code=payload.icao_code,
        band=payload.band,
        status=payload.status,
        page_num=payload.page,
        page_size=payload.page_size,
    )
    return ApiResponse(data=rows, count=total)


@app.post("/api/v1/integration/a2/download-tasks")
def upsert_integration_download_task(payload: IntegrationDownloadTaskUpsertRequest) -> ApiResponse:
    """供外部系统新增或更新下载任务。"""

    task_id = download_service.task_repo.upsert_download_task(payload)
    row = download_service.task_repo.get_download_task(task_id)
    return ApiResponse(data=row, count=1)


@app.get("/api/v1/integration/a2/system-config")
def get_integration_a2_system_config() -> ApiResponse:
    """读取系统基础配置，供外部系统展示或联调。"""

    row = download_service.task_repo.get_system_config()
    return ApiResponse(data=row, count=1 if row else 0)


@app.put("/api/v1/integration/a2/system-config")
def update_integration_a2_system_config(payload: A2SystemConfigUpdateRequest) -> ApiResponse:
    """更新系统基础配置。"""

    row = download_service.task_repo.update_system_config(payload)
    return ApiResponse(data=row, count=1 if row else 0)
