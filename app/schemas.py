from __future__ import annotations

from typing import Any
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.time_utils import parse_datetime


class VoiceRecord(BaseModel):
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
        has_socket_target = bool(self.server_addr) and self.server_port is not None
        if not has_socket_target and not self.source_url:
            raise ValueError("either source_url or server_addr/server_port must be provided")
        return self


class DownloadTaskCreate(BaseModel):
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
    source_url: str
    speed_limit_kbps: int = 0


class LiveAtcImportedFileRequest(BaseModel):
    task_id: int | None = None


class RealtimeMonitorRequest(BaseModel):
    task_id: int
    heartbeat_payload: str = "PING\n"
    heartbeat_expect: str | None = None


class RealtimeReceiveRequest(BaseModel):
    task_id: int


class RealtimeAsxCreate(BaseModel):
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


class ApiResponse(BaseModel):
    code: int = 200
    msg: str = "success"
    data: object | list[object] | dict[str, Any] | None = None
    count: int = 0
