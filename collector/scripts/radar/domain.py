from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def identity(*parts):
    return hashlib.sha256("|".join(str(p or "") for p in parts).encode()).hexdigest()[:20]


def text(value):
    return " ".join(str(value or "").replace("\xa0", " ").split())


def number(value):
    if isinstance(value, (int, float)):
        return float(value) if value == value else None
    raw = text(value).replace(" ", "").replace(",", ".")
    return float(raw) if re.fullmatch(r"-?\d+(?:\.\d+)?", raw) else None


def unit(value):
    raw = text(value).lower().replace("₽/", "").replace("руб./", "").rstrip(".")
    aliases = {"т": "т", "тн": "т", "тонна": "т", "тонн": "т", "тонны": "т", "кг": "кг", "килограмм": "кг", "м": "м", "метр": "м", "метров": "м", "пог.м": "м", "пог м": "м", "км": "км", "шт": "шт", "штука": "шт", "штук": "шт", "тыс пог м": "тыс. м", "тыс. м": "тыс. м", "тыс.м": "тыс. м", "компл": "компл", "усл ед": "усл. ед"}
    return aliases.get(raw)


def convert(amount, from_unit, to_unit):
    """Only dimensional conversions. Never invent mass per piece/metre."""
    if amount is None or not from_unit or not to_unit:
        return None
    groups = {"т": ("mass", 1000), "кг": ("mass", 1), "м": ("length", 1), "км": ("length", 1000), "тыс. м": ("length", 1000), "шт": ("count", 1), "компл": ("set", 1)}
    a, b = groups.get(from_unit), groups.get(to_unit)
    if not a or not b or a[0] != b[0]:
        return None
    return amount * a[1] / b[1]


def parse_date(value, default_tz=timezone.utc):
    if not value:
        return None
    raw = str(value).strip()
    months={"января":"01","февраля":"02","марта":"03","апреля":"04","мая":"05","июня":"06","июля":"07","августа":"08","сентября":"09","октября":"10","ноября":"11","декабря":"12"}
    named=re.search(r"(\d{1,2})\s+("+"|".join(months)+r")\s+(\d{4})",raw.lower())
    if named:
        raw=f"{int(named[1]):02d}.{months[named[2]]}.{named[3]}"
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=default_tz) if not re.search(r"Z$|[+-]\d\d:\d\d$", raw) else datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw, fmt).replace(tzinfo=default_tz)
            except ValueError:
                pass
    return None


def standards(value):
    # A year abbreviation is a spelling normalization, not verification of a GOST's status.
    found = []
    for m in re.finditer(r"ГОСТ\s*(Р\s*)?(\d{3,6}(?:\.\d+)*)(?:\s*[-–]\s*(\d{2,4}))?\b", value, re.I):
        year = m[3]
        if year and len(year) == 2:
            year = ("19" if int(year) >= 40 else "20") + year
        found.append(f"ГОСТ {'Р ' if m[1] else ''}{m[2]}" + (f"-{year}" if year else ""))
    return sorted(set(found))


def attributes(name, category="", extra=None):
    extra = extra or {}
    raw = text(" ".join([name, category, str(extra.get("standard") or ""), str(extra.get("steelGrade") or ""), str(extra.get("grade") or "")])).upper()
    s = raw.replace("×", "Х").replace("*", "Х").replace("X", "Х").replace(",", ".")
    family = next((f for f, pat in [("трубопроводная арматура",r"ТРУБОПРОВОДН.*АРМАТУР|ЗАДВИЖ|КЛАПАН"), ("кабель", r"КАБЕЛ|ПРОВОД|ВВГ|АПВ|КГТП|МКЭШ|ПУГВ|КВКБШ"), ("арматура", r"АРМАТУР(?!А ТРУБОПРОВОД)|REBAR"), ("лист", r"ЛИСТ|ПРОКАТ ЛИСТОВ"), ("труба", r"ТРУБ"), ("уголок", r"УГОЛОК|УГОЛКИ"), ("круг", r"КРУГ|ПРУТК"), ("полоса", r"ПОЛОС"), ("швеллер", r"ШВЕЛЛЕР"), ("балка", r"ДВУТАВР|БАЛКА"), ("крепёж", r"БОЛТ|ГАЙК|ШАЙБ|САМОРЕЗ|ШПИЛЬК"), ("кирпич", r"КИРПИЧ") ] if re.search(pat, s)), None)
    grade = re.search(r"(?<!\w)[АA][24568]00[СC]?(?!\w)", s)
    steel_pattern = r"(?<!\w)(?:СТ\.?\s*)?(?:\d{2}Х\d{1,2}Н\d{1,2}(?:Т)?|\d{2}Г2С(?:-\d{1,2})?|35ГС|\d{2}Г|08ПС)(?!\w)|(?<!\w)СТ\.?\s*\d{1,2}(?:СП|ПС|КП)?(?!\w)"
    steel = re.search(steel_pattern, s)
    # Prefer explicitly supplied steel; a bare diameter must not become steel grade 20.
    steel_grade = text(extra.get("steelGrade")).upper().replace("СТ.", "СТ") or (steel[0].replace(" ", "").replace("СТ.", "СТ") if steel and not steel[0].isdigit() else None)
    if not steel_grade:
        m = re.search(r"(?:СТАЛЬ|СТ\.|/\s*)(20|45|40Х)\b", s)
        steel_grade = m[1] if m else None
    if steel_grade and steel_grade.startswith("СТ") and not re.fullmatch(r"СТ[0-6](?:СП|ПС|КП)?", steel_grade):
        steel_grade = steel_grade[2:]
    # 12Х18Н10Т is a steel grade, not dimensions 12 x 18.
    size_text = re.sub(steel_pattern, " ", s)
    dimension = re.search(r"\b(\d+(?:\.\d+)?)\s*Х\s*(\d+(?:\.\d+)?)(?:\s*Х\s*(\d+(?:\.\d+)?))?(?:\s*Х\s*(\d+(?:\.\d+)?))?", size_text)
    dims = [float(x) for x in dimension.groups() if x] if dimension else []
    if family=="лист" and len(dims)==3:
        dims=sorted(dims)
    diameter = None
    if family in {"арматура", "круг"}:
        m = re.search(r"(?:АРМАТУРА|КРУГ|Ø|ДИАМЕТР)\s*(?:[АA][24568]00[СC]?\s*)?(?:Ø\s*)?(\d+(?:\.\d+)?)(?=\s|ММ\b|$)", size_text)
        if m:
            diameter = float(m[1])
        elif (m := re.search(r"(?:[DДØ]\s*|ДИАМЕТР\s*)(\d+(?:\.\d+)?)\s*(?:ММ)?\b|(?<!\d)(\d+(?:\.\d+)?)\s*ММ\b", size_text)):
            diameter = float(m[1] or m[2])
        elif number(extra.get("size")) is not None:
            diameter = number(extra["size"])
    thickness = None
    if family == "лист":
        # Broad OKPD descriptions mention width >= 600 mm, never thickness 600.
        m = re.search(r"(?:ТОЛЩИН[А-Я]*\s*|ЛИСТ\s*(?:Г/К\s*|Х/К\s*)?)(\d+(?:\.\d+)?)\s*(?:ММ)?\b", size_text)
        if not m and not re.search(r"ШИРИН|НЕ МЕНЕЕ|ОКПД",s):
            m = re.search(r"(?<!\d)(\d+(?:\.\d+)?)\s*ММ\b", s)
        thickness = float(m[1]) if m else min(dims) if dims else number(extra.get("size"))
    brand = None
    voltage = None
    if family == "кабель":
        m = re.search(r"\b((?:А?ВВГ|АПВ[А-Я]+|КВКБШВ|КГ(?:ТП)?|МКЭШ|ПУГВ|ПВС|ШВВП)[А-Я()\-A-Z]*)(?:\s|\d|$)", s)
        brand = m[1].replace("(A)", "(А)") if m else None
        m = re.search(r"(\d+(?:\.\d+)?)\s*КВ\b", s)
        voltage = float(m[1]) if m else None
    manufacture = next((v for v, p in [("горячекатаный", r"ГОРЯЧЕКАТАН|Г/К"), ("холоднокатаный", r"ХОЛОДНОКАТАН|Х/К"), ("электросварной", r"ЭЛЕКТРОСВАР|Э/С"), ("бесшовный", r"БЕСШОВ"), ("профильный", r"ПРОФИЛЬН")] if re.search(p, s)), None)
    coating = "оцинкованный" if re.search(r"ОЦИНК|ЦИНК\b", s) else None
    special = re.search(r"\b(?:HARDOX|MAGSTRONG|STRENX|DILLIDUR)\s+[A-Z]*\d+[A-Z]*\b", raw)
    material_brand = re.sub(r"\s+", " ", special[0]) if special else None
    profile = re.search(r"(?:ШВЕЛЛЕР|ДВУТАВР|БАЛКА)\s*(?:СТ\d(?:СП|ПС|КП)?\s*)?(\d+(?:\.\d+)?[ПУАБШКС]?(?:\d+)?)\b", s) if family in {"швеллер", "балка"} else None
    return {"family": family, "standards": standards(raw), "grade": grade[0].replace("A", "А").replace("C", "С") if grade else None, "steelGrade": steel_grade, "materialBrand": material_brand, "profileSize": profile[1] if profile else None, "dimensions": dims, "diameter": diameter, "thickness": thickness, "brand": brand, "voltage": voltage, "manufacture": manufacture, "coating": coating}


def compare_specs(required, offered):
    """Missing requirements never become exact matches, even after an AI score."""
    if not required.get("family") or required["family"] != offered.get("family"):
        return {"status": "rejected", "conflicts": ["Разный или нераспознанный тип материала"], "missing": [], "score": 0}
    if required.get("materialBrand") and required["materialBrand"] != offered.get("materialBrand"):
        return {"status":"rejected","conflicts":["Не подтверждена специальная марка материала"],"missing":[],"score":0}
    if not any(required.get(k) for k in ("dimensions","diameter","thickness","brand","profileSize")):
        return {"status":"rejected","conflicts":["В заявке нет размера или марки; нужна спецификация"],"missing":[],"score":0}
    conflicts, missing, same = [], [], 0
    labels = {"grade":"класс", "steelGrade":"марка стали", "materialBrand":"специальная марка", "profileSize":"номер профиля", "diameter":"диаметр", "thickness":"толщина", "brand":"марка кабеля", "voltage":"напряжение", "manufacture":"исполнение", "coating":"покрытие"}
    for k, label in labels.items():
        a, b = required.get(k), offered.get(k)
        if a is not None:
            if b is None:
                missing.append(label)
            elif a != b:
                if k=="steelGrade" and re.fullmatch(r"СТ3(?:СП|ПС|КП)?",str(a)) and re.fullmatch(r"СТ3(?:СП|ПС|КП)?",str(b)) and (a=="СТ3" or b=="СТ3"):
                    missing.append("уточнить подмарку стали Ст3")
                else:
                    conflicts.append(label)
            else:
                same += 1
    a, b = required.get("dimensions") or [], offered.get("dimensions") or []
    if a:
        if not b:
            missing.append("размеры")
        elif a != b:
            conflicts.append("размеры")
        else:
            same += 2
    a, b = set(required.get("standards") or []), set(offered.get("standards") or [])
    if a:
        for standard in a:
            if standard in b:
                same+=2
                continue
            related=[v for v in b if v.split("-")[0]==standard.split("-")[0]]
            # Product and dimensional standards can coexist (e.g. 14637 / 19903).
            # Different stated years of the same standard require rejection.
            if related and "-" in standard and all("-" in v for v in related):
                conflicts.append("год ГОСТ")
            else:
                missing.append("подтвердить "+standard)
    if any("-" not in v for v in a | b):
        missing.append("год ГОСТ не указан")
    critical = {"арматура":["diameter","grade"], "лист":["thickness"] + ([] if required.get("materialBrand") else ["steelGrade"]), "труба":["dimensions"], "кабель":["brand","dimensions","voltage"], "круг":["diameter","steelGrade"], "уголок":["dimensions"], "полоса":["dimensions"], "швеллер":["profileSize"], "балка":["profileSize"], "крепёж":["dimensions"], "кирпич":["dimensions"]}.get(required["family"], [])
    for k in critical:
        if not required.get(k):
            missing.append(f"в заявке не указано: {labels.get(k, 'размеры')}")
    if not a:
        missing.append("в заявке не указан ГОСТ")
    return {"status":"rejected" if conflicts else "specified" if not missing else "candidate", "conflicts":conflicts, "missing":list(dict.fromkeys(missing)), "score":same}
