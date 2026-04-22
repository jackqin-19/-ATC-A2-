# A-2 演示说明

## 启动

```bash
pip install -r requirements.txt
python scripts/init_db.py
python scripts/generate_demo_data.py
uvicorn app.main:app --reload
```

## 推荐演示顺序

### 1. 检查服务是否正常

```bash
curl http://127.0.0.1:8000/health
```

建议说明：

- 服务启动时会自动初始化数据库。
- 元信息同步线程也会在应用生命周期内自动启动。

### 2. 查询指定时间范围内的语音分段

```bash
curl "http://127.0.0.1:8000/api/a2/voice/query?startTime=2026-04-06%2010:00:02&endTime=2026-04-06%2010:00:12&icaoCode=ZBAA&band=tower&pageNum=1&pageSize=10"
```

建议说明：

- A-2 不是按“完整音频文件”直接查询，而是先查时间范围内重叠的分段。
- 查询结果里会返回元信息、下载地址和关联航迹标识。

### 3. 直接按起止时间导出完整音频

```bash
curl "http://127.0.0.1:8000/api/a2/voice/export?startTime=2026-04-06%2010:00:02&endTime=2026-04-06%2010:00:12&icaoCode=ZBAA&band=tower&outputFormat=wav" --output demo_export.wav
```

建议说明：

- 这个接口更适合前端联调和课堂演示，不需要额外拼装 JSON 请求体。
- 服务端会自动完成“查重叠分段 -> 裁剪 -> 拼接 -> 回传文件”。
- 导出完成后的临时切片文件会自动清理。

### 4. 演示旧版切片接口仍然可用

```bash
curl -X POST "http://127.0.0.1:8000/api/a2/voice/slice" ^
  -H "Content-Type: application/json" ^
  -d "{\"startTime\":\"2026-04-06 10:00:02\",\"endTime\":\"2026-04-06 10:00:12\",\"icaoCode\":\"ZBAA\",\"band\":\"tower\",\"outputFormat\":\"wav\"}" ^
  --output demo_slice.wav
```

建议说明：

- `export` 更偏演示与直连下载。
- `slice` 更适合保留原有接口风格，兼容已有调用方。

### 5. 手动触发元信息同步

```bash
curl -X POST "http://127.0.0.1:8000/api/a2/sync/run"
```

建议说明：

- 该接口会修正文件大小、校验值，并标记丢失文件。
- 当数据库记录存在但物理文件缺失时，下载接口会明确返回 `404`。

### 6. 展示实时流接收能力

```bash
curl -X POST "http://127.0.0.1:8000/api/a2/tasks/realtime/start-monitor" ^
  -H "Content-Type: application/json" ^
  -d "{\"task_id\":1,\"heartbeat_payload\":\"PING\\n\",\"heartbeat_expect\":null}"
```

如果已经创建了带 `source_url` 的实时任务，还可以继续展示：

```bash
curl -X POST "http://127.0.0.1:8000/api/a2/tasks/realtime/start-receive" ^
  -H "Content-Type: application/json" ^
  -d "{\"task_id\":1}"
```

建议说明：

- 实时流按接收时间切段保存。
- 状态接口可以观察 `segmentsSaved`、`lastSegmentAt`、`lastError` 等字段。

## 演示时建议强调的点

- A-2 的核心设计是“底层分段存储，上层按任意时间范围重组”。
- 历史音频和实时音频都能统一落到本地存储与元信息库中。
- 接口层已经支持“查元信息”和“直接导出音频”两种使用方式。
- 项目已经做了基础可靠性增强，包括断点续传、自动重连、元信息同步和临时文件清理。

## 配套材料

- 讲解稿和答辩话术见 [defense.md](/workspace/docs/defense.md)
