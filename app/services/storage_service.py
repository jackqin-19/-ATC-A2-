"""本地文件存储服务。

这个服务专门负责把音频内容真正写到磁盘，并生成对应的元数据对象。
它不关心这些音频来自实时流还是历史下载，只负责“怎么存”和“存完返回什么信息”。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.core.config import settings
from app.core.time_utils import utcnow_text
from app.schemas import VoiceRecord


class StorageService:
    def build_storage_path(self, icao_code: str, band: str, start_at: str, file_name: str) -> Path:
        """根据机场、频段和日期生成标准落盘路径。"""

        date_dir = start_at[:10]
        return settings.data_root / icao_code.upper() / band / date_dir / file_name

    def write_audio_bytes(
        self,
        *,
        unique_id: str,
        icao_code: str,
        band: str,
        start_at: str,
        end_at: str,
        original_time: str,
        process_time: str | None,
        data_type: str,
        extension: str,
        content: bytes,
    ) -> VoiceRecord:
        """把音频字节写入磁盘，并生成对应的元数据记录。

        这里会同时完成三件事：
        1. 生成标准文件名。
        2. 把内容写到规范目录。
        3. 计算文件大小和 SHA-256 校验值，便于后续同步校验。
        """

        file_name = f"{unique_id}_{data_type}.{extension.lstrip('.')}"
        path = self.build_storage_path(icao_code, band, start_at, file_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        checksum = hashlib.sha256(content).hexdigest()
        return VoiceRecord(
            unique_id=unique_id,
            icao_code=icao_code.upper(),
            band=band,
            original_time=original_time,
            process_time=process_time or utcnow_text(),
            file_path=str(path.resolve()),
            file_name=file_name,
            file_size=path.stat().st_size,
            data_type=data_type,
            start_at=start_at,
            end_at=end_at,
            checksum=checksum,
        )
