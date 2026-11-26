"""Проверка ответа на выдуманные нормативы (без LLM и заглушек)."""
from app.verify import unverified_values


def test_invented_value_is_flagged():
    answer = "Прогиб ремня ориентировочно 10–15 мм. Затяжка **421–461 Н·м (43–47 кгс·м)** [[yamz236_rk:41]]."
    assert unverified_values(answer, {("yamz236_rk", 41)}, None) == ["10–15 мм"]


def test_values_from_pages_pass_in_any_notation():
    answer = "Свечи **20,0…30,0 Н·м (2,1…3,1 кгс·м)** [[zmz409_rk:189]]"
    assert unverified_values(answer, {("zmz409_rk", 189)}, None) == []


def test_without_retrieved_pages_every_value_is_flagged():
    assert unverified_values("Давление 5 кгс/см²", set(), None) == ["5 кгс/см²"]
