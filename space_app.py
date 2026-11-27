"""Точка входа для стенда на Hugging Face Spaces (тип Gradio — бесплатный, без Docker).

Space запускает этот файл командой `python space_app.py`. Gradio не используется: файл поднимает
заглушки ML/ТОиР и приложение FastAPI на порту 7860, как `python -m scripts.run_all`.
Ключ LLM задаётся секретом Space `LLM_API_KEY`.
"""
import os
import threading

os.environ.setdefault("LLM_PROVIDER", "gemini")
os.environ.setdefault("HOST", "0.0.0.0")
os.environ.setdefault("PORT", "7860")
os.environ.setdefault("PYTHONUNBUFFERED", "1")


def _zero_gpu_startup() -> None:
    """Бесплатный Space с Gradio работает на ZeroGPU: при старте платформа ждёт хотя бы одну
    функцию @spaces.GPU и отчёт о запуске (Gradio шлёт его сам при launch). GPU приложению не нужен."""
    try:
        import spaces
    except ImportError:
        return

    @spaces.GPU
    def _placeholder() -> None:
        pass

    from spaces.zero import zerogpu_mode_enabled, zerogpu_startup
    if zerogpu_mode_enabled():
        zerogpu_startup()


def _fetch_manuals() -> None:
    # PDF нужны только для кнопки «Открыть PDF»; индекс уже в репозитории, поэтому ошибка не критична.
    try:
        from scripts import fetch_manuals
        fetch_manuals.main()
    except Exception as e:  # noqa: BLE001
        print(f"PDF не загружены: {e}", flush=True)


if __name__ == "__main__":
    _zero_gpu_startup()
    threading.Thread(target=_fetch_manuals, daemon=True).start()
    from scripts import run_all
    run_all.main()
