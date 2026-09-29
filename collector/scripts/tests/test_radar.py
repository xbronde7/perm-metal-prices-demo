import io
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from radar.domain import attributes, compare_specs, convert, unit
from radar.documents import extract, table_records
from radar.pipeline import match_item, Store, tender_status
from radar.procurement import parse_listing, merge_documents
from radar.transport import RobotsPolicy, Fetcher


class MatchingTests(unittest.TestCase):
    def test_same_rebar_parameters(self):
        a=attributes("Арматура А500С Ø12 ГОСТ 34028-2016")
        b=attributes("Арматура 12 А500С ГОСТ34028-16")
        self.assertEqual(compare_specs(a,b)["status"],"specified")

    def test_class_conflict_is_rejected(self):
        self.assertEqual(compare_specs(attributes("Арматура 12 А500С ГОСТ34028-2016"),attributes("Арматура 12 А400 ГОСТ34028-2016"))["status"],"rejected")

    def test_missing_grade_never_exact(self):
        self.assertEqual(compare_specs(attributes("Арматура 12 ГОСТ34028-2016"),attributes("Арматура 12 А500С ГОСТ34028-2016"))["status"],"candidate")

    def test_steel_conflict(self):
        self.assertEqual(compare_specs(attributes("Лист горячекатаный Ст3 10мм ГОСТ14637-89"),attributes("Лист г/к 10 Ст65Г"))["status"],"rejected")

    def test_prefixed_low_alloy_steel_is_not_unknown(self):
        offered=attributes("Лист г/к 10 Ст09Г2С 10.00")
        self.assertEqual(offered["steelGrade"],"09Г2С")
        self.assertEqual(compare_specs(attributes("Лист Ст3 10мм"),offered)["status"],"rejected")

    def test_special_steel_never_matches_ordinary_sheet(self):
        for required in ("Лист HARDOX 500 16*2000*4000", "Лист MAGSTRONG W700 12*2000*6000"):
            self.assertEqual(compare_specs(attributes(required),attributes("Лист Ст3 16*2000*4000"))["status"],"rejected")

    def test_stainless_grade_does_not_become_dimensions(self):
        a=attributes("Лист 12Х18Н10Т 5мм ГОСТ7350-77")
        self.assertEqual(a["steelGrade"],"12Х18Н10Т")
        self.assertEqual(a["dimensions"],[])
        self.assertEqual(a["thickness"],5)

    def test_gost_without_year_is_visible_but_not_exact(self):
        a=attributes("Лист Ст3 10мм ГОСТ14637-89")
        b=attributes("Лист Ст3 10мм ГОСТ14637")
        self.assertEqual(b["standards"],["ГОСТ 14637"])
        self.assertEqual(compare_specs(a,b)["status"],"candidate")

    def test_other_dimensional_gost_does_not_verify_product_gost(self):
        a=attributes("Лист Ст3 10мм ГОСТ14637-89")
        b=attributes("Лист Ст3 10мм ГОСТ19903")
        self.assertEqual(compare_specs(a,b)["status"],"candidate")
        c=attributes("Лист Ст3 10мм ГОСТ14637-2020")
        self.assertEqual(compare_specs(a,c)["status"],"rejected")

    def test_round_diameter_after_steel(self):
        a=attributes("Круг 09Г2С-12 D10мм ГОСТ2590-06")
        self.assertEqual(a["diameter"],10)
        self.assertEqual(a["steelGrade"],"09Г2С-12")

    def test_beam_profile_is_not_missing_family(self):
        a=attributes("Двутавр Ст3 16 ГОСТ8239-89")
        self.assertEqual(a["family"],"балка")
        self.assertEqual(a["profileSize"],"16")

    def test_coarse_okpd_is_not_product(self):
        required=attributes("Прокат листовой горячекатаный шириной не менее 600 мм")
        self.assertIsNone(required["thickness"])
        self.assertEqual(compare_specs(required,attributes("Лист 10 Ст3"))["status"],"rejected")

    def test_voltage_conflict(self):
        a=attributes("Кабель ВВГнг(А)-LS 3х2,5 0,66кВ ГОСТ31996-2012")
        b=attributes("Кабель ВВГнг(А)-LS 3х2,5 1кВ ГОСТ31996-2012")
        self.assertEqual(compare_specs(a,b)["status"],"rejected")

    def test_valve_is_not_rebar(self):
        self.assertEqual(attributes("Трубопроводная арматура")["family"],"трубопроводная арматура")

    def test_dimensional_conversion_only(self):
        self.assertEqual(convert(1200,"кг","т"),1.2)
        self.assertEqual(convert(.4,"км","м"),400)
        self.assertIsNone(convert(20,"м","т"))
        self.assertIsNone(convert(20,"шт","кг"))
        self.assertIsNone(convert(None,"кг","т"))

    def offer(self,**changes):
        return {"id":"o1","supplier":"s","supplierName":"S","name":"Арматура 12 А500С ГОСТ34028-2016","sourceUrl":"https://example.com/p.xls","price":50000,"unit":"т","vat":"included","sourceStatus":"ok","regionStatus":"registered","checkedAt":datetime.now(timezone.utc).isoformat(),"priceDate":datetime.now(timezone.utc).strftime("%d.%m.%Y"),"stockQuantity":0,"stockUnit":"т","attributes":attributes("Арматура 12 А500С ГОСТ34028-2016"),**changes}

    def requirement(self,qty=1200,u="кг"):
        return {"name":"Арматура 12 А500С ГОСТ34028-2016","quantity":qty,"unit":u,"attributes":attributes("Арматура 12 А500С ГОСТ34028-2016")}

    def test_price_times_converted_quantity(self):
        m=match_item(self.requirement(),[self.offer()])[0]
        self.assertEqual(m["indicativeCost"],60000)
        self.assertEqual(m["stockQuantity"],0)
        self.assertEqual(m["stockCheck"],"insufficient")

    def test_minimum_order_price_is_not_applicable(self):
        m=match_item(self.requirement(),[self.offer(minOrderQuantity=5,minOrderUnit="т")])[0]
        self.assertIsNone(m["indicativeCost"])

    def test_stale_offer_never_enters_estimate(self):
        m=match_item(self.requirement(),[self.offer(checkedAt=(datetime.now(timezone.utc)-timedelta(days=3)).isoformat())])[0]
        self.assertEqual(m["freshness"],"stale")
        self.assertIsNone(m["indicativeCost"])

    def test_unknown_unit_never_enters_estimate(self):
        self.assertIsNone(match_item(self.requirement(),[self.offer(unit=None)])[0]["indicativeCost"])

    def test_unknown_quantity_never_enters_estimate(self):
        self.assertIsNone(match_item(self.requirement(None),[self.offer()])[0]["indicativeCost"])

    def test_unverified_region_excluded(self):
        self.assertEqual(match_item(self.requirement(),[self.offer(regionStatus="needs_verification")]),[])


class IngestionTests(unittest.TestCase):
    def test_fractional_quantity_and_zero_placeholder(self):
        rows=[["Наименование","Количество","Ед. изм."],["Арматура 12 А500С ГОСТ34028-2016","0,092","т"],["Арматура 14 А500С ГОСТ34028-2016",0,"т"]]
        data=table_records(rows,"https://example.com",mode="demand")
        self.assertEqual(data[0]["quantity"],.092)
        self.assertIsNone(data[1]["quantity"])

    def test_csv_keeps_unknown_unit_and_zero_stock(self):
        raw="Наименование;Цена;Остаток\nКабель ВВГнг-LS 3х2.5;120;0\n".encode()
        data,_=extract(raw,"https://example.com/price.csv")
        self.assertIsNone(data[0]["unit"])
        self.assertEqual(data[0]["stockQuantity"],0)

    def test_document_link_upgrades_unavailable_filename(self):
        docs=merge_documents([{"text":"ТЗ.xlsx","url":None,"status":"link_unavailable"}],[{"text":"ТЗ.xlsx","url":"https://example.com/tz.xlsx","status":"found"}])
        self.assertEqual(len(docs),1)
        self.assertEqual(docs[0]["status"],"found")

    def test_explicit_minimum_applies_to_table_price(self):
        rows=[["Наименование","Цена руб./т"],["Лист Ст3 10мм",50000]]
        data=table_records(rows,"https://example.com/p.xls",context="Цены от 5 т, с НДС")
        self.assertEqual(data[0]["minOrderQuantity"],5)
        self.assertEqual(data[0]["minOrderUnit"],"т")

    def test_actual_event_rows_hidden_positions_and_timezone(self):
        raw=b'''<div itemtype="http://schema.org/Event"><meta itemprop="name" content="Supply metal"><meta itemprop="url" content="https://example.com/l123-1/"><meta itemprop="endDate" content="2026-10-05T06:00:00+03:00"><meta itemprop="price" content="1300000"><div class="card-item__title">x</div></div>'''
        # Use Russian names without copying real supplier/tender records.
        raw=raw.decode().replace("Supply metal","Поставка металлопроката").replace('<div class="card-item__title">x</div>','<table><tr class="card-table__position hidden"><td><table class="tru-position-table"><tr><td>1.</td><td>Арматура 12 А500С ГОСТ34028-2016</td></tr></table></td><td>0,092</td><td>т</td></tr></table>').encode()
        t=parse_listing(raw,"https://example.com/feed")[0]
        self.assertEqual(t["items"][0]["quantity"],.092)
        self.assertTrue(t["deadline"].endswith("+03:00"))

    def test_persistence_upsert_and_history(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)/"s.sqlite")
            store.save("tenders",[{"id":"x","budget":10}]);store.save("tenders",[{"id":"x","budget":20}])
            self.assertEqual(len(store.records("tenders")),1)
            self.assertEqual(store.records("tenders")[0]["budget"],20)
            self.assertEqual(store.db.execute("SELECT count(*) FROM observations").fetchone()[0],2)
            store.close()

    def test_poll_timestamp_does_not_duplicate_history(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)/"s.sqlite")
            store.save("offers",[{"id":"x","price":10,"checkedAt":"2026-09-30T00:00:00Z"}])
            store.save("offers",[{"id":"x","price":10,"checkedAt":"2026-09-30T04:00:00Z"}])
            self.assertEqual(store.db.execute("SELECT count(*) FROM observations").fetchone()[0],1)
            self.assertEqual(store.records("offers")[0]["checkedAt"],"2026-09-30T04:00:00Z")
            store.close()

    def test_deadline_overrides_reported_active(self):
        t={"deadline":"2026-09-01T12:00:00+03:00","checkedAt":"2026-09-30T12:00:00+03:00","sourceStatus":"ok"}
        self.assertEqual(tender_status(t,datetime(2026,9,30,tzinfo=timezone.utc)),"closed")

    def test_robots_wildcards_and_specific_agents(self):
        p=RobotsPolicy("User-agent: *\nDisallow: /private/*\nAllow: /private/public/\nDisallow: /search/*action=documents\n")
        self.assertFalse(p.can_fetch("PermProcurementRadar","https://example.com/private/a"))
        self.assertTrue(p.can_fetch("PermProcurementRadar","https://example.com/private/public/a"))
        self.assertFalse(p.can_fetch("PermProcurementRadar","https://example.com/search/a?action=documents"))
        self.assertTrue(p.can_fetch("PermProcurementRadar","https://example.com/search/number/a/"))

    def test_local_targets_rejected(self):
        for url in ["http://127.0.0.1/x","http://169.254.169.254/x","file:///etc/passwd","http://user:pass@example.com/"]:
            with self.assertRaises(ValueError):Fetcher.validate(url)


if __name__=="__main__":unittest.main()
