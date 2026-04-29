# A-2 语音模块代码答辩版讲解

## 1. 这个项目整体是干什么的

这套代码是一个 **ATC 语音处理后端模块**，核心目标是把实时语音流和历史语音文件统一接入系统，保存到本地和数据库里，然后对外提供 **按时间范围查询、下载、裁剪、拼接导出** 的能力。

你可以直接这样讲：

> A-2 模块主要负责空管语音数据的接入、存储、管理和查询。  
> 它既支持实时流接入，也支持历史音频导入或下载。  
> 系统把语音按分段存储，同时记录时间、机场、频段、文件路径等元数据。  
> 当用户按时间范围查询时，系统会找到所有时间重叠的片段，并在需要时裁剪拼接成完整音频返回。

## 2. 项目整体架构怎么讲

这套代码是比较典型的 **分层架构**：

- **API 层**：负责接收 HTTP 请求、返回结果，代码在 [app/api.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/api.py:99)。
- **Schema 层**：负责定义请求和响应的数据格式，并做参数校验，代码在 [app/schemas.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/schemas.py:11)。
- **Service 层**：负责核心业务逻辑，比如实时接收、历史下载、查询、切片、同步，代码在 `app/services/` 下。
- **Repository 层**：负责直接操作 SQLite 数据库，代码在 [app/repositories.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/repositories.py:16)。
- **DB 层**：负责建表和数据库初始化，代码在 [app/db.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/db.py:9)。
- **Core 层**：放公共配置和时间工具，代码在 [app/core/config.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/core/config.py:8) 和 [app/core/time_utils.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/core/time_utils.py:6)。

一句话总结就是：

> API 收请求，Schema 验参数，Service 处理业务，Repository 落数据库，Storage 落文件。

## 3. 每个核心文件分别是干什么的

- [app/main.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/main.py:1)  
  只是启动入口，本质上就是把 FastAPI 应用对象导出来。

- [app/api.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/api.py:117)  
  这是整个系统的接口总入口，里面定义了实时任务、下载任务、语音查询、语音导出、文件导入、元数据同步、集成接口等全部 API。

- [app/schemas.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/schemas.py:28)  
  用 Pydantic 定义数据模型，比如创建实时任务、创建下载任务、语音查询条件、切片请求等，并且会自动校验时间范围是否合法、ICAO 是否大写、分页参数是否为正数。

- [app/db.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/db.py:9)  
  负责建表。这里定义了几个关键表：  
  `a2_voice_info` 存语音元数据，`adsb_tracks` 存航迹，`a2_voice_track_rel` 存语音和航迹关系，`a2_task_realtime_cfg` 存实时任务配置，`a2_task_download_cfg` 存下载任务配置，`a2_sys_base_cfg` 存系统配置。

- [app/repositories.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/repositories.py:43)  
  负责写 SQL。查询语音、按时间找重叠片段、插入语音记录、更新任务状态、更新下载进度、维护系统配置，都是这一层在做。

- [app/services/storage_service.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/services/storage_service.py:11)  
  负责把音频真正写到磁盘上，并生成文件大小、校验和、文件路径等信息。它的目录规则是：  
  `storage/ICAO/频段/日期/文件名`

- [app/services/task_service.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/services/task_service.py:40)  
  这是任务业务核心。  
  `RealtimeTaskService` 负责实时任务创建和实时片段入库。  
  `DownloadTaskService` 负责历史音频下载、LiveATC 历史文件解析、限速下载、断点续传、30 分钟裁剪、最终入库。

- [app/services/runtime_service.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/services/runtime_service.py:18)  
  这是实时运行时核心。  
  `AsxStreamResolver` 负责解析 `.asx` 文件，拿到真实流地址。  
  `RealtimeConnectionManager` 负责开后台线程监控连接、接收实时流、按固定秒数切片、自动重连、记录运行状态。

- [app/services/query_service.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/services/query_service.py:7)  
  负责按时间范围查询语音，并给结果补充 `trackIds` 和 `downloadUrl`。也就是说，它不只是查数据，还会把结果包装成更适合前端和上层系统使用的格式。

- [app/services/audio_service.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/services/audio_service.py:22)  
  负责“裁剪 + 拼接”。  
  如果全是 WAV，就直接用 Python `wave` 库拼。  
  如果是 MP3 或混合格式，就调用 `ffmpeg` 处理。

- [app/services/sync_service.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/services/sync_service.py:12)  
  负责元数据同步和修复。它会扫描数据库记录对应的物理文件，看文件是否丢失、大小是否变化、校验和是否不一致，然后把 `valid_status`、`file_size`、`checksum` 修正回来。

## 4. 这套系统的核心流程怎么讲

### 实时流流程

1. 先创建实时任务，配置机场、频段、流地址、切片时长。  
2. 如果给的是 `.asx` 文件，系统会先解析出真实播放流地址。  
3. 后台线程开始接收实时音频流。  
4. 系统每隔 `segment_seconds` 秒把当前音频切成一个片段。  
5. 片段写入本地文件，同时把元数据写入 `a2_voice_info` 表。  
6. 这些实时片段的 `data_type` 会被标记成 `S`。

对应代码主要在 [app/services/runtime_service.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/services/runtime_service.py:122) 和 [app/services/task_service.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/services/task_service.py:103)。

### 历史音频流程

1. 先创建下载任务。  
2. 再通过 URL 下载历史音频，支持 `.part` 临时文件和 `Range` 简化断点续传。  
3. 如果文件名符合 LiveATC 归档命名规则，系统会从文件名中解析出 ICAO、频段、起始时间。  
4. 如果历史文件超过 30 分钟，会截断到前 30 分钟。  
5. 下载完成后写入本地，并把元数据入库。  
6. 这些历史片段的 `data_type` 会被标记成 `H`。

对应代码主要在 [app/services/task_service.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/services/task_service.py:162) 和 [app/services/task_service.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/services/task_service.py:231)。

### 查询和导出流程

1. 用户传入 `startTime`、`endTime`、`icaoCode`、`band`。  
2. 系统不是按“文件名完全匹配”查，而是按“时间重叠”查。  
3. 语音片段命中条件是：`start_at < 查询结束时间` 且 `end_at > 查询开始时间`。  
4. 如果只是查询，返回元数据列表。  
5. 如果是导出或切片，就把所有命中的片段裁剪并按顺序拼接后返回完整音频。

对应代码主要在 [app/repositories.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/repositories.py:52)、[app/services/query_service.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/services/query_service.py:11) 和 [app/services/audio_service.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/services/audio_service.py:23)。

## 5. 这套代码最值得答辩时强调的亮点

- **亮点 1：分段存储、按需重组。**  
  系统不是把所有语音当一整段大文件处理，而是先分段保存，查询时再按时间拼接，这样更灵活。

- **亮点 2：实时流和历史文件统一建模。**  
  虽然来源不同，但最后都落到同一张 `a2_voice_info` 表里，所以查询、导出、下载接口可以复用。

- **亮点 3：查询条件用了“时间重叠”而不是“时间相等”。**  
  这是为了支持任意时间窗口查询，不要求用户的查询时间刚好和分段边界一致。

- **亮点 4：做了基础可靠性处理。**  
  包括自动重连、简化断点续传、临时文件、元数据同步修复、文件缺失检测。

## 6. 这套代码目前的边界和不足怎么讲

你主动讲这些，老师一般会觉得你边界清楚：

- 这套系统的时间戳不一定等于上游原始协议时间。实时流时间主要取的是系统接收和切片时刻，[app/services/runtime_service.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/services/runtime_service.py:242)。
- LiveATC 历史文件的时间信息很多是从文件名和音频时长推断出来的，[app/services/task_service.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/services/task_service.py:294)。
- MP3 裁剪、混合格式拼接、长文件截断依赖 `ffmpeg/ffprobe`，不是纯 Python 完成的。
- 现在已经有基础的重连和断点续传，但还不是完整生产级调度系统。

## 7. 测试部分怎么讲

可以直接说：

> 这个项目不只是写了功能，还补了单元测试和接口测试。  
> 测试覆盖了历史导入、实时接收、按时间查询、跨片段导出、LiveATC 文件解析、30 分钟截断、元数据修复和集成接口。

对应文件是：

- [tests/test_a2_module.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/tests/test_a2_module.py:86)
- [tests/test_api.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/tests/test_api.py:173)

## 8. 你可以直接背的 1 分钟总结

> A-2 模块是一个空管语音处理后端，主要负责实时语音流和历史音频文件的接入、存储、管理和查询。  
> 系统采用分层设计，API 层接收请求，Service 层处理业务，Repository 层负责数据库操作。  
> 在存储策略上，系统把语音按片段保存到本地，并把 ICAO、频段、时间范围、文件路径等元数据写入数据库。  
> 查询时采用时间重叠匹配，因此支持任意时间范围检索。  
> 如果用户需要完整音频，系统会把多个片段裁剪并按顺序拼接后返回。  
> 另外系统还实现了 ASX 解析、实时流接收、历史文件下载、元数据同步修复，以及基础的自动重连和断点续传能力。  

## 9. 老师可能会问的常见问题

### 1. 为什么要分段存储，而不是直接存整段音频？

因为系统的核心需求不是“保存一个完整文件”，而是“支持任意时间范围查询”。分段存储更利于后续按时间窗口裁剪和拼接，也更方便管理。

### 2. 为什么查询条件要用时间重叠，而不是时间完全相等？

因为用户查的时间范围通常不会刚好和分段边界一致，所以必须找出所有和目标区间有重叠的片段，这样才能保证结果完整。

### 3. 实时语音和历史语音是怎么统一管理的？

虽然来源不同，但最终都会写到同一张 `a2_voice_info` 表中，只是通过 `data_type` 区分实时 `S` 和历史 `H`。

### 4. `.asx` 文件在这里的作用是什么？

`.asx` 文件本身通常不是音频，而是一个播放列表或索引文件。系统先解析 `.asx`，再从里面拿到真实的音频流地址。

### 5. 音频裁剪和拼接是怎么做的？

如果都是 WAV，就用 Python 自带的 `wave` 库直接裁剪拼接；如果是 MP3 或混合格式，就调用 `ffmpeg`。

### 6. 为什么历史 LiveATC 文件最多只保留前 30 分钟？

因为代码里做了一个课程项目层面的约束，超长文件会被自动截断到前 30 分钟，避免文件过大、处理链路过重。

### 7. 如果数据库里有记录，但磁盘上的文件没了怎么办？

系统的元数据同步服务会扫描文件是否存在，如果发现丢失，就把这条记录标记成 `missing`，下载接口也会返回明确的 `404`。

### 8. 这个项目有哪些可靠性设计？

有基础的自动重连、心跳监测、断点续传、临时文件处理、元数据同步修复，但整体还属于课程项目级，不是完整生产级实现。

### 9. 这套系统最大的技术点是什么？

最大的技术点是把“分段存储”和“任意时间范围重组”这条链路打通了，也就是既能高效保存，又能灵活查询和导出。

### 10. 这套代码后续还可以怎么优化？

可以继续补完整调度器、更强的异常恢复、更丰富的告警、孤儿文件自动补录、更强的协议适配能力，以及更完整的测试覆盖。

## 10. 你答辩时的建议讲法

如果老师让你顺着代码讲，可以按这个顺序：

1. 先讲 [app/api.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/api.py:117)，说明系统提供了哪些接口。  
2. 再讲 [app/schemas.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/schemas.py:28)，说明参数校验怎么做。  
3. 接着讲 [app/services/task_service.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/services/task_service.py:40) 和 [app/services/runtime_service.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/services/runtime_service.py:55)，说明实时和历史两条主链路。  
4. 然后讲 [app/services/query_service.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/services/query_service.py:7) 和 [app/services/audio_service.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/services/audio_service.py:22)，说明查询和导出的核心实现。  
5. 最后讲 [app/services/sync_service.py](/c:/Users/sxdlqh/Desktop/-ATC-A2-/app/services/sync_service.py:12) 和测试文件，说明可靠性和验证情况。

## 11. 一句话收尾

> 这个项目的核心不是单纯“存语音”，而是把语音数据做成了可以按时间范围检索、裁剪、拼接和导出的结构化服务。
