# ATC A-2 Voice Module API

Base URL: `http://{host}:{port}`

## 通用响应格式

所有 JSON 接口统一返回以下结构：

```json
{
  "code": 200,
  "msg": "success",
  "data": {},
  "count": 0
}
```

`data` 在模型字段描述时省略此外壳，仅给出 `data` 内部的结构。

---

## 1. 系统

### GET /health

健康检查。

**响应** `{ "data": { "status": "ok" }, "count": 1 }`

---

## 2. 实时接收任务

### POST /api/a2/tasks/realtime

创建实时任务。

**请求体 (JSON)**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| taskName | string | 是 | 任务名称 |
| icaoCode | string | 是 | ICAO 机场码，4 位，自动转大写 |
| band | string | 是 | 频段，如 `tower`、`app-dep-dir-zone` |
| sourceUrl | string | 否 | 流地址（与 serverAddr/serverPort 二选一） |
| serverAddr | string | 否 | Socket 目标地址 |
| serverPort | int | 否 | Socket 目标端口 |
| protocol | string | 否 | 协议，默认 `TCP` |
| timeout | int | 否 | 超时秒数，默认 30 |
| heartBeat | int | 否 | 心跳间隔秒数，默认 10 |
| segmentSeconds | int | 否 | 切片时长秒数，默认 60 |
| streamFormat | string | 否 | 流格式后缀，如 `mp3` |

**响应** `{ "data": { "taskId": 1 }, "count": 1 }`

---

### POST /api/a2/tasks/realtime/from-asx

上传 ASX 文件自动创建实时任务。系统解析 ASX 播放列表，抽取真实流地址。

**请求体 (multipart/form-data)**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| taskName | string | 是 | 任务名称 |
| icaoCode | string | 是 | ICAO 机场码，4 位 |
| band | string | 是 | 频段 |
| segmentSeconds | int | 否 | 切片时长秒数，默认 60 |
| preferredRef | int | 否 | 选择第几条流引用，默认 0 |
| file | file | 是 | ASX 文件 |

**响应**
```json
{
  "data": {
    "taskId": 1,
    "streamUrl": "http://...",
    "refs": ["http://...", "http://..."]
  },
  "count": 1
}
```

---

### GET /api/a2/tasks/realtime

列出全部实时任务。

**响应** `{ "data": [ {task...}, ... ], "count": N }`

---

### POST /api/a2/tasks/realtime/start-monitor

启动心跳监控（检查连接存活，断线自动退避重连）。

**请求体 (JSON)**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| taskId | int | 是 | 任务 ID |
| heartbeatPayload | string | 否 | 心跳内容，默认 `"PING\n"` |
| heartbeatExpect | string | 否 | 期待对端返回的内容片段 |

**响应** `{ "data": { state... }, "count": 1 }` — 返回任务当前运行状态（见下方 state 结构）

---

### POST /api/a2/tasks/realtime/{task_id}/stop-monitor

停止心跳监控。

**响应** `{ "data": { state... }, "count": 1 }`

---

### POST /api/a2/tasks/realtime/start-receive

启动音频流接收，开始拉取并落盘。

**请求体 (JSON)**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| taskId | int | 是 | 任务 ID |

**响应** `{ "data": { state... }, "count": 1 }`

---

### POST /api/a2/tasks/realtime/{task_id}/stop-receive

停止音频流接收。

**响应** `{ "data": { state... }, "count": 1 }`

---

### GET /api/a2/tasks/realtime/{task_id}/state

查看实时任务运行状态。

**响应 (state 结构)**
```json
{
  "data": {
    "taskId": 1,
    "running": true,
    "monitoring": true,
    "receiving": true,
    "segmentsSaved": 12,
    "lastSegmentAt": "2026-05-13 10:05:00.123",
    "lastError": null,
    "streamUrl": "http://..."
  },
  "count": 1
}
```

---

### GET /api/a2/tasks/realtime/test-connection

测试 Socket 连通性。

| 参数 (query) | 类型 | 必填 | 说明 |
|---|---|---|---|
| host | string | 是 | 主机地址 |
| port | int | 是 | 端口 |
| timeout | int | 否 | 超时秒数，默认 5 |

---

## 3. 历史下载任务

### POST /api/a2/tasks/download

创建下载任务。

**请求体 (JSON)**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| taskName | string | 是 | 任务名称 |
| icaoCode | string | 是 | ICAO 机场码，4 位 |
| band | string | 是 | 频段 |
| startTime | string | 是 | 开始时间 `YYYY-MM-DD HH:MM:SS` |
| endTime | string | 是 | 结束时间 |
| speedLimit | int | 否 | 限速，默认 0 |
| execType | int | 否 | 执行类型，默认 1 |
| execTime | string | 否 | 计划执行时间 |
| priority | string | 否 | 优先级 `high/medium/low`，默认 `medium` |

**响应** `{ "data": { "taskId": 1 }, "count": 1 }`

---

### GET /api/a2/tasks/download

列出全部下载任务。

**响应** `{ "data": [ {task...}, ... ], "count": N }`

---

### POST /api/a2/tasks/download/execute

执行普通 HTTP 下载（支持断点续传、限速）。

**请求体 (JSON)**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| taskId | int | 是 | 任务 ID |
| sourceUrl | string | 是 | 文件下载 URL |
| icaoCode | string | 否 | ICAO 机场码 |
| band | string | 否 | 频段 |
| startTime | string | 否 | 开始时间 |
| endTime | string | 否 | 结束时间 |
| originalTime | string | 否 | 原始时间 |
| speedLimitKbps | int | 否 | 限速 KB/s，默认 0 |

**响应** `{ "data": { voiceRecord... }, "count": 1 }`

---

### POST /api/a2/tasks/download/liveatc/execute

通过浏览器自动化从 LiveATC 归档页面下载。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| sourceUrl | string | 是 | LiveATC 归档页 URL，如 `https://www.liveatc.net/archive.php?m=vhhh5` |
| date | string | 是 | 日期 `YYYYMMDD`，如 `20260507` |
| time | string | 是 | 时段 `HHMM-HHMMZ`，如 `0000-0030Z` |
| icaoCode | string | 否 | ICAO 机场码（手动指定元数据） |
| band | string | 否 | 频段（手动指定元数据） |
| speedLimitKbps | int | 否 | 限速，默认 0 |

**响应**
```json
{
  "data": {
    "taskId": 1,
    "record": { "voiceRecord..." },
    "metadata": {
      "icao_code": "VHHH",
      "band": "app-dep-dir-zone",
      "original_time": "2026-05-07 00:00:00",
      "start_at": "2026-05-07 00:00:00",
      "end_at": "2026-05-07 00:30:00",
      "file_name": "VHHH5-App-Dep-Dir-Zone-May-07-2026-0000Z.mp3"
    }
  },
  "count": 1
}
```

---

## 4. 语音查询与导出

### VoiceRecord 结构

```json
{
  "unique_id": "VHHH_20260507000000123_1_a1b2c3",
  "icao_code": "VHHH",
  "band": "tower",
  "original_time": "2026-05-07 00:00:00",
  "process_time": "2026-05-07 00:30:01.123",
  "file_path": "/data/VHHH/tower/2026-05-07/...",
  "file_name": "VHHH_...",
  "file_size": 5748524,
  "data_type": "S",
  "start_at": "2026-05-07 00:00:00",
  "end_at": "2026-05-07 00:00:10",
  "checksum": "sha256...",
  "valid_status": "valid",
  "downloadUrl": "/api/a2/voice/file/VHHH_..."
}
```

- `data_type`: `S` = 实时流切片, `H` = 历史下载
- `downloadUrl`: 仅查询接口返回，指向原始文件下载地址

---

### POST /api/a2/voice/query

按时间范围查询语音元数据。

**请求体 (JSON)**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| startTime | string | 是 | `YYYY-MM-DD HH:MM:SS` |
| endTime | string | 是 | 必须 >= startTime |
| icaoCode | string | 否 | ICAO 机场码过滤 |
| band | string | 否 | 频段过滤 |
| pageNum | int | 否 | 页码，默认 1 |
| pageSize | int | 否 | 每页条数，默认 10 |

**查询逻辑**: `start_at < endTime AND end_at > startTime`（时间重叠匹配）

**响应** `{ "data": [ voiceRecords... ], "count": totalMatching }`

---

### GET /api/a2/voice/query

同 POST，参数改为 query string。

| 参数 | 类型 | 必填 |
|---|---|---|
| startTime | string | 是 |
| endTime | string | 是 |
| icaoCode | string | 否 |
| band | string | 否 |
| pageNum | int | 否 |
| pageSize | int | 否 |

---

### GET /api/a2/voice/export

导出指定时间范围的完整音频（自动裁剪+拼接）。

| 参数 (query) | 类型 | 必填 | 说明 |
|---|---|---|---|
| startTime | string | 是 | 开始时间 |
| endTime | string | 是 | 结束时间 |
| icaoCode | string | 是 | ICAO 机场码 |
| band | string | 是 | 频段 |
| outputFormat | string | 否 | `wav` 或 `mp3`，默认 `wav` |

**响应**: 二进制音频流 (`audio/wav` 或 `audio/mpeg`)，文件名为 `{ICAO}_{band}_{startTime}_{endTime}.{format}`

---

### POST /api/a2/voice/slice

按时间范围裁剪导出语音。功能同 export，通过 POST 调用。

**请求体 (JSON)**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| startTime | string | 是 | |
| endTime | string | 是 | |
| icaoCode | string | 是 | |
| band | string | 是 | |
| outputFormat | string | 否 | `wav`/`mp3`，默认 `wav` |

**响应**: 同 `export`

---

### GET /api/a2/voice/file/{unique_id}

下载单个语音文件的原始数据。

**参数 (path)**: `unique_id` — 语音记录的唯一标识

**响应**: 二进制音频流

---

## 5. 语音导入

### POST /api/a2/voice/import/realtime

手动导入实时语音片段。

**请求 (multipart/form-data)**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| file | file | 是 | 音频文件 |
| icaoCode | string(Query) | 是 | ICAO 机场码 |
| band | string(Query) | 是 | 频段 |
| originalTime | string(Query) | 是 | 原始时间 |
| startAt | string(Query) | 是 | 片段开始时间 |
| endAt | string(Query) | 是 | 片段结束时间 |

---

### POST /api/a2/voice/import/history

手动导入历史语音并关联到指定下载任务。

**请求 (multipart/form-data)**

| 字段 | 类型 | 必填 |
|---|---|---|
| file | file | 是 |
| taskId | int(Query) | 是 |
| icaoCode | string(Query) | 是 |
| band | string(Query) | 是 |
| startAt | string(Query) | 是 |
| endAt | string(Query) | 是 |
| originalTime | string(Query) | 否 |

---

### POST /api/a2/voice/import/history/liveatc

导入 LiveATC 命名的归档文件，自动解析元数据。

**请求 (multipart/form-data)**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| file | file | 是 | 文件名需符合 LiveATC 格式，如 `VHHH5-App-Dep-Dir-Zone-Apr-09-2026-0630Z.mp3` |
| taskId | int(Query) | 否 | 关联任务 ID |

文件名格式: `{ICAO}{数字}-{Band1}-{Band2}-...-{MMM}-{DD}-{YYYY}-{HHMM}Z.mp3`

---

## 6. 元数据同步

### POST /api/a2/sync/run

手动触发元数据同步。扫描全部语音记录，检查磁盘文件存在性与 SHA-256 一致性。

**响应**
```json
{
  "data": { "missing": 0, "updated": 0, "scanned": 42 },
  "count": 1
}
```

---

## 7. 集成接口 (/api/v1/integration)

### GET /api/v1/integration/audio

按条件查询语音元数据。

| 参数 (query) | 类型 | 必填 | 说明 |
|---|---|---|---|
| unique_id | string | 否 | 精确匹配 |
| icao_code | string | 否 | |
| band | string | 否 | |
| start_time | string | 否 | 开始时间过滤 |
| end_time | string | 否 | 结束时间过滤 |
| page | int | 否 | 页码，默认 1 |
| page_size | int | 否 | 每页条数，默认 20 |

---

### GET /api/v1/integration/a2/realtime-tasks

分页查询实时任务。

| 参数 (query) | 类型 | 必填 |
|---|---|---|
| icao_code | string | 否 |
| band | string | 否 |
| status | int | 否 |
| page | int | 否 |
| page_size | int | 否 |

---

### POST /api/v1/integration/a2/realtime-tasks

新增或更新实时任务（按 task_id 判断存在则更新）。

| 字段 (JSON) | 类型 | 必填 |
|---|---|---|
| taskId | int | 否 |
| taskName | string | 是 |
| icaoCode | string | 是 |
| band | string | 是 |
| serverAddr | string | 否 |
| serverPort | int | 否 |
| protocol | string | 否 |
| timeout | int | 否 |
| heartBeat | int | 否 |
| status | int | 否 |
| sourceUrl | string | 否 |
| segmentSeconds | int | 否 |
| streamFormat | string | 否 |

---

### GET /api/v1/integration/a2/download-tasks

分页查询下载任务。

| 参数 (query) | 类型 | 必填 |
|---|---|---|
| icao_code | string | 否 |
| band | string | 否 |
| status | int | 否 |
| page | int | 否 |
| page_size | int | 否 |

---

### POST /api/v1/integration/a2/download-tasks

新增或更新下载任务。

| 字段 (JSON) | 类型 | 必填 |
|---|---|---|
| taskId | int | 否 |
| taskName | string | 是 |
| icaoCode | string | 是 |
| band | string | 是 |
| startTime | string | 是 |
| endTime | string | 是 |
| speedLimit | int | 否 |
| execType | int | 否 |
| execTime | string | 否 |
| status | int | 否 |
| priority | string | 否 |

---

### GET /api/v1/integration/a2/system-config

读取系统基础配置。

**响应** `{ "data": { config... }, "count": 1 }`

---

### PUT /api/v1/integration/a2/system-config

更新系统基础配置。

**请求体 (JSON)**

| 字段 | 类型 | 必填 |
|---|---|---|
| storageRoot | string | 是 |
| sliceRule | string | 是 |
| maxDownloadTask | int | 是 |
| maxRealtimeConn | int | 是 |
| apiTimeout | int | 是 |
| syncInterval | int | 是 |
