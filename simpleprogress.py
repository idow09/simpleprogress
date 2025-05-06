"""simpleprogress.py
A dropin, zerodependency progresslogging helper.

Usage example
-------------
>>> from simpleprogress import Progress
>>> prg = Progress.open("run.progress.jsonl")
>>> with prg.task("experiments", total=60) as exp:
...     for i in range(60):
...         with exp.child(f"experiment {i}") as run:
...             run.update()

During execution, each progress event is appended as a JSON line to
"run.progress.jsonl".  Tail that file for a live view or load it into
pandas afterwards.

Design highlights
-----------------
* **JSONL sidecar**  every event is a single JSON object with ISO
  timestamp.  Easy to stream, appendonly, and postprocess.
* **Threadsafe, asyncfriendly**  all user calls are nonblocking;
  a background thread serialises writes through ``queue.Queue``.
* **Nested tasks**  each task knows its ``parent_id``; the file stores
  the whole tree.
* **Zero external deps**  only Python3.8 stdlib.
* **Portable**  works in Docker, over SSH, in CI; if env var
  ``PROGRESS_DISABLED=1`` is set, everything degrades to noops.
"""

from __future__ import annotations

import atexit
import datetime as _dt
import json
import os
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

__all__ = ["Progress"]

ISO_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _utcnow() -> str:
    """Return current UTC time formatted for ISO8601 (always Zsuffixed)."""
    return _dt.datetime.now(_dt.UTC).strftime(ISO_FMT)


class _Writer(threading.Thread):
    """Background thread that writes events to disk one JSON line at a time."""

    def __init__(self, path: Path, q: "queue.Queue[Dict[str, Any]]") -> None:
        super().__init__(name="simpleprogress-writer", daemon=True)
        self._path = path
        self._q = q
        self._fp = open(self._path, "a", buffering=1)  # linebuffered
        self._running = True

    def run(self) -> None:  # noqa: D401
        while self._running or not self._q.empty():
            try:
                item = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            self._fp.write(json.dumps(item, separators=(",", ":")) + "\n")
            self._q.task_done()

    def stop(self) -> None:
        self._running = False
        self.join(timeout=1.0)
        self._fp.close()


class _Backend:
    """Singletonish backend shared by all Progress instances in a process."""

    _instance: Optional["_Backend"] = None

    def __new__(cls, path: Path) -> "_Backend":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init(path)
        elif cls._instance.path != path:
            raise RuntimeError(
                "simpleprogress already initialised for a different path: "
                f"{cls._instance.path} (wanted {path})"
            )
        return cls._instance

    # noinspection PyAttributeOutsideInit
    def _init(self, path: Path) -> None:  # noqa: D401
        self.path: Path = path
        self.queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self.writer = _Writer(path, self.queue)
        self.writer.start()
        atexit.register(self.writer.stop)

    def emit(self, record: Dict[str, Any]) -> None:  # noqa: D401
        """Queue a record for disk. Nonblocking."""
        self.queue.put(record, block=False)


class Progress:
    """Entrypoint object  similar spirit to ``logging.Logger``."""

    def __init__(
        self,
        backend: _Backend,
        parent_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> None:
        self._backend = backend
        self._parent_id = parent_id
        self._task_id = task_id  # only real Task objects have this set

    # ------------------------------------------------------------------
    # public API (classmethods)
    # ------------------------------------------------------------------
    @classmethod
    def open(cls, file_path: os.PathLike | str) -> "Progress":
        """Initialise progress logging to *file_path* and return a root handle.

        If ``PROGRESS_DISABLED`` env var is truthy, returns a dummy object
        whose methods are noops.
        """
        if os.getenv("PROGRESS_DISABLED"):
            return _NullProgress()
        path = Path(file_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        backend = _Backend(path)
        return cls(backend)

    # ------------------------------------------------------------------
    # task helpers  root or nested
    # ------------------------------------------------------------------
    def task(self, name: str, total: Optional[int] = None) -> "_Task":
        """Create a new *named* task (contextmanager)."""
        return _Task(self._backend, name, total, self._task_id)


class _Task(Progress):
    """A concrete progress task  supports nested *child* tasks."""

    def __init__(
        self,
        backend: _Backend,
        name: str,
        total: Optional[int],
        parent_id: Optional[str],
    ) -> None:
        super().__init__(backend, parent_id)
        self._name = name
        self._total = total
        self._task_id = uuid.uuid4().hex[:8]  # unique across process, shorter hash
        self._count = 0
        self._start_ts = time.time()
        self._emit("start", total=total, name=name)
        self._lock = threading.Lock()

    # ----------------------- context management --------------------
    def __enter__(self) -> "_Task":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:  # noqa: D401
        self.close(exception=exc)
        return False  # never suppress exceptions

    async def __aenter__(self) -> "_Task":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:  # noqa: D401
        self.close(exception=exc)
        return False

    # ----------------------------- API -----------------------------
    def update(self, n: int = 1) -> None:
        """Increment progress by *n* (default 1). Nonblocking."""
        with self._lock:
            self._count += n
            now = time.time()
            self._emit("update", n=self._count, dt=now - self._start_ts)

    # alias for tqdmcompatibility
    def advance(self, n: int = 1) -> None:
        self.update(n)

    def child(self, name: str, total: Optional[int] = None) -> "_Task":
        """Spawn a nested subtask."""
        return _Task(self._backend, name, total, self._task_id)

    def close(self, *, exception: Optional[BaseException] = None) -> None:
        """Mark task finished (success or error)."""
        with self._lock:
            if self._count is None:  # already closed
                return
            status = "error" if exception else "done"
            elapsed = time.time() - self._start_ts
            self._emit(
                status,
                n=self._count,
                dt=elapsed,
                error=str(exception) if exception else None,
            )
            # idempotency
            self._count = None

    # ------------------------- internals ---------------------------
    def _emit(self, event: str, **extra: Any) -> None:  # noqa: D401
        payload: Dict[str, Any] = {
            "ts": _utcnow(),
            "event": event,
            "id": self._task_id,
        }
        if self._parent_id is not None:
            payload["parent_id"] = self._parent_id
        payload.update(extra)
        self._backend.emit(payload)


class _NullProgress(Progress):
    """A donothing dropin for when progress tracking is disabled."""

    def __init__(self) -> None:  # noqa: D401
        pass

    def task(self, *args: Any, **kwargs: Any):  # noqa: D401
        return self  # type: ignore[return-value]

    # context manager stubs
    def __enter__(self):  # noqa: D401
        return self

    def __exit__(self, *exc):  # noqa: D401
        return False

    async def __aenter__(self):  # noqa: D401
        return self

    async def __aexit__(self, *exc):  # noqa: D401
        return False

    # noops
    def update(self, *_, **__):  # noqa: D401
        pass

    advance = update

    def child(self, *_, **__):  # noqa: D401
        return self  # type: ignore[return-value]

    def close(self, *_, **__):  # noqa: D401
        pass
