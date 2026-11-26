"""Разбор PDF-руководств в фрагменты с номерами страниц PDF.

Запуск: python -m scripts.build_index
Страницы без текстового слоя (сканы) распознаются Tesseract (rus+eng), если он установлен.
Результат: data/index/chunks.jsonl
"""
import json
import os
import shutil
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.manuals import MANUALS  # noqa: E402

MANUALS_DIR = ROOT / "data" / "manuals"
OUT = ROOT / "data" / "index" / "chunks.jsonl"
CHUNK_CHARS = 1400
OVERLAP = 250
MIN_TEXT = 50


def find_tessdata() -> str | None:
    candidates = [
        os.environ.get("TESSDATA_PREFIX"),
        r"C:\Program Files\Tesseract-OCR\tessdata",
        "/usr/share/tesseract-ocr/5/tessdata",
        "/usr/share/tesseract-ocr/4.00/tessdata",
        "/opt/homebrew/share/tessdata",
    ]
    for c in candidates:
        if c and (Path(c) / "rus.traineddata").exists():
            return c
    return None


def normalize(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines()]
    out, buf = [], []
    for ln in lines:
        if not ln:
            if buf:
                out.append(" ".join(buf))
                buf = []
            continue
        buf.append(ln)
    if buf:
        out.append(" ".join(buf))
    return "\n".join(out).replace("\u00ad", "")


def split_page(text: str) -> list[str]:
    if len(text) <= CHUNK_CHARS:
        return [text]
    parts, start = [], 0
    while start < len(text):
        end = min(len(text), start + CHUNK_CHARS)
        if end < len(text):
            cut = text.rfind("\n", start + CHUNK_CHARS // 2, end)
            if cut == -1:
                cut = text.rfind(". ", start + CHUNK_CHARS // 2, end)
            if cut != -1:
                end = cut + 1
        parts.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - OVERLAP, start + 1)
    return [p for p in parts if p]


def main() -> None:
    tessdata = find_tessdata()
    if not tessdata and not shutil.which("tesseract"):
        print("Tesseract не найден: страницы-сканы будут пропущены")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    n_chunks = 0
    with OUT.open("w", encoding="utf-8") as f:
        for m in MANUALS:
            path = MANUALS_DIR / m.file
            if not path.exists():
                print(f"!! нет файла {path}")
                continue
            doc = pymupdf.open(path)
            ocr_pages = 0
            for i, page in enumerate(doc):
                raw = page.get_text("text")
                source = "text"
                if len(raw.strip()) < MIN_TEXT and tessdata:
                    try:
                        tp = page.get_textpage_ocr(language="rus+eng", dpi=300, full=True, tessdata=tessdata)
                        raw = page.get_text("text", textpage=tp)
                        source = "ocr"
                        ocr_pages += 1
                    except Exception as e:  # OCR — best effort
                        print(f"   OCR fail {m.id} p{i + 1}: {e}")
                text = normalize(raw)
                if len(text) < 20:
                    continue
                for j, part in enumerate(split_page(text)):
                    rec = {
                        "chunk_id": f"{m.id}:p{i + 1}:{j}",
                        "manual_id": m.id,
                        "pdf_page": i + 1,
                        "source": source,
                        "text": part,
                    }
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    n_chunks += 1
            print(f"{m.id}: {doc.page_count} стр., OCR {ocr_pages}")
    print(f"Готово: {n_chunks} фрагментов → {OUT}")


if __name__ == "__main__":
    main()
