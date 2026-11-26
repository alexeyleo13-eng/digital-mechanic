"""Проверка подключения LLM из .env: список моделей (для OpenAI-совместимых API) и пробный ход агента с вызовом инструмента.

python -m scripts.check_llm            # список моделей + пробный ход
python -m scripts.check_llm --models   # только список моделей
Нужны запущенные заглушки (python -m scripts.run_all) для пробного хода.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import agent, config  # noqa: E402
from app.tools import Conversation  # noqa: E402


def list_models() -> None:
    if config.LLM_PROVIDER == "anthropic":
        print("Для Anthropic список моделей не выводится; модель:", config.LLM_MODEL)
        return
    from openai import OpenAI
    client = OpenAI(api_key=config.LLM_API_KEY or "none", base_url=config.LLM_BASE_URL or None)
    ids = sorted(m.id.removeprefix("models/") for m in client.models.list())
    print(f"Доступно моделей: {len(ids)}")
    for i in ids:
        print(("→ " if i == config.LLM_MODEL else "  ") + i)
    if config.LLM_MODEL not in ids:
        print(f"\n!! LLM_MODEL={config.LLM_MODEL} нет в списке — укажите одну из моделей выше в .env")


def smoke() -> None:
    print(f"\nПробный ход: {config.LLM_PROVIDER} / {config.LLM_MODEL}")
    conv = Conversation(vehicle_id="M333YK47")
    tools_called = []
    for ev in agent.run_turn(conv, "Получить результат диагностики (проверка S-0005)"):
        if ev["type"] == "tool":
            tools_called.append(ev["name"])
            print(f"  [инструмент] {ev['name']} {ev.get('args') or ''}")
        elif ev["type"] == "delta":
            print(ev["text"], end="", flush=True)
        elif ev["type"] == "error":
            print(f"\n!! {ev['text']}")
    print("\n\nИтог:", "OK — агент вызвал инструменты" if "get_diagnosis" in tools_called else "агент не вызвал get_diagnosis")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if not config.llm_configured():
        sys.exit("LLM не настроена: заполните LLM_PROVIDER, LLM_MODEL и LLM_API_KEY в .env")
    list_models()
    if "--models" not in sys.argv:
        smoke()
