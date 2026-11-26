"""Скачивает PDF-руководств из публичной папки Яндекс Диска заказчика в data/manuals/.

Запуск: python -m scripts.fetch_manuals [ссылка_на_папку]
PDF нужны для перестроения индекса и открытия страниц-источников; готовый индекс уже лежит в репозитории.
"""
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.manuals import MANUALS  # noqa: E402

DEFAULT_URL = "https://disk.yandex.ru/d/QdAA8p4m0ySe8Q"
API = "https://cloud-api.yandex.net/v1/disk/public/resources"


def main() -> None:
    key = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    out = ROOT / "data" / "manuals"
    out.mkdir(parents=True, exist_ok=True)
    for m in MANUALS:
        dest = out / m.file
        if dest.exists():
            print(f"есть  {m.file}")
            continue
        q = urllib.parse.urlencode({"public_key": key, "path": "/" + m.file})
        href = json.load(urllib.request.urlopen(f"{API}/download?{q}"))["href"]
        urllib.request.urlretrieve(href, dest)
        print(f"скачан {m.file} ({dest.stat().st_size // 1024} КБ)")


if __name__ == "__main__":
    main()
