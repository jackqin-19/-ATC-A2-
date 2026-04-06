from __future__ import annotations

import socket
import threading
import time

from app.repositories import TaskRepository


class RealtimeConnectionManager:
    def __init__(self, repository: TaskRepository | None = None) -> None:
        self.repository = repository or TaskRepository()
        self._threads: dict[int, threading.Thread] = {}
        self._stops: dict[int, threading.Event] = {}

    def start_monitor(
        self,
        *,
        task_id: int,
        heartbeat_payload: str = "PING\n",
        heartbeat_expect: str | None = None,
    ) -> None:
        if task_id in self._threads and self._threads[task_id].is_alive():
            return
        stop_event = threading.Event()
        self._stops[task_id] = stop_event
        thread = threading.Thread(
            target=self._run_monitor,
            kwargs={
                "task_id": task_id,
                "stop_event": stop_event,
                "heartbeat_payload": heartbeat_payload,
                "heartbeat_expect": heartbeat_expect,
            },
            daemon=True,
            name=f"a2-realtime-{task_id}",
        )
        self._threads[task_id] = thread
        thread.start()

    def stop_monitor(self, task_id: int) -> None:
        event = self._stops.get(task_id)
        if event:
            event.set()
        thread = self._threads.get(task_id)
        if thread:
            thread.join(timeout=2)
        self.repository.update_realtime_status(task_id, 0)

    def get_state(self, task_id: int) -> dict[str, object]:
        thread = self._threads.get(task_id)
        return {"taskId": task_id, "running": bool(thread and thread.is_alive())}

    def _run_monitor(
        self,
        *,
        task_id: int,
        stop_event: threading.Event,
        heartbeat_payload: str,
        heartbeat_expect: str | None,
    ) -> None:
        backoff = [10, 30, 60]
        task = self.repository.get_realtime_task(task_id)
        if not task:
            return
        missed_heartbeats = 0
        backoff_index = 0
        while not stop_event.is_set():
            try:
                self.repository.update_realtime_status(task_id, 1)
                with socket.create_connection((task["server_addr"], task["server_port"]), timeout=task["timeout"]) as conn:
                    conn.settimeout(task["heart_beat"])
                    while not stop_event.wait(task["heart_beat"]):
                        conn.sendall(heartbeat_payload.encode("utf-8"))
                        if heartbeat_expect is not None:
                            data = conn.recv(1024).decode("utf-8", errors="ignore")
                            if heartbeat_expect not in data:
                                missed_heartbeats += 1
                            else:
                                missed_heartbeats = 0
                        else:
                            missed_heartbeats = 0
                        if missed_heartbeats >= 3:
                            raise ConnectionError("heartbeat response missing 3 times")
                if stop_event.is_set():
                    break
            except OSError:
                self.repository.update_realtime_status(task_id, 2)
                delay = backoff[min(backoff_index, len(backoff) - 1)]
                backoff_index = min(backoff_index + 1, len(backoff) - 1)
                if stop_event.wait(delay):
                    break
                continue
            backoff_index = 0
        self.repository.update_realtime_status(task_id, 0)
