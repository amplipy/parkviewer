"""Standalone entry point for the packaged ParkViewer desktop app.

Streamlit only serves a browser page and (importantly) must run in the MAIN
thread because it installs signal handlers. So the packaged app works as:

  Parent (ParkViewer):
      1. spawns itself as a child process with PARKVIEWER_CHILD=1,
      2. waits for the Streamlit health endpoint,
      3. opens a pywebview native window,
      4. kills the child when the window is closed.

  Child (same executable, PARKVIEWER_CHILD=1):
      runs the Streamlit server in its main thread and serves until killed.

Run from source the classic way:  streamlit run park_tiff_viewer.py
Set PARKVIEWER_SERVE_ONLY=1 to just serve (used for smoke tests / headless CI).
"""

import logging
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

for _k, _v in {
    "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
    "STREAMLIT_SERVER_HEADLESS": "true",
    "STREAMLIT_GLOBAL_DEVELOPMENT_MODE": "false",
    "STREAMLIT_SERVER_ADDRESS": "127.0.0.1",
}.items():
    os.environ.setdefault(_k, _v)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("parkviewer")


def _resource_dir() -> Path:
    """Where park_tiff_viewer.py lives, whether run from source or frozen."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _server_ready(port: int, timeout: float = 60.0) -> bool:
    url = f"http://127.0.0.1:{port}/_stcore/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def _run_streamlit_child(script: str) -> int:
    """Child process: serve Streamlit in the main thread (blocks forever)."""
    from streamlit.web import cli as stcli

    sys.argv = [
        "streamlit",
        "run",
        script,
        "--server.port",
        os.environ["STREAMLIT_SERVER_PORT"],
        "--server.address",
        "127.0.0.1",
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]
    sys.exit(stcli.main())


def _open_window(res: Path) -> None:
    """Open the native window; fall back to the system browser on failure."""
    opened = False
    try:
        import webview

        icon = res / "packaging" / "assets" / "icon_64.png"
        webview.create_window(
            "Park AFM TIFF Viewer",
            f"http://127.0.0.1:{os.environ['STREAMLIT_SERVER_PORT']}",
            width=1400,
            height=900,
            min_size=(900, 600),
        )
        kwargs = {"icon": str(icon)} if icon.exists() else {}
        webview.start(**kwargs)
        opened = True
    except Exception:
        log.exception("native window failed; falling back to system browser")
        import webbrowser

        webbrowser.open(f"http://127.0.0.1:{os.environ['STREAMLIT_SERVER_PORT']}")
    if not opened:
        # Keep serving until interrupted so the browser tab keeps working.
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


def main() -> int:
    res = _resource_dir()
    script = res / "park_tiff_viewer.py"
    if not script.exists():
        log.error("park_tiff_viewer.py not found next to the executable (%s)", res)
        return 1

    # ---- child mode: be the streamlit server (main thread!) ----
    if os.environ.get("PARKVIEWER_CHILD") == "1":
        _run_streamlit_child(str(script))

    # ---- serve-only mode: parent-less smoke test (CI, headless) ----
    serve_only = os.environ.get("PARKVIEWER_SERVE_ONLY") == "1"

    os.environ["STREAMLIT_SERVER_PORT"] = str(_free_port())

    if serve_only:
        print(f"PARKVIEWER_URL=http://127.0.0.1:{os.environ['STREAMLIT_SERVER_PORT']}", flush=True)
        _run_streamlit_child(str(script))

    # ---- parent mode ----
    env = os.environ.copy()
    env["PARKVIEWER_CHILD"] = "1"
    log.info("starting Streamlit server child on port %s", env["STREAMLIT_SERVER_PORT"])
    if getattr(sys, "frozen", False):
        child_cmd = [sys.executable]  # same frozen exe, child mode via env flag
    else:
        child_cmd = [sys.executable, str(Path(__file__).resolve())]
    child = subprocess.Popen(child_cmd, env=env)
    try:
        if not _server_ready(int(env["STREAMLIT_SERVER_PORT"])):
            log.error("Streamlit server failed to start on port %s", env["STREAMLIT_SERVER_PORT"])
            return 1
        _open_window(res)
    finally:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())