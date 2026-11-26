"""Конфигурация из переменных окружения (.env). URL внешних сервисов меняются без правки кода (F6)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


MOCK_ML_URL = _env("MOCK_ML_URL", "http://127.0.0.1:8001").rstrip("/")
MOCK_TOIR_URL = _env("MOCK_TOIR_URL", "http://127.0.0.1:8002").rstrip("/")
HTTP_TIMEOUT = float(_env("HTTP_TIMEOUT", "15"))

# LLM: anthropic | gemini (Google AI Studio) | openai (любой OpenAI-совместимый endpoint: OpenRouter, YandexGPT, Ollama, vLLM)
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
LLM_PROVIDER = _env("LLM_PROVIDER", "anthropic").lower()
_DEFAULT_MODEL = {"anthropic": "claude-opus-5-5", "gemini": "gemini-3.8-flash"}
LLM_MODEL = _env("LLM_MODEL", _DEFAULT_MODEL.get(LLM_PROVIDER, ""))
LLM_API_KEY = _env("LLM_API_KEY") or (_env("GEMINI_API_KEY") if LLM_PROVIDER == "gemini" else "")
LLM_BASE_URL = _env("LLM_BASE_URL", GEMINI_BASE_URL if LLM_PROVIDER == "gemini" else "")
LLM_FALLBACK_MODELS = [m.strip() for m in _env(
    "LLM_FALLBACK_MODELS", "gemini-3.7-flash,gemini-3.5-flash-lite" if LLM_PROVIDER == "gemini" else "").split(",") if m.strip()]
LLM_EFFORT = _env("LLM_EFFORT", "medium")  # Anthropic: effort; Gemini/OpenAI: reasoning_effort (пусто — не передавать)
LLM_FALLBACKS = _env("LLM_FALLBACKS", "1") == "1"  # серверный fallback при отказе модели (Anthropic)
LLM_MAX_STEPS = int(_env("LLM_MAX_STEPS", "8"))

DATA_DIR = ROOT / "data"
VEHICLES_FILE = Path(_env("VEHICLES_FILE", str(DATA_DIR / "vehicles.json")))
FAULT_TYPES_FILE = DATA_DIR / "fault_types.json"
INDEX_FILE = DATA_DIR / "index" / "chunks.jsonl"
MANUALS_DIR = DATA_DIR / "manuals"
STATE_DIR = DATA_DIR / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)


def llm_configured() -> bool:
    if LLM_PROVIDER == "anthropic":
        return bool(LLM_API_KEY or os.environ.get("ANTHROPIC_API_KEY"))
    if LLM_PROVIDER == "gemini":
        return bool(LLM_MODEL and LLM_API_KEY)
    return bool(LLM_MODEL and (LLM_API_KEY or LLM_BASE_URL))
