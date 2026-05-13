from __future__ import annotations

import time as tm
import threading
from datetime import datetime, UTC
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin

import requests
from pydub import AudioSegment
from seleniumbase import BaseCase, SB

from app.core.config import settings
from app.services.download_utils import retry, wait
from app.services.exception import (
    ATCAbortError,
    ATCDownloadError,
    ATCStopStreamError,
    ATCTimeoutError,
)


@wait("fails to bypass cloudflare")
def _check_bypass_cloudflare(sb: BaseCase) -> bool:
    sb.solve_captcha()
    return "ATC" in sb.get_title()


@wait("fails to load '#archiveDate'")
def _check_archive_date_present(sb: BaseCase) -> bool:
    return sb.is_element_present("#archiveDate")


@wait("fails to load 'select[name=\"time\"]'")
def _check_time_selectable(sb: BaseCase) -> bool:
    return sb.is_element_present('select[name="time"]')


@wait("fails to load '#archiveSubmit'")
def _check_archive_submit_present(sb: BaseCase) -> bool:
    return sb.is_element_present("#archiveSubmit")


@wait("fails to load '#archiveResults'")
def _check_archive_results_present(sb: BaseCase) -> bool:
    return sb.is_element_present("#archiveResults")


@wait("fails to load 'source'")
def _check_source_present(sb: BaseCase) -> bool:
    return sb.is_element_present("source")


@wait("fails to load '#container'")
def _check_container_present(sb: BaseCase) -> bool:
    return sb.is_element_present("#container")


def normalize_audio(file_path: str) -> None:
    ext = file_path.rsplit(".", 1)[-1].lower()
    if ext == "wav":
        _normalize_wav(file_path)
    else:
        _normalize_via_pydub(file_path)


def _normalize_via_pydub(file_path: str) -> None:
    audio = AudioSegment.from_file(file_path)
    audio = audio.apply_gain(settings.audio_loudness - audio.dBFS)
    audio = audio.set_frame_rate(settings.audio_sample_rate)
    audio = audio.set_sample_width(settings.audio_bit_depth // 8)
    audio.export(file_path, format=file_path.rsplit(".", 1)[-1])


def _normalize_wav(file_path: str) -> None:
    import math
    import struct
    import wave

    with wave.open(file_path, "rb") as wf:
        params = wf.getparams()
        nchannels, sampwidth, framerate, nframes = params[:4]
        raw = wf.readframes(nframes)

    fmt = {1: "b", 2: "<h", 4: "<i"}[sampwidth]
    max_val = float(2 ** (sampwidth * 8 - 1))

    samples = [
        struct.unpack_from(fmt, raw, i * sampwidth)[0]
        for i in range(nframes * nchannels)
    ]

    if not samples:
        return

    squared = [(s / max_val) ** 2 for s in samples]
    rms = (sum(squared) / len(squared)) ** 0.5
    current_dbfs = 20.0 * math.log10(max(rms, 1e-10))
    gain_db = settings.audio_loudness - current_dbfs
    gain_linear = 10.0 ** (gain_db / 20.0)

    if sampwidth != settings.audio_bit_depth // 8:
        sampwidth = settings.audio_bit_depth // 8
        fmt = {1: "b", 2: "<h", 4: "<i"}[sampwidth]
        max_val = float(2 ** (sampwidth * 8 - 1))

    adjusted = [int(max(-max_val, min(max_val - 1, s * gain_linear))) for s in samples]
    new_raw = b"".join(struct.pack(fmt, s) for s in adjusted)

    with wave.open(file_path, "wb") as wf:
        wf.setnchannels(nchannels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(framerate)
        wf.writeframes(new_raw)


class _BrowserLock:
    """模块级浏览器锁，防止多次并发打开 SeleniumBase 浏览器。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ref = 0

    def acquire(self) -> None:
        self._lock.acquire()
        self._ref += 1

    def release(self) -> None:
        self._ref = max(0, self._ref - 1)
        if hasattr(self._lock, "_is_owned") and self._lock._is_owned():
            self._lock.release()

    @property
    def in_use(self) -> bool:
        return self._ref > 0


_browser_lock = _BrowserLock()


def cleanup_temp_files() -> None:
    """清理残留的临时下载文件。"""
    import shutil

    dirs_to_scan = [
        settings.temp_root / "downloads",
        Path.cwd() / "downloaded_files",
    ]
    for d in dirs_to_scan:
        if not d.exists():
            continue
        for entry in d.iterdir():
            try:
                if entry.is_file():
                    entry.unlink(missing_ok=True)
                elif entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                pass


def shutdown_browser() -> None:
    """服务关闭时不再需要显式释放浏览器 — `with SB()` 会自动清理。"""


class ArchiveDownloader:
    def __init__(
        self,
        url: str,
        date: str,
        time_slot: str,
        file_dir: str | Path,
        download_timeout: int | None = None,
    ) -> None:
        self.url = url
        self.date = date
        self.time_slot = time_slot
        self.file_dir = Path(file_dir)
        self.audio_file_name: str = ""
        self.download_timeout = download_timeout or settings.download_timeout
        self.stop_event = threading.Event()
        self._dl_dir = Path.cwd() / "downloaded_files"

    @retry("fails to download archive audio file due to excessive max retry")
    def run(self) -> Path:
        _browser_lock.acquire()
        try:
            with SB(uc=True) as sb:
                sb.activate_cdp_mode(self.url)
                _check_bypass_cloudflare(sb)
                _check_archive_date_present(sb)
                sb.execute_script(
                    'document.querySelector("#archiveDate").value="{}";'.format(self.date)
                )
                _check_time_selectable(sb)
                sb.select_option_by_text('[name="time"]', self.time_slot)
                _check_archive_submit_present(sb)
                sb.click("#archiveSubmit")
                _check_archive_results_present(sb)
                sb.click('#archiveResults tr[bgcolor="lightgray"] a font[color="blue"]')
                _check_source_present(sb)

                audio_url = sb.get_attribute("source", "src")
                self.audio_file_name = Path(audio_url.split("?")[0]).name or f"{self.date}.mp3"

                self._dl_dir.mkdir(parents=True, exist_ok=True)
                cr_path = self._dl_dir / f"{self.audio_file_name}.crdownload"
                if cr_path.exists():
                    cr_path.unlink()
                mp3_download_path = self._dl_dir / self.audio_file_name
                mp3_file_path = self.file_dir / self.audio_file_name
                self.file_dir.mkdir(parents=True, exist_ok=True)

                if mp3_file_path.exists():
                    return mp3_file_path
                if mp3_download_path.exists():
                    mp3_download_path.rename(str(mp3_file_path))
                    return mp3_file_path

                sb.execute_script(
                    f"const source = document.querySelector('source');"
                    f"const audioUrl = source.src;"
                    f"const a = document.createElement('a');"
                    f"a.href = audioUrl;"
                    f"a.download = '{self.audio_file_name}';"
                    f"document.body.appendChild(a);"
                    f"a.click();"
                    f"document.body.removeChild(a);"
                )

                start_time = tm.time()
                while not self.stop_event.is_set():
                    progress = self._verify_download_progress()
                    if progress == "HALT":
                        if cr_path.exists():
                            cr_path.unlink()
                        raise ATCDownloadError("fails to download archive audio due to irresistible reason")
                    elif progress == "FINISH":
                        normalize_audio(str(mp3_download_path))
                        mp3_download_path.rename(str(mp3_file_path))
                        return mp3_file_path
                    if tm.time() - start_time > self.download_timeout:
                        if cr_path.exists():
                            cr_path.unlink()
                        raise ATCTimeoutError("fails to download archive audio due to taking too long")

                if cr_path.exists():
                    cr_path.unlink()
                if mp3_download_path.exists():
                    mp3_download_path.unlink()
                raise ATCAbortError("aborts archive downloading")
        finally:
            _browser_lock.release()

    def stop(self) -> None:
        self.stop_event.set()

    def _verify_download_progress(self) -> Literal["BEGIN", "PROGRESS", "HALT", "FINISH"]:
        cr_path = self._dl_dir / f"{self.audio_file_name}.crdownload"
        mp3_path = self._dl_dir / self.audio_file_name
        if not cr_path.exists() and not mp3_path.exists():
            return "BEGIN"
        elif mp3_path.exists():
            return "FINISH"
        elif cr_path.exists():
            start_size = cr_path.stat().st_size
            tm.sleep(settings.fresh_time)
            if mp3_path.exists():
                return "FINISH"
            end_size = cr_path.stat().st_size
            if end_size > start_size:
                return "PROGRESS"
            else:
                return "HALT"
        return "PROGRESS"


class StreamDownloader:
    def __init__(
        self,
        url: str,
        file_dir: str | Path,
        chunk_size: int | None = None,
    ) -> None:
        self.url = url
        self.file_dir = Path(file_dir)
        self.chunk_size = chunk_size or settings.download_chunk_size
        self.stop_event = threading.Event()

    def resolve_stream_url(self) -> tuple[str, dict[str, str], dict[str, str]]:
        _browser_lock.acquire()
        try:
            with SB(uc=True) as sb:
                sb.activate_cdp_mode(self.url)
                _check_bypass_cloudflare(sb)
                _check_container_present(sb)
                stream_url = sb.get_attribute("#player2_html5", "src")
                stream_url = urljoin(sb.get_current_url(), stream_url)
                cookies_dict = sb.get_cookies()
                cookies = {c["name"]: c["value"] for c in cookies_dict}
                user_agent = sb.get_user_agent()
                referer = sb.get_current_url()
        finally:
            _browser_lock.release()
        headers = {
            "User-Agent": user_agent,
            "Referer": referer,
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        return stream_url, headers, cookies

    @retry("fails to download stream audio due to excessive max retry")
    def run(self) -> Path:
        stream_url, headers, cookies = self.resolve_stream_url()
        self.file_dir.mkdir(parents=True, exist_ok=True)
        temp_file_name = f"temp-{str(tm.time())}.mp3"
        temp_file_path = self.file_dir / temp_file_name
        response = requests.get(
            stream_url, headers=headers, cookies=cookies, stream=True, timeout=settings.stream_timeout
        )
        response.raise_for_status()
        start_timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
        with open(temp_file_path, "ab") as f:
            for chunk in response.iter_content(chunk_size=self.chunk_size):
                if self.stop_event.is_set():
                    end_timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
                    file_name = f"{start_timestamp}-{end_timestamp}Z.mp3"
                    file_path = self.file_dir / file_name
                    normalize_audio(str(temp_file_path))
                    temp_file_path.rename(file_path)
                    return file_path
                if chunk:
                    f.write(chunk)
                    f.flush()
        file_path = self.file_dir / f"{start_timestamp}-{datetime.now(UTC).strftime('%Y%m%d-%H%M')}Z.mp3"
        normalize_audio(str(temp_file_path))
        temp_file_path.rename(file_path)
        return file_path

    def stop(self) -> None:
        self.stop_event.set()
