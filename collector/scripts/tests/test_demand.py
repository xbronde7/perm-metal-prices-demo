import io
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import openpyxl

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from radar.demand import synapse, pzsp_plan, supl, public_details
from radar.pipeline import Store, tender_status
from radar.procurement import parse_listing, detail_fields

FEED={"id":"synapse-test","url":"https://example.com/feed","region":"Пермский край","scope":"all"}


class DemandTests(unittest.TestCase):
    def test_survey_lot_identity_and_moscow_deadline(self):
        def card(lot):
            return f'''<div class="sp-tender-block"><a class="sp-tb-title" href="/zakupki/etp/123%23{lot}--x">Опрос рынка на кабель</a><meta itemprop="location" content="Пермский край"><div class="sp-tb-source">Запрос ценовой информации</div><div class="pro-pcr-desc">прием заявок 10:00 · 29.09.2026 — 12:00 · 05.10.2026</div></div>'''
        rows=synapse((card(1)+card(2)).encode(),FEED["url"],FEED)
        self.assertEqual(len(rows),2)
        self.assertNotEqual(rows[0]["id"],rows[1]["id"])
        self.assertEqual(rows[0]["deadline"],"2026-10-05T12:00:00+03:00")
        self.assertEqual(rows[0]["noticeKind"],"request")
        self.assertIsNone(rows[0]["budget"])

    def test_plan_is_not_an_active_notice_or_exact_deadline(self):
        book=openpyxl.Workbook();s=book.active
        s.append(["План"]);s.append(["№","Объект","Работы","Период"])
        s.append([1,"Дом 1","Окна","Октябрь 2026"])
        s.append([None,None,"Вентиляция","Ноябрь 2026"])
        buf=io.BytesIO();book.save(buf)
        rows=pzsp_plan(buf.getvalue(),"https://example.com/plan.xlsx",FEED,datetime(2026,9,30,tzinfo=timezone.utc))
        self.assertEqual(len(rows),2)
        self.assertIn("Дом 1",rows[1]["title"])
        self.assertIsNone(rows[0]["deadline"])
        self.assertEqual(rows[0]["items"],[])
        self.assertEqual(tender_status(rows[0],datetime(2026,9,30,tzinfo=timezone.utc)),"planned")

    def test_public_doc_name_does_not_invent_download(self):
        t={"feedId":"synapse-test","documents":[]}
        public_details('<div class="tender-docs-line"><div class="tender-link pro-open-form">ТЗ</div></div>'.encode(),"https://example.com",t)
        self.assertIsNone(t["documents"][0]["url"])
        self.assertEqual(t["documents"][0]["status"],"registration_required")

    def test_budget_uses_only_price_cell_and_currency_is_not_a_digit(self):
        card='''<div class="sp-tender-block"><div class="sp-tb-right-block"><div>Закупка</div><div>123</div><div><div>начальная цена</div><div>1 234 567,89<span>₽</span></div></div></div><a class="sp-tb-title" href="/zakupki/etp/123--x">Ремонт</a><meta itemprop="location" content="Пермский край"></div>'''
        self.assertEqual(synapse(card.encode(),FEED["url"],FEED)[0]["budget"],1234567.89)
        self.assertIsNone(synapse(card.replace('1 234 567,89','не указана').encode(),FEED["url"],FEED)[0]["budget"])

    def test_regional_feed_does_not_establish_delivery_region(self):
        card='''<div class="a_2BCsImjE"><h4><a href="/orders/cable-42/">Кабель</a></h4><time datetime="2026-09-28T00:00:00Z"></time><a href="/orders/moscow-region1149/">Москва</a></div>'''
        self.assertEqual(supl(card.encode(),"https://example.com/orders/perm-region362/",FEED),[])
        rows=supl(card.replace('moscow-region1149','perm-region362').replace('Москва','Пермь').encode(),FEED["url"],FEED)
        self.assertEqual(len(rows),1)
        self.assertIsNone(rows[0]["deadline"])

    def test_history_ignores_poll_and_offer_results_tracks_source_changes(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/"state.db")
            t={"id":"test","title":"Уборка","deadline":"2026-10-05T00:00:00Z","checkedAt":"2026-09-30T00:00:00Z","items":[],"documents":[]}
            store.track_demand(t)
            self.assertEqual(t["changes"],[])
            t.update(checkedAt="2026-09-30T04:00:00Z",matchedItems=3,issues=["HTTP 403"])
            store.track_demand(t)
            self.assertEqual(t["changes"],[])
            t["deadline"]="2026-10-06T00:00:00Z"
            store.track_demand(t)
            self.assertEqual(t["changes"][0]["fields"],["Срок подачи"])
            self.assertEqual(t["firstSeenAt"],"2026-09-30T00:00:00Z")
            store.close()

    def test_b2b_live_buyer_and_canonical_title_ignore_sale_and_header(self):
        row='''<tr><td><a href="/market/x/tender-123/">Тендер № 123<div class="search-results-title-desc"><p class="search-results-title-type">Тип закупки: Запрос предложений</p>Перевозки персонала</div></a></td><td>ООО Покупатель</td><td>Пермский край</td><td>29.09.2026 11:00</td><td>05.10.2026 12:00</td></tr>'''
        rows=parse_listing(('<table>'+row+row.replace('Запрос предложений','Объявление о продаже')+'</table>').encode(),FEED["url"],scope="all")
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["buyer"],"ООО Покупатель")
        self.assertEqual(rows[0]["title"],"Перевозки персонала")
        detail_fields('<h1>Тендер № 123 Перевозки персонала</h1>'.encode(),"https://example.com/market/x",rows[0])
        self.assertEqual(rows[0]["title"],"Перевозки персонала")


if __name__=="__main__":unittest.main()
