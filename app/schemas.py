from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

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
    server_addr: str
    server_port: int
    protocol: str = "TCP"
    timeout: int = 30
    heart_beat: int = 10
    icao_code: str = Field(min_length=4, max_length=4)
    band: str

    @field_validator("icao_code")
    @classmethod
    def upper_icao(cls, value: str) -> str:
        return value.upper()


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
    icao_code: str = Field(min_length=4, max_length=4)
    band: str
    start_time: str
    end_time: str
    original_time: str | None = None
    speed_limit_kbps: int = 0

    @field_validator("icao_code")
    @classmethod
    def upper_icao(cls, value: str) -> str:
        return value.upper()


class RealtimeMonitorRequest(BaseModel):
    task_id: int
    heartbeat_payload: str = "PING\n"
    heartbeat_expect: str | None = None


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
    data: object | list[object] | None = None
    count: int = 0
