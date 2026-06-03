from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"
BACKEND_FILE = BASE_DIR / "web_app.py"


def _require_command(name: str) -> str:
    resolved = shutil.which(name)
    if not resolved:
        raise RuntimeError(f"Required command not found in PATH: {name}")
    return resolved


def main() -> int:
    if not os.getenv("TMDB_API_KEY", "").strip():
        print("TMDB_API_KEY is not set.", file=sys.stderr)
        return 1

    npm = _require_command("npm")
    env = os.environ.copy()
    env.setdefault("FLASK_SECRET_KEY", "dev-secret-change-me")
    env.setdefault("NEXT_PUBLIC_API_BASE_URL", "http://localhost:5001")
    env.setdefault("BACKEND_ORIGIN", "http://localhost:5001")

    processes: list[subprocess.Popen] = []

    try:
        backend = subprocess.Popen(
            [sys.executable, str(BACKEND_FILE)],
            cwd=str(BASE_DIR),
            env=env,
        )
        processes.append(backend)

        time.sleep(1.0)

        frontend = subprocess.Popen(
            [npm, "run", "dev"],
            cwd=str(FRONTEND_DIR),
            env=env,
        )
        processes.append(frontend)

        print("Backend: http://localhost:5001")
        print("Frontend: http://localhost:3000")
        print("Press Ctrl+C to stop both.")

        while True:
            for process in processes:
                return_code = process.poll()
                if return_code is not None:
                    return return_code
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping services...")
        return 0
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
        time.sleep(0.5)
        for process in reversed(processes):
            if process.poll() is None:
                process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
