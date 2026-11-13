"""Предпросмотр и выполнение native-цепочки только для локальной копии ERP."""
from __future__ import annotations

from collections import defaultdict
from functools import wraps
import json
import secrets
from typing import Any

from flask import g, jsonify, request, session

from . import erp_chain_service as chain

# Владелец, 10.11.2026: направление заказа (DOCUMENTS.fDIVISION) определяет склад отгрузки; таблицы в ERP для этого нет.
DIVISION_WAREHOUSE = {'000000': '000', '000003': '016', '000004': '007', '000006': '007',
                      '000007': '000', '000012': '000', '000014': '000', '000017': '015'}


def register(bp: Any) -> None:
    from . import views as v
    from . import dispatch as dp
    from .vehicle_authorization import require_vehicle_approval

    def service():
        value = v._state().erp_chain
        if value is None:
            raise chain.ServiceError('not_found', 404)
        return value

    def actor():
        user = session.get('username')
        if not isinstance(user, str) or not user:
            raise chain.ServiceError('not_found', 403)
        return user

    def endpoint(fn):
        @wraps(fn)
        def run(*args, **kwargs):
            try:
                service()
                return fn(*args, **kwargs)
            except chain.ServiceError as error:
                return jsonify(success=False, code=error.code, error=str(error),
                               no_write_dispatched=error.no_write), error.http_status
        return v._api(v._admin_only(run))

    def body():
        value, error = v._json_body()
        if error is not None:
            return None, error
        if not isinstance(value, dict):
            raise chain.ServiceError('bad_request', 400)
        supplied = value.get('csrf')
        expected = session.get('erp_chain_csrf')
        if not isinstance(supplied, str) or not isinstance(expected, str) or not secrets.compare_digest(supplied, expected):
            raise chain.ServiceError('not_found', 403)
        return value, None

    def day_value(raw):
        day = v._parse_day(raw)
        if day is None:
            raise chain.ServiceError('bad_request', 400)
        if day < v._same_day_now().date():
            raise chain.ServiceError('past')
        return day

    def sent(day):
        draft, revision = v._stored_draft(v._state(), day)
        if draft is None or draft.sent is None:
            raise dp.DispatchError(v.ERP_WRITE_NOT_SENT)
        return draft.for_drivers(), revision

    def open_couriers(agents):
        return {a.code: a for a in agents.values() if not a.closed and a.code.startswith(v.ew.COURIER_PREFIX)}

    def same_person(name, cards):
        """Карточки этого человека: пробелы, регистр и порядок слов не важны. Только окно цепочки: erp_write.courier_of строже."""
        key = sorted(v.ew.name_key(name).split())
        return sorted((a for a in cards.values() if key and sorted(v.ew.name_key(a.name).split()) == key), key=lambda a: a.code)

    def terminal_people(day):
        """Машина → (id человека, имя) по последнему PIN дня. Сбой чтения «Առաքիչ» — людей нет: выбирает оператор."""
        facts = v._state().crew_facts
        if facts is None:
            return {}
        try:
            return facts.terminal_people(day.isoformat())
        except Exception:
            v.logger.warning('[Routes] ERP-цепочка: экипаж терминалов не прочитан (%s)', day, exc_info=True)
            return {}

    def pin_default(person, bound, cards):
        """Карточка по умолчанию — только для человека с PIN: привязанная, иначе единственная по имени."""
        if person is None:
            return None
        code = bound.get(person[0])
        if code in cards:
            return code
        found = same_person(person[1], cards)
        return found[0].code if len(found) == 1 else None

    def courier_code(default, submitted, cards):
        """Код ERP машины: выбор оператора (только открытая карточка B…) или значение по умолчанию."""
        if submitted:
            if submitted not in cards:
                raise chain.ServiceError('directory')
            return submitted
        if default is None:
            raise chain.ServiceError('courier_choice')
        return default

    def read_for_dialog(plan):
        """Заказы и карточки ERP для окна одним чтением; ERP не прочиталась — None."""
        state = v._state()
        if plan is None or state.erp_import_loader is None:
            return None
        try:
            states, agents, _, _ = state.erp_import_loader([i.upper() for i in plan.sent.get('orders') or {}])
        except v.ErpError:
            return None
        return states, agents

    def order_rows(plan, loaded):
        """Заказы отправленного плана по рейсам: открытые; оформленные — сводкой по рейсу (сколько и чьи տրամադրում);
        без документов, но занятые записью сайта (complete — документы удалены в ՀԾ). ERP не прочиталась — (None, 0, [], [])."""
        state = v._state()
        if plan is None:
            return [], 0, [], []
        if loaded is None or state.erp_warehouse_loader is None:
            return None, 0, [], []
        published = {i.upper(): row for i, row in (plan.sent.get('orders') or {}).items()}
        try:
            facts = state.erp_warehouse_loader(list(published))
            snap, _ = state.snapshots.get(allow_stale=True)
        except v.ErpError:
            return None, 0, [], []
        claims = service().claims(list(published))
        states, rows, left_out, seen, done, held = loaded[0], [], 0, set(), [], []
        for trip in plan.trips:
            documented, providings = 0, {}
            for isn, row in published.items():
                if isn in seen or isn not in states or len(row) < 4 or row[0] != trip.truck or row[3] not in trip.stops:
                    continue
                seen.add(isn)
                fact = facts.get(isn)
                if fact is not None and fact.get('documents'):
                    documented += 1
                    for made in fact.get('providings') or ():
                        providings.setdefault(made['number'], dict(made))
                    continue
                customer = snap.customers.get(row[3])
                store = customer.name if customer is not None else str(row[3])
                if isn in claims:
                    # В план native такой заказ не идёт (build): в список выбора тоже, окно только объясняет почему.
                    held.append({'isn': isn, 'number': states[isn].doc_num, 'store': store, 'car': trip.truck, 'trip': str(trip.id),
                                 'status': claims[isn], 'deleted': claims[isn] == 'complete'})
                    continue
                home, reserved = order_home(fact), held_reserve(fact)
                rows.append({'isn': isn, 'number': states[isn].doc_num, 'store': store,
                             'car': trip.truck, 'trip': str(trip.id), 'division': fact['division'] if fact is not None else '',
                             'division_name': fact.get('division_name', '') if fact is not None else '',
                             'warehouse': home, 'reserved': reserved, 'reserved_elsewhere': bool(set(reserved) - {home})})
            if documented:
                left_out += documented
                done.append({'trip': str(trip.id), 'car': trip.truck, 'count': documented,
                             'providings': sorted(providings.values(), key=lambda p: p['number'])})
        return rows, left_out, done, held

    def truck_rows(plan, day, loaded):
        """Строка на машину плана. ERP не прочиталась — пусто: предпросмотр всё равно проверит сам."""
        if plan is None or loaded is None:
            return []
        cards = open_couriers(loaded[1])
        people = terminal_people(day)
        bound = service().bindings()
        everyone = [{'code': a.code, 'name': a.name} for a in sorted(cards.values(), key=lambda a: a.code)]
        rows = []
        for car in dict.fromkeys(t.truck for t in plan.trips):
            person = people.get(car)
            code = pin_default(person, bound, cards)
            rows.append({'car': car, 'person': person[0] if person is not None else None,
                         'person_name': person[1] if person is not None else None,
                         'code': code, 'name': cards[code].name if code is not None else None, 'candidates': everyone})
        return rows

    def order_home(facts):
        """Склад заказа — только по его направлению (владелец, 10.11.2026); None — направление неизвестно."""
        return DIVISION_WAREHOUSE.get(facts['division']) if facts is not None else None

    def held_reserve(facts):
        """Склады, где за заказом ещё держится резерв ERP."""
        return list(facts.get('reserved') or ()) if facts is not None else []

    def build(day, selected, chosen=None, overrides=None, *, stored=False):
        state = v._state()
        plan, revision = sent(day)
        if (not isinstance(selected, list) or not selected or not all(isinstance(x, str) for x in selected)
                or len(selected) != len(set(selected))):
            raise chain.ServiceError('bad_request', 400)
        chosen = {} if chosen is None else chosen
        if not isinstance(chosen, dict) or not all(
                isinstance(x, dict) and isinstance(x.get('code'), str) and 'person' in x
                and (x['person'] is None or (isinstance(x['person'], int) and not isinstance(x['person'], bool))) for x in chosen.values()):
            raise chain.ServiceError('bad_request', 400)
        overrides = {} if overrides is None else overrides
        if not isinstance(overrides, dict) or not all(isinstance(x, str) for x in overrides.values()):
            raise chain.ServiceError('bad_request', 400)
        overrides = {isn.upper(): code for isn, code in overrides.items()}
        trips = {str(t.id): t for t in plan.trips}
        if not set(selected) <= trips.keys():
            raise chain.ServiceError('changed')
        bundle = v._bundle(state)
        stops = v._load_day(state, bundle, day, draft=plan, rev=revision, live=False).stops
        published = plan.sent.get('orders')
        if not isinstance(published, dict):
            raise dp.DispatchError('Ուղարկված պատվերների կամ վարորդների պատճենը բացակայում է․ նախ նորից ուղարկեք պլանը։')
        published = {i.upper(): row for i, row in published.items()}
        seen = set(published)
        orders = [o for s in stops for o in s.orders if o.isn.upper() in seen]
        if state.erp_import_loader is None:
            raise chain.ServiceError('not_found', 404)
        _, agents, cars, _ = state.erp_import_loader([o.isn for o in orders])
        memberships = defaultdict(set)
        for trip in plan.trips:
            for customer in trip.stops:
                memberships[customer].add(str(trip.id))
        cards = open_couriers(agents)
        people = terminal_people(day)
        bound = service().bindings()
        defaults = {t.truck: pin_default(people.get(t.truck), bound, cards) for t in plan.trips}
        if state.erp_warehouse_loader is None:
            raise chain.ServiceError('not_found', 404)
        ordered = sorted(selected, key=lambda x: list(trips).index(x))
        trip_isns = {tid: [o.isn for o in orders if o.customer_id in trips[tid].stops] for tid in ordered}
        everyone = [i for tid in ordered for i in trip_isns[tid]]
        if not stored and not set(overrides) <= {i.upper() for i in everyone}:
            raise chain.ServiceError('bad_request', 400)
        facts = state.erp_warehouse_loader(everyone)
        # Оформленный или занятый записью сайта заказ в план native не идёт: его не будет ни в проверках склада, ни в записи.
        # Если между предпросмотром и записью он станет свободным (или наоборот), состав плана изменится — запись откажет
        # как 'changed', а не пойдёт со складом, который оператор не видел.
        claimed = service().claims(everyone)
        done = {i for i in everyone if (facts.get(i.upper()) or {}).get('documents') or i.upper() in claimed}
        codes = {}
        pending = []
        for tid in ordered:
            trip = trips[tid]
            isns = trip_isns[tid]
            if trip.truck not in cars or cars[trip.truck].closed:
                raise chain.ServiceError('directory')
            for order in (o for o in orders if o.isn in isns):
                sent_row = published[order.isn.upper()]
                if (sent_row[0] != trip.truck or round(sent_row[1], 3) != round(order.kg, 3)
                        or round(sent_row[2], 2) != round(order.revenue, 2)
                        or len(sent_row) < 4 or sent_row[3] != order.customer_id):
                    raise chain.ServiceError('changed')
            if any(len(memberships[o.customer_id]) != 1 for o in orders if o.isn in isns):
                raise dp.DispatchError('Խանութը մի քանի երթում է․ նախ հստակեցրեք պատվերի երթը։')
            isns = [i for i in isns if i not in done]
            if isns:
                submitted = chosen.get(trip.truck)
                person = people.get(trip.truck)
                if submitted is not None and submitted['person'] != (person[0] if person is not None else None):
                    raise chain.ServiceError('person_changed')  # за время открытого окна на терминале вошёл другой человек
                picked = submitted['code'] if submitted is not None and submitted['code'] else None
                codes[trip.truck] = courier_code(defaults.get(trip.truck), picked, cards)
                pending.append((tid, trip.truck, person, isns))
        if len(set(codes.values())) != len(codes):
            raise chain.ServiceError('courier_twice')
        if not pending:
            raise chain.ServiceError('empty')
        for truck, default in defaults.items():
            if default is not None and any(code == default and car != truck for car, code in codes.items()):
                raise chain.ServiceError('courier_twice')
        service().check_cards(day.isoformat(), codes)
        wanted = [i for item in pending for i in item[3]]
        homes = {i: overrides.get(i.upper(), order_home(facts.get(i.upper()))) for i in wanted}
        g.erp_chain_native = service().options()
        allowed = {w['code'] for w in g.erp_chain_native.get('warehouses', ())}  # без 006, 011 и складов без выдачи
        unknown = [o.doc_num for o in orders if o.isn in homes and homes[o.isn] not in allowed]
        if unknown:
            raise chain.ServiceError('order_warehouse', detail='Պատվեր՝ ' + ', '.join(unknown[:10]))
        # Տրամադրում с другого склада оставит этот резерв висеть: такой склад только по явному выбору оператора.
        elsewhere = [o.doc_num for o in orders if o.isn in homes and o.isn.upper() not in overrides
                     and set(held_reserve(facts.get(o.isn.upper()))) - {homes[o.isn]}]
        if elsewhere:
            raise chain.ServiceError('reserve_choice', detail='Պատվեր՝ ' + ', '.join(elsewhere[:10]))
        loads = []
        for tid, truck, person, isns in pending:
            for warehouse in sorted({homes[i] for i in isns}):
                loads.append({'trip': f'{tid}/{warehouse}', 'warehouse': warehouse, 'courier': codes[truck],
                              'courier_name': cards[codes[truck]].name, 'person': person[0] if person is not None else None,
                              'person_name': person[1] if person is not None else None, 'car': truck,
                              'orders': sorted(i for i in isns if homes[i] == warehouse)})
        return {'day': day.isoformat(), 'revision': revision, 'loads': loads}

    @bp.get('/api/routes/dispatch/erp-chain/options')
    @endpoint
    def options():
        day = day_value(request.args.get('date'))
        # Запись, застрявшая в другой день, должна открываться и пока план этого дня не отправлен.
        recent = service().recent(actor(), day.isoformat(), plan_store=v._state().store)
        try:
            plan = sent(day)[0]
        except dp.DispatchError:
            if not recent:
                raise
            plan = None
        result = service().options()
        result['trips'] = [{'id': str(t.id), 'car': t.truck, 'stops': len(t.stops)} for t in plan.trips] if plan is not None else []
        loaded = read_for_dialog(plan)
        result['trucks'] = truck_rows(plan, day, loaded)
        result['orders'], result['orders_left_out'], result['documented'], result['orders_claimed'] = order_rows(plan, loaded)
        result['recent'] = recent
        result['csrf'] = session.setdefault('erp_chain_csrf', secrets.token_urlsafe(32))
        return jsonify(success=True, **result)

    @bp.post('/api/routes/dispatch/erp-chain/preview')
    @endpoint
    def preview():
        value, error = body()
        if error is not None:
            return error
        plan = build(day_value(value.get('date')), value.get('trips'), value.get('couriers'), value.get('warehouses'))
        native = g.erp_chain_native  # только что прочитано при сборке: заголовок окна не должен устаревать
        target = {key: native.get(key) for key in ('database', 'environment', 'writes_enabled', 'max_orders')}
        return jsonify(success=True, result=service().preview(actor(), plan), target=target)

    @bp.post('/api/routes/dispatch/erp-chain/execute')
    @endpoint
    def execute():
        value, error = body()
        if error is not None:
            return error
        require_vehicle_approval()
        token = chain.identifier(value.get('id'))
        record = service().get(token, actor())
        if record['status'] != 'ready':
            return jsonify(success=True, result=service().status(token, actor(), plan_store=v._state().store))
        old = json.loads(record['plan'])
        def current():
            selected = list(dict.fromkeys(str(x['trip']).split('/')[0] for x in old['loads']))
            warehouses = {isn: x['warehouse'] for x in old['loads'] for isn in x['orders']}  # склады предпросмотра не меняются
            return build(day_value(record['day']), selected, {x['car']: {'code': x['courier'], 'person': x.get('person')} for x in old['loads']},
                         warehouses, stored=True)
        return jsonify(success=True, result=service().execute(token, actor(), current, plan_store=v._state().store))

    @bp.get('/api/routes/dispatch/erp-chain/status/<token>')
    @endpoint
    def status(token):
        return jsonify(success=True, result=service().status(chain.identifier(token), actor(), plan_store=v._state().store))

    @bp.get('/api/routes/dispatch/erp-chain/progress/<token>')
    @endpoint
    def progress(token):
        """Ход записи для окна: сколько шагов уже в ERP. Сайт читает ERP сам, мост не вызывается (он занят записью)."""
        record = service().get(chain.identifier(token), actor())
        loader = v._state().erp_chain_progress_loader
        if loader is None:
            raise chain.ServiceError('not_found', 404)
        loads = [{'orders': [{'isn': o['isn'], 'version': o.get('version')} for o in x['orders']]}
                 for x in json.loads(record['native_preview'])['loads']]
        try:
            counts = loader(loads)
        except v.ErpError as error:
            raise chain.ServiceError('erp_unavailable', 503) from error
        return jsonify(success=True, progress={**counts, 'status': record['status']})

    @bp.post('/api/routes/dispatch/erp-chain/release-plan')
    @endpoint
    def release_plan():
        value, error = body()
        if error is not None:
            return error
        require_vehicle_approval()
        token = chain.identifier(value.get('id'))
        return jsonify(success=True, result=service().release_plan(token, actor(), value.get('reason'),
                                                                   plan_store=v._state().store))

    @bp.post('/api/routes/dispatch/erp-chain/reconcile-preview')
    @endpoint
    def reconcile_preview():
        value, error = body()
        if error is not None:
            return error
        return jsonify(success=True, result=service().reconciliation_preview(chain.identifier(value.get('id')), actor()))

    @bp.post('/api/routes/dispatch/erp-chain/reconcile')
    @endpoint
    def reconcile():
        value, error = body()
        if error is not None:
            return error
        require_vehicle_approval()
        return jsonify(success=True, result=service().reconcile(value.get('review_id'), actor(), value.get('reason'),
                                                               plan_store=v._state().store))
