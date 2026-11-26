"""Веб-сервер: API для интерфейса механика и главного механика, SSE-стрим ответов агента."""
import json
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, domain, rag, services
from .agent import backend, run_turn
from .manuals import BY_ID
from .tools import Conversation, ToolError, submit_draft

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Цифровой механик")
_conversations: dict[str, Conversation] = {}
_lock = threading.Lock()


class ChatIn(BaseModel):
    vehicle_id: str
    message: str
    conversation_id: str | None = None


class ConfirmIn(BaseModel):
    conversation_id: str
    draft_id: str


def _session_brief(s: dict | None) -> dict | None:
    if not s:
        return None
    return {k: s.get(k) for k in ("session_id", "checked_at", "status", "anomaly_type", "anomaly_title",
                                   "anomaly_score", "confidence", "signal_quality")}


@app.get("/api/config")
def get_config():
    b = backend()
    return {"llm_mode": type(b).__name__, "provider": config.LLM_PROVIDER if config.llm_configured() else "offline",
            "model": config.LLM_MODEL if config.llm_configured() else None, "services": services.health()}


@app.get("/api/vehicles")
def list_vehicles():
    return [{**domain.vehicle_card(v), "status": domain.fleet_status(v),
             "last": _session_brief(domain.latest_session(v)), "sessions": len(v["sessions"])} for v in domain.vehicles()]


@app.get("/api/vehicles/{vehicle_id}")
def get_vehicle(vehicle_id: str):
    v = domain.vehicle(vehicle_id)
    if not v:
        raise HTTPException(404, "ТС не найдено")
    sessions = []
    for s in v["sessions"]:
        a = domain.assess(s, v["engine_model"])
        sessions.append({**_session_brief(s), "description": s["description"], "urgency": a.get("urgency")})
    return {"vehicle": domain.vehicle_card(v), "status": domain.fleet_status(v), "sessions": sessions,
            "trend": domain.trend(v), "tickets": domain.tickets(vehicle_id),
            "manuals": [{"manual_id": m.id, "designation": m.designation, "short": m.short, "kind": m.kind}
                        for m in BY_ID.values() if v["engine_model"] in m.engines]}


@app.get("/api/fleet")
def fleet():
    rows = []
    for v in domain.vehicles():
        last = domain.latest_session(v)
        a = domain.assess(last, v["engine_model"]) if last else {}
        rows.append({**domain.vehicle_card(v), "status": domain.fleet_status(v), "last": _session_brief(last),
                     "urgency": a.get("urgency"), "trend": domain.trend(v)["direction"],
                     "tickets": len(domain.tickets(v["vehicle_id"]))})
    order = {"quarantine": 0, "attention": 1, "normal": 2}
    rows.sort(key=lambda r: (order[r["status"]], -((r["last"] or {}).get("anomaly_score") or 0)))
    counts = {k: sum(r["status"] == k for r in rows) for k in order}
    return {"counts": counts, "vehicles": rows}


@app.post("/api/chat")
def chat(body: ChatIn):
    if not domain.vehicle(body.vehicle_id):
        raise HTTPException(404, "ТС не найдено")
    with _lock:
        conv = _conversations.get(body.conversation_id or "")
        if not conv or conv.vehicle_id != body.vehicle_id:
            conv = Conversation(vehicle_id=body.vehicle_id)
            _conversations[conv.id] = conv

    def stream():
        yield f"data: {json.dumps({'type': 'conversation', 'id': conv.id}, ensure_ascii=False)}\n\n"
        for ev in run_turn(conv, body.message.strip()[:4000]):
            yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/ticket/confirm")
def confirm_ticket(body: ConfirmIn):
    conv = _conversations.get(body.conversation_id)
    if not conv or not conv.draft or conv.draft["draft_id"] != body.draft_id:
        raise HTTPException(409, "Черновик заявки устарел — подготовьте заявку заново")
    try:
        out = submit_draft(conv)
    except ToolError as e:
        raise HTTPException(502, str(e)) from e
    conv.pending_notes.append(f"механик подтвердил, заявка {out['ticket_id']} создана в ТОиР")
    return out


@app.get("/api/source/{manual_id}/{pdf_page}")
def source(manual_id: str, pdf_page: int):
    m = BY_ID.get(manual_id)
    if not m:
        raise HTTPException(404, "Документ не найден")
    return {"manual_id": m.id, "designation": m.designation, "title": m.title, "pdf_page": pdf_page,
            "text": rag.get_page(manual_id, pdf_page), "pdf_available": (config.MANUALS_DIR / m.file).exists()}


@app.get("/manuals/{manual_id}.pdf")
def manual_pdf(manual_id: str):
    m = BY_ID.get(manual_id)
    path = config.MANUALS_DIR / m.file if m else None
    if not path or not path.exists():
        raise HTTPException(404, "PDF не загружен на этот стенд")
    return FileResponse(path, media_type="application/pdf")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
