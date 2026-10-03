"""Количество определяется по конкретному штрихкоду ERP, без выбора водителем."""
import pytest

from courier import erp_day as ed, day as dy, events as ev

def test_distinct_unit_and_box_codes_keep_their_own_quantities(monkeypatch):
    rows = [(7, '4850002370146', 1, 1), (7, '4850002370382', 24, 2)]
    monkeypatch.setattr(ed, '_select', lambda *args: rows)
    gtins, units = ed.barcode_data(object(), [7])
    assert gtins == {7: ('04850002370146', '04850002370382')}
    assert units == {7: {'04850002370146': 1, '04850002370382': 12}}

def test_invalid_quantity_keeps_recognised_product_but_blocks_counting(monkeypatch):
    for base, measure in [(0, 1), (1, 0), (-1, 1), (None, 1), (float('inf'), 1),
                          (1, float('inf')), (float('nan'), 1), ('bad', 1)]:
        monkeypatch.setattr(ed, '_select', lambda *args: [(7, '4850002370146', base, measure)])
        gtins, units = ed.barcode_data(object(), [7])
        assert gtins == {7: ('04850002370146',)}
        assert units == {7: {'04850002370146': None}}

def test_conflicting_normalised_aliases_are_not_silently_overwritten(monkeypatch):
    monkeypatch.setattr(ed, '_select', lambda *args: [(7, '4850002370146', 1, 1), (7, '04850002370146', 12, 1)])
    assert ed.barcode_data(object(), [7])[1] == {7: {'04850002370146': None}}

def test_day_exposes_barcode_quantity_independently_of_generic_pack_size():
    line = ed.Line('test', 1, 7, 24, 100, 2400)
    mark = dy.MarkSetting(7, True, 6)
    value = dy._line_json(line, None, ('04850002370146', '04850002370382'), mark,
                          {'04850002370146': 1, '04850002370382': 12, '04850002370351': None})
    assert value['gtin_units'] == {'04850002370146': 1, '04850002370382': 12, '04850002370351': None}
    assert value['pack_qty'] == 6

def test_quantity_fields_are_selected_from_read_only_erp():
    from route_optimizer.erp import check_sql
    sql = ed.SQL_BARCODES.format(ph='?')
    check_sql(sql)
    assert 'fBASEUNITQUANTITY' in sql and 'fMEASUREUNITQUANTITY' in sql


@pytest.mark.parametrize('gtin,units,is_group,expected_flags', [
    ('04850002370146', 1, False, []),
    ('04850002370382', 12, True, []),
    ('04850002370146', 6, True, ['units_mismatch']),
    ('04850002370382', 6, True, ['units_mismatch']),
    ('04850002370351', 1, False, ['units_mismatch']),
])
def test_office_scan_validation_uses_each_barcode_quantity(gtin, units, is_group, expected_flags):
    class Tx:
        def scans_with_raw(self, *args): return []
        def pending_cancel(self, *args): return None

    line = dict(line_id='L:1', product_id=7, pack_qty=6, gtins=[],
                gtin_units={'04850002370146': 1, '04850002370382': 12, '04850002370351': None})
    stop = ev.StopCtx(dict(lines=[line]), False)
    event = dict(id='test', date='2000-01-01', at='2000-01-01T10:00:00+04:00')
    payload = dict(raw='01'+gtin+'21TEST', kind='sale', line_id=None, gtin=gtin,
                   serial='TEST', is_group=is_group, units=units)
    flags, row = ev._scan(Tx(), event, payload, stop, ev.Who(1, 'TEST', 1, 'test'), 'S:1')
    assert flags == expected_flags
    assert row['line_id'] == 'L:1' and row['product_id'] == 7 and row['units'] == units
