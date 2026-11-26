"""Запускает заглушки ML (8001), ТОиР (8002) и приложение (PORT, по умолчанию 8000) одной командой.

python -m scripts.run_all
"""
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def main() -> None:
    port = os.environ.get("PORT", "8000")
    host = os.environ.get("HOST", "127.0.0.1")
    procs = [
        subprocess.Popen([PY, "-m", "uvicorn", "mock_ml.app:app", "--port", "8001"], cwd=ROOT / "mock_services"),
        subprocess.Popen([PY, "-m", "uvicorn", "mock_toir.app:app", "--port", "8002"], cwd=ROOT / "mock_services"),
    ]
    time.sleep(1.5)
    procs.append(subprocess.Popen([PY, "-m", "uvicorn", "app.server:app", "--host", host, "--port", port], cwd=ROOT))
    print(f"\nЦифровой механик: http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}\n", flush=True)
    try:
        while all(p.poll() is None for p in procs):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            p.wait(timeout=10)


if __name__ == "__main__":
    main()
