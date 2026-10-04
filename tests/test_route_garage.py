"""Журнал гаража (ответ владельца №53): ремонт ֏/км машины, хранение (схема 16), где цена входит в расчёт, API страницы."""
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from route_optimizer import fleet as fl  # noqa: E402
from route_optimizer import garage as gr  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from route_optimizer import views  # noqa: E402
from route_optimizer import vrp  # noqa: E402
from route_optimizer.snapshot import SnapshotCache  # noqa: E402
from test_route_optimizer import _dispatch_setup, _dorder, _no_road_map, client  # noqa: E402,F401
from test_route_store_unload import GOLDEN, _golden_digest  # noqa: E402

AS_OF = date(2026, 10, 1)
WS = AS_OF - timedelta(days=365)            # начало окна (не входит)
DAY = '2026-10-01'
NOW = datetime(2026, 10, 3, 10, 0)


def E(day, kind, amount, km):
    return gr.Entry(day, kind, amount, km)


def _d(s):
    return date.fromisoformat(s)


# ============================== расчёт ֏/км (garage.py) ==============================

def test_price_basic_ready():
    """Первое показание — точка отсчёта (ремонт в этот день не входит); ремонты (start, end]; ДТП и страховка — только
    итоги; 267 дней ≥ 182 и 20 000 км ≥ 500 — готово: 600 000 / 20 000 = 30,0 ֏/км."""
    entries = [E(_d('2026-01-04'), 'repair', 90_000, 100_000), E(_d('2026-03-10'), 'repair', 200_000, 104_000),
               E(_d('2026-04-01'), 'accident', 500_000, 106_000), E(_d('2026-05-01'), 'fixed', 70_000, 108_000),
               E(_d('2026-06-15'), 'repair', 400_000, 112_000), E(_d('2026-09-28'), 'odometer', 0, 120_000)]
    p = gr.price(entries, AS_OF)
    assert (p.status, p.price, p.cost_amd, p.km, p.start, p.end, p.days, p.months) == \
        ('ready', 30.0, 600_000, 20_000.0, _d('2026-01-04'), _d('2026-09-28'), 267, 8)
    assert (p.repair_amd, p.accident_amd, p.fixed_amd) == (690_000, 500_000, 70_000)
    assert gr.effective({'CAR1': p, 'CAR2': replace(p, price=None, status='accumulating')}) == {'CAR1': 30.0}


def test_price_interpolates_at_window_start():
    """Есть показание раньше окна — start = as_of − 365, км в start — по дням между последним до и первым после
    (100 км/день: 30 дней после 100 000 → 103 000); ремонты до начала окна не входят ни в цену, ни в итоги."""
    entries = [E(WS - timedelta(days=30), 'repair', 999_000, 100_000), E(WS + timedelta(days=30), 'odometer', 0, 106_000),
               E(WS + timedelta(days=31), 'repair', 120_000, 106_100), E(AS_OF, 'repair', 30_000, 133_000)]
    p = gr.price(entries, AS_OF)
    assert (p.start, p.end, p.days, p.months) == (WS, AS_OF, 365, 12)
    assert p.km == pytest.approx(30_000.0) and p.cost_amd == 150_000 and p.price == 5.0
    assert p.repair_amd == 150_000


def test_price_interpolation_not_midpoint():
    """Интерполяция — пропорционально дням, а не середина: 10 дней до окна, 30 после."""
    entries = [E(WS - timedelta(days=10), 'odometer', 0, 50_000), E(WS + timedelta(days=30), 'odometer', 0, 54_000),
               E(AS_OF, 'repair', 100_000, 60_000)]
    p = gr.price(entries, AS_OF)
    assert p.km == pytest.approx(60_000 - 51_000) and p.start == WS


def test_price_reading_exactly_at_window_start():
    """Показание ровно в день as_of − 365 — оно и есть км начала (не интерполяция с более ранним)."""
    entries = [E(WS - timedelta(days=10), 'odometer', 0, 1_000), E(WS, 'odometer', 0, 5_000),
               E(WS + timedelta(days=10), 'odometer', 0, 5_100), E(AS_OF, 'repair', 70_000, 12_000)]
    p = gr.price(entries, AS_OF)
    assert p.km == pytest.approx(7_000.0) and p.price == 10.0


def test_price_window_is_half_open_at_start():
    """Запись ровно в день as_of − 365 — вне окна: не в итогах и сама окно не открывает (status none)."""
    p = gr.price([E(WS, 'repair', 10_000, 1_000), E(WS, 'accident', 5_000, 1_000)], AS_OF)
    assert (p.status, p.price, p.repair_amd, p.accident_amd) == ('none', None, 0, 0)
    q = gr.price([E(WS + timedelta(days=1), 'fixed', 5_000, 1_000)], AS_OF)
    assert (q.status, q.fixed_amd, q.days) == ('accumulating', 5_000, 0)


def test_price_first_day_repair_is_reference_point():
    start = _d('2026-02-01')
    entries = [E(start, 'repair', 300_000, 10_000), E(start + timedelta(days=200), 'repair', 60_000, 16_000)]
    p = gr.price(entries, AS_OF)
    assert (p.start, p.cost_amd, p.km, p.price) == (start, 60_000, 6_000.0, 10.0)
    assert p.repair_amd == 360_000          # итоги окна — все ремонты окна


def test_price_ready_thresholds():
    start = _d('2026-03-01')

    def p(days, km):
        return gr.price([E(start, 'odometer', 0, 10_000), E(start + timedelta(days=days), 'repair', 10_000, 10_000 + km)],
                        AS_OF)
    assert (p(182, 500).status, p(182, 500).price) == ('ready', 20.0)
    assert (p(181, 5000).status, p(181, 5000).months, p(181, 5000).price) == ('accumulating', 5, None)
    assert (p(200, 499).status, p(200, 499).price) == ('low_km', None)
    assert p(91, 5000).months == 3


def test_price_rounds_to_tenth():
    start = _d('2026-01-01')
    entries = [E(start, 'odometer', 0, 0), E(start + timedelta(days=200), 'repair', 100_000, 3_000)]
    assert gr.price(entries, AS_OF).price == 33.3


def test_price_ignores_entries_after_as_of_and_none_without_entries():
    entries = [E(_d('2026-01-01'), 'odometer', 0, 1_000), E(_d('2026-08-01'), 'repair', 50_000, 6_000),
               E(_d('2026-10-02'), 'repair', 9_999_999, 6_100)]
    p = gr.price(entries, AS_OF)
    assert (p.end, p.cost_amd, p.repair_amd, p.price) == (_d('2026-08-01'), 50_000, 50_000, 10.0)
    assert gr.price([], AS_OF).status == 'none'
    assert gr.price(entries, _d('2025-12-31')).status == 'none'
    assert gr.price(entries, _d('2027-10-02')).status == 'none'   # журнал за 12 месяцев молчит
    assert gr.price(entries, _d('2027-10-01')).status == 'accumulating'


def test_price_same_day_readings():
    """Несколько показаний в один день: начало — меньшее дня, конец — большее."""
    d0, d1 = _d('2026-01-10'), _d('2026-09-10')
    entries = [E(d0, 'repair', 1, 1_050), E(d0, 'odometer', 0, 1_000), E(d1, 'repair', 30_000, 3_000),
               E(d1, 'fixed', 1, 3_020)]
    p = gr.price(entries, AS_OF)
    assert p.km == 2_020.0 and p.cost_amd == 30_000


# ---- одометр APK ----

def test_apk_readings_extend_journal():
    """Одометр заправок дополняет показания: последнее показание — заправка, покрытие больше 6 месяцев."""
    journal = [E(_d('2026-03-01'), 'odometer', 0, 50_000), E(_d('2026-04-01'), 'repair', 40_000, 51_000)]
    assert gr.price(journal, AS_OF).status == 'accumulating'
    p = gr.price(journal, AS_OF, [(_d('2026-06-01'), 55_000), (_d('2026-09-30'), 58_000)])
    assert (p.status, p.end, p.km, p.price, p.cost_amd) == ('ready', _d('2026-09-30'), 8_000.0, 5.0, 40_000)


def test_no_repairs_is_missing_data_not_zero():
    """Полгода и больше без ремонтов в (start, end] — не 0 ֏/км, а нет данных: только пробег, только страховка и
    заправки, ремонт лишь в день первого показания (точка отсчёта) — цены нет, в расчёте ручное значение."""
    odo = [E(_d('2026-02-01'), 'odometer', 0, 10_000), E(_d('2026-09-01'), 'odometer', 0, 20_000)]
    fixed = [E(_d('2026-02-01'), 'fixed', 90_000, 10_000)]
    first_day = [E(_d('2026-02-01'), 'repair', 70_000, 10_000), E(_d('2026-09-01'), 'accident', 50_000, 20_000)]
    for entries, apk in ((odo, ()), (fixed, [(_d('2026-09-01'), 20_000.0)]), (first_day, ())):
        p = gr.price(entries, AS_OF, apk)
        assert (p.status, p.price, p.cost_amd, p.days >= gr.READY_DAYS, p.km) == ('no_repairs', None, 0, True, 10_000.0)
        assert gr.effective({'CAR1': p}) == {}
    one = gr.price(odo + [E(_d('2026-05-01'), 'repair', 1_000, 15_000)], AS_OF)
    assert (one.status, one.price) == ('ready', 0.1)


def test_apk_conflicting_with_journal_dropped():
    """Журнал главнее: APK меньше более раннего показания журнала, больше более позднего, в день записи журнала —
    отбрасываются; среди APK — меньше уже принятого раннего (опечатка) тоже."""
    journal = [E(_d('2026-01-01'), 'odometer', 0, 10_000), E(_d('2026-05-01'), 'repair', 20_000, 20_000)]
    apk = [(_d('2026-02-01'), 9_000), (_d('2026-03-01'), 25_000), (_d('2026-05-01'), 19_000),
           (_d('2026-03-15'), 15_000), (_d('2026-07-01'), 24_000), (_d('2026-08-01'), 23_000), (_d('2026-09-01'), 26_000)]
    pts = gr.readings(journal, apk)
    assert pts == [(_d('2026-01-01'), 10_000.0), (_d('2026-03-15'), 15_000.0), (_d('2026-05-01'), 20_000.0),
                   (_d('2026-07-01'), 24_000.0), (_d('2026-09-01'), 26_000.0)]
    p = gr.price(journal, AS_OF, apk)
    assert (p.end, p.km, p.cost_amd, p.price) == (_d('2026-09-01'), 16_000.0, 20_000, 1.2)


def test_apk_alone_does_not_start_a_price():
    """Без записей журнала в окне цены нет (иначе машина без ремонтов в журнале получила бы 0 ֏/км)."""
    apk = [(_d('2026-01-01'), 1_000), (_d('2026-09-01'), 30_000)]
    assert gr.price([], AS_OF, apk).status == 'none'
    assert gr.price([E(WS - timedelta(days=5), 'odometer', 0, 900)], AS_OF, apk).status == 'none'
    assert gr.price([E(_d('2026-08-01'), 'odometer', 0, 25_000)], AS_OF, apk + [(_d('2026-10-02'), 99_000)]).end \
        == _d('2026-09-01')   # заправки позже as_of — тоже нет


# ============================== хранение (схема 16) ==============================

TODAY = date(2026, 10, 3)
CARS = {'CAR1', 'CAR2'}


def _item(**kw):
    raw = {'car_code': 'CAR1', 'day': '2026-09-01', 'kind': 'repair', 'what': 'Արգելակներ', 'amount_amd': 50_000,
           'odometer_km': 100_000}
    raw.update(kw)
    item, errors = st.check_garage_entry({k: v for k, v in raw.items() if v is not ...}, CARS, TODAY)
    assert not errors, errors
    return item


@pytest.mark.parametrize('patch,field', [
    ({'car_code': 'CARX'}, 'car_code'), ({'car_code': None}, 'car_code'), ({'day': '2026-10-04'}, 'day'),
    ({'day': '2023-10-02'}, 'day'), ({'day': '2026-W40-1'}, 'day'), ({'day': '03.10.2026'}, 'day'),
    ({'kind': 'tax'}, 'kind'), ({'what': '  '}, 'what'), ({'what': 'x' * 301}, 'what'), ({'what': 5}, 'what'),
    ({'amount_amd': 0}, 'amount_amd'), ({'amount_amd': 100_000_001}, 'amount_amd'), ({'amount_amd': 10.5}, 'amount_amd'),
    ({'amount_amd': True}, 'amount_amd'), ({'amount_amd': '500'}, 'amount_amd'), ({'amount_amd': 10 ** 400}, 'amount_amd'),
    ({'odometer_km': -1}, 'odometer_km'), ({'odometer_km': 2_000_001}, 'odometer_km'), ({'odometer_km': None}, 'odometer_km'),
    ({'note': 'n' * 301}, 'note'), ({'kind': 'odometer', 'amount_amd': 100}, 'amount_amd'), ({'extra': 1}, '_'),
])
def test_check_garage_entry_rejects_in_armenian(patch, field):
    raw = {'car_code': 'CAR1', 'day': '2026-09-01', 'kind': 'repair', 'what': 'Արգելակներ', 'amount_amd': 50_000,
           'odometer_km': 100_000, **patch}
    item, errors = st.check_garage_entry(raw, CARS, TODAY)
    assert item is None and field in errors
    assert all(any('Ա' <= ch <= '֏' for ch in text) for text in errors.values())   # по-армянски


def test_check_garage_entry_accepts_and_normalizes():
    item, errors = st.check_garage_entry({'car_code': 'CAR1', 'day': '2023-10-03', 'kind': 'fixed', 'what': '  ОСАГО ',
                                          'amount_amd': 45000.0, 'odometer_km': 0, 'note': ' '}, CARS, TODAY)
    assert not errors and (item.day, item.what, item.amount_amd, item.note) == (date(2023, 10, 3), 'ОСАГО', 45000, None)
    odo, errors = st.check_garage_entry({'car_code': 'CAR2', 'day': '2026-10-03', 'kind': 'odometer',
                                         'odometer_km': 2_000_000}, CARS, TODAY)
    assert not errors and (odo.amount_amd, odo.what, odo.odometer_km) == (0, None, 2_000_000)
    assert st.check_garage_entry({'car_code': 'CAR2', 'day': '2026-10-03', 'kind': 'odometer', 'odometer_km': 5,
                                  'amount_amd': None, 'what': 'ստուգում'}, CARS, TODAY)[0].what == 'ստուգում'


def test_store_odometer_never_decreases_and_names_the_record(tmp_path):
    s = st.Store(str(tmp_path / 'r.db'))
    s.save_garage_entry(_item(day='2026-03-01', odometer_km=100_000), 'qa')
    s.save_garage_entry(_item(day='2026-06-01', odometer_km=110_000), 'qa')
    with pytest.raises(st.GarageError) as e:
        s.save_garage_entry(_item(day='2026-07-01', odometer_km=109_999), 'qa')
    assert e.value.errors == {'odometer_km': 'Սպիդոմետրը չի կարող նվազել. 01.06.2026-ին արդեն գրանցված է 110 000 կմ'}
    with pytest.raises(st.GarageError) as e:
        s.save_garage_entry(_item(day='2026-02-01', odometer_km=100_001), 'qa')
    assert '01.03.2026' in e.value.errors['odometer_km'] and '100 000' in e.value.errors['odometer_km']
    s.save_garage_entry(_item(day='2026-06-01', odometer_km=109_000), 'qa')        # в тот же день — и меньше
    s.save_garage_entry(_item(day='2026-04-01', odometer_km=100_000), 'qa')        # равное раннему — можно
    s.save_garage_entry(_item(day='2026-07-01', odometer_km=50, car_code='CAR2'), 'qa')   # другая машина
    assert len(s.garage_entries()) == 5


def test_store_edit_excludes_itself_and_soft_delete_frees(tmp_path):
    s = st.Store(str(tmp_path / 'r.db'))
    a = s.save_garage_entry(_item(day='2026-03-01', odometer_km=100_000), 'qa')
    b = s.save_garage_entry(_item(day='2026-06-01', odometer_km=110_000), 'qa')
    assert s.save_garage_entry(_item(day='2026-06-01', odometer_km=109_000, what='Ուղղված'), 'boss', a) == a  # правка a
    with pytest.raises(st.GarageError):
        s.save_garage_entry(_item(day='2026-07-01', odometer_km=1), 'qa', b)
    assert s.delete_garage_entry(b, 'boss') is True and s.delete_garage_entry(b, 'boss') is False
    with pytest.raises(st.GarageError, match='չի գտնվել'):
        s.save_garage_entry(_item(), 'qa', b)                                     # удалённую не правят
    s.save_garage_entry(_item(day='2026-07-01', odometer_km=109_500), 'qa')        # удалённая (110 000) не мешает
    live = s.garage_entries()
    assert [(e.id, e.what, e.odometer_km, e.updated_by) for e in live if e.id == a] == [(a, 'Ուղղված', 109_000, 'boss')]
    assert b not in {e.id for e in live}
    gone = [e for e in s.garage_entries(deleted=True) if e.id == b]
    assert gone and gone[0].deleted_by == 'boss' and gone[0].deleted_at


def test_store_odometer_rate_rejects_typos_and_names_neighbour(tmp_path):
    """Спидометр правдоподобен: от ближайшего показания раньше, позже и того же дня — не больше 1 500 км в сутки (как
    одометр APK; в один день — как за сутки). Лишний и потерянный разряд не принимаются и не запирают верные показания;
    ошибка называет соседнюю запись и темп."""
    s = st.Store(str(tmp_path / 'r.db'))
    s.save_garage_entry(_item(day='2026-01-10', odometer_km=120_500), 'qa')
    for day, km, rate in (('2026-09-10', 1_305_000, '4 874'), ('2026-01-10', 12_050, '108 450'),
                          ('2026-01-09', 100_000, '20 500')):
        with pytest.raises(st.GarageError) as e:
            s.save_garage_entry(_item(day=day, odometer_km=km), 'qa')
        assert e.value.errors == {'odometer_km': 'Ստուգեք սպիդոմետրը. 10.01.2026-ի 120 500 կմ-ի համեմատ սա օրական '
                                                 f'{rate} կմ է (առավելագույնը՝ 1 500 կմ)'}, day
    s.save_garage_entry(_item(day='2026-09-10', odometer_km=130_500), 'qa')            # верное — принято
    s.save_garage_entry(_item(day='2026-09-20', odometer_km=131_000), 'qa')
    s.save_garage_entry(_item(day='2026-09-22', odometer_km=134_000), 'qa')            # ровно 1 500 в сутки — можно
    with pytest.raises(st.GarageError, match='օրական 1 501 կմ'):
        s.save_garage_entry(_item(day='2026-09-23', odometer_km=135_501), 'qa')
    with pytest.raises(st.GarageError, match='22.09.2026-ի 134 000'):
        s.save_garage_entry(_item(day='2026-09-22', odometer_km=132_499), 'qa')        # в тот же день — 1 501
    s.save_garage_entry(_item(day='2026-09-22', odometer_km=132_500), 'qa')
    x = s.save_garage_entry(_item(day='2026-09-25', odometer_km=138_000), 'qa')
    assert s.save_garage_entry(_item(day='2026-09-25', odometer_km=135_000), 'qa', x) == x   # правка — без себя
    errors = s.save_garage_odometers([replace(_item(day='2026-09-30', odometer_km=1_350_000), kind='odometer',
                                              what=None, amount_amd=0)], 'qa')
    assert list(errors) == [0] and '25.09.2026-ի 135 000' in errors[0]               # и в таблице пробега


def test_store_edit_keeps_previous_version(tmp_path):
    """Правка не теряет денег: прежняя версия — удалённая строка с replaced_by (номер живой записи тот же); без изменений —
    новой версии нет. Прежние версии не участвуют ни в расчёте, ни в проверке спидометра."""
    s = st.Store(str(tmp_path / 'r.db'))
    a = s.save_garage_entry(_item(day='2026-03-01', odometer_km=100_000, amount_amd=50_000), 'qa')
    b = s.save_garage_entry(_item(day='2026-06-01', odometer_km=110_000), 'qa')
    edited = _item(day='2026-03-02', odometer_km=100_100, amount_amd=55_000, what='Կոճղակներ')
    assert s.save_garage_entry(edited, 'boss', a) == a and s.save_garage_entry(edited, 'boss', a) == a
    old = [e for e in s.garage_entries(deleted=True) if e.replaced_by == a]
    assert [(e.day, e.amount_amd, e.odometer_km, e.what, e.created_by, e.deleted_by, e.id != a) for e in old] == \
        [(date(2026, 3, 1), 50_000, 100_000, 'Արգելակներ', 'qa', 'boss', True)] and old[0].deleted_at
    live = {e.id: e for e in s.garage_entries()}
    assert (live[a].amount_amd, live[a].day, live[a].replaced_by, set(live)) == (55_000, date(2026, 3, 2), None, {a, b})
    s.save_garage_entry(_item(day='2026-06-01', odometer_km=105_000), 'boss', b)   # правка вниз: 110 000 — в истории
    s.save_garage_entry(_item(day='2026-07-01', odometer_km=106_000), 'qa')        # прежняя версия не мешает
    by_car = views._garage_by_car(s.garage_entries())
    assert [e.odometer_km for e in by_car['CAR1']] == [100_100, 105_000, 106_000]
    first = s.save_garage_odometers([replace(_item(day='2026-08-01', odometer_km=107_000), kind='odometer', what=None,
                                             amount_amd=0)], 'qa')
    again = s.save_garage_odometers([replace(_item(day='2026-08-01', odometer_km=107_500), kind='odometer', what=None,
                                             amount_amd=0)], 'qa')
    assert first == again == {}
    hist = [e for e in s.garage_entries(deleted=True) if e.replaced_by is not None and e.kind == 'odometer']
    assert [e.odometer_km for e in hist] == [107_000]                              # и у пробега того же дня
    with closing(sqlite3.connect(s.path)) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO garage_entry(car_code, day, kind, amount_amd, odometer_km, created_at, replaced_by) "
                     "VALUES('CAR1', '2026-01-01', 'odometer', 0, 5, 'x', 1)")             # замена — только удалённой


def test_store_one_odometer_per_car_day(tmp_path):
    s = st.Store(str(tmp_path / 'r.db'))
    first = s.save_garage_entry(_item(kind='odometer', amount_amd=..., what=..., odometer_km=1_000), 'qa')
    again = s.save_garage_entry(_item(kind='odometer', amount_amd=..., what=..., odometer_km=1_200), 'qa')
    assert first == again and [(e.kind, e.odometer_km) for e in s.garage_entries()] == [('odometer', 1_200)]
    other = s.save_garage_entry(_item(day='2026-09-02', kind='odometer', amount_amd=..., what=..., odometer_km=1_300), 'qa')
    with pytest.raises(st.GarageError) as e:
        s.save_garage_entry(_item(day='2026-09-01', kind='odometer', amount_amd=..., what=..., odometer_km=1_250), 'qa',
                            other)
    assert 'day' in e.value.errors
    with closing(sqlite3.connect(s.path)) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO garage_entry(car_code, day, kind, amount_amd, odometer_km, created_at) "
                     "VALUES('CAR1', '2026-09-01', 'odometer', 0, 5, 'x')")


def test_store_bulk_odometers_rowwise(tmp_path):
    s = st.Store(str(tmp_path / 'r.db'))
    s.save_garage_entry(_item(car_code='CAR2', day='2026-09-01', odometer_km=50_000), 'qa')
    rows = [_item(kind='odometer', amount_amd=..., what=..., day='2026-10-01', odometer_km=10_000),
            _item(kind='odometer', amount_amd=..., what=..., day='2026-10-01', odometer_km=40_000, car_code='CAR2')]
    errors = s.save_garage_odometers(rows, 'qa')
    assert list(errors) == [1] and '50 000' in errors[1]
    assert [(e.car_code, e.odometer_km) for e in s.garage_entries() if e.kind == 'odometer'] == [('CAR1', 10_000)]
    assert s.save_garage_odometers([replace(rows[0], odometer_km=10_100)], 'qa') == {}
    assert [e.odometer_km for e in s.garage_entries() if e.kind == 'odometer'] == [10_100]    # тот же день — обновил
    with pytest.raises(ValueError):
        s.save_garage_odometers([_item()], 'qa')


def test_store_db_checks_and_broken_rows(tmp_path):
    s = st.Store(str(tmp_path / 'r.db'))
    s.load()
    with closing(sqlite3.connect(s.path)) as conn:
        for sql in ("INSERT INTO garage_entry(car_code, day, kind, what, amount_amd, odometer_km, created_at) "
                    "VALUES('C', '2026-01-01', 'repair', 'x', 0, 1, 'x')",            # ремонт без суммы
                    "INSERT INTO garage_entry(car_code, day, kind, what, amount_amd, odometer_km, created_at) "
                    "VALUES('C', '2026-01-01', 'repair', NULL, 5, 1, 'x')",           # ремонт без «что»
                    "INSERT INTO garage_entry(car_code, day, kind, what, amount_amd, odometer_km, created_at) "
                    "VALUES('C', '2026-01-01', 'tax', 'x', 5, 1, 'x')",
                    "INSERT INTO garage_entry(car_code, day, kind, what, amount_amd, odometer_km, created_at) "
                    "VALUES('C', '2026-01-01', 'repair', 'x', 5, 3000000, 'x')"):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(sql)
        conn.execute("INSERT INTO garage_entry(car_code, day, kind, what, amount_amd, odometer_km, created_at) "
                     "VALUES('C', '01.01.2026', 'repair', 'x', 5, 1, 'x')")
        conn.commit()
    with pytest.raises(st.StoreError, match='журнала гаража'):
        s.garage_entries()


def test_store_migrates_15_to_16_keeps_all_rows(tmp_path):
    """15 → 16: только CREATE TABLE garage_entry и его индекс — все прежние таблицы и строки как были."""
    path = str(tmp_path / 'v15.db')
    s = st.Store(path)
    s.save_customer_constraints(101, None, st.CustomerWindow('between', 600, 720), 'qa', 40)
    s.save_geo_override(102, (40.2, 44.5), 'qa')
    s.save_dispatch(DAY, {'trips': [{'id': 1, 'truck': 'CAR1', 'stops': [101]}]}, 'qa')
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('DROP TABLE garage_entry')
        conn.execute("UPDATE meta SET value = '15' WHERE key = 'schema_version'")
        conn.commit()
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        before = {t: conn.execute(f'SELECT * FROM {t} ORDER BY 1').fetchall() for t in tables if t != 'meta'}
    b = st.Store(path).load()
    assert b.unload_min == {101: 40.0} and b.garage_wear == {} and st.Store(path).garage_entries() == []
    with closing(sqlite3.connect(path)) as conn:
        assert {t: conn.execute(f'SELECT * FROM {t} ORDER BY 1').fetchall() for t in before} == before
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == ('16',)
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'garage_one_odometer'").fetchone()


OWNER_DB = Path(__file__).resolve().parents[1] / 'route_optimizer.db'


@pytest.mark.skipif(not OWNER_DB.exists(), reason='нет базы маршрутов владельца')
def test_owner_db_copy_migrates_to_16(tmp_path):
    """Копия базы владельца (только чтение исходника): после миграции все таблицы и строки те же, журнал пуст."""
    copy = tmp_path / 'owner.db'
    with closing(sqlite3.connect(f'file:{OWNER_DB.as_posix()}?mode=ro', uri=True)) as src, \
            closing(sqlite3.connect(str(copy))) as dst:
        src.backup(dst)
    with closing(sqlite3.connect(str(copy))) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        before = {t: conn.execute(f'SELECT * FROM {t} ORDER BY 1').fetchall() for t in tables if t != 'meta'}
    b = st.Store(str(copy)).load()
    assert b.garage_wear == {}
    with closing(sqlite3.connect(str(copy))) as conn:
        assert {t: conn.execute(f'SELECT * FROM {t} ORDER BY 1').fetchall() for t in before} == before
        assert conn.execute('SELECT COUNT(*) FROM garage_entry').fetchone() == (0,)


# ============================== где ֏/км входит в расчёт ==============================

def test_bundle_resolved_trucks_single_point():
    trucks = {'CAR1': st.Truck('CAR1', 5000.0, 18.0, active=None, wear_amd_per_km=12.0),
              'CAR2': st.Truck('CAR2', 2500.0, 15.0, active=True),
              'CAR3': st.Truck('CAR3', 2500.0, 15.0, active=True, wear_load_amd_per_km=3.0)}
    plain = st.Bundle(dict(st.DEFAULT_SETTINGS), None, trucks, {})
    assert plain.resolved_trucks({'CAR1'}) == {'CAR1': replace(trucks['CAR1'], active=True), 'CAR2': trucks['CAR2'],
                                               'CAR3': trucks['CAR3']}
    with_garage = replace(plain, garage_wear={'CAR1': 30.0, 'CAR3': 7.5, 'GONE': 1.0})
    r = with_garage.resolved_trucks(set())
    assert (r['CAR1'].wear_amd_per_km, r['CAR1'].active, r['CAR2'], r['CAR3'].wear_amd_per_km,
            r['CAR3'].wear_load_amd_per_km) == (30.0, False, trucks['CAR2'], 7.5, 3.0)
    assert 'GONE' not in r and with_garage.trucks == trucks            # ручное в записи машины не меняется
    assert [with_garage.wear_source(c) for c in ('CAR1', 'CAR2', 'CAR3', 'NONE')] == ['garage', None, 'garage', None]
    assert [plain.wear_source(c) for c in ('CAR1', 'CAR2')] == ['manual', None]
    fleet, _ = fl.fleet_trucks(with_garage.resolved_trucks({'CAR1'}), {})   # модель парка (оценка, оптимизация)
    assert {t.car_code: t.wear_amd_per_km for t in fleet} == {'CAR1': 30.0, 'CAR2': None, 'CAR3': 7.5}
    assert plain.fingerprint() == replace(plain, garage_wear={}).fingerprint() != with_garage.fingerprint()
    assert with_garage.fingerprint() != replace(plain, garage_wear={'CAR1': 30.1, 'CAR3': 7.5, 'GONE': 1.0}).fingerprint()


@pytest.mark.parametrize('l100', [0.1, 8.0, 10.04, 10.05, 12.35, 15.5, 18.0, 30.0, 79.99])
def test_vrp_unit_cost_unchanged_without_wear(l100):
    for wear in (None, 0, 0.0):
        assert vrp.unit_cost(vrp.Vehicle('T', 1000.0, l100, False, wear, 700.0)) == int(round(l100 * 10))
    assert vrp.unit_cost(vrp.Vehicle('T', 1000.0, l100, False)) == int(round(l100 * 10))


def test_vrp_unit_cost_with_wear_in_liters():
    """30 ֏/км при 700 ֏/л — 4,2857 л/100 км: (18 + 4,2857) × 10 → 223."""
    assert vrp.unit_cost(vrp.Vehicle('T', 5000.0, 18.0, False, 30.0, 700.0)) == 223
    assert vrp.unit_cost(vrp.Vehicle('T', 5000.0, 18.0, False, 30.0, 500.0)) == 240


def test_fleet_solver_passes_wear_and_fuel_price(monkeypatch):
    seen = []

    def fake(pieces, km, minutes, vehicles, *a, **kw):
        seen.append(vehicles)
        return None
    monkeypatch.setattr(vrp, 'solve', fake)
    monkeypatch.setattr(vrp, 'available', lambda: True)
    trucks = [fl.FleetTruck('A', 'x', 5000.0, 18.0, wear_amd_per_km=30.0), fl.FleetTruck('B', 'y', 2500.0, 12.0)]
    tn = fl.TruckNorms(540.0, 8.0, 6.0, fuel_price=700.0)
    stops = [fl._Stop(1, 100.0, 1000.0, 9.0)]
    trip = fl.Trip('A', 1, 100.0, 1000.0, 10.0, 30.0, 5000.0, 1.8, False, (0,))
    d = [[0.0, 5.0], [5.0, 0.0]]
    fl._solver([trip], stops, d, d, trucks, tn, None, None)
    assert seen and [(v.code, v.wear_amd_per_km, v.fuel_price) for v in seen[0]] == [('A', 30.0, 700.0), ('B', None, 700.0)]


# ---- через API: «Развоз», настройки, оценка ----

def _ready_journal(store, car='CAR1', price_km=20_000, cost=600_000):
    """Журнал машины с готовой ценой на 01.10.2026: 04.01 — 28.09 (267 дней), price_km км, ремонт cost ֏."""
    base = 100_000
    rows = [st.GarageInput(car, date(2026, 1, 4), 'odometer', None, 0, base),
            st.GarageInput(car, date(2026, 3, 10), 'repair', 'Կոճղակներ', cost // 3, base + price_km // 5),
            st.GarageInput(car, date(2026, 6, 15), 'repair', 'Անվադողեր', cost - cost // 3, base + price_km // 2),
            st.GarageInput(car, date(2026, 9, 28), 'odometer', None, 0, base + price_km)]
    for r in rows:
        store.save_garage_entry(r, 'qa')


def test_empty_journal_bundle_and_plan_identical_to_1425544(client):
    """Пустой журнал: настройки расчёта — те же объекты и отпечаток, план «Развоза» — байт-в-байт как на 1425544
    (отпечаток GOLDEN посчитан кодом 8a4cce2 и не менялся до 1425544)."""
    state = client.application.extensions['route_optimizer']
    assert views._bundle(state) == state.store.load()
    assert _golden_digest(client, False) == GOLDEN['plain']


def test_not_ready_or_foreign_journal_keeps_plan_identical(client):
    """Журнал есть, но цена не готова (CAR1 — 3 месяца) или готова у машины не из «Развоза» — план тот же."""
    state = client.application.extensions['route_optimizer']
    state.store.save_garage_entry(st.GarageInput('CAR1', date(2026, 7, 1), 'odometer', None, 0, 1_000), 'qa')
    state.store.save_garage_entry(st.GarageInput('CAR1', date(2026, 9, 20), 'repair', 'x', 900_000, 9_000), 'qa')
    _ready_journal(state.store, car='OLD7')
    assert views._with_garage(state, state.store.load(), AS_OF).garage_wear == {'OLD7': 30.0}
    assert _golden_digest(client, False) == GOLDEN['plain']


def test_ready_journal_changes_plan_and_costs(client):
    """Готовая цена CAR1 (30 ֏/км) — износ CAR1 в плане = 30 × км; план другой (PyVRP и выбор машины видят износ)."""
    state = client.application.extensions['route_optimizer']
    _ready_journal(state.store)
    digest = _golden_digest(client, False)
    assert digest != GOLDEN['plain']
    body = client.get(f'/api/routes/dispatch?date={DAY}').get_json()
    car1 = next(t for t in body['plan']['trucks'] if t['car_code'] == 'CAR1')
    assert car1['trips'] and all(tr['wear_configured'] for tr in car1['trips'])
    assert all(abs(tr['wear_amd'] - 30.0 * tr['km']) <= 2 for tr in car1['trips'])
    assert all(not tr['wear_configured'] and tr['wear_amd'] == 0 for t in body['plan']['trucks']
               if t['car_code'] == 'CAR2' for tr in t['trips'])
    trucks = {t['car_code']: t for t in body['trucks']}
    assert (trucks['CAR1']['wear_source'], trucks['CAR1']['wear_amd_per_km']) == ('garage', 30.0)
    assert (trucks['CAR2']['wear_source'], trucks['CAR2']['wear_amd_per_km']) == (None, None)


def test_dispatch_day_uses_its_own_as_of(client, monkeypatch):
    """«Развоз» дня D — цена журнала на D: 30.06 у CAR1 ещё накапливается (ручное 12), 01.10 — 30 ֏/км."""
    monkeypatch.setattr(views, '_clock', lambda: NOW)
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2)])
    state = client.application.extensions['route_optimizer']
    assert client.post('/api/routes/settings', json={'trucks': [{'car_code': 'CAR1', 'wear_amd_per_km': 12.0}]}
                       ).status_code == 200
    _ready_journal(state.store)
    early = {t['car_code']: t for t in client.get('/api/routes/dispatch?date=2026-06-30').get_json()['trucks']}
    late = {t['car_code']: t for t in client.get(f'/api/routes/dispatch?date={DAY}').get_json()['trucks']}
    assert (early['CAR1']['wear_source'], early['CAR1']['wear_amd_per_km']) == ('manual', 12.0)
    assert (late['CAR1']['wear_source'], late['CAR1']['wear_amd_per_km']) == ('garage', 30.0)
    seen = []
    real = views._dispatch_ctx
    monkeypatch.setattr(views, '_dispatch_ctx', lambda *a, **kw: seen.append(a[4]) or real(*a, **kw))
    assert client.get('/api/routes/dispatch/fact?date=2026-06-30').status_code == 200
    assert client.get('/api/routes/dispatch/fact?date=2026-09-30').status_code == 200
    assert [t['CAR1'].wear_amd_per_km for t in seen] == [12.0, 30.0]          # «план — факт» — тоже на свой день


def test_settings_show_garage_and_never_persist_it(client, monkeypatch):
    """Настройки: поле — ручное значение, рядом — журнал (только чтение) и что в расчёте; сохранение страницы журнал в
    trucks не пишет — ни поверх ручного, ни в пустое."""
    monkeypatch.setattr(views, '_clock', lambda: NOW)
    _dispatch_setup(client, [_dorder(1, 101, 400.0)])
    state = client.application.extensions['route_optimizer']
    assert client.post('/api/routes/settings', json={'trucks': [{'car_code': 'CAR1', 'wear_amd_per_km': 12.0}]}
                       ).status_code == 200
    _ready_journal(state.store)
    _ready_journal(state.store, car='CAR2', price_km=10_000, cost=50_000)
    trucks = {t['car_code']: t for t in client.get('/api/routes/settings').get_json()['trucks']}
    assert (trucks['CAR1']['wear_amd_per_km'], trucks['CAR1']['wear_source']) == (12.0, 'garage')
    assert trucks['CAR1']['garage']['price'] == 30.0 and trucks['CAR1']['garage']['status'] == 'ready'
    assert (trucks['CAR2']['wear_amd_per_km'], trucks['CAR2']['garage']['price']) == (None, 5.0)
    page = [{'car_code': c, 'wear_amd_per_km': trucks[c]['wear_amd_per_km']} for c in ('CAR1', 'CAR2')]
    assert client.post('/api/routes/settings', json={'trucks': page}).status_code == 200
    stored = state.store.load().trucks
    assert (stored['CAR1'].wear_amd_per_km, stored['CAR2'].wear_amd_per_km) == (12.0, None)
    with closing(sqlite3.connect(state.store.path)) as conn:
        assert conn.execute('SELECT car_code, wear_amd_per_km FROM trucks ORDER BY 1').fetchall() == \
            [('CAR1', 12.0), ('CAR2', None)]


def test_overview_fleet_model_picks_garage_wear(client, monkeypatch):
    """Оценка (модель парка) берёт ремонт ֏/км журнала: машина с готовой ценой — «износ настроен»; кэш — по отпечатку."""
    monkeypatch.setattr(views, '_clock', lambda: NOW)
    _dispatch_setup(client, [_dorder(1, 101, 400.0)])
    state = client.application.extensions['route_optimizer']
    before = client.get('/api/routes/overview').get_json()['totals']
    _ready_journal(state.store)
    one = client.get('/api/routes/overview').get_json()
    _ready_journal(state.store, car='CAR2')
    both = client.get('/api/routes/overview').get_json()
    assert one['from_cache'] is False and both['from_cache'] is False
    assert [x['truck_wear_unconfigured'] for x in (before, one['totals'], both['totals'])] == [2, 1, 0]
    assert before['truck_wear_amd_week'] == 0 < both['totals']['truck_wear_amd_week']


# ---- одометр APK из «Առաքիչ» ----

class _Facts:
    def __init__(self, refuels=None, fail=False):
        self._refuels, self._fail, self.calls = refuels or [], fail, []

    def refuels(self, since=''):
        self.calls.append(since)
        if self._fail:
            raise RuntimeError('courier.db')
        return [r for r in self._refuels if r['at_utc'][:10] >= since]


def _refuel(eid, car, at, km, superseded=False, eff=None):
    return {'id': eid, 'car_code': car, 'at_utc': at, 'eff_at_utc': eff or at, 'superseded': superseded,
            'payload': {'liters': 50.0, 'odometer_km': km}}


def test_apk_odometers_reader(client):
    state = client.application.extensions['route_optimizer']
    since = date(2024, 1, 1)
    assert views._apk_odometers(state, since) == {}                            # «Առաքիչ» не подключён
    state.fleet_facts = _Facts(fail=True)
    assert views._apk_odometers(state, since) == {}                            # сбой чтения — без APK
    state.fleet_facts = _Facts([
        _refuel('a', 'CAR1', '2026-09-01T21:30:00+00:00', 10_000),            # 02.09 по Еревану
        _refuel('b', 'CAR1', '2026-09-05T08:00:00+00:00', 99_999, superseded=True),
        _refuel('c', 'CAR1', '2026-09-10T08:00:00+00:00', 'x'),
        _refuel('d', 'CAR1', '2026-09-12T08:00:00+00:00', 10_900),
        _refuel('e', 'CAR1', '2026-09-13T08:00:00+00:00', 900_000),           # опечатка — вне цепочки
        _refuel('f', 'CAR1', '2026-09-14T08:00:00+00:00', 11_100),
        _refuel('g', 'CAR2', '2026-09-14T08:00:00+00:00', 5_000)])
    assert views._apk_odometers(state, since) == {'CAR1': [(date(2026, 9, 2), 10_000.0), (date(2026, 9, 12), 10_900.0),
                                                           (date(2026, 9, 14), 11_100.0)],
                                                  'CAR2': [(date(2026, 9, 14), 5_000.0)]}
    assert state.fleet_facts.calls[-1] == '2024-01-01'                        # заправки — с дня since, а не все


def test_apk_odometer_completes_garage_price(client, monkeypatch):
    """Журнал CAR1 — 4 месяца; заправки APK покрывают ещё 3 — цена готова; без журнала заправки цену не дают."""
    monkeypatch.setattr(views, '_clock', lambda: NOW)
    state = client.application.extensions['route_optimizer']
    state.store.save_garage_entry(st.GarageInput('CAR1', date(2026, 2, 1), 'odometer', None, 0, 50_000), 'qa')
    state.store.save_garage_entry(st.GarageInput('CAR1', date(2026, 6, 1), 'repair', 'x', 80_000, 58_000), 'qa')
    assert views._garage_prices(state, AS_OF)['CAR1'].status == 'accumulating'
    state.fleet_facts = _Facts([_refuel('a', 'CAR1', '2026-09-20T08:00:00+00:00', 66_000),
                                _refuel('b', 'CAR2', '2026-01-20T08:00:00+00:00', 1_000),
                                _refuel('c', 'CAR2', '2026-09-20T08:00:00+00:00', 30_000)])
    prices = views._garage_prices(state, AS_OF)
    assert (prices['CAR1'].status, prices['CAR1'].price) == ('ready', 5.0) and 'CAR2' not in prices
    assert views._bundle(state).garage_wear == {'CAR1': 5.0}


def test_dispatch_reads_journal_once_per_request(client, monkeypatch):
    """«Развоз» берёт цену журнала на сегодня и на свой день — журнал и заправки читаются один раз на запрос;
    следующий запрос читает заново (правка журнала видна сразу)."""
    monkeypatch.setattr(views, '_clock', lambda: NOW)
    _dispatch_setup(client, [_dorder(1, 101, 400.0)])
    state = client.application.extensions['route_optimizer']
    _ready_journal(state.store)
    state.fleet_facts = _Facts([])
    calls = []
    real = state.store.garage_entries
    monkeypatch.setattr(state.store, 'garage_entries', lambda deleted=False: calls.append(deleted) or real(deleted))
    for _ in range(2):
        calls.clear()
        state.fleet_facts.calls.clear()
        body = client.get(f'/api/routes/dispatch?date={DAY}').get_json()
        assert calls == [False] and state.fleet_facts.calls == ['2024-01-01']
        assert {t['car_code']: t['wear_source'] for t in body['trucks']}['CAR1'] == 'garage'


# ============================== API журнала ==============================

@pytest.fixture
def gclient(client, monkeypatch):
    """Клиент «Маршрутов» с машинами CAR1/CAR2, «сегодня» — 03.10.2026; роль — из g.user_role (как ставит app_v2)."""
    monkeypatch.setattr(views, '_clock', lambda: NOW)
    app = client.application
    role = {'value': None}

    @app.before_request
    def _role():
        from flask import g
        if role['value']:
            g.user_role = role['value']
    _dispatch_setup(client, [_dorder(1, 101, 400.0)])
    client.role = role
    return client


def _post(c, body, path='/api/routes/garage/entries'):
    return c.post(path, json=body)


def test_api_add_edit_delete_and_list(gclient):
    c = gclient
    r = _post(c, {'car_code': 'CAR1', 'day': '2026-09-01', 'kind': 'repair', 'what': 'Կոճղակներ', 'amount_amd': 85_000,
                  'odometer_km': 120_500, 'note': 'Ավտոսերվիս №3'})
    assert r.status_code == 200, r.get_json()
    first = r.get_json()['id']
    assert _post(c, {'car_code': 'CAR1', 'day': '2026-09-20', 'kind': 'accident', 'what': 'Հայելի', 'amount_amd': 40_000,
                     'odometer_km': 121_000}).status_code == 200
    assert _post(c, {'car_code': 'CAR2', 'day': '2026-08-10', 'kind': 'fixed', 'what': 'ԱՊՊԱ', 'amount_amd': 60_000,
                     'odometer_km': 7_000}).status_code == 200
    body = c.get('/api/routes/garage/entries').get_json()
    assert [e['day'] for e in body['entries']] == ['2026-09-20', '2026-09-01', '2026-08-10'] and body['total_amd'] == 185_000
    assert c.get('/api/routes/garage/entries?car=CAR1&month=2026-09').get_json()['total_amd'] == 125_000
    assert c.get('/api/routes/garage/entries?month=2026-08').get_json()['total_amd'] == 60_000
    assert c.get('/api/routes/garage/entries?month=2026-8').status_code == 400
    r = _post(c, {'id': first, 'car_code': 'CAR1', 'day': '2026-09-01', 'kind': 'repair', 'what': 'Կոճղակներ',
                  'amount_amd': 95_000, 'odometer_km': 120_500})
    assert r.status_code == 200 and r.get_json()['id'] == first
    assert c.post('/api/routes/garage/entries/delete', json={'id': first}).status_code == 200
    assert c.post('/api/routes/garage/entries/delete', json={'id': first}).status_code == 404
    body = c.get('/api/routes/garage/entries?car=CAR1&deleted=1').get_json()
    assert [e['id'] for e in body['entries']] != [] and first not in [e['id'] for e in body['entries']]
    c.role['value'] = 'admin'                                                     # удалённые — только администратору
    body = c.get('/api/routes/garage/entries?car=CAR1&deleted=1').get_json()
    gone = [e for e in body['entries'] if e['id'] == first]
    assert gone and gone[0]['amount_amd'] == 95_000 and gone[0]['deleted_at'] and body['total_amd'] == 40_000
    assert c.get('/api/routes/garage').get_json()['admin'] is True


def test_api_edit_history_and_login_names(gclient):
    """Администратор в «удалённых» видит прежнюю версию изменённой записи (replaced_by); логины — только ему."""
    c = gclient
    first = _post(c, {'car_code': 'CAR1', 'day': '2026-09-01', 'kind': 'repair', 'what': 'Կոճղակներ', 'amount_amd': 85_000,
                      'odometer_km': 120_500}).get_json()['id']
    assert _post(c, {'id': first, 'car_code': 'CAR1', 'day': '2026-09-01', 'kind': 'repair', 'what': 'Կոճղակներ',
                     'amount_amd': 95_000, 'odometer_km': 120_500}).status_code == 200
    plain = c.get('/api/routes/garage/entries?deleted=1').get_json()['entries']
    assert [(e['id'], e['amount_amd']) for e in plain] == [(first, 95_000)]
    assert not {'created_by', 'updated_by', 'deleted_by'} & set(plain[0])
    c.role['value'] = 'admin'
    rows = c.get('/api/routes/garage/entries?deleted=1').get_json()
    old = [e for e in rows['entries'] if e['replaced_by'] == first]
    assert len(old) == 1 and old[0]['amount_amd'] == 85_000 and old[0]['deleted_at'] and rows['total_amd'] == 95_000
    assert {'created_by', 'updated_by', 'deleted_by'} <= set(old[0])


@pytest.mark.parametrize('body,field', [
    ({'car_code': 'NOPE'}, 'car_code'), ({'day': '2026-10-04'}, 'day'), ({'kind': 'other'}, 'kind'),
    ({'what': ''}, 'what'), ({'amount_amd': 1.5}, 'amount_amd'), ({'odometer_km': '12'}, 'odometer_km'),
])
def test_api_validation_errors_by_field(gclient, body, field):
    base = {'car_code': 'CAR1', 'day': '2026-09-01', 'kind': 'repair', 'what': 'x', 'amount_amd': 1, 'odometer_km': 1}
    r = _post(gclient, {**base, **body})
    assert r.status_code == 400 and field in r.get_json()['errors'] and r.get_json()['success'] is False


def test_api_rejects_bad_requests(gclient):
    c = gclient
    assert c.post('/api/routes/garage/entries', data='x', content_type='text/plain').status_code == 415
    assert _post(c, [1]).status_code == 400
    for bad in ('7', 0, -1, True, 1.5):
        assert _post(c, {'id': bad, 'car_code': 'CAR1'}).status_code == 400
        assert c.post('/api/routes/garage/entries/delete', json={'id': bad}).status_code == 400
    r = _post(c, {'id': 999, 'car_code': 'CAR1', 'day': '2026-09-01', 'kind': 'odometer', 'odometer_km': 5})
    assert r.status_code == 400 and 'չի գտնվել' in r.get_json()['error']
    assert c.post('/api/routes/garage/odometers', json={'items': []}).status_code == 400
    assert c.post('/api/routes/garage/odometers', json={'items': [{}] * 201}).status_code == 400


def test_api_odometer_conflict_names_record(gclient):
    c = gclient
    assert _post(c, {'car_code': 'CAR1', 'day': '2026-08-01', 'kind': 'odometer', 'odometer_km': 150_000}).status_code == 200
    r = _post(c, {'car_code': 'CAR1', 'day': '2026-09-01', 'kind': 'repair', 'what': 'x', 'amount_amd': 5,
                  'odometer_km': 149_000})
    assert r.status_code == 400
    assert r.get_json()['errors'] == {'odometer_km': 'Սպիդոմետրը չի կարող նվազել. 01.08.2026-ին արդեն գրանցված է 150 000 կմ'}


def test_api_bulk_odometers(gclient):
    c = gclient
    assert _post(c, {'car_code': 'CAR2', 'day': '2026-09-01', 'kind': 'odometer', 'odometer_km': 80_000}).status_code == 200
    r = c.post('/api/routes/garage/odometers', json={'items': [
        {'car_code': 'CAR1', 'day': '2026-10-01', 'odometer_km': 12_000},
        {'car_code': 'CAR2', 'day': '2026-10-01', 'odometer_km': 79_000},
        {'car_code': 'CAR1', 'day': '2026-10-01', 'odometer_km': 12_001},
        {'car_code': 'NOPE', 'day': '2026-10-01', 'odometer_km': 1},
        {'car_code': 'CAR1', 'day': '2026-10-01', 'odometer_km': 1, 'kind': 'repair'}]})
    body = r.get_json()
    assert r.status_code == 200 and body['saved'] == [0] and sorted(body['errors']) == ['1', '2', '3', '4']
    assert '80 000' in body['errors']['1'] and 'կրկնվում' in body['errors']['2']
    g = c.get('/api/routes/garage').get_json()
    assert {t['car_code']: (t['last_km'], t['last_day']) for t in g['trucks']} == \
        {'CAR1': (12_000, '2026-10-01'), 'CAR2': (80_000, '2026-09-01')}


def test_api_summary_and_months(gclient):
    c = gclient
    state = c.application.extensions['route_optimizer']
    _ready_journal(state.store)
    assert _post(c, {'car_code': 'CAR2', 'day': '2026-09-15', 'kind': 'accident', 'what': 'x', 'amount_amd': 70_000,
                     'odometer_km': 5_000}).status_code == 200
    g = c.get('/api/routes/garage').get_json()
    rows = {r['car_code']: r for r in g['summary']}
    car1 = rows['CAR1']
    assert (car1['in_calc'], car1['garage']['price'], car1['garage']['status'], car1['garage']['months']) == \
        (True, 30.0, 'ready', 8)
    assert (car1['days_since_last'], car1['stale']) == (5, False)
    car2 = rows['CAR2']
    assert (car2['in_calc'], car2['garage']['status'], car2['garage']['accident_amd'], car2['days_since_last']) == \
        (False, 'accumulating', 70_000, 18)
    months = {m['month']: m for m in g['months']}
    assert list(months) == [f'2025-{m:02d}' for m in (11, 12)] + [f'2026-{m:02d}' for m in range(1, 11)]
    assert months['2026-03']['repair'] == 200_000 and months['2026-09'] == \
        {'month': '2026-09', 'repair': 0, 'accident': 70_000, 'fixed': 0, 'total': 70_000}
    assert g['rules'] == {'ready_months': 6, 'ready_km': 500, 'stale_days': 45}


def test_api_garage_works_when_erp_is_down(gclient):
    """ERP недоступна (снимка нет) — машины без названий ERP, журнал работает."""
    from route_optimizer.erp import ErpError
    state = gclient.application.extensions['route_optimizer']

    def boom():
        raise ErpError('нет связи')
    state.snapshots = SnapshotCache(boom)
    g = gclient.get('/api/routes/garage')
    assert g.status_code == 200 and {t['car_code'] for t in g.get_json()['trucks']} == {'CAR1', 'CAR2'}
