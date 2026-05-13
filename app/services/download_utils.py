from __future__ import annotations

import time
from functools import wraps
from typing import Callable, TypeVar

from app.core.config import settings
from app.services.exception import ATCRetryError, ATCTimeoutError

F = TypeVar("F", bound=Callable)


def wait(msg: str, timeout: int | None = None, timewait: int | None = None) -> Callable[[F], F]:
    t_limit = timeout if timeout is not None else settings.wait_timeout
    t_wait = timewait if timewait is not None else settings.timewait

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: object, **kwargs: object) -> None:
            start_time = time.time()
            while True:
                if func(*args, **kwargs):
                    return
                if time.time() - start_time > t_limit:
                    raise ATCTimeoutError(msg)
                time.sleep(t_wait)

        return wrapper  # type: ignore[return-value]
    return decorator


def retry(msg: str, max_retry: int | None = None) -> Callable[[F], F]:
    attempts = max_retry if max_retry is not None else settings.max_retry

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: object, **kwargs: object) -> object:
            for attempt in range(attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    if attempt == attempts:
                        raise ATCRetryError(msg) from exc
                    time.sleep(min(2 ** attempt, 60))

        return wrapper  # type: ignore[return-value]
    return decorator
