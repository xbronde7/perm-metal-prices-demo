"""Public demand adapters. Plans and market surveys are not open tenders."""
from __future__ import annotations

import calendar
import io
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, unquote, urlparse

from bs4 import BeautifulSoup
import openpyxl

from .domain import identity, now_iso, number, parse_date, text
from .documents import links
from .procurement import MOSCOW, parse_listing

CATEGORIES = [
    ("Услуги и подрядные работы", r"оказани.*услуг|выполнени.*работ|строительств|ремонт|обслуживан|уборк|клининг|проектирован|охран[аыу]|перевозк|аренд"),
    ("Металл", r"металл|прокат|арматур|сталь|стальн|швеллер"),
    ("Кабель и электрика", r"кабел|провод|эл[.\s-]*техничес|электротех|электрон|трансформатор|светильник"),
    ("Стройматериалы", r"кирпич|бетон|цемент|инертн|песок|щебень|окон|окна|двер|блок|пиломат|доск|кровл|изоляц|лент.*(?:ФУМ|ПВХ)"),
    ("Оборудование и комплектующие", r"оборудован|комплект|запчаст|зип\b|насос|клапан|задвиж|фильтр|труб|вентиляц|отоплен|подшипник|креп[её]ж"),
    ("Химия и расходники", r"клей|краск|реагент|антикор|химич|смаз|isofol"),
    ("Товары для офиса", r"канцеляр|бумаг|мебел|компьютер|оргтехник"),
]


def category(title):
    return next((label for label,pattern in CATEGORIES if re.search(pattern,title,re.I)), "Прочее")


def record(id, title, url, feed, **fields):
    return {"id":id,"number":id,"title":text(title),"buyer":None,"region":feed["region"],
        "sourceUrl":url,"listingUrl":url,"feedUrl":feed["url"],"feedId":feed["id"],
        "sourceName":feed.get("name"),"noticeKind":"tender","category":category(title),
        "publishedAt":None,"deadline":None,"budget":None,"budgetVat":"unknown",
        "items":[],"documents":[],"checkedAt":now_iso(),"sourceStatus":"ok","issues":[],
        "paymentTerms":None,"deliveryTerms":None,**fields}


def synapse(blob, url, feed):
    soup=BeautifulSoup(blob,"html.parser");result=[]
    for card in soup.select(".sp-tender-block"):
        a=card.select_one(".sp-tb-title[href]")
        if not a:continue
        title=a.get_text(" ",strip=True)
        if re.search(r"продажа|реализация имущества|право заключения договора аренды",title,re.I):continue
        if feed.get("scope","materials")!="all" and category(title)=="Прочее":continue
        location=card.select_one('[itemprop="location"]')
        if not location or "Перм" not in location.get("content",""):continue
        target=urljoin(url,a["href"])
        parts=unquote(urlparse(target).path).split("/")
        ref=parts[-1].split("--")[0]
        buyer=card.select_one('.pro-pcr-grey-before a')
        price=card.select_one(".sp-tb-right-block > div:nth-of-type(3)")
        price_cells=price.find_all("div",recursive=False) if price else []
        budget=number(price_cells[-1].get_text().replace("₽","")) if price_cells else None
        source=card.select_one(".sp-tb-source")
        procedure=text(source.get_text(" ",strip=True)) if source else ""
        period=next((x.get_text(" ",strip=True) for x in card.select(".pro-pcr-desc") if "прием заявок" in x.get_text().lower()),"")
        dates=re.findall(r"(\d{2}:\d{2})\s*·\s*(\d{2}\.\d{2}\.\d{4})",period)
        published=parse_date(f"{dates[0][1]} {dates[0][0]}",MOSCOW) if dates else None
        deadline=parse_date(f"{dates[-1][1]} {dates[-1][0]}",MOSCOW) if len(dates)>1 else None
        status=card.select_one(".pro-pc-status")
        survey=bool(re.search(r"Запрос ценовой информации|Опрос рынка",procedure+title,re.I))
        result.append(record("syn-"+identity(parts[-2],ref),title,target,feed,number=ref,
            buyer=text(buyer.get_text()) if buyer else None,procedure=procedure,
            noticeKind="request" if survey else "tender",primarySourceStatus="link_unavailable",
            publishedAt=published.isoformat() if published else None,deadline=deadline.isoformat() if deadline else None,
            budget=budget if budget and budget>0 else None,
            reportedArchived=bool(status and re.search(r"заверш|отмен",status.get_text(),re.I)),
            evidenceLevel="public_card",issues=["Карточка агрегатора; срок и условия нужно сверить на площадке"] + (["Сбор ценовой информации: это не обязательство заключить договор"] if survey else [])))
        if deadline and deadline>datetime.now(timezone.utc)+timedelta(days=180):
            result[-1]["issues"].append("Срок больше 180 дней; проверьте год и актуальность на первичной площадке")
        if budget is not None and budget<=1:
            result[-1]["issues"].append("Опубликована цена 1 ₽ или меньше; проверьте, отражает ли она реальный бюджет или условный параметр")
    return result


MONTHS={"январ":1,"феврал":2,"март":3,"апрел":4,"май":5,"мая":5,"июн":6,"июл":7,"август":8,"сентябр":9,"сентяр":9,"октябр":10,"ноябр":11,"декабр":12}


def pzsp_plan(raw, url, feed, now=None):
    now=now or datetime.now(timezone.utc);result=[];obj=None
    book=openpyxl.load_workbook(io.BytesIO(raw),read_only=True,data_only=True)
    try:
        for sheet in book:
            for rownum,row in enumerate(sheet.values,1):
                if len(row)<4:continue
                if row[1] and rownum>2:obj=text(row[1])
                title,period=text(row[2]),text(row[3])
                year=re.search(r"20\d{2}",period)
                month=next((v for k,v in MONTHS.items() if k in period.lower()),None)
                if not title or not year or not month or not obj:continue
                start=datetime(int(year[0]),month,1,tzinfo=MOSCOW)
                end=start.replace(day=calendar.monthrange(start.year,start.month)[1],hour=23,minute=59)
                if end<now or start>now+timedelta(days=120):continue
                # A construction plan can reveal demand but does not establish a material order.
                result.append(record("pzsp-"+identity(obj,title,period),f"{title} · {obj}",url,feed,
                    buyer="АО «ПЗСП»",noticeKind="plan",category="Строительство / план",plannedPeriod=period,
                    plannedStart=start.isoformat(),plannedEnd=end.isoformat(),evidenceLevel="public_plan",
                    documents=[{"text":"План-график ПЗСП","url":url,"status":"plan","items":0}],
                    issues=["План выбора подрядчика. Приём заявок, объём материалов и бюджет не объявлены"],
                    evidence=f"{sheet.title}, строка {rownum}"))
    finally:book.close()
    return result


def public_details(blob,url,tender):
    """Only real links. A login button or masked contact is not public data."""
    soup=BeautifulSoup(blob,"html.parser")
    if tender.get("feedId","").startswith("synapse"):
        for node in soup.select(".tender-docs-line .tender-link"):
            name=text(node.get_text())
            if name and name!="Все документы процедуры" and not node.get("href"):
                tender["documents"].append({"text":name,"url":None,"status":"registration_required"})
        primary=next((a for a in soup.select("a[href]") if "перейти на площадку" in a.get_text().lower()),None)
        if primary:
            tender["sourceUrl"]=urljoin(url,primary["href"])
            tender["primarySourceStatus"]="found"
        tender["contactStatus"]="not_public" if "░" in soup.get_text() else "not_extracted"
    return tender


def collect_feed(fetcher,feed):
    raw,meta=fetcher.fetch(feed["url"]);adapter=feed.get("adapter","b2b")
    note=None
    if adapter=="synapse":
        rows=synapse(raw,feed["url"],feed)
        soup=BeautifulSoup(raw,"html.parser")
        pages=[urljoin(feed["url"],a["href"]) for a in soup.select('a[href]') if re.fullmatch(r"[2-9]",a.get_text(strip=True)) and "?page=" in a["href"]]
        for page in list(dict.fromkeys(pages))[:max(0,feed.get("pages",1)-1)]:
            page_raw,_=fetcher.fetch(page);rows+=synapse(page_raw,page,feed)
        note="Публичные карточки агрегатора. Документы и переход на площадку могут требовать входа."
    elif adapter=="pzsp":
        rows=[]
        plan=next((l for l in links(raw,feed["url"]) if "grafik-konkursnykh" in l["url"] and l["file"]),None)
        if plan:
            plan_raw,_=fetcher.fetch(plan["url"]);rows=pzsp_plan(plan_raw,plan["url"],feed)
        active=next((l for l in links(raw,feed["url"]) if "embed/tenders" in l["url"]),None)
        if active:
            active_raw,_=fetcher.fetch(active["url"])
            body=BeautifulSoup(active_raw,"html.parser").get_text(" ",strip=True)
            if re.search(r"найдено\s*:?\s*0 закупок",body,re.I):note="Действующих процедур: 0. Открыт план выбора подрядчиков."
            else:note="Открытый план; активный список требует отдельного адаптера."
    elif adapter=="supl":
        rows=supl(raw,feed["url"],feed)
        note="Запросы Перми. Заказы за последние сутки и контакты требуют входа; публикуются только видимые поля."
    else:
        rows=parse_listing(raw,feed["url"],feed["region"],feed.get("scope","materials"))
        for t in rows:t.update(feedId=feed["id"],sourceName=feed.get("name"),category=category(t["title"]),noticeKind="request" if re.search(r"опрос рынка|запрос ценовой информации",t["title"],re.I) else "tender",evidenceLevel="public_card")
        if not rows:raise ValueError("Нет распознанных закупок; источник требует проверки")
    return list({r["id"]:r for r in rows}.values()),meta,note


def supl(blob,url,feed):
    soup=BeautifulSoup(blob,"html.parser");result=[]
    for a in soup.select('h4 a[href*="/orders/"]'):
        card=a.find_parent("div",class_="a_2BCsImjE")
        if not card:continue
        title=a.get_text(" ",strip=True);body=card.get_text(" ",strip=True)
        if category(title)=="Прочее" and feed.get("scope","materials")!="all":continue
        delivery=next((x for x in card.select('a[href*="region"]') if "perm-region362" in x["href"]),None)
        if not delivery:continue # delivery, not the location of a possible supplier
        target=urljoin(url,a["href"]);ref=re.search(r"-(\d+)/?$",target)
        if not ref:continue
        dates=card.select("time[datetime]")
        deadline=parse_date(dates[-1]["datetime"]) if len(dates)>1 else None
        description=card.select_one("p")
        result.append(record("supl-"+ref[1],title,target,feed,number=ref[1],noticeKind="request",
            publishedAt=dates[0]["datetime"] if dates else None,deadline=deadline.isoformat() if deadline else None,
            description=text(description.get_text()) if description else None,
            reportedArchived=bool(re.search(r"Закрыт|Closed|Old order|Старый заказ",body,re.I)),
            contactStatus="registration_required",evidenceLevel="public_card",
            issues=["Контакты и свежие заказы требуют входа. Бюджетный диапазон не считается точной ценой закупки"]))
    return result
