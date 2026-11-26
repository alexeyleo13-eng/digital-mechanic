"""Резервный режим без LLM: те же инструменты, простая маршрутизация по ключевым словам.

Нужен, чтобы прототип работал без ключа API и интернета. Основной режим — agent.AnthropicBackend / OpenAICompatBackend.
"""
import json
import re
from collections.abc import Iterator

from . import domain
from .agent import _run_tool
from .tools import Conversation

_FAULT_OPS = {
    "F01": ["Проверить и отрегулировать зазоры в клапанном механизме", "Осмотреть коромысла, толкатели, пружины клапанов"],
    "F02": ["Проверить уровень и состояние масла", "Замерить давление масла контрольным манометром", "Проверить масляный насос и редукционный клапан"],
    "F03": ["Проверить систему охлаждения и термостат", "Проверить масло на наличие охлаждающей жидкости", "Проверить герметичность прокладки головки цилиндров"],
    "F04": ["Проверить натяжение и состояние ремней привода агрегатов", "Проверить натяжитель и ролики"],
    "F05": ["Проверить генератор: напряжение, подшипники, крепление шкива"],
    "F06": ["Проверить свободный ход педали и работу сцепления"],
    "F07": ["Проверить коробку передач на шум и чёткость переключения"],
    "F08": ["Проверить форсунки и ТНВД", "Проверить угол опережения впрыскивания топлива"],
    "F09": ["Проверить турбокомпрессор: люфт ротора, давление наддува, утечки воздуха"],
    "F10": ["Проверить компрессор пневмосистемы и насос ГУР"],
    "F11": ["Проверить клапан и каналы системы рециркуляции отработавших газов"],
    "F12": ["Проверить систему зажигания: свечи, катушки, провода"],
    "F13": ["Проверить систему питания: давление топлива, фильтры, насос"],
    "F14": ["Проверить цепь привода распредвалов и гидронатяжитель", "Проверить метки фаз газораспределения"],
}


def _call(conv, name, args, sink: list) -> Iterator[dict]:
    for ev in _run_tool(conv, name, args):
        if ev["type"] == "_result":
            sink.append(ev)
        else:
            yield ev


def _excerpt(text: str, n: int = 260) -> str:
    t = " ".join(text.split())
    return t[:n] + ("…" if len(t) > n else "")


class OfflineBackend:
    def run(self, conv: Conversation, text: str) -> Iterator[dict]:
        low = text.lower()
        yield {"type": "status", "text": "Демо-режим без LLM: ответы собираются из найденных фрагментов руководства."}
        if conv.draft and re.search(r"\b(да|подтвер|отправ)", low):
            yield from self._submit(conv)
        elif "заявк" in low:
            yield from self._ticket(conv)
        elif re.search(r"истори|динамик|прошл", low):
            yield from self._history(conv)
        elif re.search(r"результат|диагност|провер", low) and "?" not in low:
            m = re.search(r"S-\d+", text)
            yield from self._diagnosis(conv, {"session_id": m.group(0)} if m else {})
        else:
            yield from self._question(conv, text)

    def _diagnosis(self, conv, args):
        sink: list = []
        yield from _call(conv, "get_diagnosis", args, sink)
        if sink[-1]["is_error"]:
            yield {"type": "delta", "text": sink[-1]["content"]}
            return
        d = json.loads(sink[-1]["content"])
        r, a = d["ml_result"], d["assessment"]
        if r["status"] == "check_failed":
            yield {"type": "delta", "text": f"**Сбой проверки.** {r['description']}.\n\nЗаключение о двигателе не формируется. "
                   "Повторите запись: прогретый двигатель, холостой ход, 20–30 с, без посторонних шумов."}
            return
        if r["status"] == "normal":
            yield {"type": "delta", "text": "**Норма.** Значимых отклонений не обнаружено. ТС можно эксплуатировать в обычном режиме."}
            return
        out = [f"**Отклонение: {r['anomaly_title']}** — риск {a['risk']}, уверенность {r['confidence']:.0%}.",
               f"Признаки: {r['description']}.", f"**Что делать:** {a['action']}"]
        out += [f"⚠️ {n}" for n in a.get("notes", [])]
        if not a["compatibility"]["compatible"]:
            out.append(a["compatibility"]["reason"])
            yield {"type": "delta", "text": "\n\n".join(out)}
            return
        sink = []
        yield from _call(conv, "search_manuals", {"query": d["suggested_search"]}, sink)
        hits = json.loads(sink[-1]["content"]).get("results", [])[:3]
        if hits:
            out.append("**Что смотреть в руководстве:**")
            out += [f"- {_excerpt(h['text'])} {h['cite']}" for h in hits]
        else:
            out.append("В руководствах на этот двигатель подходящего раздела не найдено — рекомендуется дефектовка.")
        yield {"type": "delta", "text": "\n\n".join(out)}

    def _question(self, conv, text):
        sink: list = []
        yield from _call(conv, "search_manuals", {"query": text}, sink)
        hits = json.loads(sink[-1]["content"]).get("results", [])[:3] if not sink[-1]["is_error"] else []
        if not hits:
            yield {"type": "delta", "text": f"В руководствах на {conv.engine} по этому вопросу ничего не найдено. Рекомендуется дефектовка."}
            return
        lines = ["Найдено в руководстве (без LLM — проверьте формулировку по источнику):"]
        lines += [f"- {_excerpt(h['text'], 420)} {h['cite']}" for h in hits]
        yield {"type": "delta", "text": "\n\n".join(lines)}

    def _history(self, conv):
        sink: list = []
        yield from _call(conv, "get_vehicle_history", {}, sink)
        d = json.loads(sink[-1]["content"])
        t = d["trend"]
        yield {"type": "delta", "text": f"Проверок: {len(d['sessions'])}. Динамика anomaly_score: **{t['direction']}**. "
                                        f"Заявок по ТС: {len(d['tickets'])}."}

    def _ticket(self, conv):
        if not conv.diagnosis:
            sink: list = []
            yield from _call(conv, "get_diagnosis", {}, sink)
        d = conv.diagnosis
        if not d or d["result"]["status"] != "anomaly":
            yield {"type": "delta", "text": "Заявка на дефектовку оформляется только по проверке с отклонением."}
            return
        r = d["result"]
        sink = []
        yield from _call(conv, "search_manuals", {"query": domain.FAULTS[r["anomaly_type"]]["query"]}, sink)
        refs = [{"manual_id": m, "pdf_page": p} for m, p in conv.last_hits[:3]]
        sink = []
        yield from _call(conv, "prepare_ticket", {
            "conclusion": f"Подозрение: {r['anomaly_title'].lower()}. Признаки по записи: {r['description']}. "
                          f"anomaly_score {r['anomaly_score']}, уверенность {r['confidence']}.",
            "urgency": d["assessment"]["urgency"],
            "operations": _FAULT_OPS.get(r["anomaly_type"], ["Дефектовка узла по руководству"]),
            "spare_parts": [],
            "references": refs,
        }, sink)
        if sink[-1]["is_error"]:
            yield {"type": "delta", "text": sink[-1]["content"]}
        else:
            yield {"type": "delta", "text": "Черновик заявки готов. Проверьте и нажмите «Отправить в ТОиР»."}

    def _submit(self, conv):
        sink: list = []
        yield from _call(conv, "submit_ticket", {}, sink)
        if sink[-1]["is_error"]:
            yield {"type": "delta", "text": sink[-1]["content"]}
        else:
            yield {"type": "delta", "text": f"✅ Заявка **{json.loads(sink[-1]['content'])['ticket_id']}** создана в ТОиР."}
