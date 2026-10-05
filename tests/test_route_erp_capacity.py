"""Тоннаж машины из карточки ERP (CARS.fMAXCAPACITYBYWEIGHT, тонны; владелец 05.10): в расчёте, когда своё поле в
настройках пусто; заданное в настройках не перекрывается; у ручной машины карточки ERP нет."""
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from route_optimizer import erp  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from route_optimizer import views  # noqa: E402

ERP = {'EMPTY': 2500.0, 'SET': 2000.0, 'BIG': 5000.0, 'MAN': 3000.0}


class _Cursor:
    def __init__(self, rows):
        self.rows, self.sql = rows, None

    def execute(self, sql, *params):
        self.sql = sql

    def fetchall(self):
        return self.rows

    def close(self):
        pass


def test_erp_cars_reads_capacity_tonnes_to_kg():
    rows = [('123AV61', 'HOWO', False, Decimal('5.0000')), ('333NO33', 'FORD', False, Decimal('2.0000')),
            ('1', '991AT61', True, Decimal('0.0000')), ('X', 'JAC', False, None)]
    cur = _Cursor(rows)
    cars = erp.cars(SimpleNamespace(cursor=lambda: cur))
    assert 'fMAXCAPACITYBYWEIGHT' in cur.sql
    assert {c: car.capacity_kg for c, car in cars.items()} == {'123AV61': 5000.0, '333NO33': 2000.0, '1': None, 'X': None}
    assert erp.Car('A', 'HOWO', False).capacity_kg is None          # старые снимки и тесты без тоннажа


def _bundle(tmp_path):
    b = st.Store(str(tmp_path / 'r.db')).load()
    return replace(b, trucks={'EMPTY': st.Truck('EMPTY', None, 16.0), 'SET': st.Truck('SET', 2500.0, 16.0),
                              'BIG': st.Truck('BIG', None, 20.0),
                              'MAN': st.Truck('MAN', None, 12.0, manual=True, name='Gazel')})


def test_resolved_trucks_fill_only_empty_erp_capacity(tmp_path):
    b = _bundle(tmp_path)
    r = b.resolved_trucks(set(), ERP)
    assert {c: t.capacity_kg for c, t in r.items()} == {'EMPTY': 2500.0, 'SET': 2500.0, 'BIG': 5000.0, 'MAN': None}
    assert {c: t.capacity_kg for c, t in b.resolved_trucks(set()).items()} == {      # без ERP — как раньше
        'EMPTY': None, 'SET': 2500.0, 'BIG': None, 'MAN': None}
    assert b.trucks['EMPTY'].capacity_kg is None                                     # настройки не меняются
    assert [b.truck_capacity(c, ERP) for c in ('EMPTY', 'SET', 'MAN', 'NONE')] == [2500.0, 2500.0, None, None]


def test_truck_big_auto_uses_erp_capacity_when_empty(tmp_path):
    b = _bundle(tmp_path)
    assert [b.truck_big(c, ERP) for c in ('EMPTY', 'SET', 'BIG', 'MAN')] == [False, False, True, False]
    assert not b.truck_big('BIG')                                                    # без ERP — тоннажа нет
    assert not replace(b, trucks={**b.trucks, 'BIG': replace(b.trucks['BIG'], big=False)}).truck_big('BIG', ERP)


def test_ready_trucks_take_erp_capacity(tmp_path):
    b = _bundle(tmp_path)
    cars = {c: erp.Car(c, 'HOWO' if c == 'BIG' else 'FORD', False, ERP[c]) for c in ('EMPTY', 'SET', 'BIG')}
    snap = SimpleNamespace(cars=cars, active_cars=frozenset(cars), car_capacity={c: ERP[c] for c in cars})
    ready = views._ready_trucks(snap, b)
    assert {c: (t.capacity_kg, t.big) for c, t in ready.items()} == {
        'EMPTY': (2500.0, False), 'SET': (2500.0, False), 'BIG': (5000.0, True)}


def test_erp_capacity_rounded_and_out_of_range_ignored():
    from route_optimizer.snapshot import Snapshot
    assert erp._capacity_kg(Decimal('1.2346')) == 1235.0                          # целые кг, как сохраняет страница
    cars = {'OK': erp.Car('OK', 'FORD', False, 2500.0), 'KG': erp.Car('KG', 'HOWO', False, 3500000.0),  # 3500 «т»
            'TINY': erp.Car('TINY', 'X', False, 50.0), 'NONE': erp.Car('NONE', 'Y', False)}
    assert Snapshot.car_capacity.fget(SimpleNamespace(cars=cars)) == {'OK': 2500.0}
