import { useCallback, useEffect, useMemo, useState } from "react";
import { Bookmark, Check, ClipboardList, Clock3, Download, ExternalLink, FileText, RefreshCw, Search } from "lucide-react";

type Document = { text: string; url?: string | null; status: string; items?: number; error?: string };
type Item = { id: string; name: string; quantity?: number | null; unit?: string | null; sourceUrl: string; evidence?: string; attributes?: { standards?: string[] } };
type Notice = {
  id: string; number?: string; title: string; buyer?: string | null; category?: string; noticeKind?: string;
  sourceName?: string; sourceUrl: string; listingUrl: string; sourceStatus: string; status: string;
  checkedAt: string; publishedAt?: string | null; deadline?: string | null; budget?: number | null;
  plannedPeriod?: string; plannedStart?: string; plannedEnd?: string; description?: string; procedure?: string;
  firstSeenAt?: string; lastChangedAt?: string | null; changes?: { at: string; fields: string[] }[];
  documents: Document[]; documentsCheckedAt?: string; items: Item[]; issues: string[]; evidence?: string;
  paymentTerms?: string; deliveryTerms?: string; deliveryAddress?: string; contactStatus?: string; primarySourceStatus?: string;
};
type Source = { id: string; name: string; kind: string; url: string; status: string; rows?: number; error?: string; note?: string; access?: string; checkedAt?: string };
type Data = { generatedAt: string; tenders: Notice[]; sources: Source[]; scope: string; intervalMinutes: number; pricesEnabled?: boolean; automation: { mode: string } };
type Profile = { words: string; exclude: string; category: string; budget: string; days: string; unknownBudget: boolean };
const emptyProfile: Profile = { words: "", exclude: "", category: "all", budget: "", days: "", unknownBudget: true };
const fmt = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 3 });
const money = (x?: number | null) => x != null ? `${fmt.format(x)} ₽` : "Не опубликован";
const date = (x?: string | null) => x ? new Date(x).toLocaleString("ru-RU", { timeZone: "Asia/Yekaterinburg", day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "Не опубликован";
const typeLabel: Record<string, string> = { tender: "Закупка", request: "Запрос покупателя", plan: "План" };
const statusLabel: Record<string, string> = { active: "Открыта по карточке", planned: "Планируется", closed: "Срок завершён", unknown_deadline: "Срок неизвестен", needs_update: "Требует обновления" };
const docLabel: Record<string, string> = { parsed: "Разобран", found: "Ссылка найдена", plan: "План", registration_required: "Загрузка требует входа", link_unavailable: "Прямая загрузка недоступна", needs_adapter: "Требуется разбор формата", error: "Не загружен" };
function state(n: Notice) {
  if (n.deadline && Date.parse(n.deadline) <= Date.now() || n.plannedEnd && Date.parse(n.plannedEnd) <= Date.now()) return "closed";
  if (Date.now() - Date.parse(n.checkedAt) > 86400000 || n.sourceStatus !== "ok") return "needs_update";
  return n.status;
}
function recent(x?: string | null) { return !!x && Date.now() - Date.parse(x) < 86400000; }
function readLocal<T>(key: string, fallback: T): T { try { return JSON.parse(localStorage.getItem(key) || "null") ?? fallback; } catch { return fallback; } }
function csv(rows: unknown[][], name: string) {
  const quote = (x: unknown) => { const s = String(x ?? ""); return `"${(/^[=+\-@]/.test(s) ? "'" : "") + s.replaceAll('"', '""')}"`; };
  const url = URL.createObjectURL(new Blob(["\ufeff" + rows.map(r => r.map(quote).join(";")).join("\r\n")], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a"); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function DemandMonitor({ dataUrl }: { dataUrl: string }) {
  const [data, setData] = useState<Data | null>(null), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const [tab, setTab] = useState("all"), [status, setStatus] = useState("current"), [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState(""), [sort, setSort] = useState("deadline"), [favoritesOnly, setFavoritesOnly] = useState(false);
  const [profile, setProfile] = useState<Profile>(() => ({ ...emptyProfile, ...readLocal<Partial<Profile>>("demand-profile", {}) }));
  const [work, setWork] = useState<Record<string, string>>(() => readLocal("demand-work", {}));
  const [favorites, setFavorites] = useState<string[]>(() => { const v = readLocal<unknown>("demand-favorites", []); return Array.isArray(v) ? v.filter(x => typeof x === "string") : []; });
  const [tick, setTick] = useState(0);
  useEffect(() => { try { localStorage.setItem("demand-profile", JSON.stringify(profile)); localStorage.setItem("demand-work", JSON.stringify(work)); localStorage.setItem("demand-favorites", JSON.stringify(favorites)); } catch { /* Browsers can disable local storage. */ } }, [profile, work, favorites]);
  const load = useCallback(async () => {
    setBusy(true);
    try { const r = await fetch(dataUrl, { cache: "no-store" }); if (!r.ok) throw new Error(); const value = await r.json() as Data; if (!Array.isArray(value.tenders)) throw new Error(); setData(value); setError(""); }
    catch { setError("Не удалось получить срез. Последний загруженный список сохранён на экране."); }
    finally { setBusy(false); }
  }, [dataUrl]);
  useEffect(() => { void load(); const t = setInterval(() => { setTick(x => x + 1); void load(); }, 90000); return () => clearInterval(t); }, [load]);
  const notices = data?.tenders || [];
  const categories = [...new Set(notices.map(n => n.category || "Прочее"))].sort();
  const results = useMemo(() => {
    const words = profile.words.toLowerCase().split(",").map(x => x.trim()).filter(Boolean), excludes = profile.exclude.toLowerCase().split(",").map(x => x.trim()).filter(Boolean);
    return (data?.tenders || []).filter(n => {
      const s = state(n), body = `${n.title} ${n.buyer || ""} ${n.number || n.id} ${n.description || ""} ${n.items.map(i => i.name).join(" ")}`.toLowerCase();
      const kind = n.noticeKind || "tender";
      if (tab !== "all" && tab !== "sources" && tab !== kind) return false;
      if (status === "current" && !["active", "unknown_deadline", "planned"].includes(s) || status === "new" && !recent(n.firstSeenAt) || status === "changed" && !recent(n.lastChangedAt) || !["current", "all", "new", "changed"].includes(status) && status !== s) return false;
      if (favoritesOnly && !favorites.includes(n.id) || profile.category !== "all" && (n.category || "Прочее") !== profile.category) return false;
      if (!body.includes(query.toLowerCase()) || words.length && !words.some(w => body.includes(w)) || excludes.some(w => body.includes(w))) return false;
      if (n.budget == null && !profile.unknownBudget || n.budget != null && profile.budget && n.budget < Number(profile.budget)) return false;
      if (profile.days) { const due = n.deadline || n.plannedStart; if (!due || Date.parse(due) < Date.now() + Number(profile.days) * 86400000) return false; }
      return true;
    }).sort((a, b) => {
      if (sort === "budget") return (b.budget ?? -1) - (a.budget ?? -1);
      if (sort === "newest") return Date.parse(b.publishedAt || b.firstSeenAt || b.checkedAt) - Date.parse(a.publishedAt || a.firstSeenAt || a.checkedAt);
      const rank = (n: Notice) => ["active", "unknown_deadline", "planned", "closed", "needs_update"].indexOf(state(n));
      return rank(a) - rank(b) || Date.parse(a.deadline || a.plannedStart || "9999-12-31") - Date.parse(b.deadline || b.plannedStart || "9999-12-31");
    });
  }, [data, profile, query, tab, status, favoritesOnly, favorites, sort, tick]);
  const current = results.find(n => n.id === selected) || results[0];
  const setField = (key: keyof Profile, value: string | boolean) => setProfile(p => ({ ...p, [key]: value }));
  function toggle(n: Notice) { setFavorites(xs => xs.includes(n.id) ? xs.filter(x => x !== n.id) : [...xs, n.id]); }
  function exportList() { csv([["Номер", "Название", "Вид", "Заказчик", "Категория", "Бюджет, RUB", "Срок, ISO", "Планируемый период", "Статус", "Первое обнаружение", "Последнее изменение", "Карточка"], ...results.map(n => [n.number || n.id, n.title, typeLabel[n.noticeKind || "tender"], n.buyer, n.category, n.budget, n.deadline, n.plannedPeriod, statusLabel[state(n)], n.firstSeenAt, n.lastChangedAt, n.listingUrl])], "perm-zakupki.csv"); }
  return <div className="radar demand">
    <div className="radar-toolbar"><div><span className="section-label">МОНИТОРИНГ СПРОСА</span><p>{data ? `Сбор: ${date(data.generatedAt)} · время Перми · каждые ${data.intervalMinutes / 60} ч.` : "Загружаем закупки…"}</p></div><div className="radar-actions"><button onClick={() => void load()} disabled={busy}><RefreshCw size={15} className={busy ? "radar-spin" : ""}/>Обновить список</button><button onClick={exportList} disabled={!data}><Download size={15}/>CSV списка</button></div></div>
    {error && <div className="error-box" role="alert">{error}</div>}
    <div className="metric-grid radar-metrics">
      {[[ClipboardList, "Открыты по карточкам", notices.filter(n => state(n) === "active").length, "Срок и условия сверяем на площадке"], [Search, "Впервые найдены за сутки", notices.filter(n => recent(n.firstSeenAt)).length, "Дата обнаружения нашим сборщиком"], [Clock3, "Планы на ближайшие месяцы", notices.filter(n => state(n) === "planned").length, "Отдельно от приёма заявок"], [FileText, "С извлечённым объёмом", notices.filter(n => n.items.some(i => i.quantity != null)).length, "Количество указано в источнике"]].map(([Icon, label, count, hint], i) => { const I = Icon as typeof Search; return <div className="metric-card" key={i}><div className={`metric-icon ${["amber", "blue", "green", "violet"][i]}`}><I size={20}/></div><span>{String(label)}</span><strong>{Number(count)}</strong><small>{String(hint)}</small></div>; })}
    </div>
    <details className="demand-profile"><summary>Мой профиль поиска <span>Сохраняется в этом браузере</span></summary><div className="demand-profile-grid">
      <label>Искать хотя бы одно слово<input value={profile.words} onChange={e => setField("words", e.target.value)} placeholder="кабель, ремонт, уборка"/></label>
      <label>Исключить слова<input value={profile.exclude} onChange={e => setField("exclude", e.target.value)} placeholder="канцелярия, аренда"/></label>
      <label>Бюджет от, ₽<input type="number" min="0" value={profile.budget} onChange={e => setField("budget", e.target.value)}/></label>
      <label>До подачи не меньше, дней<input type="number" min="0" max="365" value={profile.days} onChange={e => setField("days", e.target.value)}/></label>
      <label className="demand-checkbox"><input type="checkbox" checked={profile.unknownBudget} onChange={e => setField("unknownBudget", e.target.checked)}/>Показывать без бюджета</label>
      <button onClick={() => setProfile(emptyProfile)}>Сбросить профиль</button>
    </div></details>
    <div className="radar-tabs" role="tablist" aria-label="Виды спроса">{[["all", "Весь спрос"], ["tender", "Закупки"], ["request", "Запросы покупателей"], ["plan", "Планы"], ["sources", "Источники"]].map(([key, label]) => <button key={key} role="tab" aria-selected={tab === key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}>{label}</button>)}</div>
    {tab === "sources" ? <div className="demand-sources"><p className="demand-scope">{data?.scope}</p>{data?.sources.filter(s => s.kind === "procurement").map((s, i) => <article className="panel" key={`${s.id}-${i}`}><div><a href={s.url} target="_blank" rel="noreferrer">{s.name} <ExternalLink size={13}/></a><span className={`radar-source-status ${s.status === "ok" ? "ok" : ""}`}>{s.status === "ok" ? `${s.rows ?? 0} записей` : "Доступ не подтверждён"}</span></div><p>{s.access || s.note}</p>{s.access && s.note && <p>{s.note}</p>}{s.error && <p className="demand-source-error">{s.error}</p>}<small>Проверка: {date(s.checkedAt)}</small></article>)}<article className="panel"><strong>Что означает открытый доступ</strong><p>Карточка, документация, контакты и подача заявки — разные уровни доступа. ЕИС и отдельные ЭТП могут ограничивать автоматический сбор. Supl.biz скрывает за входом заказы последних суток. Охват источников указан выше.</p></article></div> : <>
      <div className="demand-filters"><label className="search-field"><Search size={16}/><input aria-label="Поиск по закупкам" value={query} onChange={e => setQuery(e.target.value)} placeholder="Название, заказчик, номер"/></label>
        <select aria-label="Категория закупки" value={profile.category} onChange={e => setField("category", e.target.value)}><option value="all">Все категории</option>{categories.map(x => <option key={x}>{x}</option>)}</select>
        <select aria-label="Статус закупки" value={status} onChange={e => setStatus(e.target.value)}><option value="current">Текущие и планы</option><option value="active">Открыты по карточкам</option><option value="new">Впервые найдены за сутки</option><option value="changed">Изменились за сутки</option><option value="unknown_deadline">Без срока</option><option value="closed">Срок завершён</option><option value="needs_update">Требуют обновления</option><option value="all">Все записи</option></select>
        <select aria-label="Сортировка" value={sort} onChange={e => setSort(e.target.value)}><option value="deadline">Ближайший срок</option><option value="newest">Свежие публикации</option><option value="budget">Больший бюджет</option></select>
        <button className={favoritesOnly ? "demand-selected" : ""} onClick={() => setFavoritesOnly(x => !x)}><Bookmark size={14}/>Избранное ({favorites.length})</button>
      </div>
      <div className="radar-workspace"><section className="radar-list" aria-label="Список спроса"><div className="radar-list-count">{results.length} записей по фильтрам</div>{results.map(n => <button key={n.id} className={`radar-card ${current?.id === n.id ? "selected" : ""}`} onClick={() => setSelected(n.id)}><span className={`radar-status ${state(n) === "active" ? "open" : ""}`}>{typeLabel[n.noticeKind || "tender"]}</span>{recent(n.lastChangedAt) ? <span className="demand-badge">Изменилась</span> : recent(n.firstSeenAt) ? <span className="demand-badge">Впервые найдена</span> : null}{favorites.includes(n.id) && <Bookmark className="demand-star" size={13}/>}<strong>{n.title}</strong><span className="radar-buyer">{n.buyer || "Заказчик не опубликован"}</span><span className="radar-card-bottom"><b>{money(n.budget)}</b><span><Clock3 size={13}/>{n.plannedPeriod || date(n.deadline)}</span></span><span className="radar-coverage">{n.category || "Прочее"} · {statusLabel[state(n)]}</span></button>)}{!results.length && <div className="radar-empty"><Search size={25}/><strong>Записей по фильтрам нет</strong><p>Сбросьте профиль или измените статус.</p></div>}</section>
        {current ? <section className="panel radar-detail" aria-label="Карточка спроса"><div className="radar-detail-head"><div><span className="section-label">{typeLabel[current.noticeKind || "tender"]} · {current.number || current.id}</span><h2>{current.title}</h2><p>{current.buyer || "Заказчик не опубликован"}</p></div><button onClick={() => toggle(current)} aria-label={favorites.includes(current.id) ? "Убрать из избранного" : "В избранное"}>{favorites.includes(current.id) ? <Check size={17}/> : <Bookmark size={17}/>}</button></div>
          <div className="radar-detail-meta"><div><span>Опубликованный бюджет</span><strong>{money(current.budget)}</strong><small>НДС и обеспечение — в документации</small></div><div><span>{current.noticeKind === "plan" ? "Период из плана" : "Приём до, время Перми"}</span><strong>{current.plannedPeriod || date(current.deadline)}</strong><small>{statusLabel[state(current)]}</small></div><div><span>Источник</span><strong>{current.sourceName || "Открытая карточка"}</strong><small>Проверка: {date(current.checkedAt)}</small></div></div>
          <div className="radar-detail-links"><a href={current.listingUrl} target="_blank" rel="noreferrer">Открыть карточку <ExternalLink size={13}/></a>{current.sourceUrl !== current.listingUrl && <a href={current.sourceUrl} target="_blank" rel="noreferrer">Первичная площадка <ExternalLink size={13}/></a>}</div>
          <div className="demand-work"><label>Мой этап<select aria-label="Мой этап" value={work[current.id] || "new"} onChange={e => setWork(x => ({ ...x, [current.id]: e.target.value }))}><option value="new">Не разобрана</option><option value="review">Проверяю условия</option><option value="proposal">Готовлю предложение</option><option value="skip">Пропускаю</option></select></label><small>Личная отметка в этом браузере</small></div>
          {!!current.issues.length && <div className="radar-warning"><div>{current.issues.map((x, i) => <p key={i}>{x}</p>)}</div></div>}
          {current.description && <p className="demand-description">{current.description}</p>}{current.procedure && <p className="demand-description">{current.procedure}</p>}
          {(current.paymentTerms || current.deliveryTerms || current.deliveryAddress) && <dl className="radar-terms">{current.paymentTerms && <><dt>Оплата</dt><dd>{current.paymentTerms}</dd></>}{current.deliveryTerms && <><dt>Поставка</dt><dd>{current.deliveryTerms}</dd></>}{current.deliveryAddress && <><dt>Адрес</dt><dd>{current.deliveryAddress}</dd></>}</dl>}
          <div className="radar-items-title"><h3>Позиции и объёмы из источника</h3><span>{current.items.length} позиций</span></div>{!current.items.length ? <p className="demand-description">Спецификация ещё не извлечена. Название процедуры не устанавливает точный объём заказа.</p> : <><button onClick={() => csv([["Позиция", "Количество", "Единица", "ГОСТ из источника", "Доказательство", "Источник"], ...current.items.map(i => [i.name, i.quantity, i.unit, i.attributes?.standards?.join("; "), i.evidence, i.sourceUrl])], `spec-${current.id}.csv`)}><Download size={14}/>CSV спецификации</button>{current.items.map(i => <div className="demand-item" key={i.id}><strong>{i.name}</strong><span>{i.quantity != null ? `${fmt.format(i.quantity)} ${i.unit || "единица не указана"}` : "Объём не опубликован"}</span><small>{i.attributes?.standards?.join(" · ") || "ГОСТ не указан"}</small><a href={i.sourceUrl} target="_blank" rel="noreferrer">{i.evidence || "Источник позиции"} <ExternalLink size={11}/></a></div>)}</>}
          <div className="radar-documents"><h3>Документация</h3>{!current.documents.length && <p className="demand-description">Прямая ссылка на документы пока не получена. Проверьте карточку на площадке.</p>}{current.documents.map((d, i) => <div key={i}>{d.url ? <a href={d.url} target="_blank" rel="noreferrer">{d.text} <ExternalLink size={12}/></a> : <span>{d.text}</span>}<small>{docLabel[d.status] || d.status}{d.items ? ` · ${d.items} позиций` : ""}{d.error ? ` · ${d.error}` : ""}</small></div>)}{current.documentsCheckedAt && <small className="demand-doc-time">Проверка документации: {date(current.documentsCheckedAt)}</small>}</div>
          <div className="demand-history"><h3>История наблюдений</h3><p>Впервые найдена: {date(current.firstSeenAt)}. Публикация: {date(current.publishedAt)}.</p>{current.changes?.length ? current.changes.map((c, i) => <p key={i}>{date(c.at)} · {c.fields.join(", ")}</p>) : <p>Изменений после первого наблюдения не зафиксировано.</p>}</div>
          <p className="fine-print">Сначала проверяем доступ к документации, условия участия и срок. Цена контракта не является прогнозом прибыли.</p>
        </section> : <section className="panel radar-detail radar-empty"><ClipboardList size={30}/><p>Выберите запись из списка.</p></section>}
      </div>
    </>}
  </div>;
}
