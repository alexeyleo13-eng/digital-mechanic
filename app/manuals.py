"""Реестр руководств базы знаний и их привязка к моделям двигателей.

`designation` — то, что уходит в поле `manual` заявки ТОиР (раздел 3 ТЗ).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Manual:
    id: str
    file: str
    designation: str
    title: str
    short: str
    kind: str  # "РЭ" — эксплуатация, "РК" — ремонт, "РЭ+РК" — совмещённое
    year: int
    engines: tuple[str, ...]
    note: str = ""


MANUALS: tuple[Manual, ...] = (
    Manual(
        id="yamz236_re",
        file="ЯМЗ_236_руководство по эксплуатации.pdf",
        designation="236-3902150-Б РЭ",
        title="Двигатели ЯМЗ-236М2, ЯМЗ-238М2. Руководство по эксплуатации",
        short="ЯМЗ-236 РЭ",
        kind="РЭ",
        year=2010,
        engines=("ЯМЗ-236М2",),
        note="Документ общий для ЯМЗ-236М2 и семейства ЯМЗ-238: берите значения только для ЯМЗ-236М2.",
    ),
    Manual(
        id="yamz236_rk",
        file="ЯМЗ_236_руководство по РЕМОНТУ.pdf",
        designation="236.01.01 РК",
        title="Двигатели ЯМЗ-236М2. Руководство по ремонту",
        short="ЯМЗ-236 РК",
        kind="РК",
        year=2014,
        engines=("ЯМЗ-236М2",),
    ),
    Manual(
        id="yamz536_re",
        file="yamz-536_re_2016.pdf",
        designation="536.3902150 РЭ",
        title="Двигатели ЯМЗ-536. Руководство по эксплуатации",
        short="ЯМЗ-536 РЭ",
        kind="РЭ",
        year=2016,
        engines=("ЯМЗ-536",),
    ),
    Manual(
        id="yamz536_rk",
        file="yamz-536_rk_2013.pdf",
        designation="536.01.02 РК",
        title="Двигатели ЯМЗ-536. Руководство по ремонту",
        short="ЯМЗ-536 РК",
        kind="РК",
        year=2013,
        engines=("ЯМЗ-536",),
        note="Печатная нумерация идёт заново в каждом разделе; ссылаемся только на страницу PDF.",
    ),
    Manual(
        id="zmz5234_rk",
        file="zmz-5234_rk_2025.pdf",
        designation="Двигатели ЗМЗ-5234.10 со сцеплением. Руководство по ремонту",
        title="Двигатели ЗМЗ-5234.10 со сцеплением. Руководство по ремонту (изд. 5)",
        short="ЗМЗ-5234 РК",
        kind="РК",
        year=2025,
        engines=("ЗМЗ-5234.10",),
    ),
    Manual(
        id="zmz409_rk",
        file="zmz-40904-40905_rk_2025.pdf",
        designation="Двигатели ЗМЗ-40904.10, ЗМЗ-40905.10. Руководство по эксплуатации, техническому обслуживанию и ремонту",
        title="Двигатели ЗМЗ-40904.10 и ЗМЗ-40905.10. Руководство по эксплуатации, ТО и ремонту (изд. 10)",
        short="ЗМЗ-409 РЭ/РК",
        kind="РЭ+РК",
        year=2025,
        engines=("ЗМЗ-40904.10", "ЗМЗ-40905.10"),
        note="Документ общий для ЗМЗ-40904.10 и ЗМЗ-40905.10: если значение различается, берите строку для двигателя ТС.",
    ),
)

BY_ID = {m.id: m for m in MANUALS}


def manuals_for_engine(engine_model: str) -> list[Manual]:
    return [m for m in MANUALS if engine_model in m.engines]
