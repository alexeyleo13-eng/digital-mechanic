"""Клиенты внешних сервисов по контракту раздела 5 ТЗ. Адреса — из .env."""
import httpx

from .config import HTTP_TIMEOUT, MOCK_ML_URL, MOCK_TOIR_URL


class ServiceError(Exception):
    def __init__(self, service: str, status: int | None, detail: str):
        super().__init__(f"{service}: {status} {detail}")
        self.service, self.status, self.detail = service, status, detail


def _post(service: str, url: str, payload: dict) -> dict:
    try:
        r = httpx.post(url, json=payload, timeout=HTTP_TIMEOUT)
    except httpx.HTTPError as e:
        raise ServiceError(service, None, f"сервис недоступен ({e.__class__.__name__})") from e
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise ServiceError(service, r.status_code, str(detail))
    return r.json()


def diagnose(vehicle_id: str, session_id: str) -> dict:
    return _post("ML", f"{MOCK_ML_URL}/diagnose", {"vehicle_id": vehicle_id, "session_id": session_id})


def create_ticket(ticket: dict) -> dict:
    return _post("ТОиР", f"{MOCK_TOIR_URL}/tickets", ticket)


def health() -> dict:
    out = {}
    for name, url in (("ml", MOCK_ML_URL), ("toir", MOCK_TOIR_URL)):
        try:
            out[name] = httpx.get(f"{url}/health", timeout=3).status_code == 200
        except httpx.HTTPError:
            out[name] = False
    return out
