"""Проверки инструментов и правил агента. Нужны запущенные заглушки ML (8001) и ТОиР (8002)."""
import pytest

from app import domain, rag, services
from app.tools import Conversation, ToolError, execute

pytestmark = pytest.mark.skipif(not all(services.health().values()), reason="заглушки ML/ТОиР не запущены")


def test_search_is_limited_to_vehicle_engine():
    hits = rag.search("момент затяжки болтов крышек коренных подшипников", "ЯМЗ-236М2")
    assert hits and all(h["manual_id"].startswith("yamz236") for h in hits)
    assert any(h["pdf_page"] == 41 and "421-461" in h["text"] for h in hits)


def test_check_failed_gives_no_verdict_and_no_ticket():
    conv = Conversation(vehicle_id="P456AT178")
    data, _ = execute(conv, "get_diagnosis", {"session_id": "S-0003"})
    assert data["ml_result"]["status"] == "check_failed"
    assert data["assessment"]["urgency"] is None
    with pytest.raises(ToolError):
        execute(conv, "prepare_ticket", {"conclusion": "x", "urgency": "high", "operations": ["x"], "spare_parts": [],
                                         "references": [{"manual_id": "yamz236_rk", "pdf_page": 41}]})


def test_incompatible_fault_blocks_ticket():
    conv = Conversation(vehicle_id="P854BT78")
    data, _ = execute(conv, "get_diagnosis", {"session_id": "S-0021"})  # F14 цепь ГРМ на ЯМЗ-236М2
    assert data["assessment"]["compatibility"]["compatible"] is False
    with pytest.raises(ToolError):
        execute(conv, "prepare_ticket", {"conclusion": "x", "urgency": "high", "operations": ["x"], "spare_parts": [],
                                         "references": [{"manual_id": "yamz236_rk", "pdf_page": 41}]})


def test_ticket_requires_retrieved_references_and_reaches_toir():
    conv = Conversation(vehicle_id="M333YK47")
    execute(conv, "get_diagnosis", {"session_id": "S-0005"})
    args = {"conclusion": "Подозрение на неисправность топливной аппаратуры", "urgency": "low",
            "operations": ["Проверить форсунки"], "spare_parts": [],
            "references": [{"manual_id": "yamz236_rk", "pdf_page": 41}]}
    with pytest.raises(ToolError, match="не подтверждены"):
        execute(conv, "prepare_ticket", args)
    hits, _ = execute(conv, "search_manuals", {"query": "форсунка давление начала впрыскивания"})
    page = hits["results"][0]["pdf_page"]
    manual_id = hits["results"][0]["cite"].strip("[]").split(":")[0]
    args["references"] = [{"manual_id": manual_id, "pdf_page": page}]
    data, _ = execute(conv, "prepare_ticket", args)
    assert data["draft"]["urgency"] == "medium"  # не ниже расчётного
    conv.last_user_text = "Да, отправляй"
    out, _ = execute(conv, "submit_ticket", {})
    assert out["ticket_id"].startswith("TKT-")
    assert any(t["ticket_id"] == out["ticket_id"] for t in domain.tickets("M333YK47"))


def test_submit_without_confirmation_is_refused():
    conv = Conversation(vehicle_id="M333YK47")
    conv.last_user_text = "какое давление масла?"
    with pytest.raises(ToolError):
        execute(conv, "submit_ticket", {})


def test_other_engine_manual_is_rejected():
    conv = Conversation(vehicle_id="M333YK47")
    with pytest.raises(ToolError):
        execute(conv, "search_manuals", {"query": "зазор", "manual_id": "zmz409_rk"})
