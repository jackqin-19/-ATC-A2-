"""接口请求与响应模型定义。

把参数校验前置到这一层有两个好处：
1. API 层不用手动写大量 `if` 判断，代码更干净。
2. 非法参数会在进入业务逻辑前就被拦住，减少后续出错分支。
"""

from __future__ import annotations

from typing import Any
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.time_utils import parse_datetime


class VoiceRecord(BaseModel):
    """语音文件元数据记录。

    这是系统内部最核心的一类数据：
    一条记录既描述一个物理音频文件，也描述它对应的机场、频段、
    原始时间、处理时间、起止范围和有效状态。
    """

    unique_id: str
    icao_code: str
    band: str
    original_time: str
    process_time: str
    file_path: str
    file_name: str
    file_size: int
    data_type: Literal["S", "H"]
    created_at: str | None = None
    start_at: str
    end_at: str
    checksum: str | None = None
    valid_status: str = "valid"


class RealtimeTaskCreate(BaseModel):
    """创建实时接收任务时使用的请求模型。

    实时任务既可以走传统 socket 连接，也可以直接走 HTTP 流地址，
    所以这里同时保留了两类输入字段。
    """

    task_name: str
    server_addr: str | None = None
    server_port: int | None = None
    protocol: str = "TCP"
    timeout: int = 30
    heart_beat: int = 10
    icao_code: str = Field(min_length=4, max_length=4)
    band: str
    source_url: str | None = None
    segment_seconds: int = 60
    stream_format: str | None = None

    @field_validator("icao_code")
    @classmethod
    def upper_icao(cls, value: str) -> str:
        return value.upper()

    @field_validator("segment_seconds")
    @classmethod
    def positive_segment_seconds(cls, value: int) -> int:
        if value < 1:
            raise ValueError("segment_seconds must be a positive integer")
        return value

    @model_validator(mode="after")
    def validate_source(self) -> "RealtimeTaskCreate":
        """实时任务必须提供 socket 地址或 HTTP 流地址。

        这条校验规则保证任务创建出来后一定“有东西可连”，
        否则后面的监控线程和接收线程都没法真正工作。
        """

        has_socket_target = bool(self.server_addr) and self.server_port is not None
        if not has_socket_target and not self.source_url:
            raise ValueError("either source_url or server_addr/server_port must be provided")
        return self


class DownloadTaskCreate(BaseModel):
    """创建历史下载任务时使用的请求模型。"""

    task_name: str
    icao_code: str = Field(min_length=4, max_length=4)
    band: str
    start_time: str
    end_time: str
    speed_limit: int = 0
    exec_type: int = 1
    exec_time: str | None = None
    priority: Literal["high", "medium", "low"] = "medium"

    @field_validator("icao_code")
    @classmethod
    def upper_icao(cls, value: str) -> str:
        return value.upper()

    @field_validator("end_time")
    @classmethod
    def validate_time_range(cls, value: str, info) -> str:
        start = info.data.get("start_time")
        if start and parse_datetime(start) > parse_datetime(value):
            raise ValueError("start_time must be earlier than or equal to end_time")
        return value


class DownloadExecuteRequest(BaseModel):
    """执行普通历史文件下载时使用的请求模型。

    这里允许时间和机场信息为空，是为了兼容两种场景：
    1. 手工传入完整元数据。
    2. 只传 URL，由系统后续从 LiveATC 文件名自动推断。
    """

    task_id: int
    source_url: str
    icao_code: str | None = Field(default=None, min_length=4, max_length=4)
    band: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    original_time: str | None = None
    speed_limit_kbps: int = 0

    @field_validator("icao_code")
    @classmethod
    def upper_icao(cls, value: str | None) -> str | None:
        return value.upper() if value else value


class LiveAtcDownloadExecuteRequest(BaseModel):
    """执行 LiveATC 归档下载时使用的请求模型。

    source_url 应为 LiveATC 归档页面地址（如 https://www.liveatc.net/archive.php?m=vhhh5），
    系统将通过浏览器自动化选择日期/时段并触发下载。
    """

    source_url: str
    date: str
    time_slot: str = Field(alias="time")
    icao_code: str | None = Field(default=None, min_length=4, max_length=4)
    band: str | None = None
    speed_limit_kbps: int = 0

    @field_validator("icao_code")
    @classmethod
    def upper_icao(cls, value: str | None) -> str | None:
        return value.upper() if value else value


class LiveAtcImportedFileRequest(BaseModel):
    """导入 LiveATC 归档文件时可选关联的任务信息。"""

    task_id: int | None = None


class RealtimeMonitorRequest(BaseModel):
    """启动实时监控线程时使用的请求模型。"""

    task_id: int
    heartbeat_payload: str = "PING\n"
    heartbeat_expect: str | None = None


class RealtimeReceiveRequest(BaseModel):
    """启动实时接收线程时使用的请求模型。"""

    task_id: int


class RealtimeAsxCreate(BaseModel):
    """通过上传 ASX 文件创建实时任务时使用的请求模型。"""

    task_name: str
    icao_code: str = Field(min_length=4, max_length=4)
    band: str
    segment_seconds: int = 60
    preferred_ref: int = 0

    @field_validator("icao_code")
    @classmethod
    def upper_icao(cls, value: str) -> str:
        return value.upper()

    @field_validator("segment_seconds", "preferred_ref")
    @classmethod
    def non_negative_int(cls, value: int, info) -> int:
        if info.field_name == "segment_seconds" and value < 1:
            raise ValueError("segment_seconds must be a positive integer")
        if info.field_name == "preferred_ref" and value < 0:
            raise ValueError("preferred_ref must be greater than or equal to 0")
        return value


class VoiceQueryRequest(BaseModel):
    """按时间范围查询语音元数据时使用的请求模型。

    这是 A-2 最核心的查询入口，约束了分页参数必须为正数，
    同时强制结束时间不能早于开始时间。
    """

    startTime: str
    endTime: str
    icaoCode: str | None = None
    band: str | None = None
    pageNum: int = 1
    pageSize: int = 10

    @field_validator("pageNum", "pageSize")
    @classmethod
    def positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("must be a positive integer")
        return value

    @field_validator("endTime")
    @classmethod
    def validate_range(cls, value: str, info) -> str:
        start = info.data.get("startTime")
        if start and parse_datetime(start) > parse_datetime(value):
            raise ValueError("startTime must be earlier than or equal to endTime")
        return value


class VoiceSliceRequest(BaseModel):
    """按时间范围裁剪并导出语音时使用的请求模型。

    和普通查询相比，这里多了 `outputFormat`，
    表示最终导出的音频格式是 `wav` 还是 `mp3`。
    """

    startTime: str
    endTime: str
    icaoCode: str
    band: str
    outputFormat: Literal["wav", "mp3"] = "wav"

    @field_validator("icaoCode")
    @classmethod
    def upper_icao(cls, value: str) -> str:
        return value.upper()

    @field_validator("endTime")
    @classmethod
    def validate_range(cls, value: str, info) -> str:
        start = info.data.get("startTime")
        if start and parse_datetime(start) > parse_datetime(value):
            raise ValueError("startTime must be earlier than or equal to endTime")
        return value


class IntegrationAudioQueryRequest(BaseModel):
    """面向上层系统的语音查询请求模型。"""

    unique_id: str | None = None
    icao_code: str | None = Field(default=None, min_length=4, max_length=4)
    band: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    page: int = 1
    page_size: int = 20

    @field_validator("icao_code")
    @classmethod
    def upper_icao(cls, value: str | None) -> str | None:
        return value.upper() if value else value

    @field_validator("page", "page_size")
    @classmethod
    def positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("must be a positive integer")
        return value

    @model_validator(mode="after")
    def validate_range(self) -> "IntegrationAudioQueryRequest":
        if self.start_time and self.end_time and parse_datetime(self.start_time) > parse_datetime(self.end_time):
            raise ValueError("start_time must be earlier than or equal to end_time")
        return self


class IntegrationRealtimeTaskQueryRequest(BaseModel):
    """面向上层系统的实时任务查询请求模型。"""

    icao_code: str | None = Field(default=None, min_length=4, max_length=4)
    band: str | None = None
    status: int | None = None
    page: int = 1
    page_size: int = 20

    @field_validator("icao_code")
    @classmethod
    def upper_icao(cls, value: str | None) -> str | None:
        return value.upper() if value else value

    @field_validator("page", "page_size")
    @classmethod
    def positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("must be a positive integer")
        return value


class IntegrationDownloadTaskQueryRequest(BaseModel):
    """面向上层系统的下载任务查询请求模型。"""

    icao_code: str | None = Field(default=None, min_length=4, max_length=4)
    band: str | None = None
    status: int | None = None
    page: int = 1
    page_size: int = 20

    @field_validator("icao_code")
    @classmethod
    def upper_icao(cls, value: str | None) -> str | None:
        return value.upper() if value else value

    @field_validator("page", "page_size")
    @classmethod
    def positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("must be a positive integer")
        return value


class IntegrationRealtimeTaskUpsertRequest(BaseModel):
    """面向上层系统的实时任务新增或更新模型。"""

    task_id: int | None = None
    task_name: str
    server_addr: str | None = None
    server_port: int | None = None
    protocol: str = "TCP"
    timeout: int = 30
    heart_beat: int = 10
    icao_code: str = Field(min_length=4, max_length=4)
    band: str
    status: int = 0
    source_url: str | None = None
    segment_seconds: int = 60
    stream_format: str | None = None

    @field_validator("icao_code")
    @classmethod
    def upper_icao(cls, value: str) -> str:
        return value.upper()

    @field_validator("segment_seconds")
    @classmethod
    def positive_segment_seconds(cls, value: int) -> int:
        if value < 1:
            raise ValueError("segment_seconds must be a positive integer")
        return value

    @model_validator(mode="after")
    def validate_source(self) -> "IntegrationRealtimeTaskUpsertRequest":
        """集成侧实时任务同样必须提供可连接的数据源。"""

        has_socket_target = bool(self.server_addr) and self.server_port is not None
        if not has_socket_target and not self.source_url:
            raise ValueError("either source_url or server_addr/server_port must be provided")
        return self


class IntegrationDownloadTaskUpsertRequest(BaseModel):
    """面向上层系统的下载任务新增或更新模型。"""

    task_id: int | None = None
    task_name: str
    icao_code: str = Field(min_length=4, max_length=4)
    band: str
    start_time: str
    end_time: str
    speed_limit: int = 0
    exec_type: int = 1
    exec_time: str | None = None
    status: int = 0
    priority: Literal["high", "medium", "low"] = "medium"

    @field_validator("icao_code")
    @classmethod
    def upper_icao(cls, value: str) -> str:
        return value.upper()

    @field_validator("end_time")
    @classmethod
    def validate_time_range(cls, value: str, info) -> str:
        start = info.data.get("start_time")
        if start and parse_datetime(start) > parse_datetime(value):
            raise ValueError("start_time must be earlier than or equal to end_time")
        return value


class A2SystemConfigUpdateRequest(BaseModel):
    """更新系统基础配置时使用的请求模型。"""

    storage_root: str
    slice_rule: str
    max_download_task: int
    max_realtime_conn: int
    api_timeout: int
    sync_interval: int


class ApiResponse(BaseModel):
    """统一的接口响应包装结构。

    项目所有接口都尽量返回统一外壳，方便前端和集成系统处理：
    - `code` 表示状态码语义
    - `msg` 表示文本信息
    - `data` 表示实际业务数据
    - `count` 表示记录数或结果数
    """

    code: int = 200
    msg: str = "success"
    data: object | list[object] | dict[str, Any] | None = None
    count: int = 0
