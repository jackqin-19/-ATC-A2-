# A-2 Demo Guide

## Start

```bash
pip install -r requirements.txt
python scripts/init_db.py
python scripts/generate_demo_data.py
uvicorn app.main:app --reload
```

## Demo Flow

1. Check service health:

```bash
curl http://127.0.0.1:8000/health
```

2. Query the imported voice segments:

```bash
curl "http://127.0.0.1:8000/api/a2/voice/query?startTime=2026-04-06%2010:00:02&endTime=2026-04-06%2010:00:12&icaoCode=ZBAA&band=tower&pageNum=1&pageSize=10"
```

3. Slice a complete audio file across multiple stored segments:

```bash
curl -X POST "http://127.0.0.1:8000/api/a2/voice/slice" ^
  -H "Content-Type: application/json" ^
  -d "{\"startTime\":\"2026-04-06 10:00:02\",\"endTime\":\"2026-04-06 10:00:12\",\"icaoCode\":\"ZBAA\",\"band\":\"tower\",\"outputFormat\":\"wav\"}" ^
  --output demo_slice.wav
```

4. Trigger metadata sync manually:

```bash
curl -X POST "http://127.0.0.1:8000/api/a2/sync/run"
```

5. Example realtime monitor startup:

```bash
curl -X POST "http://127.0.0.1:8000/api/a2/tasks/realtime/start-monitor" ^
  -H "Content-Type: application/json" ^
  -d "{\"task_id\":1,\"heartbeat_payload\":\"PING\\n\",\"heartbeat_expect\":null}"
```

## What To Explain

- A-2 stores source audio by segments in the database and filesystem.
- Querying uses time-range overlap instead of exact segment match.
- Arbitrary time slicing works by locating overlapping segments, clipping each one, then merging them in order.
- This is why A-2 can return any time range even though the original files are segmented.
- The module now also includes resumable history download execution and background metadata consistency checks.
