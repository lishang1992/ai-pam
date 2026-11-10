"""进程内事件总线：把 Agent 的每一步实时推送给 Web 前端。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Set


class EventBus:
    def __init__(self) -> None:
        self._subscribers: Set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def publish(self, channel: str, payload: Dict[str, Any]) -> None:
        message = {"channel": channel, "payload": payload}
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                pass

    def publish_session(self, session_id: int, event: Dict[str, Any]) -> None:
        self.publish("session", {"session_id": session_id, **event})

    def subscriber_count(self) -> int:
        return len(self._subscribers)


bus = EventBus()

