"""Download public Perm metal price files and build an auditable snapshot.

Run: python -m pip install -r scripts/requirements.txt
     python scripts/collect_prices.py
"""
from __future__ import annotations

import csv
import html
import io
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
import openpyxl
import xlrd


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "public" / "data" / "snapshot.json"
SOURCES = [
    {"id": "meg", "supplier": "МЭГ", "url": "https://prm.metexpro.ru/price/prm.csv", "landing": "https://prm.metexpro.ru/price/", "format": "CSV", "terms": "Ориентир; цена от 5 т, без доставки"},
    {"id": "metallinvest", "supplier": "Металлинвест Пермь", "url": "https://perm.m-invest.ru/upload/iblock/9c6/40ovxuilhwgttm5ue48e4122b3xb9zg7.xlsx", "landing": "https://perm.m-invest.ru/", "format": "XLSX", "terms": "Прайс; условия и наличие уточняются"},
    {"id": "stroy", "supplier": "Стройтехцентр", "url": "https://stroytehcenter.ru/price/price.xls", "landing": "https://stroytehcenter.ru/prajs-list/", "format": "XLS", "terms": "Цена с НДС от 5 т; марка арматуры не указана"},
    {"id": "permmetall", "supplier": "Пермметалл", "url": "https://mc.ru/prices/filials/price_perm.xls", "landing": "https://permmetall.ru/price/", "format": "XLS", "terms": "Цена от 5 т; часть строк объединяет А400 и А500"},
    {"id": "metwell", "supplier": "МетВэл Пермь", "url": "https://perm.metwell.ru/sitemap-products-3.xml", "landing": "https://perm.metwell.ru/", "format": "HTML", "terms": "Публичные карточки товаров; цена «от», условия сделки уточняются"},
    {"id": "mettrans", "supplier": "МетТрансТерминал Пермь", "url": "https://perm.met-trans.ru/sitemap.category.xml", "landing": "https://perm.met-trans.ru/stal/armatura-a500s", "format": "HTML", "terms": "Публичный каталог; дата цены и местный остаток не подтверждены"},
    {"id": "metallnp", "supplier": "МеталлНефтеПроект Пермь", "url": "https://perm.metallnp.ru/catalog/armatura_stalnaya/", "landing": "https://perm.metallnp.ru/", "format": "HTML", "terms": "Публичная таблица арматуры; дата цены и наличие не подтверждены"},
]

SUPPLIERS = [
    {"id":"meg","name":"МЭГ","location":"Пермь — региональный прайс","website":"https://prm.metexpro.ru/price/","kind":"Региональный поставщик","source":"public_file","note":"Наличие на местном складе требует подтверждения"},
    {"id":"metallinvest","name":"Металлинвест Пермь","location":"Пермь, Луначарского, 3/2","website":"https://perm.m-invest.ru/","kind":"Пермский филиал","source":"public_file","note":"Офис указан на сайте; склад и наличие по позиции уточняются"},
    {"id":"stroy","name":"Стройтехцентр","location":"Пермь, Красина, 38","website":"https://stroytehcenter.ru/prajs-list/","kind":"Металлобаза","source":"public_file","note":"В файле есть отдельные оптовые цены от 5 т"},
    {"id":"permmetall","name":"Пермметалл","location":"Пермь, Героев Хасана, 92","website":"https://permmetall.ru/price/","kind":"Металлобаза","source":"public_file","note":"Открытый прайс через mc.ru"},
    {"id":"metwell","name":"МетВэл Пермь","location":"Пермь — карточки с заявленным местным складом","website":"https://perm.metwell.ru/","kind":"Онлайн-каталог","source":"public_html","note":"Публичные цены «от»; наличие и условия требуют подтверждения"},
    {"id":"mettrans","name":"МетТрансТерминал Пермь","location":"Пермь — региональный каталог","website":"https://perm.met-trans.ru/","kind":"Онлайн-каталог","source":"public_html","note":"Дата цены и местный склад не подтверждены"},
    {"id":"metallnp","name":"МеталлНефтеПроект Пермь","location":"Пермь — региональный каталог","website":"https://perm.metallnp.ru/","kind":"Онлайн-каталог","source":"public_html","note":"Публичная таблица цен; дата и наличие требуют подтверждения"},
    {"id":"uvm","name":"УВМ-Сталь","location":"Пермь","website":"https://perm.uvm-steel.ru/company/price_lists/","kind":"Региональный поставщик","source":"to_verify","note":"Прайс указан на сайте; прямой файл ещё не разобран"},
    {"id":"luch","name":"ГК Луч","location":"Пермь, Фоминская, 50","website":"https://perm.luchsteel.ru/","kind":"Склад и производство","source":"to_verify","note":"Есть ссылка «Скачать прайс»; прямой файл ещё не подтверждён"},
    {"id":"platan","name":"Платан","location":"Пермь, Дзержинского, 61","website":"https://platan.perm.ru/","kind":"Металлобаза","source":"stale","note":"Открытый файл найден, но его имя датировано 2022 годом"},
    {"id":"uralprokat","name":"Уралпрокат","location":"Пермь","website":"https://metallperm59.ru/prajs-list/","kind":"Металлобаза","source":"request","note":"На сайте ориентиры «от»; полный прайс предлагается запросить"},
    {"id":"bvb","name":"БВБ-Альянс","location":"Пермь — представительство","website":"https://perm.bvb-alyans.ru/","kind":"Федеральный поставщик","source":"request","note":"Открытый машиночитаемый прайс не подтверждён"},
    {"id":"mechel","name":"Мечел-Сервис","location":"Пермский край","website":"https://www.rspm.ru/ru/members/map/?r=54","kind":"Отраслевой реестр","source":"discovery","note":"Филиал найден в РСПМ; актуальность и прайс требуют проверки"},
    {"id":"spk","name":"Сталепромышленная компания","location":"Пермь / Березники / Чайковский","website":"https://www.rspm.ru/ru/members/map/?r=54","kind":"Отраслевой реестр","source":"discovery","note":"Филиалы найдены в РСПМ; актуальность и прайс требуют проверки"},
]


def number(value):
    try:
        value = float(str(value).replace(" ", "").replace(",", "."))
        return round(value) if 1_000 <= value <= 10_000_000 else None
    except (TypeError, ValueError):
        return None


def diameter(text):
    match = re.search(r"(?<!\d)(\d{1,2})(?:[.,]0+)?(?!\d)", str(text))
    return int(match.group(1)) if match else None


STANDARD_RE = re.compile(r"\bГОСТ(?:\s+Р)?\s*\d{4,5}[-–]\d{2,4}\b", re.I)
CLASS_RE = re.compile(r"(?<!\w)[АA][24568]00[СC]?(?!\w)", re.I)
STEEL_RE = re.compile(r"(?<!\w)(?:25Г2С|35ГС|09Г2С|Ст\.?\s*3(?:сп|пс|кп)?)(?!\w)", re.I)


def standard_of(*parts):
    for part in parts:
        match = STANDARD_RE.search(str(part or ""))
        if match:
            return re.sub(r"\s+", " ", match.group(0).upper().replace("–", "-"))
    return None


def class_of(name):
    match = CLASS_RE.search(str(name))
    return match.group(0).upper().replace("A", "А").replace("C", "С") if match else None


def steel_of(*parts):
    for part in parts:
        match = STEEL_RE.search(str(part or ""))
        if match:
            return re.sub(r"\s+", "", match.group(0)).replace("Ст.", "Ст")
    return None


def add(offers, supplier, name, price, category, grade=None, size=None, comparable=False, terms=None, standard=None, steel_grade=None, source_url=None, price_date=None):
    if price is not None:
        offers.append({"supplier":supplier,"name":str(name).strip(),"price":price,"unit":"₽/т","category":category,"grade":grade,"size":size,"comparable":comparable,"terms":terms,"standard":standard,"steelGrade":steel_grade,"sourceUrl":source_url,"priceDate":price_date})


def parse_meg(blob, offers):
    lines = list(csv.reader(io.StringIO(blob.decode("utf-8-sig")), delimiter=";"))
    header = lines[0][0]
    for row in lines[2:]:
        if len(row) < 9:
            continue
        section, category, name, size, grade, gost, unit, raw_price, stock = row[:9]
        if unit.lower() != "т":
            continue
        d = diameter(size) if category == "Арматура" else None
        exact = category == "Арматура" and "А500С" in name.upper() and d is not None
        add(offers, "meg", name, number(raw_price), category, class_of(name), str(d) if d else size or None, exact, "от 5 т; ориентир", standard_of(gost, name), steel_of(grade, name), SOURCES[0]["url"])
        if offers and offers[-1]["name"] == name:
            # Stock may be an availability label; only numeric balances carry quantity.
            try:
                balance = float(str(stock).replace(" ", "").replace(",", "."))
                if balance >= 0:
                    offers[-1].update(stockQuantity=balance, stockUnit="т", stockStatus="reported")
            except (TypeError, ValueError):
                pass
    return re.search(r"\d{2}\.\d{2}\.\d{4}", header).group(0) if re.search(r"\d{2}\.\d{2}\.\d{4}", header) else None


def parse_metallinvest(blob, offers):
    book = openpyxl.load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    price_date = None
    for sheet in list(book)[1:]:
        for row in sheet.values:
            name, _, raw_price = (list(row) + [None] * 3)[:3]
            if not isinstance(name, str):
                continue
            if not price_date and name.startswith("Действует с "):
                price_date = name.removeprefix("Действует с ").rstrip(".")
            d = diameter(name.replace("Арматура", "")) if name.startswith("Арматура ") else None
            exact = bool(d and re.search(r"\bА500С\b", name.upper()))
            add(offers, "metallinvest", name, number(raw_price), sheet.title, class_of(name), str(d) if d else None, exact, "прайс; наличие уточняется", standard_of(name), steel_of(name), SOURCES[1]["url"])
    return price_date


def parse_stroy(blob, offers):
    sheet = xlrd.open_workbook(file_contents=blob).sheet_by_name("для ЧЛ")
    category = ""
    for i in range(9, sheet.nrows):
        row = sheet.row_values(i)
        name, size, price = row[1], row[2], number(row[4])
        if isinstance(name, str) and name.strip() and not size and not price:
            category = name.strip()
        if category == "Арматура" and price and isinstance(name, (int, float)):
            add(offers,"stroy",f"Арматура {int(name)}",price,"Арматура",None,str(int(name)),False,"от 5 т; марка не указана",source_url=SOURCES[2]["url"])
        elif price and isinstance(name, str) and name.strip() and category not in ("", "Арматура"):
            add(offers,"stroy",f"{category} {name.strip()} {str(size).strip()}",price,category,None,str(size).strip() or None,False,"от 5 т",standard=standard_of(category,name),steel_grade=steel_of(name),source_url=SOURCES[2]["url"])
    return None


def parse_permmetall(blob, offers):
    sheet = xlrd.open_workbook(file_contents=blob).sheet_by_index(0)
    date = None
    for i in range(min(sheet.nrows, 4)):
        line = " ".join(map(str, sheet.row_values(i)))
        found = re.search(r"\d{2}\.\d{2}\.\d{4}", line)
        if found:
            date = found.group(0)
    category = "Арматура"
    for i in range(6, sheet.nrows):
        row = sheet.row_values(i)
        name, size, unit, raw_price = row[:4]
        if isinstance(name, str) and name.isupper() and all(not str(v).strip() for v in row[1:4]):
            category = name.title()
        price = number(raw_price)
        if not price or str(unit).strip().lower() != "т" or not str(name).strip():
            continue
        if category == "Арматура":
            ds = [int(x) for x in re.findall(r"\d{1,2}", str(size)) if 5 <= int(x) <= 40]
            for d in ds:
                add(offers,"permmetall",f"{name} Ø{d}",price,category,class_of(name),str(d),False,"от 5 т; смешанная марка",standard=standard_of(category,name),steel_grade=steel_of(name),source_url=SOURCES[3]["url"])
        else:
            add(offers,"permmetall",f"{name} {size}",price,category,None,str(size),False,"от 5 т",standard=standard_of(category,name),steel_grade=steel_of(name),source_url=SOURCES[3]["url"])
    return date


def parse_metwell(session, sitemap, offers):
    """Sample public A500C cards in the Perm sitemap, keeping every product URL."""
    urls = re.findall(r"<loc>(.*?)</loc>", sitemap.decode("utf-8"))
    candidates = [html.unescape(u) for u in urls if "armatura-" in u and "a500s" in u]
    # Limit network load: one representative per (diameter, steel grade, standard).
    selected = {}
    for url in candidates:
        slug = url.rstrip("/").split("/")[-1]
        size = re.search(r"-(\d{1,2})-mm-", slug)
        steel = re.search(r"-(st3sp|st3ps|35gs|25g2s)-", slug)
        if size and steel:
            selected.setdefault((size.group(1), steel.group(1)), url)
    price_dates = []
    for url in list(selected.values())[:28]:
        try:
            response = session.get(url, timeout=15)
            response.raise_for_status()
            page = response.text
            title = re.search(r'<h1[^>]*itemprop="name"[^>]*>(.*?)</h1>', page, re.I | re.S)
            price = re.search(r'<meta\s+itemprop="price"\s+content="([^"]+)"', page, re.I)
            updated = re.search(r"Цена обновлена\s*(\d{2}\.\d{2}\.\d{4})", page)
            stock = re.search(r"В наличии на складе:\s*([^<]+)", page)
            if not title or not price or not stock or "Пермь" not in stock.group(1):
                continue
            name = html.unescape(re.sub(r"<[^>]+>", "", title.group(1))).strip()
            size = re.search(r"\b(\d{1,2})\s*мм\b", name)
            price_value = number(price.group(1).replace("\xa0", ""))
            if not size or not price_value or "₽ за т" not in page[:3000]:
                continue
            add(offers,"metwell",name,price_value,"Арматура",class_of(name),size.group(1),False,"цена от; склад Пермь указан на сайте",standard_of(name),steel_of(name),url,updated.group(1) if updated else None)
            if updated:
                price_dates.append(updated.group(1))
            time.sleep(0.2)
        except requests.RequestException:
            continue
    return max(price_dates, default=None)


def parse_mettrans(session, sitemap, offers):
    """Read public HTML catalog pages; never equate vague catalog titles with certified stock."""
    urls = re.findall(r"<loc>(.*?)</loc>", sitemap.decode("utf-8"))
    pages = [u for u in urls if re.search(r"/(?:stal/armatura-5-mm|armatura/armatura-(?:6|8|10|12|14|16|20)-mm)$", u)]
    seen = set()
    for url in pages:
        try:
            response = session.get(url, timeout=15)
            response.raise_for_status()
            page = response.text
            for match in re.finditer(r"products:\s*\[", page):
                try:
                    products, _ = json.JSONDecoder().raw_decode(page[match.end()-1:])
                except json.JSONDecodeError:
                    continue
                for row in products:
                    name = row.get("name", "")
                    description = row.get("description", "")
                    price = number(row.get("price"))
                    if "арматура стальная" not in name.lower() and not re.search(r"\b[АA]500[СC]\b", name, re.I):
                        continue
                    if "за тонну" not in description.lower() or not price:
                        continue
                    key = row.get("id")
                    if key in seen:
                        continue
                    seen.add(key)
                    size = re.search(r"\b(\d{1,2})\s*мм\b", name)
                    add(offers,"mettrans",name,price,"Арматура",class_of(name),size.group(1) if size else None,False,"публичный каталог; дата и склад не подтверждены",standard_of(name,description),steel_of(name,description),url)
            time.sleep(0.2)
        except requests.RequestException:
            continue
    return None


def parse_metallnp(blob, offers):
    page = blob.decode("utf-8", errors="replace")
    table = re.search(r'<table\s+class="cattable".*?</table>', page, re.I | re.S)
    if not table:
        raise ValueError("Armature price table was not found")
    for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>', table.group(0), re.I | re.S):
        cells = re.findall(r'<td\b[^>]*>(.*?)</td>', row, re.I | re.S)
        if len(cells) < 5:
            continue
        values = [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", cell)).split()) for cell in cells]
        name, size, characteristics, raw_steel, raw_price = values[:5]
        if "руб./тн" not in raw_price:
            continue
        amount = re.search(r"[\d\s]+", raw_price)
        price = number(amount.group(0)) if amount else None
        link = re.search(r'href="([^"]+)"', cells[0])
        source_url = urljoin(SOURCES[-1]["url"], link.group(1)) if link else SOURCES[-1]["url"]
        steel = steel_of(raw_steel, name) or raw_steel or None
        add(offers,"metallnp",f"{name} · {characteristics}",price,"Арматура",class_of(characteristics),size,False,"каталог; дата цены и наличие не подтверждены",standard_of(characteristics,name),steel,source_url)
    return None


def comparisons_for(offers, previous=None):
    groups = {}
    for offer in offers:
        if offer.get("comparable") and offer.get("standard") == "ГОСТ 34028-2016":
            groups.setdefault(offer["size"], []).append(offer)
    comparisons = []
    prior_pairs = {x["key"]: x for x in (previous or [])}
    for size, group in sorted(groups.items(), key=lambda x: int(x[0])):
        if len({x["supplier"] for x in group}) < 2:
            continue
        best = min(x["price"] for x in group)
        comparison = {"key":f"rebar-a500c-{size}","name":f"Арматура А500С Ø{size}","category":"Арматура","size":size,"standard":"ГОСТ 34028-2016","status":"needs_spec","reason":"Совпадают класс, диаметр и ГОСТ; марка стали, длина, партия и доставка могут различаться.","offers":[{"supplier":x["supplier"],"price":x["price"],"name":x["name"],"terms":x["terms"],"standard":x["standard"],"steelGrade":x["steelGrade"],"sourceUrl":x["sourceUrl"],"delta":x["price"]-best} for x in sorted(group,key=lambda o:o["price"])]}
        prior = prior_pairs.get(comparison["key"])
        if prior and prior.get("jev") and sorted((x["supplier"], x["name"], x.get("standard")) for x in prior["offers"]) == sorted((x["supplier"], x["name"], x.get("standard")) for x in comparison["offers"]):
            comparison["jev"] = prior["jev"]
        comparisons.append(comparison)
    return comparisons


def main():
    session = requests.Session()
    session.headers["User-Agent"] = "PermMetalMonitor/0.1 (+price file validation)"
    offers, sources = [], []
    parsers = {"meg":parse_meg,"metallinvest":parse_metallinvest,"stroy":parse_stroy,"permmetall":parse_permmetall}
    for source in SOURCES:
        item = dict(source)
        try:
            response = session.get(source["url"], timeout=25)
            response.raise_for_status()
            if len(response.content) > 10_000_000:
                raise ValueError("File exceeds 10 MB limit")
            before = len(offers)
            if source["id"] == "metwell":
                item["priceDate"] = parse_metwell(session, response.content, offers)
            elif source["id"] == "mettrans":
                item["priceDate"] = parse_mettrans(session, response.content, offers)
            elif source["id"] == "metallnp":
                item["priceDate"] = parse_metallnp(response.content, offers)
            else:
                item["priceDate"] = parsers[source["id"]](response.content, offers)
            item["rows"] = len(offers) - before
            item["status"] = "ok" if item["rows"] else "empty"
            item["checkedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            item["httpLastModified"] = response.headers.get("Last-Modified")
        except Exception as exc:
            item.update(status="error", error=str(exc)[:160], rows=0)
        sources.append(item)
    # Only identical stated class, diameter, standard, and unit are shown side by side.
    # Missing steel grade, length or terms are explicitly marked as unverified.
    groups = {}
    for offer in offers:
        if offer["comparable"] and offer["standard"] == "ГОСТ 34028-2016":
            groups.setdefault(offer["size"], []).append(offer)
    comparisons = []
    for size, group in sorted(groups.items(), key=lambda x: int(x[0])):
        if len({x["supplier"] for x in group}) < 2:
            continue
        best = min(x["price"] for x in group)
        comparisons.append({"key":f"rebar-a500c-{size}","name":f"Арматура А500С Ø{size}","category":"Арматура","size":size,"standard":"ГОСТ 34028-2016","status":"needs_spec","reason":"Совпадают класс, диаметр и ГОСТ; марка стали, длина, партия и доставка могут различаться.","offers":[{"supplier":x["supplier"],"price":x["price"],"name":x["name"],"terms":x["terms"],"standard":x["standard"],"steelGrade":x["steelGrade"],"sourceUrl":x["sourceUrl"],"delta":x["price"]-best} for x in sorted(group,key=lambda o:o["price"])]})
    if OUT.exists():
        previous = json.loads(OUT.read_text(encoding="utf-8"))
        prior_pairs = {x["key"]: x for x in previous.get("comparisons", [])}
        for comparison in comparisons:
            prior = prior_pairs.get(comparison["key"])
            if prior and prior.get("jev") and sorted((x["supplier"], x["name"], x.get("standard")) for x in prior["offers"]) == sorted((x["supplier"], x["name"], x.get("standard")) for x in comparison["offers"]):
                comparison["jev"] = prior["jev"]
    snapshot = {"generatedAt":datetime.now(timezone.utc).isoformat(timespec="seconds"),"scope":"Пермский край: открытые цены; полнота сайтов, наличие и доставка не подтверждены","suppliers":SUPPLIERS,"sources":sources,"offerCount":len(offers),"offers":offers,"comparisons":comparisons}
    if comparisons and all(x.get("jev") for x in comparisons):
        snapshot["jevAudit"] = {"model":"jev-1.13.0","checkedAt":max(x["jev"]["checkedAt"] for x in comparisons),"pairs":len(comparisons)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(snapshot,ensure_ascii=False,separators=(",",":")), encoding="utf-8")
    print(f"Saved {OUT}: {len(offers)} offers, {len(comparisons)} same-core comparisons requiring specification")
    for source in sources:
        print(source["supplier"],source["status"],source["rows"],source.get("priceDate") or source.get("httpLastModified"))


if __name__ == "__main__":
    main()
