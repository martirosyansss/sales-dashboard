"""Проверка прогнозов по замерам; норму нельзя подтвердить одним пробным маршрутом."""
from __future__ import annotations
import math
from collections import defaultdict
from datetime import date

FIELDS = {'km': (0, 5000), 'minutes': (0, 1440), 'liters': (0, 1000),
          'loading_minutes': (0, 1440), 'kg': (0, 500000), 'trips': (0, 100),
          'average_load_pct': (0, 100), 'maintenance_amd': (0, 1e8)}


def validate(raw, codes, today):
    errors, out = {}, {}
    if not isinstance(raw, dict):
        return {}, {'_': 'Սպասվում էր չափման օբյեկտ'}
    try:
        day = date.fromisoformat(raw.get('day', ''))
        if day > today:
            raise ValueError()
        out['day'] = day.isoformat()
    except (ValueError, TypeError):
        errors['day'] = 'նշեք արդեն ավարտված օր՝ ոչ ուշ, քան այսօր'
    if raw.get('car_code') not in codes:
        errors['car_code'] = 'ընտրեք կարգավորված մեքենա'
    else:
        out['car_code'] = raw['car_code']
    for key, (lo, hi) in FIELDS.items():
        value = raw.get(key)
        if value is None or value == '':
            out[key] = None
        elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
            errors[key] = f'թիվ՝ {lo:,.0f}-ից մինչև {hi:,.0f}'.replace(',', ' ')   # разряды — пробелом: «100 000 000»
        elif key == 'trips' and value != int(value):
            errors[key] = 'երթերի քանակը պետք է լինի ամբողջ թիվ'
        else:
            out[key] = float(value)
    if not any(out.get(key) is not None for key in FIELDS):
        errors['_'] = 'մուտքագրեք առնվազն մեկ փաստացի չափում'
    if out.get('liters', 0) and out.get('km') == 0:
        errors['km'] = 'ծախսը ստուգելու համար անհրաժեշտ է դրական վազք'
    return out, errors


def compare(row):
    predicted = row.get('predicted') or {}
    result = {}
    for key, tolerance in (('km', .15), ('minutes', .20), ('liters', .10), ('loading_minutes', .20)):
        actual, planned = row.get(key), predicted.get(key)
        supported = actual is not None and actual > 0 and planned is not None
        error = planned / actual - 1 if supported else None
        result[key] = {'actual': actual, 'predicted': planned,
                       'error_pct': round(100*error, 1) if supported else None,
                       'ok': abs(error) <= tolerance if supported else None}
    return result


def _fit(rows, xkey, ykey):
    # Нельзя выделить две нормы без различной нагрузки и достаточного числа дат.
    usable = sorted([r for r in rows if r.get(xkey) is not None and r.get(ykey) is not None], key=lambda r: r['day'])
    if len(usable) < 12 or len({r['day'] for r in usable}) < 12:
        return None
    train, test = usable[:-4], usable[-4:]
    x = [r[xkey] for r in train]; y = [r[ykey] for r in train]
    if max(x)-min(x) < .2:
        return None
    mx, my = sum(x)/len(x), sum(y)/len(y)
    denominator = sum((v-mx)**2 for v in x)
    slope = sum((a-mx)*(b-my) for a,b in zip(x,y))/denominator
    intercept = my - slope*mx
    if not (math.isfinite(intercept) and math.isfinite(slope) and intercept >= 0 and slope >= 0):
        return None
    model_error = sum(abs(intercept+slope*r[xkey]-r[ykey]) for r in test)
    flat_error = sum(abs(my-r[ykey]) for r in test)
    if model_error > flat_error + 1e-9 or any(r[ykey] <= 0 or abs((intercept+slope*r[xkey])/r[ykey]-1) > .20 for r in test):
        return None
    return {'base': round(intercept, 2), 'slope': round(slope, 2), 'train_days': len(train),
            'test_days': len(test), 'test_mae': round(model_error/len(test), 2)}


def summary(rows):
    by_car = defaultdict(list)
    for row in rows:
        r = dict(row)
        if r.get('km') and r.get('liters') is not None and r.get('average_load_pct') is not None:
            r['load'] = r['average_load_pct']/100
            r['l100'] = r['liters']/r['km']*100
        if r.get('trips') and r.get('kg') is not None and r.get('loading_minutes') is not None:
            r['tonnes_per_trip'] = r['kg']/1000/r['trips']
            r['loading_per_trip'] = r['loading_minutes']/r['trips']
        by_car[r['car_code']].append(r)
    cars = []
    for code, data in sorted(by_car.items()):
        fuel = _fit(data, 'load', 'l100')
        if fuel and not (1 <= fuel['base'] <= fuel['base']+fuel['slope'] <= 80):
            fuel = None
        load = _fit(data, 'tonnes_per_trip', 'loading_per_trip')
        if load and (load['base'] > 240 or load['slope'] > 120):
            load = None
        maintenance = [r for r in data if r.get('maintenance_amd') is not None and r.get('km')]
        wear = None
        if len(maintenance) >= 30 and (date.fromisoformat(maintenance[-1]['day'])-date.fromisoformat(maintenance[0]['day'])).days >= 90:
            km = sum(r['km'] for r in maintenance)
            if km >= 1000:
                wear = round(sum(r['maintenance_amd'] for r in maintenance)/km, 2)
        cars.append({'car_code': code, 'days': len(data), 'fuel': fuel, 'loading': load,
                     'maintenance_amd_per_km': wear})
    validations = [dict(day=r['day'], car_code=r['car_code'], metrics=compare(r),
                        prospective=r.get('prospective') is True, prediction_at=r.get('prediction_at')) for r in rows]
    return {'records': len(rows), 'cars': cars, 'validation': validations[-60:],
            'scope': 'Չափված ծախսված դիզել, ոչ թե լիցքավորման ծավալ․ սխալները համեմատվում են պահպանված կանխատեսման հետ'}
