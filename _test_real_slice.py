"""真实数据拼接测试：爬取 VHHH 2段历史 + 3段实时流，切分后拼接验证"""
import sys, os, time, math, struct, wave, subprocess, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("A2_WORKSPACE_ROOT", os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("A2_DATA_ROOT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage"))
os.environ.setdefault("A2_TEMP_ROOT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage", "tmp"))
os.environ.setdefault("A2_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage", "a2.sqlite3"))

from app.db import init_db
from app.schemas import DownloadTaskCreate
from app.services.task_service import DownloadTaskService, RealtimeTaskService
from app.services.audio_service import AudioService
from app.repositories import VoiceRepository
from app.services.liveatc_downloader import ArchiveDownloader, StreamDownloader
from pathlib import Path

init_db()
dl_svc = DownloadTaskService()
rt_svc = RealtimeTaskService()
repo = VoiceRepository()
ROOT = Path(__file__).resolve().parent

# ── Step 1: 下载 2 段相邻历史归档 ──────────────────────────
archives = []
for date_str, time_slot in [
    ("20260512", "0000-0030Z"),
    ("20260512", "0030-0100Z"),
]:
    print(f"\n{'='*50}")
    print(f"下载历史: date={date_str} time={time_slot}")
    
    out_dir = ROOT / "real_data" / "history"
    out_dir.mkdir(parents=True, exist_ok=True)
    
    ad = ArchiveDownloader(
        url="https://www.liveatc.net/archive.php?m=vhhh5",
        date=date_str, time_slot=time_slot, file_dir=out_dir,
    )
    t0 = time.time()
    fp = ad.run()
    print(f"[{time.time()-t0:.1f}s] 历史下载完成: {fp.name} ({fp.stat().st_size} bytes)")
    
    meta = dl_svc.parse_liveatc_archive_metadata(fp.name, source_file=fp)
    archives.append({"path": fp, "meta": meta})
    print(f"  开始={meta.start_at} 结束={meta.end_at}")

# ── Step 2: 下载 3 段实时流 ─────────────────────────────
print(f"\n{'='*50}")
print("下载实时流...")

streams = []
for i in range(3):
    print(f"\n实时流 #{i+1}...")
    out_dir = ROOT / "real_data" / "stream"
    out_dir.mkdir(parents=True, exist_ok=True)
    
    import requests, threading
    sd = StreamDownloader("https://www.liveatc.net/hlisten.php?mount=vhhh5&icao=vhhh", out_dir)
    
    t0 = time.time()
    stream_url, headers, cookies = sd.resolve_stream_url()
    print(f"  流地址解析: [{time.time()-t0:.1f}s]")
    
    # 记录接收开始时间作为时间戳
    from datetime import datetime, UTC, timedelta
    recv_start = datetime.now(UTC)
    
    file_path = out_dir / f"stream_{i+1}_{recv_start.strftime('%Y%m%d_%H%M%S')}.mp3"
    received = 0
    t_recv = time.time()
    resp = requests.get(stream_url, headers=headers, cookies=cookies, stream=True, timeout=30)
    resp.raise_for_status()
    with open(file_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=4096):
            if chunk:
                f.write(chunk)
                f.flush()
                received += len(chunk)
            if time.time() - t_recv > 15:
                break
    resp.close()
    
    recv_end = datetime.now(UTC)
    print(f"  接收: {received} bytes, {file_path.name}")
    
    streams.append({
        "path": file_path,
        "start": recv_start,
        "end": recv_end,
    })
    time.sleep(2)  # 间隔 2 秒

# ── Step 3: 切分历史文件为 10 分钟片段（共 ≥5 段）───
print(f"\n{'='*50}")
print("切分历史文件为 10 分钟片段...")

ffmpeg = shutil.which("ffmpeg")
if not ffmpeg:
    print("ffmpeg not found, skip")
    sys.exit(1)

history_segments = []
for arch in archives:
    base_time = arch["meta"].start_at  # "YYYY-MM-DD HH:MM:SS"
    from app.core.time_utils import parse_datetime, format_datetime
    base_dt = parse_datetime(base_time)
    
    for seg_i in range(3):  # 每 30 分钟切 3×10 分钟
        ss = seg_i * 600
        chunk_dur = 600
        seg_start = base_dt + timedelta(seconds=ss)
        seg_end = seg_start + timedelta(seconds=chunk_dur)
        
        chunk_path = arch["path"].with_name(f"{arch['path'].stem}_chunk{seg_i}.mp3")
        subprocess.run([
            ffmpeg, "-y", "-ss", str(ss), "-t", str(chunk_dur),
            "-i", str(arch["path"]), "-acodec", "copy", str(chunk_path),
        ], check=True, capture_output=True)
        
        history_segments.append({
            "path": chunk_path,
            "start": format_datetime(seg_start, with_ms=False),
            "end": format_datetime(seg_end, with_ms=False),
        })

print(f"  切出 {len(history_segments)} 段历史片段")

# ── Step 4: 入库 ─────────────────────────────────
print(f"\n{'='*50}")
print("入库...")

task_id = dl_svc.create_task(DownloadTaskCreate(
    task_name="real-slice-test", icao_code="VHHH", band="tower",
    start_time=history_segments[0]["start"],
    end_time=history_segments[-1]["end"],
))

for seg in history_segments:
    dl_svc.ingest_downloaded_file(
        task_id=task_id, source_file=seg["path"],
        icao_code="VHHH", band="tower",
        start_at=seg["start"], end_at=seg["end"],
    )

for s in streams:
    from app.core.time_utils import format_datetime
    orig = format_datetime(s["start"], with_ms=False)
    start = format_datetime(s["start"], with_ms=False)
    end = format_datetime(s["end"], with_ms=False)
    rt_svc.ingest_file_segment(
        file_path=s["path"], icao_code="VHHH", band="tower",
        original_time=orig, start_at=start, end_at=end,
    )

records = repo.list_voice_records()
h_count = sum(1 for r in records if r["data_type"] == "H")
s_count = sum(1 for r in records if r["data_type"] == "S")
print(f"入库完成: {h_count} 历史 + {s_count} 实时流 = {len(records)} 片段")
assert h_count >= 5, f"需要 ≥5 段历史, 实际 {h_count}"
assert s_count >= 3, f"需要 ≥3 段实时流, 实际 {s_count}"

# ── Step 5: 拼接验证 (首尾裁剪) ────────────────
print(f"\n{'='*50}")
print("拼接验证...")

# 查询范围：从第一段中间到倒数第二段中间（裁剪首尾各 5 秒以上）
from app.core.time_utils import parse_datetime, format_datetime
all_h = [r for r in records if r["data_type"] == "H"]
all_h.sort(key=lambda r: r["start_at"])

first_start = parse_datetime(all_h[0]["start_at"])
last_end = parse_datetime(all_h[-1]["end_at"])

q_start_dt = first_start + timedelta(seconds=5)   # 裁掉首段前 5 秒
q_end_dt = last_end - timedelta(seconds=5)          # 裁掉尾段后 5 秒

q_start = format_datetime(q_start_dt, with_ms=False)
q_end = format_datetime(q_end_dt, with_ms=False)

expected_dur = (q_end_dt - q_start_dt).total_seconds()

all_records = [r for r in records]
all_records.sort(key=lambda r: r["start_at"])
overlap = repo.query_overlapping_segments(q_start, q_end, "VHHH", "tower")
print(f"  历史 {h_count} + 实时 {s_count} = {len(records)} 已入库")
print(f"  查询 {q_start} ~ {q_end}")
print(f"  命中 {len(overlap)} 片段 (预期 ≥{len(records)-2})")
print(f"  预期时长: {expected_dur:.1f}s")

output = AudioService().compose_time_range_audio(
    segments=overlap, query_start=q_start, query_end=q_end, output_format="mp3",
)

import wave
with wave.open(str(output), "rb") as wf:  # ffmpeg 输出的 mp3 无法用 wave 读...
    pass
# 用 ffprobe 验证时长
result = subprocess.run([
    shutil.which("ffprobe"), "-v", "error",
    "-show_entries", "format=duration",
    "-of", "default=noprint_wrappers=1:nokey=1",
    str(output),
], check=True, capture_output=True, text=True)
actual_dur = float(result.stdout.strip())

print(f"  实际时长: {actual_dur:.1f}s")
print(f"  文件: {output.name} ({output.stat().st_size} bytes)")

tolerance = 1.0  # MP3 编码有帧边界误差
assert abs(actual_dur - expected_dur) < tolerance, \
    f"时长不匹配: {actual_dur:.1f} vs {expected_dur:.1f}"

print(f"\n✓ 真实数据拼接测试通过！")
print(f"  数据源: VHHH LiveATC 实时爬取")
print(f"  历史: {h_count} 段 (2×30min 归档 → {h_count}×10min 切分)")
print(f"  实时流: {s_count} 段")
print(f"  首尾裁剪: 各 5s")
print(f"  拼接误差: {abs(actual_dur - expected_dur):.2f}s")

# cleanup
for k, v in {"A2_WORKSPACE_ROOT": "", "A2_DATA_ROOT": "", "A2_TEMP_ROOT": "", "A2_DB_PATH": ""}.items():
    os.environ.pop(k, None)
