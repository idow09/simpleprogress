"""simpleprogress_view.py
=================================
Companion viewer utilities for the *simpleprogress* JSONL logger.

Public API
----------
```
from simpleprogress_view import live_view, summary
```

* **`live_view(path, refresh=0.5)`** – watch an active job in the terminal. It
  polls the JSONL file, builds the nested task tree, and re‑paints an ASCII
  overview every *refresh* seconds (Ctrl‑C or `q` to exit).

* **`summary(path)`** – once the run is done, prints a table of total elapsed
  time, iterations, and average time per iteration for each task and its
  children.

Zero external dependencies – only Python ≥ 3.8 std‑lib.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import select

__all__ = ["live_view", "summary"]


# -----------------------------------------------------------------------------
# Internal data model
# -----------------------------------------------------------------------------
class _TaskNode:
    __slots__ = (
        "id",
        "name",
        "parent_id",
        "total",
        "count",
        "start_ts",
        "end_ts",
        "status",
        "children",
    )

    def __init__(self, id_: str, name: str, parent_id: Optional[str]):
        self.id = id_
        self.name = name or id_
        self.parent_id = parent_id
        self.total: Optional[int] = None
        self.count: int = 0
        self.start_ts: Optional[float] = None
        self.end_ts: Optional[float] = None
        self.status: str = "running"
        self.children: List["_TaskNode"] = []

    # helper -------------------------------------------------------------
    def duration(self) -> Optional[float]:
        if self.start_ts is None:
            return None
        if self.end_ts is not None:
            return self.end_ts - self.start_ts
        return time.time() - self.start_ts


# -----------------------------------------------------------------------------
# JSONL parsing utilities
# -----------------------------------------------------------------------------


def _parse_event(line: str) -> Optional[Dict[str, Any]]:
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except Exception:
        return None


def _update_tree(
    evt: Dict[str, Any], tasks: Dict[str, _TaskNode], roots: List[_TaskNode]
):
    tid = evt["id"]
    t = tasks.get(tid)
    if t is None:
        t = _TaskNode(tid, evt.get("name", tid), evt.get("parent_id"))
        tasks[tid] = t
        if t.parent_id is None:
            roots.append(t)
        else:
            parent_id = tasks.get(t.parent_id)
            if parent_id:
                parent_id.children.append(t)

    event = evt["event"]
    ts_iso = evt["ts"].replace("Z", "+00:00")
    ts_float = _dt.datetime.fromisoformat(ts_iso).timestamp()
    if event == "start":
        t.total = evt.get("total")
        t.start_ts = ts_float
    elif event == "update":
        t.count = evt.get("n", t.count)
    elif event in {"done", "error"}:
        t.count = evt.get("n", t.count)
        t.status = event
        t.end_ts = ts_float


# -----------------------------------------------------------------------------
# Rendering helpers
# -----------------------------------------------------------------------------


def _human_td(seconds: float) -> str:
    ms = int((seconds % 1) * 1000)
    secs = int(seconds)
    h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
    if h:
        return f"{h:d}:{m:02d}:{s:02d}.{ms:03d}"
    return f"{m:02d}:{s:02d}.{ms:03d}"


def _get_header(task_width: int) -> str:
    return (
        f"{'Task':{max(task_width, 50)}} {'Progress':25} {'Iter':>10} {'Elapsed':>8} {'Status'}\n"
        + "-" * (max(task_width, 50) + 48)
    )


def _render_tree(
    nodes: List[_TaskNode], indent: str = "", show_tree: bool = True
) -> List[str]:
    # First pass: calculate max task name width
    def get_max_width(nodes: List[_TaskNode], current_max: int = 0) -> int:
        for n in nodes:
            name_len = len(indent + n.name)
            current_max = max(current_max, name_len)
            if n.children:
                current_max = max(current_max, get_max_width(n.children, current_max))
        return current_max

    task_width = max(get_max_width(nodes), 50)  # minimum width of 50

    lines: List[str] = []
    for i, n in enumerate(nodes):
        pct = None
        if n.total is not None and n.total > 0:
            pct = n.count / n.total
        elif n.total is None and n.count:
            pct = None  # unknown size but show counter

        bar = ""
        if pct is not None:
            filled = int(pct * 20)
            bar = "[" + "#" * filled + "." * (20 - filled) + "]"
        status = n.status
        if status == "running":
            status = "…"
        dur = _human_td(n.duration() or 0.0)
        cnt = f"{n.count}" + (f"/{n.total}" if n.total else "")

        # Tree characters
        is_last = i == len(nodes) - 1
        prefix = ""
        if show_tree:
            if indent:  # Not a root node
                prefix = "└─ " if is_last else "├─ "

        name = f"{indent}{prefix}{n.name}"
        line = f"{name:{task_width}} {bar:25} {cnt:>10} {dur:>8} {status}"
        lines.append(line.rstrip())

        if n.children:
            # For children, use vertical line if not last node
            child_indent = (
                indent + ("    " if is_last else "│   ") if show_tree else indent + "  "
            )
            lines.extend(_render_tree(n.children, child_indent, show_tree))
    return lines


# -----------------------------------------------------------------------------
# Public functions
# -----------------------------------------------------------------------------


def live_view(
    progress_path: os.PathLike | str, refresh: float = 0.5, show_tree: bool = True
) -> None:
    """Live terminal view for an active *simpleprogress* run.

    Parameters
    ----------
    progress_path
        Path to the ``*.progress.jsonl`` file being written by the job.
    refresh
        How often to poll the file (seconds).  Press Ctrl‑C or *q* then Enter
        to quit.
    show_tree
        Whether to show tree-like indentation for child tasks.
    """
    path = Path(progress_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(path)

    tasks: Dict[str, _TaskNode] = {}
    roots: List[_TaskNode] = []

    with path.open("r") as fp:
        # seek to start
        fp.seek(0, os.SEEK_SET)
        try:
            while True:
                # read all new events
                while line := fp.readline():
                    evt = _parse_event(line)
                    if evt:
                        _update_tree(evt, tasks, roots)

                # render
                tree_lines = _render_tree(roots, show_tree=show_tree)
                header = _get_header(
                    len(tree_lines[0].split()[0]) if tree_lines else 30
                )
                sys.stdout.write("\033[2J\033[H")  # clear + home
                sys.stdout.write(header + "\n" + "\n".join(tree_lines) + "\n")
                sys.stdout.flush()

                # exit if all top‑level tasks finished
                if roots and all(r.status in {"done", "error"} for r in roots):
                    break

                t0 = time.time()
                while time.time() - t0 < refresh:
                    if sys.stdin in select.select([sys.stdin], [], [], refresh)[0]:
                        ch = sys.stdin.readline().strip().lower()
                        if ch in {"q", "quit", "exit"}:
                            return
                    time.sleep(0.05)
        except KeyboardInterrupt:
            return


def summary(progress_path: os.PathLike | str, show_tree: bool = True) -> None:
    """Print a post‑run summary table.

    Parameters
    ----------
    progress_path
        Path to the progress file.
    show_tree
        Whether to show tree-like indentation for child tasks.

    Columns: *task*, *total iterations*, *elapsed*, *avg/iter*.
    """
    path = Path(progress_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(path)

    tasks: Dict[str, _TaskNode] = {}
    roots: List[_TaskNode] = []

    with path.open("r") as fp:
        for line in fp:
            evt = _parse_event(line)
            if evt:
                _update_tree(evt, tasks, roots)

    # Gather rows
    rows: List[Tuple[str, int, float, float]] = []

    def collect(node: _TaskNode, prefix: str = "", is_last: bool = True):
        if node.duration() is None:
            return
        dur = node.duration() or 0.0
        avg = dur / node.count if node.count else 0.0

        # Tree characters
        tree_prefix = ""
        if show_tree and prefix:  # Not a root node
            tree_prefix = "└─ " if is_last else "├─ "

        rows.append((prefix + tree_prefix + node.name, node.count, dur, avg))

        # Process children
        for i, child in enumerate(node.children):
            is_child_last = i == len(node.children) - 1
            child_prefix = (
                prefix + ("    " if is_last else "│   ") if show_tree else prefix + "  "
            )
            collect(child, child_prefix, is_child_last)

    for i, root in enumerate(roots):
        collect(root, is_last=(i == len(roots) - 1))

    # widths
    col1 = max(len(r[0]) for r in rows) if rows else 4
    hdr = f"{'Task':{col1}}  {'Iter':>8}  {'Elapsed':>8}  {'Avg/iter':>8}\n" + "-" * (
        col1 + 32
    )
    print(hdr)
    for name, cnt, dur, avg in rows:
        if cnt == 0:
            print(f"{name:{col1}}  {'':8}  {_human_td(dur):>8}  {'':8}")
        else:
            print(f"{name:{col1}}  {cnt:8d}  {_human_td(dur):>8}  {_human_td(avg):>8}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python simpleprogress_view.py [live|summary] <path>")
        sys.exit(1)

    command = sys.argv[1]
    path = sys.argv[2] if len(sys.argv) > 2 else None

    if not path:
        print("Error: path is required")
        sys.exit(1)

    if command == "live":
        live_view(path)
    elif command == "summary":
        summary(path)
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
