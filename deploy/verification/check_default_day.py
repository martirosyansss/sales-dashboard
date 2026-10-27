"""Exercise the actual dispatch GET handler with isolated downstream data."""
import importlib
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask


def run_checks(source):
    package = types.ModuleType('route_optimizer')
    package.__path__ = [str(Path(source) / 'route_optimizer')]
    sys.modules['route_optimizer'] = package
    views = importlib.import_module('route_optimizer.views')
    app = Flask(__name__)
    app.add_url_rule('/api/routes/dispatch', view_func=views.api_dispatch)
    client = app.test_client()
    state = object()
    bundle = SimpleNamespace(trucks={}, settings={'workdays': [1, 2, 3, 4, 5]})
    calls = []

    def load(state_, bundle_, day, **kwargs):
        assert state_ is state and bundle_ is bundle
        calls.append((day.isoformat(), kwargs))
        return SimpleNamespace(day=day)

    checks = []
    with patch.object(views, '_state', return_value=state), \
         patch.object(views, '_bundle', return_value=bundle), \
         patch.object(views, '_erp_crew'), \
         patch.object(views, '_load_day', side_effect=load), \
         patch.object(views, '_dispatch_page_body', side_effect=lambda dd: {'day': dd.day.isoformat()}):
        # UTC timestamps also verify that the real Yerevan clock requests the right timezone.
        cases = [
            ('2026-10-27T00:00:00+04:00', '2026-10-27'),
            ('2026-10-27T15:59:59.999999+04:00', '2026-10-27'),
            ('2026-10-27T16:00:00+04:00', '2026-10-28'),
            ('2026-10-27T23:59:59+04:00', '2026-10-28'),
            ('2026-10-30T16:00:00+04:00', '2026-10-31'),
            ('2026-10-31T16:00:00+04:00', '2026-11-01'),
            ('2026-12-31T16:00:00+04:00', '2027-01-01'),
            ('2028-02-28T16:00:00+04:00', '2028-02-29'),
            ('2028-02-29T16:00:00+04:00', '2028-03-01'),
            ('2026-10-28T00:00:00+04:00', '2026-10-28'),
        ]
        for moment, expected in cases:
            fixed_utc = datetime.fromisoformat(moment).astimezone(timezone.utc)

            class Clock(datetime):
                @classmethod
                def now(cls, tz=None):
                    assert tz == views.ac.YEREVAN, 'Use the business timezone, not host local time'
                    return fixed_utc.astimezone(tz)

            for query in ('', '?date='):
                with patch.object(views, 'datetime', Clock):
                    response = client.get('/api/routes/dispatch' + query)
                assert response.status_code == 200, response.json
                assert response.json == {'success': True, 'day': expected}, (moment, response.json)
                assert calls[-1] == (expected, {'refresh': False})
                checks.append({'now_yerevan': moment, 'query': query, 'day': expected})

        for chosen in ('2026-10-26', '2026-10-27', '2026-10-28', '2026-11-01'):
            for refresh in ('', '&refresh=1'):
                with patch.object(views, '_same_day_now', side_effect=AssertionError('Explicit date must stay selected')):
                    response = client.get('/api/routes/dispatch?date=' + chosen + refresh)
                assert response.status_code == 200 and response.json['day'] == chosen
                assert calls[-1] == (chosen, {'refresh': bool(refresh)})
                checks.append({'selected_date': chosen, 'refresh': bool(refresh)})

        for invalid in ('invalid', '2026-02-30', '2026-13-01'):
            before = len(calls)
            response = client.get('/api/routes/dispatch?date=' + invalid)
            assert response.status_code == 400 and len(calls) == before
            checks.append({'invalid_date_rejected': invalid})

    return {'passed': len(checks), 'checks': checks, 'operational_data_accessed': False}


if __name__ == '__main__':
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2]
    print(json.dumps(run_checks(source), indent=2))
