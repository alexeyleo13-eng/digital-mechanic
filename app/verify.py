"""Проверка ответа агента: каждое число с единицей измерения должно встречаться на страницах руководства,
которые агент получил в этом диалоге. Иначе — предупреждение механику (защита от выдуманных нормативов)."""
import re

from . import rag

_UNITS = r"(?:мм|см|м|мкм|Н\s*[·*.]?\s*м|кгс\s*[·*.]?\s*м|кгс/см\s*2|кгс/см²|кгс|МПа|кПа|бар|атм|°\s*C|°\s*С|°|град\w*|мин-1|мин⁻¹|об/мин|л|литр\w*|км|ч|час\w*|В|А|Ом)"
_NUM = r"\d+(?:[.,]\d+)?"
_VALUE = re.compile(rf"(?<![\w.,])((?:{_NUM}\s*(?:[-–—…]|\.\.\.|до|±|\+)\s*)*{_NUM})\s*{_UNITS}(?![\w])")
_CITE = re.compile(r"\[{1,2}\s*[a-z][a-z0-9]*_[a-z0-9_]+\s*:[\d,;\s]+\]{1,2}")


def _numbers(text: str) -> set[float]:
    """Все числа текста как значения: «20,0» и «20» совпадают."""
    return {float(n.replace(",", ".")) for n in re.findall(_NUM, text)}


def unverified_values(answer: str, retrieved: set, diagnosis: dict | None) -> list[str]:
    known = _numbers(" ".join(rag.get_page(m, p) for m, p in retrieved)) | _numbers(str(diagnosis or ""))
    text = _CITE.sub(" ", answer)
    bad = []
    for match in _VALUE.finditer(text):
        nums = re.findall(_NUM, match.group(1))
        missing = [n for n in nums if float(n.replace(",", ".")) not in known]
        if missing and match.group(0).strip() not in bad:
            bad.append(match.group(0).strip())
    return bad
