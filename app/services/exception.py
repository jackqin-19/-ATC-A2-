from __future__ import annotations

__all__ = [
    "ATCError",
    "ATCDownloadError",
    "ATCRetryError",
    "ATCTimeoutError",
    "ATCAbortError",
    "ATCStopStreamError",
    "ATCInvalidQueryError",
    "ATCInsufficientAudioError",
]


class ATCError(Exception):
    def __init__(self, msg: str) -> None:
        self.msg = msg

    def __str__(self) -> str:
        return f"{self.__class__.__name__}: {self.msg}"


class ATCTimeoutError(ATCError): ...


class ATCDownloadError(ATCError): ...


class ATCRetryError(ATCError): ...


class ATCStopStreamError(ATCError): ...


class ATCAbortError(ATCError): ...


class ATCInvalidQueryError(ATCError): ...


class ATCInsufficientAudioError(ATCError): ...
