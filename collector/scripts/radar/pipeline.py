from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse, urljoin, unquote

from bs4 import BeautifulSoup
import xlrd

from .documents import extract, links, DATE_RE, FILE_RE, vat_status
from .domain import attributes, compare_specs, convert, identity, now_iso, number, parse_date, text, unit
from .jev import Jev
from .procurement import parse_listing, detail_fields, merge_documents
from .demand import collect_feed, public_details
from .transport import Fetcher, error_message


def atomic_json(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",dir=path.parent,delete=False) as f:
        json.dump(data,f,ensure_ascii=False,separators=(",",":"),allow_nan=False)
        temp=f.name
    os.replace(temp,path)


class Store:
    def __init__(self,path):
        path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS tenders (id TEXT PRIMARY KEY, payload TEXT NOT NULL, last_seen TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS offers (id TEXT PRIMARY KEY, payload TEXT NOT NULL, last_seen TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT, payload TEXT);
        CREATE TABLE IF NOT EXISTS supplier_sites (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS observations (kind TEXT, id TEXT, observed_at TEXT, digest TEXT, payload TEXT, UNIQUE(kind,id,digest));
        CREATE TABLE IF NOT EXISTS demand_changes (id TEXT PRIMARY KEY, first_seen TEXT, changed_at TEXT, facts TEXT, changes TEXT);
        """)
        self.db.commit()

    def save(self,kind,records):
        if kind not in {"tenders","offers"}:
            raise ValueError(kind)
        for r in records:
            payload=json.dumps(r,ensure_ascii=False,sort_keys=True)
            self.db.execute(f"INSERT INTO {kind}(id,payload,last_seen) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,last_seen=excluded.last_seen",(r["id"],payload,r.get("checkedAt") or now_iso()))
            substantive={k:v for k,v in r.items() if k not in {"checkedAt", "sourceDigest"}}
            digest=identity(json.dumps(substantive,ensure_ascii=False,sort_keys=True))
            self.db.execute("INSERT OR IGNORE INTO observations(kind,id,observed_at,digest,payload) VALUES(?,?,?,?,?)",(kind,r["id"],r.get("checkedAt") or now_iso(),digest,payload))
        self.db.commit()

    def records(self,kind):
        if kind not in {"tenders","offers","supplier_sites"}:
            raise ValueError(kind)
        return [json.loads(r[0]) for r in self.db.execute(f"SELECT payload FROM {kind}")]

    def close(self):
        self.db.close()

    def track_demand(self, record):
        """Track source facts, not poll times, match scores or transient HTTP failures."""
        fields={k:record.get(k) for k in ("title","buyer","deadline","budget","reportedArchived","plannedPeriod","description")}
        fields["items"]=[{k:i.get(k) for k in ("name","quantity","unit","sourceUrl")} for i in record.get("items",[])]
        fields["documents"]=[{k:d.get(k) for k in ("text","url","sha256")} for d in record.get("documents",[])]
        encoded=json.dumps(fields,ensure_ascii=False,sort_keys=True)
        prior=self.db.execute("SELECT first_seen,changed_at,facts,changes FROM demand_changes WHERE id=?",(record["id"],)).fetchone()
        now=record["checkedAt"]
        if prior:
            first,changed,old,changes=prior
            events=json.loads(changes)
            if old!=encoded:
                labels={"title":"Название","buyer":"Заказчик","deadline":"Срок подачи","budget":"Бюджет","reportedArchived":"Статус площадки","plannedPeriod":"Период плана","description":"Описание","items":"Позиции и количества","documents":"Документы"}
                previous=json.loads(old)
                events=[{"at":now,"fields":[labels[k] for k in fields if previous.get(k)!=fields[k]]}]+events
                changed=now
        else:
            observed=self.db.execute("SELECT min(observed_at) FROM observations WHERE kind='tenders' AND id=?",(record["id"],)).fetchone()[0]
            first=observed or now;changed=None;events=[]
        self.db.execute("INSERT INTO demand_changes VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET changed_at=excluded.changed_at,facts=excluded.facts,changes=excluded.changes",(record["id"],first,changed,encoded,json.dumps(events[:10],ensure_ascii=False)))
        record.update(firstSeenAt=first,lastChangedAt=changed,changes=events[:10])


def tender_status(t, now=None):
    now=now or datetime.now(timezone.utc)
    deadline=parse_date(t.get("deadline"))
    if t.get("reportedArchived") or (deadline and deadline<=now):
        return "closed"
    seen=parse_date(t.get("checkedAt"))
    if t.get("sourceStatus")!="ok" or not seen or now-seen>timedelta(hours=24):
        return "needs_update"
    if t.get("noticeKind")=="plan":
        end=parse_date(t.get("plannedEnd"))
        return "closed" if end and end<now else "planned"
    return "active" if deadline else "unknown_deadline"


def legacy_offers(snapshot):
    sources={s["id"]:s for s in snapshot.get("sources",[])}
    suppliers={s["id"]:s for s in snapshot.get("suppliers",[])}
    result=[]
    for o in snapshot.get("offers",[]):
        source=sources.get(o["supplier"],{})
        rec={**o,"id":identity(o["supplier"],o.get("sourceUrl"),o["name"],o.get("size"),o.get("steelGrade"),o.get("standard")),"supplierName":suppliers.get(o["supplier"],{}).get("name",o["supplier"]),"unit":unit(o.get("unit")),"checkedAt":o.get("checkedAt") or source.get("checkedAt") or snapshot.get("generatedAt"),"priceDate":o.get("priceDate") or source.get("priceDate"),"stockQuantity":o.get("stockQuantity"),"stockUnit":o.get("stockUnit"),"stockStatus":o.get("stockStatus","unknown"),"vat":o.get("vat","unknown"),"region":"Пермский край","regionStatus":"registered","sourceStatus":source.get("status","unknown"),"attributes":attributes(o["name"],o.get("category",""),o),"minOrderQuantity":o.get("minOrderQuantity",5 if "от 5 т" in (o.get("terms") or "") else None),"minOrderUnit":"т" if "от 5 т" in (o.get("terms") or "") else None}
        result.append(rec)
        if rec["vat"]=="unknown":rec["vat"]=vat_status(o.get("terms") or source.get("terms") or "")
        if rec.get("minOrderQuantity") is None and "от 5 т" in (source.get("terms") or ""):
            rec.update(minOrderQuantity=5,minOrderUnit="т")
    return result


def refresh_price_files(fetcher,snapshot,report):
    """Reuse known file adapters; retain last good data on source failures."""
    from collect_prices import SOURCES, parse_meg, parse_metallinvest, parse_stroy, parse_permmetall
    parsers={"meg":parse_meg,"metallinvest":parse_metallinvest,"stroy":parse_stroy,"permmetall":parse_permmetall}
    for source in SOURCES:
        if source["id"] not in parsers:
            continue
        status={"id":source["id"],"name":source["supplier"],"kind":"price_file","url":source["url"],"checkedAt":now_iso()}
        prior=next((s for s in snapshot.get("sources",[]) if s["id"]==source["id"]),None)
        try:
            blob,meta=fetcher.fetch(source["url"])
            offers=[]
            price_date=parsers[source["id"]](blob,offers)
            if not offers:
                raise ValueError("Нет ценовых строк; предыдущий прайс сохранён")
            for o in offers:
                o["checkedAt"]=now_iso()
                o["priceDate"]=o.get("priceDate") or price_date
            snapshot["offers"]=[o for o in snapshot.get("offers",[]) if o["supplier"]!=source["id"]]+offers
            fresh={**source,"status":"ok","rows":len(offers),"checkedAt":now_iso(),"priceDate":price_date,"sha256":meta["sha256"]}
            if prior:
                snapshot["sources"].remove(prior)
            snapshot.setdefault("sources",[]).append(fresh)
            status.update(status="ok",rows=len(offers),sha256=meta["sha256"])
        except Exception as exc:
            status.update(status="error",error=error_message(exc),rows=0)
            if prior:
                prior["status"]="stale"
                prior["lastAttemptAt"]=now_iso()
                prior["error"]=status["error"]
        report.append(status)
    from collect_prices import comparisons_for
    snapshot["comparisons"]=comparisons_for(snapshot.get("offers",[]),snapshot.get("comparisons",[]))
    snapshot["offerCount"]=len(snapshot.get("offers",[]))
    snapshot["generatedAt"]=now_iso()
    snapshot.pop("jevAudit",None)
    return snapshot


def kvin_records(fetcher,site,blob,page_url):
    candidates=links(blob,page_url)
    price_links=[l for l in candidates if l["file"] and "прайс" in (l["text"]+unquote(l["url"])).lower()]
    stock_links=[l for l in candidates if l["file"] and "склад" in (l["text"]+unquote(l["url"])).lower()]
    # Pick the newest explicit filename date; never choose the old 2019/2020 file.
    latest=lambda xs:sorted(xs,key=lambda l: parse_date((DATE_RE.search(unquote(l["url"])) or [""])[0]) or datetime.min.replace(tzinfo=timezone.utc),reverse=True)[:1]
    records,stocks=[],[]
    for link in latest(price_links):
        raw,meta=fetcher.fetch(link["url"])
        book=xlrd.open_workbook(file_contents=raw)
        for sheet in book.sheets():
            header=None
            for i in range(sheet.nrows):
                row=sheet.row_values(i)
                if text(row[0]).lower()=="размер":
                    header=row
                    continue
                if not header or not re.search(r"\d\s*[хxХ]",text(row[0])):
                    continue
                for col,brand in enumerate(header[1:],1):
                    price=number(row[col]) if col<len(row) else None
                    if not brand or not price or price<=0:
                        continue
                    name=f"Кабель {brand} {text(row[0])}"
                    date=DATE_RE.search(unquote(link["url"]))
                    # The matrix does not state a price unit or numbered GOST: keep both unknown.
                    records.append({"id":identity(site["id"],name),"name":name,"price":round(price,2),"unit":None,"vat":"unknown","stockQuantity":None,"stockUnit":None,"stockStatus":"unknown","attributes":attributes(name),"priceDate":date[0] if date else None,"sourceUrl":link["url"],"evidence":f"Матрица, строка {i+1}, марка {brand}"})
    for link in latest(stock_links):
        raw,_=fetcher.fetch(link["url"])
        book=xlrd.open_workbook(file_contents=raw)
        sheet=book.sheet_by_index(0) # Future planned deliveries are not current stock.
        for i in range(1,sheet.nrows):
            row=sheet.row_values(i)
            if len(row)<4 or "перм" not in text(row[1]).lower():
                continue
            qty=number(row[3]);u=unit(row[2])
            if qty is None or qty<0 or not u:
                continue
            name=text(row[0])
            stocks.append({"name":name,"quantity":qty,"unit":u,"city":text(row[1]),"sourceUrl":link["url"],"attributes":attributes("Кабель "+name),"rawStandard":text(row[5]) if len(row)>5 else None,"packaging":text(row[4]) if len(row)>4 else None})
    # Preserve drum/batch quantities. Only attach stock when the complete normalized
    # name agrees; rough family/size matches never establish a common stock balance.
    canonical=lambda n:re.sub(r"\s+","",n.upper().replace("КАБЕЛЬ","").replace(",",".").replace("X","Х"))
    for rec in records:
        same=[s for s in stocks if canonical(s["name"])==canonical(rec["name"])]
        if same and len({s["unit"] for s in same})==1:
            rec.update(stockQuantity=sum(s["quantity"] for s in same),stockUnit=same[0]["unit"],stockStatus="reported",stockSourceUrl=same[0]["sourceUrl"],stockCity="Пермь")
    # Stock without a published price remains searchable; it is not a zero-price offer.
    grouped={}
    for s in stocks:
        key=(canonical(s["name"]),s["unit"])
        grouped.setdefault(key,{**s,"quantity":0})["quantity"]+=s["quantity"]
    for s in grouped.values():
        if any(canonical(r["name"])==canonical(s["name"]) for r in records):
            continue
        records.append({"id":identity(site["id"],s["name"],"stock"),"name":"Кабель "+s["name"],"price":None,"unit":None,"vat":"unknown","stockQuantity":s["quantity"],"stockUnit":s["unit"],"stockStatus":"reported","stockCity":"Пермь","attributes":s["attributes"],"sourceUrl":s["sourceUrl"],"stockSourceUrl":s["sourceUrl"],"rawStandard":s["rawStandard"],"evidence":"Складская справка, только Пермь"})
    return records


def discover_sites(fetcher,config,known,report):
    hostkey=lambda u:(urlparse(u).hostname or "").removeprefix("www.")
    found={};existing=set()
    for s in known:
        host=hostkey(s["website"])
        if host in existing:continue
        if s.get("regionStatus")=="page_evidence" and not (host.startswith(("perm.","prm.")) or "perm" in host):
            s={**s,"regionStatus":"needs_verification"}
            s.pop("regionEvidenceUrl",None)
        found[s["id"]]=s;existing.add(host)
    for url in config.get("directories",[]):
        status={"id":identity(url),"name":"Отраслевой реестр","kind":"directory","url":url,"checkedAt":now_iso()}
        try:
            blob,_=fetcher.fetch(url)
            added=0
            for link in links(blob,url):
                host=hostkey(link["url"])
                if host in existing or host==hostkey(url) or not host or re.search(r"youtube|mail\.ru|yandex|facebook|vk\.com",host):
                    continue
                if not (re.search(r"прайс",link["text"],re.I) or host.replace("www.","") in link["text"]):
                    continue
                site={"id":identity(host),"name":host,"website":link["url"],"region":config["region"],"regionStatus":"needs_verification","discoveredFrom":url}
                found[site["id"]]=site;existing.add(host);added+=1
            status.update(status="ok",rows=added)
        except Exception as exc:
            status.update(status="error",error=error_message(exc))
        report.append(status)
    endpoint=os.environ.get(config.get("searchEndpointEnv","SEARXNG_URL"))
    if endpoint:
        for query in config.get("searchQueries",[])[:3]:
            status={"id":identity(query),"name":"Поиск новых сайтов","kind":"search","url":endpoint,"checkedAt":now_iso()}
            try:
                from urllib.parse import urlencode
                raw,_=fetcher.fetch(endpoint.rstrip("/")+"/search?"+urlencode({"q":query,"format":"json"}))
                rows=json.loads(raw).get("results",[])
                for r in rows[:8]:
                    target=r.get("url","");host=hostkey(target)
                    if not host or host in existing:
                        continue
                    site={"id":identity(host),"name":text(r.get("title")) or host,"website":target,"region":config["region"],"regionStatus":"needs_verification","discoveredFrom":"web_search"}
                    found[site["id"]]=site;existing.add(host)
                status.update(status="ok",rows=len(rows))
            except Exception as exc:
                status.update(status="error",error=error_message(exc))
            report.append(status)
    else:
        report.append({"id":"web-search","name":"Поиск новых сайтов в интернете","kind":"search","status":"not_configured","rows":0,"error":"Поисковый сервер не подключён; расширение реестра идёт по отраслевому каталогу"})
    return list(found.values())[:config.get("maxSupplierSites",24)]


def crawl_supplier(fetcher,jev,site,requirements,config):
    status={"id":site["id"],"name":site["name"],"kind":"supplier","url":site["website"],"checkedAt":now_iso(),"pages":0}
    result=[]
    queue=[site["website"]];seen=set()
    tokens=set(re.findall(r"[а-яa-z]{3,}|\d{1,4}"," ".join(i["name"] for i in requirements).lower()))
    try:
        for _ in range(config.get("pagesPerSupplier",3)):
            if not queue:
                break
            target=queue.pop(0)
            if target in seen:
                continue
            seen.add(target)
            blob,meta=fetcher.fetch(target)
            status["pages"]+=1
            if site.get("regionStatus")=="needs_verification" and not blob.lstrip().startswith(b"<?xml"):
                page_text=BeautifulSoup(blob,"html.parser").get_text(" ")
                # A nationwide site listing Perm among 50 offices is not a Perm price.
                host=urlparse(target).hostname or ""
                if (host.startswith(("perm.","prm.")) or "perm" in host) and re.search(r"(?:г[.\s]*Пермь|Пермь[,\s]+ул\.|Пермский\s+(?:филиал|склад)|Пермский край)",page_text,re.I):
                    site["regionStatus"]="page_evidence"
                    site["regionEvidenceUrl"]=target
            if site.get("adapter")=="kvin" and status["pages"]==1:
                result.extend(kvin_records(fetcher,site,blob,target))
                break
            extracted,notes=extract(blob,target,meta.get("contentType") or "")
            result.extend(extracted)
            status.setdefault("notes",[]).extend(notes)
            if FILE_RE.search(unquote(target)):
                continue
            candidates=[]
            for link in links(blob,target):
                if link["url"] in seen or urlparse(link["url"]).hostname!=urlparse(site["website"]).hostname:
                    continue
                label=(link["text"]+" "+unquote(link["url"])).lower()
                if not re.search(r"прайс|price|каталог|catalog|склад|остат|кабел|armatur|арматур|труб|лист",label):
                    continue
                if re.search(r"газет|новост|политик|контакт|login|account|cart|feedback",label):
                    continue
                score=10*int(link["file"])+6*int(bool(re.search(r"прайс|price|остат|склад",label)))+sum(t in label for t in tokens)
                candidates.append({**link,"score":score})
            candidates.sort(key=lambda l:l["score"],reverse=True)
            selected=jev.choose(candidates[:20],"Find a public price/stock file or matching material for Perm procurement") if candidates and status["pages"]==1 else None
            if selected is not None:
                candidates.insert(0,candidates.pop(selected))
            queue.extend(l["url"] for l in candidates[:5] if l["url"] not in queue)
        for r in result:
            r.update(id=identity(site["id"],r["id"]),supplier=site["id"],supplierName=site["name"],region=site["region"],regionStatus=site["regionStatus"],checkedAt=now_iso(),sourceStatus="ok")
            if not r.get("priceDate"):
                m=DATE_RE.search(unquote(r.get("sourceUrl","")))
                r["priceDate"]=m[0] if m else None
            if site.get("regionEvidenceUrl"):
                r["regionEvidenceUrl"]=site["regionEvidenceUrl"]
        status.update(status="ok" if result else "no_structured_prices",rows=len(result),regionStatus=site["regionStatus"])
    except Exception as exc:
        status.update(status="partial" if result else "error",error=error_message(exc),rows=len(result))
        for r in result:
            r.update(id=identity(site["id"],r["id"]),supplier=site["id"],supplierName=site["name"],region=site["region"],regionStatus=site["regionStatus"],checkedAt=now_iso(),sourceStatus="ok")
    return result,status


def match_item(requirement,offers,age_days=7,now=None):
    now=now or datetime.now(timezone.utc)
    candidates=[]
    for o in offers:
        if o.get("regionStatus") not in {"registered","page_evidence"}:
            continue
        spec=compare_specs(requirement["attributes"],o["attributes"])
        if spec["status"]=="rejected":
            continue
        seen=parse_date(o.get("checkedAt"));dated=parse_date(o.get("priceDate"))
        stale=o.get("sourceStatus")!="ok" or not seen or now-seen>timedelta(hours=24) or bool(dated and now-dated>timedelta(days=age_days))
        freshness="stale" if stale else "dated" if dated else "date_unknown"
        quantity=convert(requirement.get("quantity"),requirement.get("unit"),o.get("unit"))
        price=o.get("price")
        cost=round(quantity*price,2) if quantity is not None and price is not None and price>0 else None
        need_stock=convert(requirement.get("quantity"),requirement.get("unit"),o.get("stockUnit"))
        stock=o.get("stockQuantity")
        stock_check="sufficient" if stock is not None and need_stock is not None and stock>=need_stock else "insufficient" if stock is not None and need_stock is not None else "unknown"
        minimum=convert(o.get("minOrderQuantity"),o.get("minOrderUnit"),requirement.get("unit"))
        below_min=minimum is not None and requirement.get("quantity") is not None and requirement["quantity"]<minimum
        issues=list(spec["missing"])
        if stale:issues.append("устаревшая цена или источник не обновлён")
        if freshness=="date_unknown":issues.append("дата прайса не указана")
        if o.get("unit") is None:issues.append("единица цены не указана")
        elif quantity is None:issues.append("количество или пересчёт единиц не подтверждён")
        if below_min:issues.append("партия ниже минимального объёма прайса")
        if o.get("vat")=="unknown":issues.append("НДС не указан")
        issues.append("доставка и договорная цена не подтверждены")
        if stock_check=="unknown":issues.append("остаток в нужной единице не подтверждён")
        if stock_check=="insufficient":issues.append("недостаточный опубликованный остаток")
        candidates.append({"offerId":o["id"],"supplier":o["supplier"],"supplierName":o["supplierName"],"name":o["name"],"sourceUrl":o["sourceUrl"],"price":price,"unit":o.get("unit"),"vat":o.get("vat","unknown"),"priceDate":o.get("priceDate"),"checkedAt":o.get("checkedAt"),"status":spec["status"],"score":spec["score"],"issues":list(dict.fromkeys(issues)),"stockQuantity":stock,"stockUnit":o.get("stockUnit"),"stockSourceUrl":o.get("stockSourceUrl"),"stockCity":o.get("stockCity"),"stockCheck":stock_check,"freshness":freshness,"rawCost":cost,"indicativeCost":cost if spec["status"]=="specified" and not stale and not below_min else None,"minOrderQuantity":o.get("minOrderQuantity"),"minOrderUnit":o.get("minOrderUnit"),"attributes":o["attributes"]})
    candidates.sort(key=lambda c:(c["status"]!="specified",c["freshness"]=="stale",-c["score"],c["stockCheck"]!="sufficient",c["price"] is None,c["price"] or 0))
    # Keep distinct suppliers visible instead of filling all slots with one catalog.
    picked=[];counts={}
    for c in candidates:
        if counts.get(c["supplier"],0)>=2:continue
        picked.append(c);counts[c["supplier"]]=counts.get(c["supplier"],0)+1
        if len(picked)>=5:break
    return picked


def run(config_path,snapshot_path,output,state_dir,refresh_prices=True):
    config=json.loads(config_path.read_text(encoding="utf-8"))
    state_dir.mkdir(parents=True,exist_ok=True)
    store=Store(state_dir/"radar.sqlite3")
    started=now_iso()
    cur=store.db.execute("INSERT INTO runs(started_at) VALUES(?)",(started,));run_id=cur.lastrowid;store.db.commit()
    try:
        return _run(config,snapshot_path,output,state_dir,refresh_prices,store,run_id,started)
    finally:
        store.close()


def _run(config,snapshot_path,output,state_dir,refresh_prices,store,run_id,started):
    fetcher=Fetcher(state_dir/"raw",max_requests=config.get("maxRequests",180))
    jev=Jev(config.get("maxJevCalls",25))
    report=[];tenders={}
    prior_tenders={t["id"]:t for t in store.records("tenders")}
    for feed in config["feeds"]:
        status={"id":feed["id"],"name":feed.get("name",feed["id"]),"kind":"procurement","url":feed["url"],"checkedAt":now_iso()}
        try:
            rows,meta,note=collect_feed(fetcher,{"region":config["region"],**feed})
            for t in rows:
                t["sourceDigest"]=meta["sha256"]
                prior=tenders.get(t["id"])
                if not prior or len(t["items"])>len(prior["items"]):tenders[t["id"]]=t
            status.update(status="ok",rows=len(rows),note=note,adapter=feed.get("adapter"),access=feed.get("access"),scope=feed.get("scope"))
        except Exception as exc:
            status.update(status="error",error=error_message(exc),rows=0)
        report.append(status)
    # Active deadlines first; preserve closed/unknown entries for inspection.
    selected=sorted(tenders.values(),key=lambda t:(tender_status(t)!="active",t.get("deadline") or "9999"))[:config.get("maxTenders",25)]
    details=0;detail_feeds={}
    feed_count=max(1,len({t.get("feedId") for t in selected if t.get("noticeKind")!="plan"}))
    per_feed=max(1,(config.get("maxDetailPages",20)+feed_count-1)//feed_count)
    for t in selected:
        if t.get("noticeKind")=="plan":continue
        if details>=config.get("maxDetailPages",12):break
        if detail_feeds.get(t.get("feedId"),0)>=per_feed:continue
        details+=1
        detail_feeds[t.get("feedId")]=detail_feeds.get(t.get("feedId"),0)+1
        try:
            blob,meta=fetcher.fetch(t["listingUrl"])
            detail_fields(blob,t["listingUrl"],t)
            public_details(blob,t["listingUrl"],t)
            t["documentsCheckedAt"]=now_iso()
            primary=t.get("sourceUrl")
            if primary and urlparse(primary).hostname!=urlparse(t["listingUrl"]).hostname:
                status={"id":identity(primary),"name":"Первичная площадка закупки № "+t["id"],"kind":"procurement","url":primary,"checkedAt":now_iso()}
                try:
                    primary_raw,_=fetcher.fetch(primary)
                    pages=[(primary,primary_raw)]
                    document_page=next((l for l in links(primary_raw,primary) if urlparse(l["url"]).hostname==urlparse(primary).hostname and re.fullmatch(r"Документы(?: закупки)?",l["text"],re.I)),None)
                    if document_page and document_page["url"]!=primary:
                        document_raw,_=fetcher.fetch(document_page["url"])
                        pages.append((document_page["url"],document_raw))
                    for page_url,page_raw in pages:
                        fields=detail_fields(page_raw,page_url,{"documents":[]})
                        t["documents"]=merge_documents(t["documents"],fields["documents"])
                    status.update(status="ok",rows=sum(bool(d.get("url")) for d in t["documents"]))
                except Exception as exc:
                    status.update(status="error",error=error_message(exc),rows=0)
                    t["issues"].append("Первичная площадка: "+error_message(exc))
                report.append(status)
            for doc in t["documents"]:
                if not doc.get("url"):continue
                try:
                    raw,dmeta=fetcher.fetch(doc["url"])
                    records,notes=extract(raw,doc["url"],dmeta.get("contentType") or "",mode="demand")
                    doc.update(status="parsed" if records else "needs_adapter",items=len(records),sha256=dmeta["sha256"],notes=notes)
                    # Exact document quantities supersede broad OKPD labels from listings.
                    if records and (not t["items"] or not any(i.get("quantity") for i in t["items"])):
                        t["items"]=[{**r,"matches":[]} for r in records]
                except Exception as exc:
                    doc.update(status="error",error=error_message(exc))
            if not t["items"] or not any(i.get("quantity") for i in t["items"]):
                t["issues"].append("Точные количества не извлечены; нужна спецификация")
        except Exception as exc:
            t["issues"].append("Детальная страница: "+error_message(exc))
    # If a feed disappears, its previous data stays with its real observation time.
    prior_tenders={t["id"]:t for t in store.records("tenders")}
    for t in selected:
        prior=prior_tenders.get(t["id"])
        if t.get("category")=="Прочее":
            cached=prior and prior.get("title")==t["title"] and prior.get("categoryModel")
            classified=prior.get("category") if cached else jev.classify_notice(t["title"])
            if classified:t.update(category=classified,categoryModel=prior["categoryModel"] if cached else jev.model)
        if prior and t.get("noticeKind")!="plan" and not t.get("documentsCheckedAt"):
            t["documents"]=prior.get("documents",[])
            t["documentsCheckedAt"]=prior.get("documentsCheckedAt")
        if prior and not any(i.get("quantity") for i in t["items"]) and any(i.get("quantity") for i in prior.get("items",[])):
            t["items"]=prior["items"]
            t["issues"].append("Позиции сохранены из предыдущего разбора; сверить обновление документа")
        store.track_demand(t)
    store.save("tenders",selected)
    snapshot=json.loads(snapshot_path.read_text(encoding="utf-8")) if snapshot_path.exists() else {"offers":[],"sources":[],"suppliers":[]}
    prices_enabled=config.get("collectSupplierPrices",True)
    if refresh_prices and prices_enabled:
        refresh_price_files(fetcher,snapshot,report)
        atomic_json(snapshot_path,snapshot)
    offers=legacy_offers(snapshot)
    known=[{"id":s["id"],"name":s["name"],"website":s["website"],"region":config["region"],"regionStatus":"registered"} for s in snapshot.get("suppliers",[]) if s.get("source")!="discovery"]
    known+=config.get("suppliers",[])
    known+=[s for s in store.records("supplier_sites") if s["id"] not in {k["id"] for k in known}]
    source_terms={s["id"]:s.get("terms") or "" for s in snapshot.get("sources",[])}
    for site in known:
        site["priceTerms"]=source_terms.get(site["id"],site.get("priceTerms",""))
    sites=discover_sites(fetcher,config,known,report) if prices_enabled else []
    requirements=[i for t in selected if tender_status(t)=="active" for i in t["items"]]
    for site in sites:
        rows,status=crawl_supplier(fetcher,jev,site,requirements,config)
        for r in rows:
            if r.get("minOrderQuantity") is None and "от 5 т" in site.get("priceTerms",""):
                r.update(minOrderQuantity=5,minOrderUnit="т")
        offers.extend(rows);report.append(status)
        store.db.execute("INSERT INTO supplier_sites(id,payload) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",(site["id"],json.dumps(site,ensure_ascii=False)))
    store.db.commit()
    # Do not leave old rows active after a supplier published a replacement file.
    for status in report:
        if status.get("kind")=="supplier" and status.get("status")=="ok":
            store.db.execute("DELETE FROM offers WHERE json_extract(payload,'$.supplier')=?",(status["id"],))
    store.db.commit()
    # Fresh legacy files replace their rows as well, including removed SKUs.
    for s in report:
        if s["kind"]=="price_file" and s["status"]=="ok":
            store.db.execute("DELETE FROM offers WHERE json_extract(payload,'$.supplier')=?",(s["id"],))
    store.db.commit()
    store.save("offers",list({o["id"]:o for o in offers}.values()))
    all_offers=store.records("offers")
    current_status={s["id"]:s["status"] for s in report if s["kind"] in {"supplier","price_file"}}
    current_sites={s["id"]:s for s in sites}
    for o in all_offers:
        o["attributes"]=attributes(o["name"],o.get("category",""),{**o.get("attributes",{}),**o})
        if o["supplier"] in current_sites:
            o["regionStatus"]=current_sites[o["supplier"]]["regionStatus"]
        elif o["supplier"] not in {s["id"] for s in snapshot.get("suppliers",[])}:
            o["regionStatus"]="needs_verification"
        if current_status.get(o["supplier"]) in {"error","partial","no_structured_prices"} and o.get("checkedAt","")<started:
            o["sourceStatus"]="stale"
    index={}
    for o in all_offers:index.setdefault(o["attributes"].get("family"),[]).append(o)
    evaluated=[]
    for t in store.records("tenders"):
        if re.search(r"Объявление о продаже",t["title"],re.I):continue
        if t["id"] not in {x["id"] for x in selected}:
            t["sourceStatus"]="stale"
            t["issues"]=list(dict.fromkeys(t.get("issues",[])+["Закупка не найдена в текущем обходе; требуется обновление источника"]))
        t["status"]=tender_status(t)
        if t["status"]=="closed" and parse_date(t.get("deadline")) and datetime.now(timezone.utc)-parse_date(t["deadline"])>timedelta(days=30):continue
        matched,specified,priced,subtotal=0,0,0,0
        cost_vat=set()
        for i in t["items"]:
            i["attributes"]=attributes(i["name"])
            i["matches"]=match_item(i,index.get(i["attributes"].get("family"),[]),config.get("maxPriceAgeDays",7)) if prices_enabled else []
            if i["matches"]:
                matched+=1
                top=i["matches"][0]
                if top["status"]=="specified":specified+=1
                valid=[m for m in i["matches"] if m.get("indicativeCost") is not None]
                if valid:
                    cheapest=min(valid,key=lambda m:m["indicativeCost"])
                    priced+=1;subtotal+=cheapest["indicativeCost"];cost_vat.add(cheapest["vat"])
                if t["status"]=="active" and jev.key and jev.calls<jev.limit:
                    review=jev.review(i["name"],top["name"])
                    if review:top["jev"]=review
        comparable=priced and len(cost_vat)==1 and "unknown" not in cost_vat
        t.update(matchedItems=matched,specifiedItems=specified,pricedItems=priced,totalItems=len(t["items"]),partialCost=round(subtotal,2) if comparable else None,partialCostVat=next(iter(cost_vat)) if comparable else None,profit=None)
        t["readiness"]="needs_documents" if not t["items"] or not any(i.get("quantity") for i in t["items"]) else "offers_found" if matched else "search_needed"
        evaluated.append(t)
    evaluated.sort(key=lambda t:(t["status"]!="active",-t["specifiedItems"],-t["matchedItems"],t.get("deadline") or "9999"))
    finished=now_iso()
    result={"schemaVersion":2,"productMode":"demand_monitor","pricesEnabled":prices_enabled,"generatedAt":finished,"startedAt":started,"region":config["region"],"scope":config["scopeNote"],"intervalMinutes":config.get("intervalMinutes",240),"automation":{"mode":"collector","lastRunAt":finished,"nextRunAt":(datetime.now(timezone.utc)+timedelta(minutes=config.get("intervalMinutes",240))).isoformat(),"scheduler":"external_or_service"},"metrics":{"tenders":len(evaluated),"plans":sum(t["status"]=="planned" for t in evaluated),"requests":sum(t.get("noticeKind")=="request" for t in evaluated),"new":sum(bool(t.get("firstSeenAt") and datetime.now(timezone.utc)-parse_date(t["firstSeenAt"])<timedelta(hours=24)) for t in evaluated),"active":sum(t["status"]=="active" for t in evaluated),"withOffers":sum(t["status"]=="active" and t["matchedItems"]>0 for t in evaluated),"needsDocuments":sum(t["status"]=="active" and t["readiness"]=="needs_documents" for t in evaluated),"supplierSites":len(sites),"offers":len(all_offers),"reportedStock":sum(o.get("stockQuantity") is not None for o in all_offers),"successfulSources":sum(s["status"]=="ok" for s in report),"sourceCount":len(report)},"jev":{"configured":bool(jev.key),"model":jev.model,"calls":jev.calls,"errors":jev.errors},"tenders":evaluated,"sources":report,"supplierSites":sites,"stockOffers":[{k:o.get(k) for k in ("id","name","supplierName","price","unit","stockQuantity","stockUnit","stockCity","sourceUrl","stockSourceUrl","checkedAt","priceDate","attributes")} for o in all_offers if o.get("stockQuantity") is not None][:2000]}
    atomic_json(output,result)
    store.db.execute("UPDATE runs SET finished_at=?,payload=? WHERE id=?",(finished,json.dumps({"metrics":result["metrics"],"sources":report},ensure_ascii=False),run_id));store.db.commit()
    print(json.dumps({"output":str(output),"metrics":result["metrics"],"jev":result["jev"]},ensure_ascii=False),flush=True)
    return result
