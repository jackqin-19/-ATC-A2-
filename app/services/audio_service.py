"""音频裁剪与拼接服务。

这个服务是 A-2 模块里“按时间范围导出语音”的核心实现。
它的思路不是直接操作数据库，而是接收已经命中的片段列表，
再计算每个片段应该从哪一秒开始截、截多长时间，最后按顺序拼成完整音频。
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core.config import settings
from app.core.time_utils import parse_datetime


@dataclass
class AudioClipSpec:
    """描述一个原始音频片段在最终导出中的裁剪信息。"""

    path: Path
    clip_start_seconds: float
    clip_duration_seconds: float


class AudioService:
    def compose_time_range_audio(
        self,
        *,
        segments: list[dict],
        query_start: str,
        query_end: str,
        output_format: str,
    ) -> Path:
        """把多个时间重叠片段裁剪后拼成目标音频。

        如果片段全是 WAV，则直接走 Python 标准库拼接；
        如果存在 MP3 或混合格式，则交给 ffmpeg 处理。
        """

        if not segments:
            raise ValueError("No overlapping voice segments found for the requested range")
        specs = self._build_clip_specs(segments, query_start, query_end)
        if output_format == "wav" and all(spec.path.suffix.lower() == ".wav" for spec in specs):
            return self._compose_wav(specs)
        return self._compose_with_ffmpeg(specs, output_format)

    def _build_clip_specs(
        self, segments: list[dict], query_start: str, query_end: str
    ) -> list[AudioClipSpec]:
        """计算每个命中片段真正需要截取的起点和时长。"""

        start_dt = parse_datetime(query_start)
        end_dt = parse_datetime(query_end)
        specs: list[AudioClipSpec] = []
        for segment in segments:
            seg_start = parse_datetime(segment["start_at"])
            seg_end = parse_datetime(segment["end_at"])
            # 只保留“查询窗口”和“原始片段”真正重叠的那部分。
            clip_start = max(start_dt, seg_start)
            clip_end = min(end_dt, seg_end)
            duration = (clip_end - clip_start).total_seconds()
            if duration <= 0:
                continue
            # offset 表示在原始文件里要从第几秒开始截。
            offset = (clip_start - seg_start).total_seconds()
            specs.append(AudioClipSpec(Path(segment["file_path"]), offset, duration))
        return specs

    def _compose_wav(self, specs: list[AudioClipSpec]) -> Path:
        """直接使用 `wave` 库拼接 WAV 文件。

        这种方式依赖所有片段参数一致，但优点是无需外部依赖，速度也更直接。
        """

        output = settings.temp_root / f"slice_{specs[0].path.stem}_{len(specs)}.wav"
        output.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.ExitStack() as stack:
            wave_files = [stack.enter_context(wave.open(str(spec.path), "rb")) for spec in specs]
            params = wave_files[0].getparams()
            with wave.open(str(output), "wb") as writer:
                writer.setparams(params)
                for spec, wav_file in zip(specs, wave_files, strict=True):
                    frame_rate = wav_file.getframerate()
                    # 音频时间要换算成帧位置，才能准确裁剪。
                    start_frame = int(spec.clip_start_seconds * frame_rate)
                    frame_count = int(spec.clip_duration_seconds * frame_rate)
                    wav_file.setpos(min(start_frame, wav_file.getnframes()))
                    writer.writeframes(wav_file.readframes(frame_count))
        return output

    def _compose_with_ffmpeg(self, specs: list[AudioClipSpec], output_format: str) -> Path:
        """调用 ffmpeg 处理 MP3 或混合格式的裁剪与拼接。"""

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError(
                "ffmpeg is required for mp3 slicing/merging or mixed audio formats, but it was not found"
            )

        final_path = settings.temp_root / f"slice_{specs[0].path.stem}_{len(specs)}.{output_format}"
        final_path.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=settings.temp_root) as temp_dir:
            temp_dir_path = Path(temp_dir)
            part_files: list[Path] = []
            concat_file = temp_dir_path / "concat.txt"
            for index, spec in enumerate(specs, start=1):
                part_path = temp_dir_path / f"part_{index}.{output_format}"
                # 先把每个原始片段裁成目标子片段，再统一拼接。
                subprocess.run(
                    [
                        ffmpeg,
                        "-y",
                        "-ss",
                        str(spec.clip_start_seconds),
                        "-t",
                        str(spec.clip_duration_seconds),
                        "-i",
                        str(spec.path),
                        "-acodec",
                        "copy",
                        str(part_path),
                    ],
                    check=True,
                    capture_output=True,
                )
                part_files.append(part_path)

            concat_file.write_text(
                "\n".join(f"file '{part.as_posix()}'" for part in part_files),
                encoding="utf-8",
            )
            # 使用 ffmpeg concat 模式把裁好的子片段按顺序合成一个文件。
            subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_file),
                    "-c",
                    "copy",
                    str(final_path),
                ],
                check=True,
                capture_output=True,
            )
        return final_path
