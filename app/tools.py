"""Инструменты агента: ML-диагностика, поиск по руководствам, история ТС, заявка в ТОиР.

Описания нейтральны к провайдеру LLM; бэкенды переводят их в свой формат.
"""
import re
import uuid
from dataclasses import dataclass, field

from . import domain, rag, services
from .manuals import BY_ID, manuals_for_engine

TOOLS: list[dict] = [
    {
        "name": "get_diagnosis",
        "description": (
            "Получить результат акустической проверки двигателя выбранного ТС из ML-сервиса НейроДВС. "
            "Возвращает статус (normal / anomaly / check_failed), тип отклонения, anomaly_score, confidence, "
            "качество записи, карточку ТС с моделью двигателя и детерминированную оценку риска. "
            "Без session_id берётся последняя проверка ТС."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Идентификатор проверки, например S-0005. Необязателен."},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_manuals",
        "description": (
            "Поиск по заводским руководствам на двигатель выбранного ТС (только его модель двигателя). "
            "Возвращает фрагменты с обозначением документа и номером страницы PDF. "
            "Формулируйте запрос терминами руководства: «момент затяжки болтов крышек коренных подшипников», "
            "«давление масла на холостом ходу», «зазор в клапанном механизме». "
            "Для сложного вопроса делайте 2–3 запроса с разными формулировками."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Поисковый запрос на русском"},
                "manual_id": {"type": "string", "description": "Ограничить одним документом (id из списка руководств ТС). Необязателен."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_manual_page",
        "description": "Прочитать целиком страницу руководства (номер страницы PDF), например чтобы дочитать таблицу или продолжение процедуры со следующей страницы.",
        "parameters": {
            "type": "object",
            "properties": {
                "manual_id": {"type": "string"},
                "pdf_page": {"type": "integer", "minimum": 1},
            },
            "required": ["manual_id", "pdf_page"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_vehicle_history",
        "description": "История проверок выбранного ТС (до 5 последних), динамика anomaly_score и ранее созданные заявки.",
        "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "prepare_ticket",
        "description": (
            "Подготовить черновик заявки на дефектовку для ТОиР по последней полученной диагностике со статусом anomaly. "
            "Заявка НЕ отправляется: механик видит карточку и подтверждает её кнопкой. "
            "operations и spare_parts — только из найденных фрагментов руководства; references — страницы, "
            "которые вы действительно получили через search_manuals или read_manual_page."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "conclusion": {"type": "string", "description": "Предполагаемая неисправность и основание, 1–3 предложения"},
                "urgency": {"type": "string", "enum": ["low", "medium", "high"]},
                "operations": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "spare_parts": {"type": "array", "items": {"type": "string"}},
                "references": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {"manual_id": {"type": "string"}, "pdf_page": {"type": "integer", "minimum": 1}},
                        "required": ["manual_id", "pdf_page"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["conclusion", "urgency", "operations", "spare_parts", "references"],
            "additionalProperties": False,
        },
    },
    {
        "name": "submit_ticket",
        "description": "Отправить подготовленный черновик заявки в ТОиР. Вызывайте только если механик в последнем сообщении явно подтвердил отправку.",
        "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
]

_CONFIRM = re.compile(r"\b(да|подтвер\w*|отправ\w*|согласен|ок|оформля\w*|создава\w*)\b", re.IGNORECASE)


class ToolError(Exception):
    pass


@dataclass
class Conversation:
    """Состояние диалога по одному ТС."""
    vehicle_id: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    diagnosis: dict | None = None
    retrieved: set = field(default_factory=set)  # {(manual_id, pdf_page)} — что агент реально видел
    last_hits: list = field(default_factory=list)  # порядок релевантности последнего поиска
    draft: dict | None = None
    last_user_text: str = ""
    pending_notes: list = field(default_factory=list)  # системные заметки для следующего хода (напр. заявка создана кнопкой)
    backend_state: object = None

    @property
    def vehicle(self) -> dict:
        v = domain.vehicle(self.vehicle_id)
        if not v:
            raise ToolError(f"ТС {self.vehicle_id} не найдено")
        return v

    @property
    def engine(self) -> str:
        return self.vehicle["engine_model"]


def manuals_brief(engine_model: str) -> list[dict]:
    return [{"manual_id": m.id, "designation": m.designation, "kind": m.kind, "year": m.year,
             "pages": rag.page_count(m.id), "note": m.note} for m in manuals_for_engine(engine_model)]


def execute(conv: Conversation, name: str, args: dict) -> tuple[dict, list[dict]]:
    """Возвращает (результат для модели, карточки для интерфейса)."""
    fn = _HANDLERS.get(name)
    if not fn:
        raise ToolError(f"Неизвестный инструмент {name}")
    return fn(conv, args or {})


def _get_diagnosis(conv: Conversation, args: dict) -> tuple[dict, list[dict]]:
    v = conv.vehicle
    session_id = args.get("session_id") or (domain.latest_session(v) or {}).get("session_id")
    if not session_id:
        raise ToolError("У ТС нет проверок")
    try:
        result = services.diagnose(v["vehicle_id"], session_id)
    except services.ServiceError as e:
        if e.status == 404:
            raise ToolError(f"Проверка {session_id} не найдена для ТС {v['vehicle_id']}") from e
        raise ToolError(f"ML-сервис: {e.detail}") from e
    assessment = domain.assess(result, v["engine_model"])
    conv.diagnosis = {"result": result, "assessment": assessment}
    conv.draft = None
    payload = {
        "vehicle": domain.vehicle_card(v),
        "ml_result": result,
        "assessment": assessment,
        "manuals_for_engine": manuals_brief(v["engine_model"]),
    }
    if result["status"] == "anomaly":
        payload["suggested_search"] = domain.FAULTS.get(result["anomaly_type"], {}).get("query")
    return payload, [{"kind": "diagnosis", "data": payload}]


def _search(conv: Conversation, args: dict) -> tuple[dict, list[dict]]:
    query = (args.get("query") or "").strip()
    if not query:
        raise ToolError("Пустой запрос")
    manual_id = args.get("manual_id") or None
    if manual_id and manual_id not in {m.id for m in manuals_for_engine(conv.engine)}:
        raise ToolError(f"Документ {manual_id} не относится к двигателю {conv.engine}")
    hits = rag.search(query, conv.engine, k=6, manual_id=manual_id)
    for h in hits:
        conv.retrieved.add((h["manual_id"], h["pdf_page"]))
    conv.last_hits = [(h["manual_id"], h["pdf_page"]) for h in hits]
    out = [{"cite": f"[[{h['manual_id']}:{h['pdf_page']}]]", "manual": h["manual"], "pdf_page": h["pdf_page"],
            "ocr": h["ocr"], "text": h["text"]} for h in hits]
    return ({"engine_model": conv.engine, "query": query, "results": out,
             "note": "Пусто — значит, в руководствах на этот двигатель нет подходящего текста." if not out else ""},
            [{"kind": "search", "data": {"query": query, "hits": [{k: h[k] for k in ("manual", "manual_id", "pdf_page")} for h in hits]}}])


def _read_page(conv: Conversation, args: dict) -> tuple[dict, list[dict]]:
    mid, page = args.get("manual_id"), int(args.get("pdf_page") or 0)
    if mid not in {m.id for m in manuals_for_engine(conv.engine)}:
        raise ToolError(f"Документ {mid} не относится к двигателю {conv.engine}")
    text = rag.get_page(mid, page)
    if not text:
        raise ToolError(f"Страница {page} в {mid} пуста или отсутствует (всего {rag.page_count(mid)})")
    conv.retrieved.add((mid, page))
    return ({"cite": f"[[{mid}:{page}]]", "manual": BY_ID[mid].designation, "pdf_page": page, "text": text},
            [{"kind": "read", "data": {"manual_id": mid, "manual": BY_ID[mid].designation, "pdf_page": page}}])


def _history(conv: Conversation, args: dict) -> tuple[dict, list[dict]]:
    v = conv.vehicle
    sessions = [{k: s[k] for k in ("session_id", "checked_at", "status", "anomaly_type", "anomaly_title",
                                   "anomaly_score", "confidence", "signal_quality", "description")} for s in v["sessions"][-5:]]
    data = {"vehicle": domain.vehicle_card(v), "sessions": sessions, "trend": domain.trend(v),
            "fleet_status": domain.fleet_status(v), "tickets": domain.tickets(v["vehicle_id"])}
    return data, [{"kind": "history", "data": data}]


def _prepare_ticket(conv: Conversation, args: dict) -> tuple[dict, list[dict]]:
    d = conv.diagnosis
    if not d:
        raise ToolError("Сначала получите результат диагностики (get_diagnosis)")
    res = d["result"]
    if res["status"] != "anomaly":
        raise ToolError(f"Заявка на дефектовку оформляется только по отклонению; статус проверки: {res['status']}")
    compat = d["assessment"].get("compatibility", {})
    if not compat.get("compatible", True):
        raise ToolError("Тип неисправности не соответствует двигателю, инструкции в руководстве нет — заявку в ТОиР не отправляем. "
                        "Сообщите механику и порекомендуйте повторную запись и общую дефектовку. " + compat.get("reason", ""))
    ops = [o.strip() for o in args.get("operations", []) if o and o.strip()]
    if not ops:
        raise ToolError("Нужен хотя бы один пункт работ")
    refs, bad = [], []
    for r in args.get("references", []):
        key = (r.get("manual_id"), int(r.get("pdf_page") or 0))
        if key not in conv.retrieved:
            bad.append(f"{key[0]}:{key[1]}")
            continue
        refs.append({"manual": BY_ID[key[0]].designation, "pdf_page": key[1], "manual_id": key[0]})
    if bad:
        raise ToolError("Ссылки не подтверждены поиском в этом диалоге: " + ", ".join(bad) +
                        ". Найдите их через search_manuals или read_manual_page либо уберите.")
    if not refs:
        raise ToolError("Нужна хотя бы одна ссылка на руководство")
    urgency = args.get("urgency") or d["assessment"].get("urgency") or "medium"
    min_urg = d["assessment"].get("urgency")
    if min_urg and not domain.urgency_ge(urgency, min_urg):
        urgency = min_urg  # приоритет не ниже расчётного
    draft = {
        "draft_id": uuid.uuid4().hex[:8],
        "ticket": {
            "vehicle_id": res["vehicle_id"],
            "session_id": res["session_id"],
            "anomaly_type": res["anomaly_type"],
            "conclusion": args["conclusion"].strip(),
            "urgency": urgency,
            "operations": ops,
            "spare_parts": [p.strip() for p in args.get("spare_parts", []) if p and p.strip()],
            "references": [{"manual": r["manual"], "pdf_page": r["pdf_page"]} for r in refs],
        },
        "ref_ids": [r["manual_id"] for r in refs],
        "anomaly_title": res["anomaly_title"],
        "existing": [t["ticket_id"] for t in domain.tickets(res["vehicle_id"]) if t["session_id"] == res["session_id"]],
    }
    conv.draft = draft
    return ({"status": "draft_ready", "draft": draft["ticket"], "existing_tickets_for_session": draft["existing"],
             "next": "Покажите механику краткое содержание и попросите подтвердить отправку кнопкой «Отправить в ТОиР»."},
            [{"kind": "ticket_draft", "data": draft}])


def submit_draft(conv: Conversation) -> dict:
    if not conv.draft:
        raise ToolError("Нет подготовленного черновика заявки")
    ticket = conv.draft["ticket"]
    try:
        resp = services.create_ticket(ticket)
    except services.ServiceError as e:
        raise ToolError(f"ТОиР отклонил заявку: {e.detail}") from e
    domain.save_ticket({**ticket, "ticket_id": resp["ticket_id"], "anomaly_title": conv.draft["anomaly_title"]})
    conv.draft = None
    return {"ticket_id": resp["ticket_id"], "status": resp.get("status"), "ticket": ticket}


def _submit_ticket(conv: Conversation, args: dict) -> tuple[dict, list[dict]]:
    if not _CONFIRM.search(conv.last_user_text or ""):
        raise ToolError("Механик не подтвердил отправку. Попросите подтвердить или нажать кнопку в карточке заявки.")
    out = submit_draft(conv)
    return out, [{"kind": "ticket_created", "data": out}]


_HANDLERS = {
    "get_diagnosis": _get_diagnosis,
    "search_manuals": _search,
    "read_manual_page": _read_page,
    "get_vehicle_history": _history,
    "prepare_ticket": _prepare_ticket,
    "submit_ticket": _submit_ticket,
}
