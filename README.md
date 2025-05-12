# simpleprogress

[![License](https://img.shields.io/github/license/idow09/simpleprogress?label=license&message=MIT)](https://github.com/idow09/simpleprogress/blob/main/LICENSE)
[![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)](https://github.com/idow09/simpleprogress)
[![Code style: Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Python Version](https://img.shields.io/badge/python-3.10+-blue)](https://www.python.org/)

<p align="center">
  <i>✨ A drop-in, zero-dependency progress logging helper for Python ✨</i>
</p>

---

**simpleprogress** is a lightweight Python library for logging progress of potentially long-running tasks, especially those with nested steps. It outputs progress updates as a stream of JSON objects to a `.jsonl` file, making it easy to monitor live or analyze after completion.

It requires only the Python 3.8+ standard library.

## 🚀 Features

*   **JSONL Sidecar File:** Events are logged as timestamped JSON objects, one per line. Easy to stream, parse, and robust against interruptions.
*   **Thread-safe & Async-friendly:** Uses a background thread for non-blocking writes via a queue.
*   **Nested Tasks:** Tasks can have children, creating a hierarchy that's preserved in the logs.
*   **Zero External Dependencies:** Relies only on the Python 3.8+ standard library.
*   **Portable:** Works anywhere Python runs (local, Docker, CI). Logging can be disabled via the `PROGRESS_DISABLED=1` environment variable.
*   **Live Terminal Viewer:** Includes `simpleprogress_view.py` for a live, refreshing terminal dashboard.

## 🔧 Installation

Since `simpleprogress` has no external dependencies, you have a few options:

1.  **Copy the file:** Simply copy `simpleprogress.py` (and optionally `simpleprogress_view.py`) into your project.
2.  **Pip install (if setup.py/pyproject.toml exists):**
    If you plan to package this or add a `setup.py`/`pyproject.toml`, you could install it locally:
    ```bash
    # Assuming you are in the simpleprogress directory
    pip install .
    # Or for development:
    # pip install -e .
    ```

## 💡 Usage

### Logging Progress

Here's the basic pattern:

```python
from simpleprogress import Progress

# Initialize logging to a file
prg = Progress.open("my_run.progress.jsonl")

# Create a main task (optionally with a total count)
with prg.task("Main Process", total=100) as main_task:
    for i in range(100):
        # Do some work...
        time.sleep(0.1)

        # Create a sub-task for detailed steps
        with main_task.child(f"Processing item {i}", total=5) as sub_task:
            for j in range(5):
                # Do sub-step work...
                time.sleep(0.05)
                sub_task.update() # Increment sub-task progress

        main_task.update() # Increment main task progress

print("Run complete. Check my_run.progress.jsonl")
```

### Viewing Progress Live

While your script is running, open another terminal and use the companion viewer:

```bash
python simpleprogress_view.py my_run.progress.jsonl
```

This will show a continuously updating tree view of your tasks, progress bars, timings, and status. Press `Ctrl+C` or `q` then `Enter` to exit the viewer.

(See `example.py` for a more complex scenario involving concurrent tasks.)

## 🤝 Contributing

Contributions are welcome!

1.  Fork the Project
2.  Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3.  Make your changes.
4.  Run `ruff check . --fix && ruff format .` to ensure code style.
5.  Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
6.  Push to the Branch (`git push origin feature/AmazingFeature`)
7.  Open a Pull Request

Consider creating a `CONTRIBUTING.md` file for more detailed guidelines if needed.

## 📜 License

This project is licensed under the MIT License - see the `LICENSE` file for details.

*(A basic MIT `LICENSE` file is recommended)*

## 📫 Contact

Ido Weiss – [@idow09](https://twitter.com/idow09) – idow09@gmail.com

Project Link: [https://github.com/idow/simpleprogress](https://github.com/idow/simpleprogress)
