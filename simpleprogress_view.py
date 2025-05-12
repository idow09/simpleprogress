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
import select
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["live_view"]


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
    if seconds < 0:
        return (
            ""  # Handle cases where duration might be slightly negative due to timing
        )
    ms = int((seconds % 1) * 1000)
    secs = int(seconds)
    h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
    if h:
        return f"{h:d}:{m:02d}:{s:02d}.{ms:03d}"
    return f"{m:02d}:{s:02d}.{ms:03d}"


def _get_header(task_width: int) -> str:
    return (
        f"{'Task':{task_width}} {'Progress':25} {'Iter':>10} {'Elapsed':>12} {'Avg/iter':>12} {'Status'}\n"
        + "-" * (task_width + 25 + 10 + 12 + 12 + 7 + 5)  # Adjust total width
    )


def _render_tree(
    nodes: List[_TaskNode], indent: str = "", show_tree: bool = True
) -> Tuple[List[str], int]:
    # List to store tuples of (name_str, bar, cnt_str, dur_str, avg_dur_str, status_char_val)
    # for all nodes in the current call's scope, in the correct order.
    collected_data: List[Tuple[str, str, str, str, str, str]] = []

    # Inner helper function to recursively gather data and the maximum name length.
    def gather_data_recursive(
        current_nodes: List[_TaskNode], current_indent: str, current_show_tree: bool
    ) -> int:  # Returns max_name_len for this level and below
        level_max_name_len = 0
        for i, n in enumerate(current_nodes):
            is_last = i == len(current_nodes) - 1
            prefix = ""
            if current_show_tree:
                if current_indent:  # Not a root node
                    prefix = "└─ " if is_last else "├─ "

            name_str = f"{current_indent}{prefix}{n.name}"
            level_max_name_len = max(level_max_name_len, len(name_str))

            pct = None
            if n.total is not None and n.total > 0:
                pct = n.count / n.total
            elif n.total is None and n.count > 0:
                # Show count even if total is unknown, but no percentage bar
                pass

            bar = ""
            if pct is not None:
                filled = int(pct * 20)
                bar = "[" + "#" * filled + "." * (20 - filled) + "]"
            elif n.count > 0:
                # Indicate activity even without a total
                bar = f"{n.count:>20} it"
            else:
                bar = " " * 21  # Keep alignment if no progress info

            status_char_val = "…" if n.status == "running" else n.status
            dur_val = n.duration() or 0.0
            avg_dur_val = dur_val / n.count if n.count > 0 else -1.0
            cnt_val = f"{n.count}" + (f"/{n.total}" if n.total is not None else "")
            dur_str_val = _human_td(dur_val)
            avg_dur_str_val = _human_td(avg_dur_val) if avg_dur_val >= 0 else ""

            collected_data.append(
                (name_str, bar, cnt_val, dur_str_val, avg_dur_str_val, status_char_val)
            )

            if n.children:
                child_indent_str = (
                    current_indent + ("    " if is_last else "│   ")
                    if current_show_tree
                    else current_indent + "  "
                )
                child_max_len = gather_data_recursive(
                    n.children, child_indent_str, current_show_tree
                )
                level_max_name_len = max(level_max_name_len, child_max_len)
        return level_max_name_len

    # --- Body of _render_tree ---
    # Pass 1: Gather all data and find the true max name length for this call's scope.
    # The `indent` for the top-level call to `gather_data_recursive` is the `indent` passed to `_render_tree`.
    overall_max_name_len = gather_data_recursive(nodes, indent, show_tree)

    # Determine task_width based on the true max name length.
    # This width will be used for formatting all names and for the header.
    task_width_to_use = max(overall_max_name_len, 50)  # Ensure minimum width

    # Pass 2: Format all collected data using the determined task_width.
    formatted_lines_list: List[str] = []
    for name, bar, cnt, dur_str, avg_dur_str, status_char_item in collected_data:
        formatted_lines_list.append(
            f"{name:<{task_width_to_use}} {bar:<25} {cnt:>10} {dur_str:>12} {avg_dur_str:>12} {status_char_item}".rstrip()
        )

    return formatted_lines_list, task_width_to_use


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
        How often to poll the file (seconds). Press Ctrl‑C or *q* then Enter
        to quit.
    show_tree
        Whether to show tree-like indentation for child tasks.
    """
    path = Path(progress_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(path)

    tasks: Dict[str, _TaskNode] = {}
    roots: List[_TaskNode] = []

    try:
        with path.open("r") as fp:
            # seek to start
            fp.seek(0, os.SEEK_SET)
            last_file_size = 0
            last_mod_time = 0.0

            while True:
                current_mod_time = path.stat().st_mtime
                current_file_size = path.stat().st_size

                # Read only if file changed
                if (
                    current_mod_time > last_mod_time
                    or current_file_size > last_file_size
                ):
                    fp.seek(last_file_size)
                    while line := fp.readline():
                        evt = _parse_event(line)
                        if evt:
                            _update_tree(evt, tasks, roots)
                    last_file_size = fp.tell()
                    last_mod_time = current_mod_time
                # render
                tree_lines, task_width = _render_tree(roots, show_tree=show_tree)
                header = _get_header(task_width)
                sys.stdout.write("\033[2J\033[H")  # clear + home
                sys.stdout.write(header + "\n" + "\n".join(tree_lines) + "\n")
                sys.stdout.flush()

                # exit if all top‑level tasks finished
                if roots and all(r.status in {"done", "error"} for r in roots):
                    break

                # Wait or check for input
                t0 = time.time()
                while time.time() - t0 < refresh:
                    if sys.stdin in select.select([sys.stdin], [], [], 0.05)[0]:
                        ch = sys.stdin.readline().strip().lower()
                        if ch in {"q", "quit", "exit"}:
                            return
                    time.sleep(0.05)
    except FileNotFoundError:
        print(f"Error: Progress file not found at {path}", file=sys.stderr)
        return
    except KeyboardInterrupt:
        print("\nExiting live view.")
        return
    except Exception as e:
        print(f"\nAn error occurred: {e}", file=sys.stderr)
        # Optionally render one last time before exiting on error
        tree_lines, task_width = _render_tree(roots, show_tree=show_tree)
        header = _get_header(task_width)
        sys.stdout.write("\033[2J\033[H")  # clear + home
        sys.stdout.write(header + "\n" + "\n".join(tree_lines) + "\n")
        sys.stdout.flush()
        return
    finally:
        # Attempt a final render to show the completed state
        try:
            if tasks:
                tree_lines, task_width = _render_tree(roots, show_tree=show_tree)
                header = _get_header(task_width)
                # Don't clear screen on final print, just print below last view
                sys.stdout.write("\nFinal State:\n")
                sys.stdout.write(header + "\n" + "\n".join(tree_lines) + "\n")
                sys.stdout.flush()
        except Exception as final_render_e:
            # Ignore errors during final render attempt
            pass


# if __name__ == "__main__":
#     if len(sys.argv) != 2:
#         print("Usage: python simpleprogress_view.py <path_to_progress.jsonl>")
#         sys.exit(1)

#     path = sys.argv[1]

#     if not path:
#         print("Error: path is required", file=sys.stderr)
#         sys.exit(1)

#     # Check if the path seems valid before starting
#     if not Path(path).expanduser().is_file():
#         print(f"Error: File not found or is not a file: {path}", file=sys.stderr)
#         sys.exit(1)

#     live_view(path)


if __name__ == "__main__":
    # live_view("logs/run_20250512_150401.progress.jsonl")
    live_view("../rag-pipeline-eval/logs/run_20250507_110029.progress.jsonl")
