"""Замена сломанной машины на свободную. Только план и локальная SQLite, ERP — чтение."""
from __future__ import annotations

import copy
from typing import Any, Mapping

from . import dispatch as dp

CLOSED = frozenset(('full', 'partial', 'refused', 'covered'))


def records(raw: Any) -> list[dict[str, Any]]:
    """Ограниченный, проверенный журнал дня; старые планы без журнала остаются совместимыми."""
    out = []
    for r in raw[:100] if isinstance(raw, list) else ():
        if not isinstance(r, dict) or not all(isinstance(r.get(k), str) and r[k] for k in ('from', 'to', 'at')):
            continue
        if r['from'] == r['to']:
            continue
        v = copy.deepcopy(r)
        for k in ('customers', 'completed'):
            values = r.get(k) if isinstance(r.get(k), list) else []
            v[k] = [x for x in values if (dp._is_int(x) if k == 'customers' else isinstance(x, str))]
        values = r.get('stop_ids') if isinstance(r.get('stop_ids'), list) else []
        v['stop_ids'] = [x for x in values if isinstance(x, str) and x.startswith(('O:', 'S:'))]
        out.append(v)
    return out


def broken(draft: dp.Draft | None) -> set[str]:
    return {r['from'] for r in draft.replacements} if draft is not None else set()


def completed(draft: dp.Draft | None) -> set[str]:
    return {x for r in draft.replacements for x in r['completed']} if draft is not None else set()


def work(dd: Any, source: str, fleet: Mapping[str, Any]) -> dict[str, Any]:
    """Факт закрытия сопоставляется документу, а не всему магазину: два документа могут иметь разный статус."""
    if not any(t.truck == source for t in dd.draft.trips) or source in broken(dd.draft):
        raise dp.DispatchError('Ընտրեք փոխարինվող մեքենան՝ պահպանված երթերով')
    assigned = dp.published_orders(dd.draft, dd.stops)
    orders = [o for s in dd.stops for o in s.orders if assigned.get(o.isn, [None])[0] == source]
    if not orders or not any(t.truck == source for t in dd.draft.trips):
        raise dp.DispatchError('Մեքենան տեղափոխելու պատվերներ չունի')
    source_facts = (fleet.get(source) or {}).get('stops', [])
    known = {'O:' + o.isn for o in orders} | {'S:' + o.invoice for o in orders if o.invoice}
    def aliases(fact):
        raw = fact.get('replaces')
        return {x for x in raw if isinstance(x, str)} if isinstance(raw, (list, tuple)) else set()
    for fact in source_facts:
        if fact.get('status') not in CLOSED and not ({fact.get('stop_id')} | aliases(fact)) & known:
            raise dp.DispatchError('Տերմինալում կան պլանից դուրս չավարտված փաստաթղթեր։ '
                                   'Նախ համաժամացրեք և հաշտեցրեք պլանը՝ ամբողջ բեռը ստուգելու համար։')
    facts = {s['stop_id']: s for s in source_facts}
    # Накладная, созданная после последнего чтения плана, узнаётся по исходным заказам.
    for fact in source_facts:
        if fact['stop_id'].startswith('S:'):
            facts.update({sid: fact for sid in aliases(fact)})
    done, ids, customers, kg = set(), set(), set(), 0.0
    for o in orders:
        sid = 'S:' + o.invoice if o.invoice else 'O:' + o.isn
        fact = facts.get(sid) or facts.get('O:' + o.isn) or {}
        if fact.get('status') in CLOSED:
            done.add(o.isn)
            continue
        if fact.get('status') == 'in_progress' or any((fact.get('money') or {}).get(k) for k in ('paid', 'debt')):
            raise dp.DispatchError('Խանութում աշխատանքը դեռ չի ավարտվել․ նախ համաժամացրեք և փակեք ընթացիկ առաքումը')
        ids.update((sid, 'O:' + o.isn))
        customers.add(o.customer_id)
        kg += o.kg
    if not customers:
        raise dp.DispatchError('Մեքենայի բոլոր առաքումներն ավարտված են՝ տեղափոխելու բան չկա')
    # Не переносим закрытую накладную вместе с незакрытой накладной того же магазина.
    for s in source_facts:
        sid = s['stop_id']
        if s.get('customer_id') in customers and s.get('status') not in CLOSED:
            if s.get('status') == 'in_progress':
                raise dp.DispatchError('Նախ ավարտեք և համաժամացրեք խանութի ընթացիկ աշխատանքը')
            ids.add(sid)
            ids.update(aliases(s) & known)
    return {'completed': sorted(done), 'stop_ids': sorted(ids), 'customers': sorted(customers),
            'orders': len(orders) - len(done), 'completed_count': len(done), 'kg': round(kg, 3)}


def apply(dd: Any, source: str, target: str, work: Mapping[str, Any], edit: Mapping[str, Any],
          at: str, user: str | None, crew: Mapping[str, str], crew_before: Mapping[str, Any],
          now_min: float | None) -> dp.Draft:
    if source == target or target not in dd.ctx.trucks or target in broken(dd.draft):
        raise dp.DispatchError('Ընտրեք այլ ազատ մեքենա')
    if len(dd.draft.replacements) >= 100:
        raise dp.DispatchError('Այս օրվա մեքենաների փոխարինումների սահմանը լրացել է')
    if any(t.truck == target for t in dd.draft.trips):
        raise dp.DispatchError('Մեքենան արդեն երթ ունի․ ընտրեք ազատ մեքենա կամ օգտագործեք «Փոխանակել»')
    trial = copy.deepcopy(dd.draft)
    old_json = dd.draft.to_json()
    trial.replacements.append({'from': source, 'to': target, 'at': at, 'by': user,
                               **dict(work), 'crew': dict(crew), 'crew_before': dict(crew_before),
                               'crew_mode': edit.get('crew_mode', 'source'),
                               'handover': copy.deepcopy(edit.get('handover') or {'mode': 'depot', 'delay_min': 0}),
                               'request_id': edit.get('request_id'),
                               'signature': edit.get('replacement_signature')})
    remaining = [s for s in dd.stops if any(o.isn not in completed(trial) for o in s.orders)]
    # Пересчитать вес магазина, в котором закрыта лишь часть документов.
    from dataclasses import replace
    remaining = [replace(s, orders=tuple(o for o in s.orders if o.isn not in completed(trial)),
                         kg=sum(o.kg for o in s.orders if o.isn not in completed(trial)),
                         revenue=sum(o.revenue for o in s.orders if o.isn not in completed(trial))) for s in remaining]
    dp.prune(trial, remaining)
    trial.trucks = sorted({*trial.trucks, target})
    trial.unmanned.pop(target, None)
    first = next((t for t in trial.trips if t.truck == source), None)
    handover = edit.get('handover') or {}
    if first is not None:
        if now_min is not None or handover.get('point') is not None or handover.get('delay_min', 0):
            first.not_before = max(first.not_before or 0, dd.ctx.work_start_min + (now_min or 0) + handover.get('delay_min', 0))
        first.origin = tuple(handover['point']) if handover.get('point') is not None else None
    trial = dp._swap_trucks(dd.ctx, remaining, trial, {'truck': source, 'other_truck': target,
                            'confirm_reserve': edit.get('confirm_reserve'), 'confirm_loaded': edit.get('confirm_loaded'),
                            'action': 'swap_trucks'}, edit.get('confirm_loaded') is True)
    from .assignment import register, validate
    # Сдвиг на «сейчас» и передачу проверяется против исходного полного плана,
    # включая уже закрытые рейсы: _swap_trucks получает только оставшуюся работу.
    validate(dd.ctx, remaining, trial, dd.draft, before_stops=dd.stops)
    register(dd.draft, trial, remaining)
    trial.trucks = [c for c in trial.trucks if c != source]
    trial.unmanned.pop(source, None)
    trial.seats.pop(source, None)
    trial.seats.pop(target, None)
    trial.same_day_trucks.discard(source)
    trial.same_day_trucks.discard(target)
    trial.undo = {k: v for k, v in old_json.items() if k not in ('undo', 'prediction', 'sent')}
    return trial
