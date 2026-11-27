"""Данные парка, справочник неисправностей, оценка риска, применимость типа неисправности к двигателю."""
import json
import threading
from datetime import datetime
from functools import lru_cache

from .config import FAULT_TYPES_FILE, STATE_DIR, VEHICLES_FILE
from . import rag

# Тип топлива по модели двигателя (конструктивный факт; карточка ТС тоже содержит fuel).
DIESEL = {"ЯМЗ-236М2", "ЯМЗ-536"}
PETROL = {"ЗМЗ-5234.10", "ЗМЗ-40904.10", "ЗМЗ-40905.10"}

FAULTS: dict[str, dict] = {
    "F01": {"criticality": "high", "query": "клапанный механизм зазор между клапаном и коромыслом регулировка стук клапанов"},
    "F02": {"criticality": "critical", "query": "давление масла в системе смазки низкое причины неисправности масляный насос"},
    "F03": {"criticality": "critical", "query": "перегрев двигателя охлаждающая жидкость в масле прокладка головки цилиндров термостат"},
    "F04": {"criticality": "medium", "query": "натяжение ремня привода генератора водяного насоса вентилятора прогиб ремня"},
    "F05": {"criticality": "medium", "query": "генератор неисправности проверка напряжение подшипники шкив"},
    "F06": {"criticality": "medium", "query": "сцепление неисправности пробуксовка свободный ход педали регулировка"},
    "F07": {"criticality": "medium", "query": "коробка передач неисправности шум переключение передач"},
    "F08": {"criticality": "high", "query": "топливный насос высокого давления форсунка угол опережения впрыскивания неисправности"},
    "F09": {"criticality": "high", "query": "турбокомпрессор неисправности давление наддува люфт ротора"},
    "F10": {"criticality": "medium", "query": "компрессор пневматической системы тормозов насос гидроусилителя руля неисправности"},
    "F11": {"criticality": "medium", "query": "система рециркуляции отработавших газов клапан неисправности"},
    "F12": {"criticality": "medium", "query": "система зажигания свечи зажигания катушка пропуски воспламенения"},
    "F13": {"criticality": "medium", "query": "система питания бензин топливный насос карбюратор форсунки давление топлива"},
    "F14": {"criticality": "critical", "query": "цепь привода распределительных валов гидронатяжитель шум цепи замена"},
}

# Узлы, наличие которых проверяется по самим руководствам на двигатель (шаблон, минимум страниц).
_EVIDENCE = {
    "F09": (r"турбокомпрессор", 3),
    "F11": (r"рециркуляц", 3),
    "F14": (r"гидронатяжител|цеп[ьи] привода распредел", 3),
}

_LEVEL = {"low": 0, "medium": 1, "high": 2}


@lru_cache(maxsize=1)
def fault_titles() -> dict[str, str]:
    return {f["code"]: f["title"] for f in json.loads(FAULT_TYPES_FILE.read_text(encoding="utf-8"))}


@lru_cache(maxsize=1)
def _fleet() -> dict:
    return json.loads(VEHICLES_FILE.read_text(encoding="utf-8"))


def vehicles() -> list[dict]:
    return _fleet()["vehicles"]


def vehicle(vehicle_id: str) -> dict | None:
    return next((v for v in vehicles() if v["vehicle_id"] == vehicle_id), None)


def vehicle_card(v: dict) -> dict:
    return {k: v[k] for k in ("vehicle_id", "garage_no", "make", "model", "vehicle_type", "engine_model", "fuel")}


def latest_session(v: dict) -> dict | None:
    return v["sessions"][-1] if v["sessions"] else None


def compatibility(engine_model: str, code: str | None) -> dict:
    """Проверяет, может ли тип неисправности относиться к этому двигателю и есть ли данные в руководствах."""
    if not code:
        return {"compatible": True}
    if engine_model in DIESEL and code in ("F12", "F13"):
        return {"compatible": False, "reason": f"{engine_model} — дизель: системы зажигания и подачи бензина у него нет."}
    if engine_model in PETROL and code == "F08":
        return {"compatible": False, "reason": f"{engine_model} — бензиновый двигатель: дизельной топливоподачи (ТНВД) у него нет."}
    if code in _EVIDENCE:
        pattern, min_pages = _EVIDENCE[code]
        found = rag.manual_mentions(engine_model, pattern)
        if found < min_pages:
            return {
                "compatible": False,
                "reason": f"В руководствах на {engine_model} узел «{fault_titles()[code]}» не описан "
                          f"(совпадений по документации: {found}). Вероятно, у этого двигателя его нет.",
            }
    return {"compatible": True}


CONFIDENCE_MIN = 0.50  # ниже — заявка не оформляется, нужна повторная запись (гипотеза команды, принята организаторами)
PRIORITY_MEANING = {
    "high": "дефектовка до выпуска в рейс",
    "medium": "дефектовка на ближайшем ТО, машина работает под наблюдением",
    "low": "узел проверяется на следующей проверке",
}


def assess(result: dict, engine_model: str) -> dict:
    """Детерминированная оценка риска и приоритета — агент её объясняет, но не пересчитывает."""
    status = result.get("status")
    quality = result.get("signal_quality")
    if status == "check_failed":
        return {"verdict": "check_failed", "risk": None, "urgency": None,
                "action": "Повторить запись: заключение о двигателе не формируется."}
    if status == "normal":
        return {"verdict": "normal", "risk": "низкий", "urgency": None,
                "action": "Значимых отклонений нет. ТС можно эксплуатировать в обычном режиме."}
    code = result.get("anomaly_type")
    score = result.get("anomaly_score") or 0
    conf = result.get("confidence") or 0
    crit = FAULTS.get(code, {}).get("criticality", "medium")
    crit_ru = {"critical": "критичный узел", "high": "важный узел", "medium": "вспомогательный узел"}[crit]
    if crit == "critical" or score >= 0.8:
        urgency = "high"
    elif crit == "high" or score >= 0.65:
        urgency = "medium"
    else:
        urgency = "low"
    basis = [
        f"Тип {code} «{result.get('anomaly_title')}» — {crit_ru}"
        + (" (отказ ведёт к тяжёлому повреждению двигателя)" if crit == "critical" else ""),
        f"Оценка отклонения anomaly_score = {score:.2f}" + (" — высокая (≥ 0,80)" if score >= 0.8 else
                                                           " — заметная (≥ 0,65)" if score >= 0.65 else " — умеренная"),
        f"Уверенность модели {conf:.0%}, качество записи: {quality}",
        "Правило: базовая критичность узла (критичный → высокий, важный → средний, прочие → низкий), "
        "поправка по оценке: score ≥ 0,80 поднимает до высокого, ≥ 0,65 — до среднего. Окончательный приоритет выбирает механик.",
    ]
    notes = []
    if quality == "warning":
        notes.append("Запись с помехами: уверенность модели снижена, рекомендуется повторная запись для подтверждения.")
    ticket_allowed, ticket_block = True, None
    if conf < CONFIDENCE_MIN:
        ticket_allowed = False
        ticket_block = (f"Уверенность модели {conf:.0%} ниже порога 0,50: заявка не оформляется. "
                        "Повторите запись; если отклонение подтвердится с достаточной уверенностью — оформите заявку.")
        notes.append(ticket_block)
    compat = compatibility(engine_model, code)
    if not compat["compatible"]:
        notes.append("Тип неисправности не соответствует конструкции двигателя: возможна ошибка классификации.")
        basis.append("Тип не применим к этому двигателю — приоритет снижен до среднего, заявка по этому типу не оформляется.")
        return {"verdict": "anomaly", "risk": "не определён", "urgency": "medium", "criticality": crit,
                "action": "Повторить запись. Если отклонение подтвердится — общая дефектовка двигателя: "
                          "инструкции по этому типу в руководстве на двигатель нет.",
                "basis": basis, "notes": notes, "compatibility": compat, "warning": quality == "warning",
                "ticket_allowed": False,
                "ticket_block": "В руководстве на этот двигатель нет инструкции по найденному типу неисправности: "
                                "результат похож на ошибку классификации, заявка в ТОиР не отправляется.",
                "confidence_min": CONFIDENCE_MIN}
    risk = {"high": "высокий", "medium": "средний", "low": "низкий"}[urgency]
    action = {
        "high": "Рекомендация: дефектовка до выпуска в рейс. Оформить заявку на дефектовку.",
        "medium": "Провести проверки по руководству в ближайшее ТО, оформить заявку на дефектовку.",
        "low": "Проверить при плановом ТО, наблюдать динамику.",
    }[urgency]
    if not ticket_allowed:
        action = "Повторить запись для подтверждения: уверенность модели ниже порога 0,50, заявка пока не оформляется."
    return {"verdict": "anomaly", "risk": risk, "urgency": urgency, "criticality": crit,
            "action": action, "basis": basis, "notes": notes, "compatibility": compat,
            "warning": quality == "warning", "ticket_allowed": ticket_allowed, "ticket_block": ticket_block,
            "confidence_min": CONFIDENCE_MIN}


def fleet_status(v: dict) -> str:
    """normal | attention | quarantine — для панели главного механика."""
    last = latest_session(v)
    if not last:
        return "normal"
    if last["status"] == "check_failed":
        return "attention"
    if last["status"] == "normal":
        return "normal"
    a = assess(last, v["engine_model"])
    return "quarantine" if a["urgency"] == "high" else "attention"


def trend(v: dict) -> dict:
    pts = [(s["checked_at"], s["anomaly_score"]) for s in v["sessions"][-5:] if s["anomaly_score"] is not None]
    if len(pts) < 2:
        return {"points": pts, "direction": "недостаточно данных"}
    delta = pts[-1][1] - pts[0][1]
    direction = "рост" if delta > 0.15 else "снижение" if delta < -0.15 else "стабильно"
    return {"points": pts, "direction": direction, "delta": round(delta, 2)}


# --- Хранилище заявок (Mock ТОиР списка не отдаёт, историю ведём сами) ---
_TICKETS_FILE = STATE_DIR / "tickets.json"
_lock = threading.Lock()


def tickets(vehicle_id: str | None = None) -> list[dict]:
    if not _TICKETS_FILE.exists():
        return []
    items = json.loads(_TICKETS_FILE.read_text(encoding="utf-8"))
    return [t for t in items if vehicle_id in (None, t["vehicle_id"])]


def save_ticket(record: dict) -> None:
    with _lock:
        items = tickets()
        record = {**record, "created_at": datetime.now().astimezone().isoformat(timespec="seconds")}
        items.append(record)
        _TICKETS_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def urgency_ge(a: str, b: str) -> bool:
    return _LEVEL[a] >= _LEVEL[b]
