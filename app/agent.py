"""Агентный цикл. LLM сама выбирает инструменты; бэкенд LLM меняется через .env.

Каждый ход — генератор событий для интерфейса:
  {"type": "status", "text"} · {"type": "tool", "name", "args"} · {"type": "card", "kind", "data"}
  {"type": "delta", "text"} · {"type": "error", "text"} · {"type": "done"}
"""
import json
from collections.abc import Iterator

from . import config
from .prompt import SYSTEM_PROMPT, vehicle_context
from .tools import TOOLS, Conversation, ToolError, execute

TOOL_LABELS = {
    "get_diagnosis": "Запрашиваю результат проверки в НейроДВС",
    "search_manuals": "Ищу в руководстве",
    "read_manual_page": "Читаю страницу руководства",
    "get_vehicle_history": "Смотрю историю проверок",
    "prepare_ticket": "Готовлю заявку на дефектовку",
    "submit_ticket": "Отправляю заявку в ТОиР",
}


def _run_tool(conv: Conversation, name: str, args: dict) -> Iterator[dict]:
    """Выполняет инструмент, отдаёт события; результат для модели кладёт в последний элемент."""
    yield {"type": "tool", "name": name, "label": TOOL_LABELS.get(name, name), "args": args}
    try:
        result, cards = execute(conv, name, args)
        for c in cards:
            yield {"type": "card", **c}
        yield {"type": "_result", "content": json.dumps(result, ensure_ascii=False), "is_error": False}
    except ToolError as e:
        yield {"type": "_result", "content": f"Ошибка: {e}", "is_error": True}


def _user_text(conv: Conversation, text: str) -> str:
    if conv.pending_notes:
        notes = "\n".join(conv.pending_notes)
        conv.pending_notes = []
        return f"[Система: {notes}]\n\n{text}"
    return text


class AnthropicBackend:
    """Claude через официальный SDK; ручной цикл со стримингом текста."""

    def __init__(self):
        import anthropic
        self.anthropic = anthropic
        self.client = anthropic.Anthropic(
            api_key=config.LLM_API_KEY or None,
            base_url=config.LLM_BASE_URL or "https://api.anthropic.com",
        )
        self.tools = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in TOOLS]

    def run(self, conv: Conversation, text: str) -> Iterator[dict]:
        if conv.backend_state is None:
            conv.backend_state = []
        messages: list = conv.backend_state
        messages.append({"role": "user", "content": _user_text(conv, text)})
        system = [{"type": "text", "text": SYSTEM_PROMPT},
                  {"type": "text", "text": vehicle_context(conv.vehicle_id), "cache_control": {"type": "ephemeral"}}]
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"} if config.LLM_FALLBACKS else {}
        for _ in range(config.LLM_MAX_STEPS):
            try:
                with self.client.beta.messages.stream(
                    model=config.LLM_MODEL,
                    max_tokens=16000,
                    system=system,
                    tools=self.tools,
                    messages=messages,
                    output_config={"effort": config.LLM_EFFORT},
                    **extra,
                ) as stream:
                    for event in stream:
                        if event.type == "text":
                            yield {"type": "delta", "text": event.text}
                    response = stream.get_final_message()
            except self.anthropic.APIStatusError as e:
                messages.pop() if messages and messages[-1]["role"] == "user" and not _has_tool_results(messages[-1]) else None
                yield {"type": "error", "text": f"LLM вернула ошибку {e.status_code}: {e.message}"}
                return
            except self.anthropic.APIConnectionError:
                yield {"type": "error", "text": "Нет связи с LLM. Проверьте сеть и LLM_BASE_URL."}
                return
            messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason == "refusal":
                yield {"type": "error", "text": "Модель отказалась отвечать на этот запрос. Переформулируйте вопрос."}
                return
            if response.stop_reason == "pause_turn":
                continue
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                if response.stop_reason == "max_tokens":
                    yield {"type": "delta", "text": "\n\n_Ответ обрезан по длине._"}
                return
            results = []
            for tu in tool_uses:
                res = None
                for ev in _run_tool(conv, tu.name, tu.input or {}):
                    if ev["type"] == "_result":
                        res = ev
                    else:
                        yield ev
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": res["content"], "is_error": res["is_error"]})
            messages.append({"role": "user", "content": results})
            yield {"type": "delta", "text": "\n\n"}
        yield {"type": "error", "text": "Агент сделал слишком много шагов. Уточните вопрос."}


def _has_tool_results(msg: dict) -> bool:
    return isinstance(msg.get("content"), list)


def _error_text(e) -> str:
    """Gemini отдаёт ошибку списком [{"error": {...}}], и SDK OpenAI не извлекает из него текст."""
    body = e.body[0] if isinstance(e.body, list) and e.body else e.body
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict) and err.get("message"):
            return err["message"]
    try:
        return e.response.text[:300]
    except Exception:
        return str(e)


_SCHEMA_DROP = {"additionalProperties", "minItems", "minimum"}


def _portable_schema(node):
    """Убирает ключи JSON Schema, которые поддерживают не все OpenAI-совместимые API (Gemini и др.).
    Те же ограничения всё равно проверяются в инструментах."""
    if isinstance(node, dict):
        return {k: _portable_schema(v) for k, v in node.items() if k not in _SCHEMA_DROP}
    if isinstance(node, list):
        return [_portable_schema(v) for v in node]
    return node


class OpenAICompatBackend:
    """Gemini (Google AI Studio) и любой OpenAI-совместимый endpoint (OpenRouter, YandexGPT, Ollama, vLLM)."""

    def __init__(self):
        from openai import OpenAI
        import openai
        self.openai = openai
        self.client = OpenAI(api_key=config.LLM_API_KEY or "none", base_url=config.LLM_BASE_URL or None)
        self.tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                        "parameters": _portable_schema(t["parameters"])}} for t in TOOLS]
        self.extra = {"reasoning_effort": config.LLM_EFFORT} if config.LLM_PROVIDER == "gemini" and config.LLM_EFFORT else {}

    def run(self, conv: Conversation, text: str) -> Iterator[dict]:
        if conv.backend_state is None:
            conv.backend_state = [{"role": "system", "content": SYSTEM_PROMPT + "\n\n" + vehicle_context(conv.vehicle_id)}]
        messages: list = conv.backend_state
        messages.append({"role": "user", "content": _user_text(conv, text)})
        for _ in range(config.LLM_MAX_STEPS):
            try:
                resp = self.client.chat.completions.create(model=config.LLM_MODEL, messages=messages, tools=self.tools,
                                                           tool_choice="auto", **self.extra)
            except self.openai.APIStatusError as e:
                yield {"type": "error", "text": f"LLM вернула ошибку {e.status_code}: {_error_text(e)}"}
                return
            except self.openai.APIConnectionError:
                yield {"type": "error", "text": "Нет связи с LLM. Проверьте сеть и LLM_BASE_URL."}
                return
            if not resp.choices:
                yield {"type": "error", "text": "LLM вернула пустой ответ. Повторите вопрос."}
                return
            msg = resp.choices[0].message
            entry = {"role": "assistant", "content": msg.content or (None if msg.tool_calls else "")}
            if msg.tool_calls:
                entry["tool_calls"] = [{"id": c.id, "type": "function",
                                        "function": {"name": c.function.name, "arguments": c.function.arguments}}
                                       for c in msg.tool_calls]
            messages.append(entry)
            if msg.content:
                yield {"type": "delta", "text": msg.content}
            if not msg.tool_calls:
                return
            for c in msg.tool_calls:
                try:
                    args = json.loads(c.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = None
                if not isinstance(args, dict):
                    messages.append({"role": "tool", "tool_call_id": c.id, "content": "Ошибка: аргументы не JSON-объект"})
                    continue
                res = None
                for ev in _run_tool(conv, c.function.name, args):
                    if ev["type"] == "_result":
                        res = ev
                    else:
                        yield ev
                messages.append({"role": "tool", "tool_call_id": c.id, "content": res["content"]})
            yield {"type": "delta", "text": "\n\n"}
        yield {"type": "error", "text": "Агент сделал слишком много шагов. Уточните вопрос."}


_backend = None


def backend():
    global _backend
    if _backend is None:
        if not config.llm_configured():
            from .offline import OfflineBackend
            _backend = OfflineBackend()
        elif config.LLM_PROVIDER == "anthropic":
            _backend = AnthropicBackend()
        else:
            _backend = OpenAICompatBackend()
    return _backend


def run_turn(conv: Conversation, text: str) -> Iterator[dict]:
    conv.last_user_text = text
    try:
        yield from backend().run(conv, text)
    except Exception as e:  # демо не должно падать
        yield {"type": "error", "text": f"Внутренняя ошибка агента: {e.__class__.__name__}: {e}"}
    yield {"type": "done"}
