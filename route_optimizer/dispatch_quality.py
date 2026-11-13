"""Сбалансированный выбор с приоритетом маршрута и ресурсов, единый бюджет дня.

Доплата ограничивает весь день относительно исходной экономичной сборки.
Это ограниченный локальный поиск, а не доказательство глобального оптимума.
"""
from __future__ import annotations

from collections import Counter, OrderedDict
from dataclasses import replace
import hashlib
import heapq
import json
import math

MAX_PASSES = 4
MAX_PROPOSALS = 12000
MAX_CHECKS = 48
MIN_GAIN = .005
PROXY_CACHE_SIZE = 4096
GROUPS = ('reliability', 'workload', 'geography', 'resources')
# Обязательные ограничения проверяются до оценки. Выравнивание возвратов
# остаётся дополнительным преимуществом, а не поводом для дорогого объезда.
GROUP_WEIGHTS = {'reliability': .20, 'workload': .10, 'geography': .40, 'resources': .30}
SCORING_METHOD = 'balanced-benefit-v5'
BENEFIT_POLICY = 'operational-gain-v1'


def signature(draft):
    return hashlib.sha256(json.dumps([(t.id, t.truck, t.stops, t.not_before) for t in draft.trips],
                                    separators=(',', ':')).encode()).hexdigest()


class Evaluation:
    def __init__(self, ctx, stops, draft):
        from . import dispatch as dp
        ctx = replace(ctx, norms=ctx.norms.for_trucks())
        self.ctx, self.stops = ctx, stops
        self.shares = dp._shares(draft.trips)
        self.static_cache = {}
        self.proxy_cache = OrderedDict()
        self.proxy_cache_hits = 0
        ids = sorted({c for t in draft.trips for c in t.stops if c in stops})
        # Три ближайших соседа по дорогам: дробление соседних магазинов между машинами.
        self.neighbors = {c: sorted((x for x in ids if x != c),
                                  key=lambda x: (ctx.norms.km(stops[c].point, stops[x].point), x))[:3]
                          for c in ids}

    def static(self, code, cids):
        from . import dispatch as dp
        from . import fleet as fl
        key = code, tuple(cids)
        if key not in self.static_cache:
            kg = math.fsum(self.stops[c].kg / self.shares.get(c, 1) for c in cids)
            points = [self.stops[c].point for c in cids]
            cost = fl.trip_running_cost(points, [self.stops[c].kg / self.shares.get(c, 1) for c in cids],
                                        self.ctx.depot, self.ctx.norms, self.ctx.trucks[code])
            km = dp._closed_km(self.ctx, cids, self.stops)
            # Только очередь предварительного отбора. Решение принимает полная шкала ниже.
            mu, mt = self.ctx.tn.pace_of(code)
            approx = (km / max(1., self.ctx.norms.speed_city_kmh) * 60 * mt
                      + sum(self.ctx.tn.unload_at(self.stops[c].kg, self.stops[c].point) * mu for c in cids))
            self.static_cache[key] = cost.total_amd(self.ctx.tn.fuel_price), km, kg, approx
        return self.static_cache[key]

    def proxy(self, trips):
        """Общая ограниченная очередь приближённых метрик для разных поисков.

        Приближение не использует номера, закрепления и время начала рейсов.
        Полное расписание и его ограничения всегда проверяются отдельно.
        """
        key = tuple((t.truck, tuple(t.stops)) for t in trips)
        if key in self.proxy_cache:
            self.proxy_cache_hits += 1
            self.proxy_cache.move_to_end(key)
        else:
            self.proxy_cache[key] = self.metrics(trips, exact=False)[0]
            if len(self.proxy_cache) > PROXY_CACHE_SIZE:
                self.proxy_cache.popitem(last=False)
        return self.proxy_cache[key]

    def metrics(self, trips, exact=True):
        from . import dispatch as dp
        ctx = self.ctx
        costs, km, utils, spares, estimates, owners = [], [], [], [], {}, {}
        for t in trips:
            value, distance, kg, approx = self.static(t.truck, t.stops)
            costs.append(value); km.append(distance)
            cap = ctx.trucks[t.truck].capacity_kg
            utils.append(kg / cap)
            spares.append(cap - kg)
            estimates[t.truck] = estimates.get(t.truck, 0.) + approx
            for c in t.stops:
                owners.setdefault(c, set()).add(t.truck)
        fragments = [owners[c].isdisjoint(owners[x]) for c, neighbors in self.neighbors.items() for x in neighbors]
        margins, ends, minutes = [], estimates, sum(estimates.values())
        valid = True
        timeline = {}
        if exact:
            parts = {}
            timeline = dp._timeline(ctx, trips, self.stops, self.shares, parts,
                                    kg_of=dp._weights(trips, self.stops))
            ends = {}
            for t in trips:
                depart, duration, arrivals = timeline[t.id]
                ends[t.truck] = depart + duration
                for c, at in zip(t.stops, arrivals):
                    if at > dp._span(ctx, c)[1] + dp._EPS:
                        valid = False
                    margin = dp._real_end(ctx, c) - at
                    if math.isfinite(margin):
                        margins.append(margin)
            minutes = 0.
            for code, end in ends.items():
                mine = [t for t in trips if t.truck == code]
                first = mine[0]
                depart = timeline[first.id][0]
                if parts[first.id].get('preloaded'):
                    depart += parts[first.id].get('loading', 0.)
                minutes += end - max(0., depart)
                if end > dp._end_limit(ctx, trips, self.stops, self.shares, code) + dp._EPS:
                    valid = False
        peak = max(utils, default=0.)
        risk = (sum(max(0., 30. - x) / 30. for x in margins) / max(1, len(margins))
                + max(0., (peak - .9) / .1))
        m = {'cost_amd': sum(costs), 'km': sum(km), 'vehicle_minutes': minutes,
             'last_return_min': max(ends.values(), default=0.),
             'work_spread_min': max(ends.values(), default=0.) - min(ends.values(), default=0.),
             'min_weight_spare_kg': min(spares, default=0.), 'peak_load_pct': peak * 100,
             'window_margin_min': min(margins) if margins else None,
             'neighbor_split_pct': sum(fragments) / max(1, len(fragments)) * 100,
             'trips': len(trips), 'vehicles': len(ends), 'risk': risk,
             'unknown_weight_stops': sum(self.stops[c].kg <= 0 for c in owners)}
        return m, valid, timeline


def group_scores(metrics, reference):
    def ratio(key, floor=1.):
        return metrics[key] / max(floor, reference[key])
    return {
        'reliability': (1. + metrics['risk']) / (1. + reference['risk']),
        'workload': (ratio('last_return_min') + (metrics['work_spread_min'] + 30.) /
                     (reference['work_spread_min'] + 30.)) / 2.,
        'geography': (ratio('km') + (metrics['neighbor_split_pct'] + 10.) /
                      (reference['neighbor_split_pct'] + 10.)) / 2.,
        'resources': (ratio('vehicle_minutes') + ratio('trips') + ratio('vehicles') + ratio('cost_amd')) / 4.,
    }


def score(metrics, reference, weights=None):
    groups = group_scores(metrics, reference)
    weights = GROUP_WEIGHTS if weights is None else weights
    return sum(groups[key] * weights[key] for key in GROUPS)


def public(metrics):
    return {k: round(v, 3) if isinstance(v, float) else v for k, v in metrics.items()}


def resource_benefit(metrics, reference):
    """Дополнительные ресурсы требуют операционной пользы, а не одной смены владельца.

    Пороги существенности совпадают с уже используемыми в выборе пула.
    Денежная цена минуты не вводится: весовая шкала и общий бюджет сохраняются.
    """
    # Решение использует ту же точность, что опубликованные метрики интерфейса.
    metrics, reference = public(metrics), public(reference)
    reasons = []
    if metrics['cost_amd'] < reference['cost_amd'] - 1.:
        reasons.append('lower_cost')
    if metrics['vehicle_minutes'] < reference['vehicle_minutes'] - 1.:
        reasons.append('total_time')
    if metrics['km'] < reference['km'] - .1:
        reasons.append('distance')
    if metrics['trips'] < reference['trips'] - .5:
        reasons.append('fewer_trips')
    if metrics['vehicles'] < reference['vehicles'] - .5:
        reasons.append('fewer_vehicles')
    if (metrics['last_return_min'] < reference['last_return_min'] - 1.
            and metrics['vehicle_minutes'] <= reference['vehicle_minutes'] + .5):
        reasons.append('earlier_finish')
    old_margin, new_margin = reference.get('window_margin_min'), metrics.get('window_margin_min')
    deadline_gain = (old_margin is not None and new_margin is not None
                     and math.isfinite(old_margin) and math.isfinite(new_margin)
                     and old_margin < 30. and new_margin > old_margin + 1.)
    if deadline_gain:
        reasons.append('deadline_margin')
    fields = ('cost_amd', 'km', 'vehicle_minutes', 'trips', 'vehicles')
    material = (1., .1, 1., .5, .5)
    increases = [key for key, delta in zip(fields, material) if metrics[key] > reference[key] + delta]
    return {'policy': BENEFIT_POLICY, 'allowed': not increases or bool(reasons), 'reasons': reasons,
            'resource_increases': increases,
            'extra_cost_amd': max(0., metrics['cost_amd'] - reference['cost_amd']),
            'extra_km': max(0., metrics['km'] - reference['km']),
            'added_vehicle_minutes': max(0., metrics['vehicle_minutes'] - reference['vehicle_minutes']),
            'saved_vehicle_minutes': max(0., reference['vehicle_minutes'] - metrics['vehicle_minutes']),
            'earlier_last_return_min': max(0., reference['last_return_min'] - metrics['last_return_min']),
            'deadline_margin_gain_min': new_margin - old_margin if deadline_gain else 0.}


def _recover_previous(ctx, routable, draft, previous, ev, invalid, baseline_tl):
    """Сохраняем допустимый прежний состав, если новая сборка нарушила сроки.

    Переносится только состав рейсов. Текущие статусы, закрепления и время
    защищённых работ остаются источником истины; свободные рейсы получают новые ID.
    """
    from . import dispatch as dp
    from .dispatch_candidates import restore
    if previous is None:
        return draft
    fixed = {t.id for t in draft.trips if t.pinned or t.loaded is not None or t.not_before is not None}
    # Метка взятия заказов дня без защищённого рейса не должна остаться на старом ID.
    if not draft.same_day_trips.issubset(fixed):
        return draft
    prior = restore({'trips': previous.to_json()['trips']}, draft)
    if prior is None or len({t.id for t in prior}) != len(prior):
        return draft
    shares = Counter(c for t in draft.trips for c in t.stops)
    if Counter(c for t in prior for c in t.stops) != shares or dp.multi_truck(prior):
        return draft
    selected = dp._selected(ctx, draft.trucks)
    if not all(t.truck in draft.trucks and t.truck in ctx.trucks and t.origin is None
               and fits_previous(ctx, draft, t)
               and dp._can_carry(ctx, selected, t.truck, t.stops, routable, shares) for t in prior):
        return draft
    candidate, valid, timeline = ev.metrics(prior)
    if not valid or any(abs(timeline[tid][0] - baseline_tl[tid][0]) > dp._EPS for tid in fixed):
        return draft
    # Недопустимая дешёвая сборка не может быть финансовым ориентиром.
    result = replace(draft, trips=prior, next_id=max(draft.next_id, max(t.id for t in prior) + 1))
    result.quality_selection = {
        'reference': public(candidate), 'selected': public(candidate),
        'groups': {k: round(v, 6) for k, v in group_scores(candidate, candidate).items()},
        'extra_limit_amd': ctx.extra_cost_amd, 'checked': 0,
        'signature': signature(result), 'method': SCORING_METHOD, 'group_weights': dict(GROUP_WEIGHTS),
        'reference_source': {'kind': 'previous'}, 'selected_source': {'kind': 'previous'},
        'history': {'read': 0, 'unique': 0, 'valid': 0},
        'search': {'profiles': [], 'retained_candidates': 1, 'proxy_cache_hits': ev.proxy_cache_hits},
        'benefit': public(resource_benefit(candidate, candidate)),
        'recovery': {'reason': 'invalid_rebuild_timeline', 'rejected': public(invalid)},
    }
    return result


def improve(ctx, stops, draft, previous=None):
    """Не меняет покрытие, ручные/загруженные рейсы и допуски. Бюджет не растёт с каждым переносом."""
    from . import dispatch as dp
    if not ctx.balanced or len(draft.trips) < 1:
        return draft
    routable = {s.customer_id: s for s in stops if s.point is not None}
    if any(t.origin is not None or any(c not in routable for c in t.stops) for t in draft.trips):
        return draft
    ev = Evaluation(ctx, routable, draft)
    base, valid, baseline_tl = ev.metrics(draft.trips)
    # Сначала проверяем прежний допустимый состав, не ослабляя ограничения.
    if not valid:
        return _recover_previous(ctx, routable, draft, previous, ev, base, baseline_tl)
    shares = Counter(c for t in draft.trips for c in t.stops)
    selected = dp._selected(ctx, draft.trucks)
    if not all(dp._can_carry(ctx, selected, t.truck, t.stops, routable, shares) for t in draft.trips):
        return draft
    fixed = {t.id for t in draft.trips if t.pinned or t.loaded is not None or t.not_before is not None}
    from .dispatch_candidates import restore, route_key
    pool = [(base, list(draft.trips), {'kind': 'economic_build'})]
    seen = {route_key(draft.trips)}
    history_read = len(ctx.quality_history)
    history_unique = history_valid = 0
    archived = []
    if previous is not None and not draft.same_day_trips:
        archived.append((previous.trips, {'kind': 'previous'}))
    if not draft.same_day_trips:
        for record in ctx.quality_history:
            prior = restore(record, draft)
            if prior is not None:
                archived.append((prior, {'kind': 'history', 'rev': record['rev']}))
    for prior, source in archived:
        key = route_key(prior)
        if key in seen:
            continue
        seen.add(key)
        history_unique += source['kind'] == 'history'
        protected = {t.id: (t.truck, t.stops, t.not_before, t.loaded) for t in draft.trips if t.id in fixed}
        prior_protected = {t.id: (t.truck, t.stops, t.not_before, t.loaded) for t in prior if t.id in fixed}
        if (Counter(c for t in prior for c in t.stops) == shares and prior_protected == protected
                and len({t.id for t in prior}) == len(prior)
                and all(t.truck in draft.trucks and t.origin is None and fits_previous(ctx, draft, t)
                        and dp._can_carry(ctx, selected, t.truck, t.stops, routable, shares) for t in prior)):
            candidate, ok, tl = ev.metrics(prior)
            if ok and all(abs(tl[tid][0] - baseline_tl[tid][0]) <= dp._EPS for tid in fixed):
                pool.append((candidate, list(prior), source))
                history_valid += source['kind'] == 'history'
    initial = list(pool)
    profiles = [('weighted', dict(GROUP_WEIGHTS))]
    # Равные веса используются только для разнообразия поиска. Окончательная
    # оценка всех найденных вариантов всегда идёт по действующим весам.
    if MAX_PASSES and any(abs(GROUP_WEIGHTS[k] - .25) > 1e-9 for k in GROUPS):
        profiles.append(('equal', dict.fromkeys(GROUPS, .25)))
    checked = 0
    for _, weights in profiles:
        found, count = _search(ctx, draft, ev, list(initial), fixed, baseline_tl, shares, selected, weights)
        pool.extend(found[len(initial):])
        checked += count
    # Одинаковый маршрут, найденный разными поисками, не раздувает общий пул.
    retained = {}
    for row in pool:
        retained.setdefault(route_key(row[1]), row)
    base, reference_source, current, out, selected_source = choose_pool(list(retained.values()), ctx.extra_cost_amd)
    result = replace(draft, trips=out, next_id=max(draft.next_id, max(t.id for t in out) + 1))
    result.quality_selection = {'reference': public(base), 'selected': public(current),
                                'groups': {k: round(v, 6) for k, v in group_scores(current, base).items()},
                                'extra_limit_amd': ctx.extra_cost_amd, 'checked': checked,
                                'signature': signature(result), 'method': SCORING_METHOD,
                                'group_weights': dict(GROUP_WEIGHTS),
                                'reference_source': reference_source, 'selected_source': selected_source,
                                'history': {'read': history_read, 'unique': history_unique, 'valid': history_valid},
                                'search': {'profiles': [name for name, _ in profiles],
                                           'retained_candidates': len(retained),
                                           'proxy_cache_hits': ev.proxy_cache_hits}}
    result.quality_selection['benefit'] = public(resource_benefit(current, base))
    return result


def _search(ctx, draft, ev, pool, fixed, baseline_tl, shares, selected, weights):
    """Ограниченный поиск кандидатов; не меняет веса и не отбрасывает находки другого поиска."""
    from . import dispatch as dp
    from .dispatch_candidates import route_key
    routable = ev.stops
    # Единый бюджет — от самого дешёвого допустимого полного дня во всём известном пуле.
    # Ни возврат к дорогому архиву, ни отдельный перенос не увеличивают этот предел.
    base, reference_source, current, out, selected_source = choose_pool(pool, ctx.extra_cost_amd, weights=weights, _discovery=True)
    cost_limit = base['cost_amd'] + ctx.extra_cost_amd
    checked = 0

    def free(t):
        return (t.id not in fixed and not any(shares[c] != 1 or c in ctx.solo for c in t.stops))

    def fits(t, seq):
        return (not seq or dp._can_carry(ctx, selected, t.truck, seq, routable, shares)) and all(
            c not in draft.locks or draft.locks[c] == t.truck for c in seq)

    for _ in range(MAX_PASSES if len(routable) <= 80 else 2):
        free_slots = [i for i, t in enumerate(out) if free(t)]
        proxy_base = ev.proxy(out)
        estimate_ref = {**base, 'vehicle_minutes': proxy_base['vehicle_minutes'],
                        'last_return_min': proxy_base['last_return_min'], 'work_spread_min': proxy_base['work_spread_min']}
        # Для очереди отдельно учитываем относительное изменение приближённой длительности.
        def proposals():
            count = 0
            for a in free_slots:
                ta = out[a]
                for b in free_slots:
                    tb = out[b]
                    for p, c in enumerate(ta.stops):
                        for q in range(len(tb.stops) + 1):
                            count += 1
                            if count > MAX_PROPOSALS:
                                return
                            if a == b:
                                left = list(ta.stops); left.pop(p)
                                pos = q - (q > p)
                                left.insert(pos, c)
                                if left == ta.stops:
                                    continue
                                changes = {a: left}
                            else:
                                if c in draft.locks and draft.locks[c] != tb.truck:
                                    continue
                                if ctx.trucks[tb.truck].big and not ctx.trucks[ta.truck].big and dp._yerevan(ctx, routable[c]):
                                    continue
                                changes = {a: ta.stops[:p] + ta.stops[p+1:], b: tb.stops[:q] + [c] + tb.stops[q:]}
                            if not all(fits(out[k], seq) for k, seq in changes.items()):
                                continue
                            trial = [replace(t, stops=changes.get(k, t.stops)) for k, t in enumerate(out)
                                     if k not in changes or changes[k]]
                            m = ev.proxy(trial)
                            if m['cost_amd'] > cost_limit + 1e-6:
                                continue
                            yield score(m, estimate_ref, weights), count, trial
                    if a < b:
                        for p, c in enumerate(ta.stops):
                            for q, x in enumerate(tb.stops):
                                count += 1
                                if count > MAX_PROPOSALS:
                                    return
                                if (ctx.trucks[ta.truck].big and not ctx.trucks[tb.truck].big and dp._yerevan(ctx, routable[x]) or
                                    ctx.trucks[tb.truck].big and not ctx.trucks[ta.truck].big and dp._yerevan(ctx, routable[c])):
                                    continue
                                left, right = list(ta.stops), list(tb.stops)
                                left[p], right[q] = x, c
                                if not fits(ta, left) or not fits(tb, right):
                                    continue
                                trial = [replace(t, stops=left if k == a else right if k == b else t.stops)
                                         for k, t in enumerate(out)]
                                m = ev.proxy(trial)
                                if m['cost_amd'] <= cost_limit + 1e-6:
                                    yield score(m, estimate_ref, weights), count, trial
        for _, _, trial in heapq.nsmallest(MAX_CHECKS, proposals()):
            checked += 1
            metrics, valid, tl = ev.metrics(trial)
            if not valid or any(abs(tl[tid][0] - baseline_tl[tid][0]) > dp._EPS for tid in fixed):
                continue
            # Нельзя «выравнивать» работу, заставляя свободную машину ездить дольше.
            # При прежних владельцах магазинов нужен реальный выигрыш, кроме уменьшения разницы возвратов.
            if actual_gain(trial, out, metrics, current) or metrics['cost_amd'] < base['cost_amd'] - 1e-6:
                pool.append((metrics, trial, {**selected_source, 'refined': True}))
        base, reference_source, best_m, best, best_source = choose_pool(pool, ctx.extra_cost_amd, weights=weights, _discovery=True)
        cost_limit = base['cost_amd'] + ctx.extra_cost_amd
        # Новый дешёвый вариант может сузить бюджет. Дорогой текущий вариант тогда исключается,
        # даже если он был допустим до находки; доплата не считается от устаревшего ориентира.
        if (current['cost_amd'] <= cost_limit + 1e-6 and
                score(best_m, base, weights) >= score(current, base, weights) - MIN_GAIN):
            break
        if route_key(best) == route_key(out):
            break
        out, current, selected_source = best, best_m, best_source
    return pool, checked


def fits_previous(ctx, draft, trip):
    return all(c not in draft.locks or draft.locks[c] == trip.truck for c in trip.stops)


def actual_gain(trial, current, metrics, reference):
    """Одно удлинение пути свободной машины не считается улучшением баланса."""
    return ({c: t.truck for t in trial for c in t.stops} != {c: t.truck for t in current for c in t.stops}
            or metrics['vehicle_minutes'] < reference['vehicle_minutes'] - 1.
            or metrics['last_return_min'] < reference['last_return_min'] - 1.
            or metrics['risk'] < reference['risk'] - .01
            or metrics['cost_amd'] < reference['cost_amd'] - 1. and
            metrics['vehicle_minutes'] <= reference['vehicle_minutes'] + .5)


def choose_pool(pool, extra_cost, *, weights=None, _discovery=False):
    """Сначала самый дешёвый допустимый ориентир, затем качество в его бюджете."""
    from .dispatch_candidates import route_key
    # Архив не должен возвращать объезд, который лишь задержал быструю машину.
    # Сравниваем такие варианты с теми же владельцами магазинов, а не только с дешёвой сборкой.
    by_owners = {}
    for row in pool:
        owners = tuple(sorted((c, t.truck) for t in row[1] for c in t.stops))
        by_owners.setdefault(owners, []).append(row)
    useful = []
    fields = ('cost_amd', 'km', 'vehicle_minutes', 'last_return_min', 'risk', 'trips')
    material = (1., .1, 1., 1., .01, .5)
    for rows in by_owners.values():
        for row in rows:
            m = row[0]
            padded = any(all(m[k] >= other[0][k] - 1e-6 for k in fields) and
                         any(m[k] > other[0][k] + delta for k, delta in zip(fields, material)) for other in rows)
            if not padded:
                useful.append(row)
    pool = useful or pool
    reference, initial, reference_source = min(pool, key=lambda row: (row[0]['cost_amd'], route_key(row[1])))
    current, out, source = reference, initial, reference_source
    for metrics, trips, candidate_source in sorted(pool, key=lambda row: (score(row[0], reference, weights), row[0]['cost_amd'])):
        if (metrics['cost_amd'] <= reference['cost_amd'] + extra_cost + 1e-6
                # Поиск может пройти через промежуточный вариант; реальный выбор — только с пользой.
                and (_discovery or resource_benefit(metrics, reference)['allowed'])
                and score(metrics, reference, weights) < score(current, reference, weights) - MIN_GAIN
                and actual_gain(trips, out, metrics, current)):
            current, out, source = metrics, trips, candidate_source
    return reference, reference_source, current, out, source


def describe(ctx, stops, draft):
    """Текущие цифры всегда свежие; прежнее объяснение выбора скрывается после правок/смены модели."""
    if not ctx.balanced or not draft.trips:
        return None
    routable = {s.customer_id: s for s in stops if s.point is not None}
    if any(t.truck not in ctx.trucks or t.origin is not None or any(c not in routable for c in t.stops) for t in draft.trips):
        return None
    metrics = public(Evaluation(ctx, routable, draft).metrics(draft.trips)[0])
    saved = draft.quality_selection
    matches = (saved and saved.get('signature') == signature(draft) and saved.get('selected') == metrics
               and saved.get('extra_limit_amd') == ctx.extra_cost_amd
               and saved.get('method') == SCORING_METHOD and saved.get('group_weights') == GROUP_WEIGHTS)
    return {'profile': 'balanced', 'extra_limit_amd': ctx.extra_cost_amd, 'metrics': metrics,
            'selection': saved if matches else None, 'equal_group_weights': False,
            'group_weights': dict(GROUP_WEIGHTS), 'scoring_method': SCORING_METHOD,
            'benefit_policy': BENEFIT_POLICY,
            'cost_scope': 'fuel_and_configured_wear', 'salary_included': False}
