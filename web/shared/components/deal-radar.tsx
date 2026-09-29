"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ClipboardList, Clock3, Download, ExternalLink, FileText, PackageSearch, RefreshCw, Search, ShieldCheck, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

type Match = {
  offerId: string; supplierName: string; name: string; sourceUrl: string; price: number | null; unit: string | null; vat: string;
  priceDate?: string | null; checkedAt: string; status: string; issues: string[]; stockQuantity: number | null; stockUnit: string | null;
  stockSourceUrl?: string; stockCity?: string; stockCheck: string; freshness: string; indicativeCost: number | null;
  attributes: { standards: string[]; steelGrade?: string; grade?: string; materialBrand?: string }; jev?: { probability: number; model: string };
};
type Item = { id: string; name: string; quantity: number | null; unit: string | null; sourceUrl: string; evidence: string; attributes: { standards: string[] }; matches: Match[] };
type Tender = {
  id: string; title: string; buyer: string | null; sourceUrl: string; listingUrl: string; region: string; deadline: string | null; budget: number | null;
  checkedAt: string; status: string; readiness: string; issues: string[]; paymentTerms?: string | null; deliveryTerms?: string | null;
  deliveryAddress?: string; smeOnly?: boolean; totalItems: number; matchedItems: number; specifiedItems: number; pricedItems: number; partialCost: number | null;
  items: Item[]; documents: { text: string; url: string | null; status: string; items?: number; error?: string }[];
};
type Source = { id: string; name: string; kind: string; url?: string; status: string; rows?: number; pages?: number; error?: string; checkedAt?: string };
type Stock = { id: string; name: string; supplierName: string; price: number | null; unit: string | null; stockQuantity: number; stockUnit: string; stockCity?: string; sourceUrl: string; checkedAt: string; priceDate?: string };
type Data = {
  generatedAt: string; region: string; scope: string; intervalMinutes: number; tenders: Tender[]; sources: Source[]; stockOffers: Stock[];
  metrics: { active: number; withOffers: number; needsDocuments: number; supplierSites: number; offers: number; reportedStock: number; successfulSources: number; sourceCount: number };
  automation: { mode: string; nextRunAt: string }; jev: { configured: boolean; calls: number; errors: number; model: string };
};

const fmt = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 3 });
const money = (n: number) => `${fmt.format(n)} ₽`;
function datetime(s?: string | null) { if (!s) return "не указан"; const d = new Date(s); return Number.isNaN(d.getTime()) ? s : d.toLocaleString("ru-RU", { timeZone: "Asia/Yekaterinburg", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }); }
function effectiveStatus(t: Tender) { return t.deadline && new Date(t.deadline).getTime() <= Date.now() ? "closed" : Date.now() - new Date(t.checkedAt).getTime() > 86400000 && t.status === "active" ? "needs_update" : t.status; }
const statusLabel: Record<string, string> = { active: "Приём заявок", closed: "Срок завершён", needs_update: "Нужна актуализация", unknown_deadline: "Срок не извлечён" };
const sourceLabel: Record<string, string> = { ok: "Обработан", error: "Ошибка доступа", partial: "Частично", no_structured_prices: "Цены не извлечены", not_configured: "Не подключён" };
const kindLabel: Record<string, string> = { procurement: "Закупки", supplier: "Сайт поставщика", price_file: "Прайс", directory: "Реестр компаний", search: "Поиск сайтов" };

function exportTender(t: Tender) {
  const clean = (v: unknown) => { const s = String(v ?? ""); return `"${(/^[=+\-@]/.test(s) ? "'" : "") + s.replaceAll('"', '""')}"`; };
  const rows: unknown[][] = [["Закупка", "Материал", "Нужно", "Единица", "Поставщик", "Предложение", "Цена", "Единица цены", "НДС", "Остаток", "Единица остатка", "Статус", "Уточнения", "Источник"]];
  for (const i of t.items) {
    if (!i.matches.length) rows.push([t.id, i.name, i.quantity, i.unit, "", "", "", "", "", "", "", "Нет предложения", "", i.sourceUrl]);
    for (const m of i.matches) rows.push([t.id, i.name, i.quantity, i.unit, m.supplierName, m.name, m.price, m.unit, m.vat, m.stockQuantity, m.stockUnit, m.status, m.issues.join("; "), m.sourceUrl]);
  }
  const blob = new Blob(["\ufeff" + rows.map(r => r.map(clean).join(";")).join("\r\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob), a = document.createElement("a"); a.href = url; a.download = `zakupka-${t.id}.csv`; a.click(); URL.revokeObjectURL(url);
}

export default function DealRadar({ dataUrl = "/data/deals.json" }: { dataUrl?: string }) {
  const [data, setData] = useState<Data | null>(null), [error, setError] = useState("");
  const [busy, setBusy] = useState(false), [tab, setTab] = useState("tenders"), [query, setQuery] = useState("");
  const [filter, setFilter] = useState("active"), [selected, setSelected] = useState<string | null>(null);
  const [api, setApi] = useState<{ running: boolean; lastError?: string | null } | null>(null), [runMessage, setRunMessage] = useState("");
  const [operatorToken, setOperatorToken] = useState("");
  const reload = useCallback(async () => {
    setBusy(true);
    try {
      const r = await fetch(dataUrl, { cache: "no-store" });
      if (!r.ok) throw new Error();
      const next = await r.json() as Data;
      if (!Array.isArray(next.tenders)) throw new Error();
      setData(next); setError("");
    } catch { setError("Срез закупок пока недоступен. Повторите загрузку после завершения сбора."); }
    finally { setBusy(false); }
  }, [dataUrl]);
  useEffect(() => { void reload(); const timer = setInterval(() => void reload(), 90000); return () => clearInterval(timer); }, [reload]);
  useEffect(() => {
    let alive = true;
    const read = () => fetch("/api/radar/status", { cache: "no-store" }).then(r => {
      if (r.status === 404) { clearInterval(timer); return null; }
      return r.ok ? r.json() as Promise<{ running: boolean; lastError?: string | null }> : null;
    }).then(x => { if (alive && x && typeof x.running === "boolean") setApi(x); }).catch(() => {});
    void read(); const timer = setInterval(read, 15000); return () => { alive = false; clearInterval(timer); };
  }, []);
  const tenders = useMemo(() => (data?.tenders || []).filter(t => {
    const status = effectiveStatus(t);
    return (filter === "all" || filter === status || filter === "offers" && status === "active" && t.matchedItems > 0 || filter === "documents" && status === "active" && t.readiness === "needs_documents") && `${t.title} ${t.buyer} ${t.id} ${t.items.map(i => i.name).join(" ")}`.toLowerCase().includes(query.toLowerCase());
  }), [data, query, filter]);
  const current = tenders.find(t => t.id === selected) || tenders[0];
  const stock = useMemo(() => (data?.stockOffers || []).filter(o => `${o.name} ${o.supplierName}`.toLowerCase().includes(query.toLowerCase())), [data, query]);
  const active = data?.tenders.filter(t => effectiveStatus(t) === "active").length || 0;
  async function collect() {
    setRunMessage("Запускаем сбор…");
    try {
      const r = await fetch("/api/radar/refresh", { method: "POST", headers: { "Content-Type": "application/json", ...(operatorToken ? { Authorization: `Bearer ${operatorToken}` } : {}) }, body: "{}" });
      const result = await r.json() as { error?: string };
      setRunMessage(r.ok ? "Сбор запущен. Список обновится после завершения." : result.error || "Не удалось запустить сбор");
      if (r.ok) setApi({ running: true });
    } catch { setRunMessage("Нет связи с сервером сбора"); }
  }
  return <div className="radar">
    <div className="radar-toolbar"><div><span className="section-label">АВТОМАТИЧЕСКИЙ СБОР</span><p>{data ? `Последний сбор: ${datetime(data.generatedAt)} · время Перми` : "Загружаем закупки и предложения…"}</p></div><div className="radar-actions"><Button variant="outline" disabled={busy} onClick={() => void reload()}><RefreshCw size={15} className={busy ? "radar-spin" : ""} />Обновить список</Button>{api && <Button disabled={api.running} onClick={() => void collect()}>{api.running ? "Сбор выполняется…" : "Запустить сбор"}</Button>}</div></div>
    {error && <div className="error-box" role="alert">{error}</div>}
    {runMessage && <div className="notice" role="status">{runMessage}</div>}
    {api?.lastError && <div className="error-box" role="alert">Последний сбор не завершился. Сохранён предыдущий срез; проверьте состояние источников.</div>}
    {api && <details className="radar-auth"><summary>Доступ оператора сервера</summary><Input type="password" autoComplete="off" aria-label="Токен оператора" value={operatorToken} onChange={e => setOperatorToken(e.target.value)} placeholder="Токен для удалённого сервера" /><small>Используется только для запуска сбора. Не сохраняется в браузере.</small></details>}
    {data && <>
      <div className="metric-grid radar-metrics"><div className="metric-card"><div className="metric-icon amber"><ClipboardList size={20}/></div><span>Открытых закупок</span><strong>{active}</strong><small>По сроку из источника</small></div><div className="metric-card"><div className="metric-icon blue"><PackageSearch size={20}/></div><span>С предложениями</span><strong>{data.tenders.filter(t => effectiveStatus(t) === "active" && t.matchedItems > 0).length}</strong><small>Требуют проверки условий</small></div><div className="metric-card"><div className="metric-icon green"><Search size={20}/></div><span>Сайтов в обходе</span><strong>{data.metrics.supplierSites}</strong><small>Проверяется доступ и регион</small></div><div className="metric-card"><div className="metric-icon violet"><FileText size={20}/></div><span>Строк с остатком</span><strong>{fmt.format(data.metrics.reportedStock)}</strong><small>Количество из источника</small></div></div>
      <div className="radar-tabs" role="tablist" aria-label="Раздел закупок">{[["tenders", "Закупки"], ["stock", "Складские предложения"], ["sources", "Источники сбора"]].map(([key, title]) => <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}>{title}</button>)}</div>
      <div className="radar-filter"><label className="search-field"><Search size={17}/><Input aria-label="Поиск закупок и материалов" value={query} onChange={e => setQuery(e.target.value)} placeholder="Материал, заказчик или номер закупки"/></label>{tab === "tenders" && <select aria-label="Статус закупки" value={filter} onChange={e => setFilter(e.target.value)}><option value="active">Приём заявок</option><option value="offers">С предложениями</option><option value="documents">Нужна спецификация</option><option value="closed">Завершённые сроки</option><option value="needs_update">Нужна актуализация</option><option value="all">Все закупки</option></select>}</div>
      {tab === "tenders" && <div className="radar-workspace"><section className="radar-list" aria-label="Список закупок"><div className="radar-list-count">{tenders.length} закупок по фильтру</div>{!tenders.length && <div className="radar-empty"><ClipboardList size={28}/><strong>Закупок по фильтру нет</strong><p>Попробуйте другой статус или название материала.</p></div>}{tenders.map(t => <button className={`radar-card ${current?.id === t.id ? "selected" : ""}`} key={t.id} onClick={() => setSelected(t.id)}><span className={`radar-status ${effectiveStatus(t) === "active" ? "open" : ""}`}>{statusLabel[effectiveStatus(t)] || t.status}</span><span className="radar-card-id">№ {t.id}</span><strong>{t.title}</strong><span className="radar-buyer">{t.buyer || "Заказчик не извлечён"}</span><span className="radar-card-bottom"><b>{t.budget ? money(t.budget) : "Бюджет не указан"}</b><span><Clock3 size={13}/>{datetime(t.deadline)}</span></span><span className="radar-coverage">Предложения: {t.matchedItems} / {t.totalItems} позиций{t.readiness === "needs_documents" ? " · нужна спецификация" : ""}</span></button>)}</section>
        {current ? <section className="panel radar-detail" aria-label="Карточка закупки"><div className="radar-detail-head"><div><span className="section-label">ЗАКУПКА № {current.id}</span><h2>{current.title}</h2><p>{current.buyer}</p></div><Button variant="outline" size="sm" onClick={() => exportTender(current)}><Download size={15}/>CSV</Button></div><div className="radar-detail-meta"><div><span>Начальная цена</span><strong>{current.budget ? money(current.budget) : "Не указана"}</strong><small>Условия НДС сверить в документации</small></div><div><span>Приём до, время Перми</span><strong>{datetime(current.deadline)}</strong><small>{statusLabel[effectiveStatus(current)]}</small></div><div><span>Указанные параметры совпадают</span><strong>{current.specifiedItems} / {current.totalItems}</strong><small>Сертификат и условия поставки требуют проверки</small></div></div><div className="radar-detail-links"><a href={current.sourceUrl} target="_blank" rel="noreferrer">Источник закупки <ExternalLink size={13}/></a><a href={current.listingUrl} target="_blank" rel="noreferrer">Карточка и документация <ExternalLink size={13}/></a>{current.smeOnly && <span>Только МСП</span>}</div>
          {!!current.issues.length && <div className="radar-warning"><TriangleAlert size={16}/><div>{current.issues.map((x, i) => <p key={i}>{x}</p>)}</div></div>}
          {(current.paymentTerms || current.deliveryTerms || current.deliveryAddress) && <dl className="radar-terms">{current.paymentTerms && <><dt>Оплата</dt><dd>{current.paymentTerms}</dd></>}{current.deliveryTerms && <><dt>Поставка</dt><dd>{current.deliveryTerms}</dd></>}{current.deliveryAddress && <><dt>Адрес</dt><dd>{current.deliveryAddress}</dd></>}</dl>}
          <div className="radar-items-title"><h3>Материалы и предложения</h3><span>{current.totalItems} позиций</span></div>
          {!current.items.length && <div className="radar-empty"><FileText size={25}/><p>Перечень материалов ещё не извлечён. Проверьте доступ к техническому заданию.</p></div>}
          {current.items.map((i, index) => <details className="radar-item" key={i.id} open={index < 2}>
            <summary><span className="radar-item-number">{index + 1}</span><div><strong>{i.name}</strong><small>{i.attributes.standards.length ? i.attributes.standards.join(" · ") : "Номер ГОСТ не указан"}</small></div><span className="radar-item-qty">{i.quantity !== null ? `${fmt.format(i.quantity)} ${i.unit || "ед. не указана"}` : "Количество не извлечено"}<small>{i.matches.length ? `${i.matches.length} предложений` : "Предложений нет"}</small></span></summary>
            <div className="radar-item-body"><a className="radar-evidence" href={i.sourceUrl} target="_blank" rel="noreferrer">{i.evidence} в источнике <ExternalLink size={12}/></a>
              {!i.matches.length && <p className="radar-no-match">В подключённых источниках не найдено предложения без явных расхождений характеристик.</p>}
              {i.matches.map(m => <article className="radar-offer" key={m.offerId}><div className="radar-offer-top"><a href={m.sourceUrl} target="_blank" rel="noreferrer">{m.supplierName} <ExternalLink size={12}/></a><span className={`radar-match ${m.status === "specified" ? "specified" : ""}`}>{m.status === "specified" ? "Указанные параметры совпадают" : "Кандидат — нужны уточнения"}</span></div><p className="radar-offer-name">{m.name}</p><div className="radar-offer-facts"><div><small>Цена из источника</small><strong>{m.price !== null ? `${money(m.price)} / ${m.unit || "ед. не указана"}` : "По запросу"}</strong><span>{m.priceDate || "Дата цены не указана"}{m.freshness === "stale" ? " · устарела" : ""}</span></div><div><small>Опубликованный остаток</small><strong>{m.stockQuantity !== null ? `${fmt.format(m.stockQuantity)} ${m.stockUnit || ""}` : "Не указан"}</strong><span>{m.stockCity || "Регион склада не подтверждён"}</span>{m.stockSourceUrl && <a href={m.stockSourceUrl} target="_blank" rel="noreferrer">Складская справка</a>}</div>{m.indicativeCost !== null && <div><small>Цена × объём</small><strong>{money(m.indicativeCost)}</strong><span>Доставка не учтена</span></div>}</div><div className="radar-offer-spec">{m.attributes.standards.length ? m.attributes.standards.join(" · ") : "Номер ГОСТ не указан"}{m.attributes.steelGrade ? ` · ${m.attributes.steelGrade}` : ""}{m.jev && <span>Jev: {Math.round(m.jev.probability * 100)}% · {m.jev.model}</span>}</div><ul className="radar-issues">{m.issues.map(x => <li key={x}>{x}</li>)}</ul></article>)}
            </div></details>)}
          {current.documents.length > 0 && <div className="radar-documents"><h3>Документы</h3>{current.documents.map((doc, i) => <div key={i}>{doc.url ? <a href={doc.url} target="_blank" rel="noreferrer">{doc.text || "Документ"} <ExternalLink size={12}/></a> : <span>{doc.text}</span>}<small>{doc.status === "parsed" ? `${doc.items} позиций извлечено` : doc.status === "link_unavailable" ? "Прямая загрузка недоступна" : doc.error || "Нужен разбор формата"}</small></div>)}</div>}
          <p className="fine-print"><ShieldCheck size={15}/>Начальная цена закупки не гарантирует выручку. Прибыль появится в расчёте после проверки всех позиций, НДС, доставки и условий.</p>
        </section> : <section className="panel radar-detail radar-empty"><PackageSearch size={34}/><p>Выберите закупку из списка.</p></section>}
      </div>}
      {tab === "stock" && <div className="panel"><div className="radar-table-caption"><h2>Количество, опубликованное поставщиками</h2><p>{stock.length} строк по фильтру · показаны первые 100. Складская справка отражает дату источника, наличие перед заказом уточняется.</p></div><Table><TableHeader><TableRow><TableHead>Материал</TableHead><TableHead>Поставщик / склад</TableHead><TableHead>Количество</TableHead><TableHead>Цена</TableHead></TableRow></TableHeader><TableBody>{stock.slice(0,100).map(o => <TableRow key={o.id}><TableCell className="radar-stock-name"><a href={o.sourceUrl} target="_blank" rel="noreferrer">{o.name}</a></TableCell><TableCell>{o.supplierName}<span className="product-sub">{o.stockCity || "Склад не подтверждён"}</span></TableCell><TableCell>{fmt.format(o.stockQuantity)} {o.stockUnit}</TableCell><TableCell>{o.price !== null ? `${money(o.price)} / ${o.unit || "ед. не указана"}` : "По запросу"}</TableCell></TableRow>)}</TableBody></Table></div>}
      {tab === "sources" && <div className="panel"><div className="radar-table-caption"><h2>Охват и состояние сбора</h2><p>{data.scope}</p><p>Jev: {data.jev.configured ? `${data.jev.calls} вызовов · ошибок ${data.jev.errors}` : "ключ на сборщике не настроен"}. {data.automation.mode === "manual" ? "Срез получен ручным запуском." : `Автоматическое обновление: ориентир каждые ${data.intervalMinutes / 60} ч.`}</p></div><Table><TableHeader><TableRow><TableHead>Источник</TableHead><TableHead>Тип</TableHead><TableHead>Результат</TableHead><TableHead>Строк</TableHead></TableRow></TableHeader><TableBody>{data.sources.map((s, i) => <TableRow key={`${s.id}-${i}`}><TableCell>{s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.name}</a> : s.name}</TableCell><TableCell>{kindLabel[s.kind] || s.kind}</TableCell><TableCell><span className={`radar-source-status ${s.status === "ok" ? "ok" : ""}`}>{sourceLabel[s.status] || s.status}</span>{s.error && <span className="product-sub radar-source-error">{s.error}</span>}</TableCell><TableCell>{s.rows ?? "—"}</TableCell></TableRow>)}</TableBody></Table></div>}
    </>}
  </div>;
}
