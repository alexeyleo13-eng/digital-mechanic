"""Прогон контрольных сценариев через настоящего агента (LLM из .env). Результат — в консоль и data/state/eval.md.

python -m scripts.eval_questions
Нужны запущенные заглушки (python -m scripts.run_all).
"""
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import agent, config, rag  # noqa: E402
from app.tools import Conversation  # noqa: E402

CASES = [
    ("ЯМЗ-236М2: норматив", "P456AT178", ["Какой момент затяжки болтов крышек коренных подшипников?"]),
    ("ЯМЗ-536: давление масла", "O727CA198", ["Какое давление масла должно быть на прогретом двигателе на холостом ходу?"]),
    ("ЗМЗ-40905.10: клапаны", "Y815BM178", ["Как регулировать зазоры в клапанах?"]),
    ("ЗМЗ-5234.10: ремень", "O819CY198", ["Какой прогиб ремня привода генератора?"]),
    ("ЗМЗ-40904.10: свечи", "C997KC78", ["Какой момент затяжки свечей зажигания?"]),
    ("Вне базы", "O819CY198", ["Как проверить давление наддува турбокомпрессора?"]),
    ("С2: своим ходом при F02", "E549YT198", ["Получить результат диагностики (проверка S-0011)",
                                              "Можно ли своим ходом перегнать машину на базу?"]),
    ("С3: сбой записи", "P456AT178", ["Получить результат диагностики (проверка S-0003)"]),
    ("Тип не подходит к двигателю", "P854BT78", ["Получить результат диагностики (проверка S-0021)",
                                                 "Создай заявку на дефектовку"]),
    ("С1: заявка", "M333YK47", ["Получить результат диагностики (проверка S-0005)", "Создай заявку на дефектовку"]),
]
USAGE: list = []
CITE = re.compile(r"\[{1,2}\s*([a-z][a-z0-9]*_[a-z0-9_]+)\s*:\s*(\d+)\s*\]{1,2}")


def run_case(title, vehicle_id, turns, out):
    conv = Conversation(vehicle_id=vehicle_id)
    out.append(f"\n## {title} — {vehicle_id} ({conv.engine})")
    for text in turns:
        t0 = time.time()
        answer, tools, errors, cards, usage = "", [], [], [], []
        for ev in agent.run_turn(conv, text):
            if ev["type"] == "usage":
                usage.append(ev)
                USAGE.append({**ev, "case": title, "turn": text})
            elif ev["type"] == "tool":
                tools.append(f"{ev['name']}({', '.join(f'{k}={v}' for k, v in (ev.get('args') or {}).items() if k != 'references')})")
            elif ev["type"] == "delta":
                answer += ev["text"]
            elif ev["type"] == "error":
                errors.append(ev["text"])
            elif ev["type"] == "card" and ev["kind"] in ("ticket_draft", "ticket_created"):
                cards.append(ev["kind"])
        cites = CITE.findall(answer)
        bad = [f"{m}:{p}" for m, p in cites if (m, int(p)) not in conv.retrieved]
        foreign = [m for m, _ in cites if conv.engine not in rag.BY_ID[m].engines] if cites else []
        out.append(f"\n**Механик:** {text}  \n_{time.time() - t0:.0f} с · инструменты: {'; '.join(tools) or '—'}"
                   f" · ссылок: {len(cites)}, не найденных поиском: {bad or 'нет'}, чужой двигатель: {foreign or 'нет'}"
                   f"{' · карточки: ' + ', '.join(cards) if cards else ''}_\n")
        out.append(answer.strip() or "(пустой ответ)")
        for e in errors:
            out.append(f"\n!! {e}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    only = sys.argv[1:]
    out = [f"# Контрольный прогон: {config.LLM_PROVIDER} / {config.LLM_MODEL}"]
    pause = float(os.environ.get("EVAL_PAUSE", "0"))  # для бесплатных тарифов с лимитом запросов в минуту
    for title, vid, turns in CASES:
        if only and not any(o.lower() in title.lower() for o in only):
            continue
        n = len(out)
        run_case(title, vid, turns, out)
        time.sleep(pause)
        print("\n".join(out[n:]), flush=True)
    import json
    (config.STATE_DIR / "usage.json").write_text(json.dumps(USAGE, ensure_ascii=False, indent=1), encoding="utf-8")
    dest = config.STATE_DIR / "eval.md"
    dest.write_text("\n".join(out), encoding="utf-8")
    print(f"\nСохранено: {dest}")


if __name__ == "__main__":
    main()
