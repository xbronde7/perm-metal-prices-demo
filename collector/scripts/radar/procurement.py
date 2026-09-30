from __future__ import annotations

import re
from datetime import timezone, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .documents import links, FILE_RE
from .domain import attributes, identity, now_iso, number, parse_date, text, unit

MOSCOW = timezone(timedelta(hours=3))
MATERIAL = re.compile(r"металл|прокат|арматур|труб|кабел|провод|креп[её]ж|кирпич|лист.*сталь",re.I)


def item(name, quantity, measure, url, index):
    return {"id":identity(url,index,name),"name":text(name),"quantity":quantity if quantity is not None and quantity > 0 else None,"unit":unit(measure),"attributes":attributes(name),"sourceUrl":url,"evidence":f"Позиция {index}","matches":[]}


def parse_listing(blob, url, region="Пермский край", scope="materials"):
    soup = BeautifulSoup(blob,"html.parser")
    result = []
    for card in soup.select('[itemtype="http://schema.org/Event"]'):
        meta = lambda k: (card.select_one(f'meta[itemprop="{k}"]').get("content") if card.select_one(f'meta[itemprop="{k}"]') else None)
        title, detail = meta("name"), meta("url")
        if not title or not detail or scope!="all" and not MATERIAL.search(title):
            continue
        tags = card.select_one(".card-item__tag-container")
        if tags and re.search(r"Продажа|Договор|Контракт",tags.get_text()):
            continue
        organizer = card.select_one(".card-item__organization-name")
        source = card.select_one(".card-item__about a[href]")
        deadline = parse_date(meta("endDate"),MOSCOW)
        budget = number(meta("price"))
        rows = []
        for i,row in enumerate(card.select("tr.card-table__position"),1):
            cells = row.find_all("td",recursive=False)
            if len(cells)<3:
                continue
            inner = cells[0].select_one(".tru-position-table tr")
            name_cells = inner.find_all("td",recursive=False) if inner else []
            name = name_cells[-1].get_text(" ",strip=True) if name_cells else cells[0].get_text(" ",strip=True)
            rows.append(item(name,number(cells[-2].get_text()),cells[-1].get_text(),detail,i))
        num = re.search(r"(?:[?&]regNumber=|/l)(\d+)",detail)
        result.append({"id":num[1] if num else identity(detail),"title":text(title),"buyer":text(organizer.get_text()) if organizer else None,"region":region,"sourceUrl":source["href"] if source else detail,"listingUrl":detail,"feedUrl":url,"publishedAt":meta("startDate"),"deadline":deadline.isoformat() if deadline else None,"budget":budget if budget and budget>0 else None,"budgetVat":"unknown","items":rows,"documents":[],"checkedAt":now_iso(),"sourceStatus":"ok","issues":[],"paymentTerms":None,"deliveryTerms":None})
    # The marketplace's live regional list uses a simpler table.
    if not result:
        for a in soup.select('a[href*="/tender-"]'):
            desc=a.select_one(".search-results-title-desc")
            kind=a.select_one(".search-results-title-type")
            procedure=kind.get_text(" ",strip=True) if kind else ""
            if "Объявление о продаже" in procedure:continue
            if desc:
                copy=BeautifulSoup(str(desc),"html.parser")
                for label in copy.select(".search-results-title-type"):label.decompose()
                title=copy.get_text(" ",strip=True)
            else:title = a.get_text(" ",strip=True)
            if scope!="all" and not MATERIAL.search(title):
                continue
            target = urljoin(url,a["href"])
            num = re.search(r"tender-(\d+)",target)
            if not num:
                continue
            row = a.find_parent("tr")
            cells=row.find_all("td",recursive=False) if row else []
            buyer=cells[1].get_text(" ",strip=True) if len(cells)>1 else None
            dates = re.findall(r"\d{2}\.\d{2}\.20\d{2}\s+\d{2}:\d{2}",row.get_text(" ") if row else "")
            deadline = parse_date(dates[-1],MOSCOW) if dates else None
            published=parse_date(dates[0],MOSCOW) if len(dates)>1 else None
            result.append({"id":num[1],"title":title,"buyer":buyer,"procedure":procedure,"region":region,"sourceUrl":target,"listingUrl":target,"feedUrl":url,"publishedAt":published.isoformat() if published else None,"deadline":deadline.isoformat() if deadline else None,"budget":None,"budgetVat":"unknown","items":[],"documents":[],"checkedAt":now_iso(),"sourceStatus":"ok","issues":[],"paymentTerms":None,"deliveryTerms":None})
    return list({r["id"]:r for r in result}.values())


def detail_fields(blob, url, tender):
    soup = BeautifulSoup(blob,"html.parser")
    h1=soup.select_one("h1")
    if h1 and not tender.get("title") and "market" in url:
        tender["title"]=text(h1.get_text())
    content = soup.get_text(" ",strip=True)
    for label,key in [("Условия оплаты","paymentTerms"),("Условия поставки","deliveryTerms"),("Адрес места поставки","deliveryAddress")]:
        match = re.search(re.escape(label)+r"[^:]{0,80}:?\s*(.{1,650}?)(?=Условия|Адрес|Место проведения|Дополнительн|Дата |Контакт|Порядок|$)",content,re.I)
        if match:
            tender[key] = text(match[1])
    if "Процедура находится в архиве" in content or "Итоги подведены" in content:
        tender["reportedArchived"] = True
    if "только субъекты малого" in content.lower():
        tender["smeOnly"] = True
    docs = []
    for link in links(blob,url):
        if link["file"] or (re.search(r"download|documentlink|attachment",link["url"],re.I) and re.search(r"ТЗ|техническ|спецификац|обоснован|документац",link["text"],re.I)):
            if re.search(r"политик|согласие|обработк.*данных|privacy",link["text"]+link["url"],re.I):
                continue
            docs.append({**link,"status":"found"})
    # Document names may be visible while download requires account/API access.
    for name in re.findall(r"[^<>\n]{0,120}\.(?:pdf|xlsx?|docx)\b",soup.get_text("\n",strip=True),re.I):
        name = text(name)
        if re.search(r"ТЗ|метал|кабел|спецификац|обоснован|документац",name,re.I) and not any(d["text"]==name for d in docs):
            docs.append({"text":name,"url":None,"status":"link_unavailable"})
    tender["documents"] = docs[:12]
    return tender


def merge_documents(existing, incoming):
    """An actual public URL replaces the same visible but un-downloadable filename."""
    docs=list(existing)
    for doc in incoming:
        match=next((i for i,d in enumerate(docs) if (doc.get("url") and doc.get("url")==d.get("url")) or (text(doc.get("text")) and text(doc.get("text"))==text(d.get("text")))),None)
        if match is None:docs.append(doc)
        elif doc.get("url") and not docs[match].get("url"):docs[match]=doc
    return docs[:12]
