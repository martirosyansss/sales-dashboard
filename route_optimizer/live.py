# -*- coding: utf-8 -*-
"""«Մեքենաները առցանց» — машины на карте в реальном времени (ответ владельца №76, docs/plans/live-map-plan.md).

Чистые функции — без Flask, БД и ERP: факт терминала машины за день (LiveFacts, раздел «Առաքիչ») + план «Развоза» +
нормы машины → карточка машины. Время — по Еревану; now — момент расчёта (у прошлого дня «сейчас» нет: без ETA и без
тревог «сейчас»).

Расчёты (№76, «≈» — оценка, не измерение):
- км сегодня — км по GPS тем же правилом, что «Առաքում այսօր» и «план — факт» (actuals.reconstruct: стоянки у точек дня
  и склада — 0 км, дрожание на месте км не добавляет);
- рейсы. Точка дня относится к рейсу плана, где есть её клиент (первое появление; нет в плане или плана нет — к
  первому рейсу). «Касание» закрытой точки — прибытие обслуживающего визита по GPS, без него — момент отметки доставки;
  открытой водителем (in_progress) — первого долгого заезда; ожидающей — засчитанного заезда (см. «GPS-визит точки»
  ниже: долгий, после выезда её рейса). Выезды
  со склада — концы стоянок на складе, после которых машина была вне склада, и начало трека, если он начался вне
  склада. Рейс уехал, если у его точек есть касание (выезд — последний выезд не позже первого касания, нет такого —
  начало трека) или, когда касаний нет, после последнего касания предыдущего рейса (у первого — когда угодно) был
  выезд (берётся последний такой). Рейсы — по порядку: не уехавший рейс останавливает счёт;
- груз на борту в момент t = Σ вес накладных уехавших рейсов (с момента их выезда) − Σ доставлено (вес × доля
  доставленного, №65) по точкам с моментом выгрузки до t (выгрузка — касание точки, не раньше выезда её рейса; доля
  известна без момента — с выезда) + Σ возвратов товара (событие return, вес по строкам накладных) с их момента −
  Σ выгруженного на складе. Недовезённое (отказ, частичная доставка) и возвраты остаются в машине до входа в зону
  склада (DEPOT_RADIUS_M) после последнего магазина рейса (возврата) — тогда считаются выгруженными (этап 2). Рейс
  «загружен» при выезде со склада после стоянки на нём (departed_trips): «загружено в этом рейсе» — вес накладных
  последнего уехавшего рейса. Не меньше 0. Остаток груза — груз сейчас; строки без веса — отдельным числом;
- топливо ≈ Σ по сегментам км дня (geo.track_steps по тем же точкам, что км) км × (пусто + (полно − пусто) × груз в
  конце сегмента / грузоподъёмность) / 100 — формула running_costs.route_cost; расход по загрузке не задан — расход
  машины (l/100), без загрузки; нет ни того, ни другого — неизвестно. Сверка с заправками (этап 2, refuel_check —
  «Նորմ և փաստ»): интервал «полный бак → полный бак» (learning.fuel_intervals) — залито фактически против км одометра ×
  норма машины / 100 (норма — середина «пустой — полный», нет — расход машины); заправка сегодня — расчёт с её момента;
- следующий магазин — первая незакрытая (pending/in_progress) точка с координатой последнего уехавшего рейса (нет
  уехавших — первого) по порядку плана (вне плана — по порядку терминала), затем следующих рейсов, затем пропущенные
  раньше. Точка, у которой GPS-визит уже кончился (unmarked — машина там была и уехала, водитель не отметил), — уже
  посещена: в следующий магазин и в очередь ETA не идёт, в «точки предыдущего рейса закрыты» (departed_trips) считается
  закрытой (её касание — прибытие визита — и так есть). ETA (этап 2, eta_plan) — путь по очереди оставшихся точек: участок «машина → магазин» (положение машины в
  таблицы дорог не кладётся — Road.legs(here=True) спрашивает движок отдельно) и дальше участки между магазинами и
  складом — дорожная модель «Развоза» (Road.legs: Valhalla или граф OSM, часовой профиль пробок, выученные поправки;
  нет — запасная по прямой × извилистость, и ETA помечен eta_source «model»); у каждого магазина — время №50/№60
  (Road.stay: введённое/выученное по GPS, иначе норма на точку + на тонну; у магазина, где машина уже стоит, — остаток);
  приехала раньше начала окна приёма магазина — ждёт его начала, потом разгрузка (как «Развоз»; прибытие — без ожидания);
  обед (№61), если ещё не был: после разгрузки не раньше начала окна, а если перегон кончается позже конца окна —
  в дороге; рейс ещё не уехал — через склад, загрузка (настройки; первый рейс вне сезона утренней погрузки, №78, — без
  неё: загружен с вечера) и не раньше планового выезда. Машина в STOP_RADIUS_M от
  магазина — «на месте», ETA = сейчас. Опоздание = ETA − плановое ETA (прогноз сборки «Развоза»);
- возвращение на склад — ETA прихода на склад после оставшихся точек рейса, на котором машина сейчас (тот же расчёт);
- «не успеет» (№87, late_forecast) — по тем же ETA: магазин с окном приёма — позже конца окна, без окна — позже плана на
  late_nowin_min и больше; машина — возвращение после всех рейсов позже конца рабочего дня. Только сегодня, только от
  положения не старше LATE_FIX_MAX; в журнале тревог — вид late (активен, пока прогноз такой; состояние машины не меняет);
- прогноз (ETA следующего и каждого магазина, опоздание, возвращение, «не успеет») — только при свежей связи (forecast):
  последняя связь (позже из last_contact и последней точки GPS — data_until) не старше no_contact_min у APK с device и
  OLD_APK_SILENT_MIN у старого APK, т.е. не «կապ չկա», и GPS терминала не выключен (device.gps off / no_permission —
  положение неизвестно). Стоящая машина со старой точкой, но свежей связью — прогноз есть. Прогноза нет (нет связи, GPS
  выключен, точки GPS ещё нет) — ETA «как будто машина ещё там, где её видели» вводит в заблуждение: следующий магазин
  называется с плановым ETA, eta/delay_min/eta_source — None, eta_unknown — true, here — false (где машина сейчас,
  неизвестно); ETA точек и возвращения — None, late — пусто. Шкала хода дня «Развоза» (views._progress_fill) берёт ETA и
  «на месте» из той же карточки: у такой машины и у unmarked-точек кружок — «ожидает» без времени;
- груз по плану (planned_kg) — вес накладных рейса, который машина повезёт следующим: первый рейс с точками после
  последнего уехавшего (ничего не уехало — первый рейс с точками); все уехали — None. До выезда карточка показывает его
  вместо «0 кг»;
- GPS-визит точки (gps) — из всех заездов actuals к ней (визиты с точкой в keys: повторный заезд, общее место двух
  магазинов, стоянка, разорванная дрожанием скорости, — каждый отдельно). Считаются только заезды не раньше выезда её
  рейса (стоянка у магазина следующего рейса по пути — не посещение) и не короче GPS_VISIT_MIN, кроме идущего сейчас.
  Рейсы — по порядку: заезды ожидающей точки засчитываются, когда её рейс уехал (departed_trips по отметкам водителя,
  точкам in_progress и уже засчитанным заездам прежних рейсов), не раньше первого выезда со склада после последнего
  касания прежних рейсов; засчитанный заезд — её касание. Показывается идущий сейчас заезд (сегодня: стоянка до
  последней точки трека; отъезд — None), иначе первый. Закрытая точка — обслуживающий визит (нет — первый заезд) без
  порога и без рейса: доставка отмечена, визит — факт. here («на месте», то же правило, что next.here и «стоит у
  магазина» в ETA) — есть прогноз (без связи «տեղում է» — не «сейчас»), последняя точка в STOP_RADIUS_M точки и её
  последний заезд идёт или кончился не раньше JITTER_BREAK до последней точки (стоянку оборвало дрожание скорости —
  машина там же); тогда показывается этот заезд, отъезд — None, минуты — от его прибытия до сейчас.
  unmarked — точка не закрыта водителем (pending/in_progress), заезд был, ни один не идёт сейчас и последняя точка
  сегодня не в STOP_RADIUS_M точки: «GPS-ով այցելած, տերմինալում չնշված». Касание незакрытой точки (рейсы, груз) — тоже
  только такой долгий заезд. Вес unmarked-точки остаётся в грузе и в расходе топлива (сколько отдали — неизвестно, пока
  водитель не отметит). В сводке магазинов — gps_visited (точек с таким заездом) и unmarked;
- воспроизведение дня: track_t — момент (секунды эпохи, UTC) каждой точки линии track, 1:1 с ней (те же упрощённые точки);
- подсказка точки линии трека (владелец 08.10, track_hover): скорость (track_v) и км дня (track_km) — 1:1 с track,
  расстояние до плановой линии — только у вершин внутри отклонений (track_dev_m);
- плановая линия (владелец 08.10, PlanRoute — собирает views): что водитель получил — отправленный план «Развоза» (№81),
  по рейсам склад → магазины плана по порядку → склад, по дорогам в объезд малого центра, как линии «Развоза»; дорог нет
  или линии ещё строятся — по прямой (road false: отклонение не считается). Номер магазина на карте — место клиента в
  плане машины за день (plan_no, 1…N по всем рейсам);
- отклонение от плановой линии (deviation_runs): подряд точки км-трека (ac.moving_track) дальше live_deviation_m от
  участков плана по дорогам (RouteGeometry.road_parts). Счёт — с первого настоящего выезда (конец стоянки на складе;
  трек начался вне склада — с первого магазина: дорога из дома не отклонение) до входа в зону склада после последнего
  магазина, когда все точки закрыты или посещены (дорога домой — не отклонение), и не позже закрытия дня. Не считаются
  точки в DEPOT_RADIUS_M склада и в STOP_RADIUS_M магазинов дня (терминала и плана), точки стоянки обеда (та же стоянка,
  что у тревоги «стоянка»; заезд к ней и отъезд — если не длиннее LUNCH_DETOUR_MAX_KM) и точки «по пути» участков
  плана по прямой (дороги в карте нет: _along). Отклонение — не меньше DEVIATION_MIN_KM пути (geo.track_steps, как км дня) и DEVIATION_MIN_S от
  первой до последней точки (скачок GPS и короткий заезд к кафе у дороги — не отклонение); перерыв трека дольше
  STATS_GAP его прерывает. Сводка — число и км, в деталях — линии отклонений;
- перепробег (владелец 08.10, detour_legs; только при плане): счёт дня (тот же, что у отклонения) делится на участки между
  соседними опорами фактического порядка — стоянки на складе и обслуживающие GPS-визиты магазинов (тот визит, что в
  таблице магазинов: закрытой — обслуживающий, нет — первый; открытой — засчитанный; магазины одного визита — одна опора):
  выезд → прибытие следующей опоры. Факт участка — км км-трека (geo.track_steps, как км дня) и минуты без стоянки обеда;
  план — A → B соседние в плане (склад → первый магазин рейса, магазин → следующий, последний → склад) — км линии плана по
  дорогам (RouteGeometry.leg_km), иначе (и у не соседних в плане) — дорожная модель «Развоза» (Road.pair: те же дороги и
  минуты, что участки ETA, таблицы дня — без запросов Valhalla), нет её — по прямой × извилистость (approx); минуты плана
  — всегда дорожной моделью (нет — запасная). Точки рейса плана между A и B, закрытые водителем, но без своего GPS-визита
  (координата магазина неточна — визит не нашёлся; отметка — в пределах участка или без момента), — промежуточные: план
  A → B идёт через них (по звеньям тем же правилом; «соседние в плане» — если все звенья соседние). Перепробег = факт −
  план; ≈ ֏ — за лишние км с грузом на борту в начале
  участка по формуле running_costs.route_cost (топливо по загрузке × цена дизеля настроек, нет — запасная и
  fuel_price_estimated, + износ машины: журнал гаража или ручной), расхода машины нет — ֏ неизвестно. Сегодня, пока день
  идёт, — и идущий участок: от последнего отъезда до последней точки к следующему магазину своего рейса (нет — к складу);
  его перепробег — прогноз: проехано + от последней точки до цели по прямой × извилистость самого плана участка (км
  плана / по прямой A → B, 1–LEG_RATIO_MAX; короче LEG_RATIO_MIN_KM — Road.detour) − план участка (только для
  решения «тревога или փոքր շեղում» и подсветки; ни в итог дня, ни в ֏ не входит — там только кончившиеся участки).
  Итог дня — сумма кончившихся участков с перепробегом не меньше detour_min_km (live_detour_min_km: меньше — шум GPS и
  дорог, не перепробег);
- малые отклонения (п. 5): отклонение — тревога, только если перепробег его участка (по первой точке отклонения; идущий
  участок — прогноз; участка нет — длина самого отклонения) не меньше detour_min_km; меньше — minor, «փոքր շեղում»: в журнале и на карте, не активно,
  не в «Խնդիրներ հիմա», не Telegram, не в счётчике тревог;
- порядок объезда (sequence_check; только при плане): внутри рейса плана. Точка обслужена — у неё есть касание (выше:
  закрытая — визит по GPS или отметка, in_progress — долгий визит, ожидающая — засчитанный GPS-визит; незакрытая с
  кончившимся GPS-визитом не короче ac.MIN_DWELL после выезда её рейса — короткая разгрузка без отметки — тоже
  обслужена). Пока обслужена
  точка с местом в рейсе дальше открытой (не обслуженной) точки того же рейса, открытая — «пропущена» (բաց թողնված);
  закрытая водителем без момента — обслужена, не пропущена и порядок не меняет. События — по моментам касаний: тревога
  sequence — эпизод от касания, после которого появились пропущенные, до касания, после которого их не осталось (идёт —
  активна сегодня), со списком пропущенных за эпизод (open — пропущен и сейчас) и тем, кто «перепрыгнул» (jump). Пары —
  соседние в фактическом порядке обслуженные точки рейса, где следующая в плане раньше предыдущей (одно касание — один
  визит общего места: не пара);
- следование плану (adherence): 100 × (1 − км отклонений без объяснённых / км км-трека в счёте дня); меньше
  ADHERENCE_MIN_KM езды или линии по дорогам нет — None. «փոքր շեղում» — тоже вне коридора и в него входит;
- объяснения диспетчера (apply_explanations, explains; store.live_explanations, схема 26): тревога deviation или
  sequence того же вида с тем же началом ± EXPLAIN_FROM_TOL (пересчёт сдвигает отклонение на несколько точек; случай,
  начавшийся позже, — другой и объясняется отдельно), у sequence — ещё все пропущенные эпизода есть в объяснении (эпизод
  разросся новыми пропусками — объяснять заново), — объяснена: не активна (не «сейчас», не Telegram), в журнале с
  причиной, её км не уменьшают следование плану и «Երթուղի» «Վարորդներ». В базе — время тревоги на момент объяснения
  (идущей — до последней точки GPS, не «сейчас») и пропущенные точки;
- показатели дня (day_stats): максимальная скорость терминала (момент, место; больше ac.MAX_SPEED_KMH — сбой GPS, мимо),
  время в движении, на месте и без данных (перерыв трека дольше STATS_GAP), средняя скорость в движении; превышения
  скорости — число и минуты; км плана — у PlanRoute. Трека нет — None («տվյալ չկա»), а не нули;

Тревоги (пороги — в настройках «Маршрутов», №76):
- скорость: скорость терминала > live_speed_kmh подряд не меньше live_speed_sec (от первой до последней точки подряд;
  перерыв трека дольше SPEED_GAP или точка без скорости прерывают);
- стоянка не по плану (actuals: ≥ 5 мин в 50 м вне склада и вне STOP_RADIUS_M точек дня) дольше live_stop_min — после
  первого выезда и до закрытия дня. Обед: самая длинная такая стоянка, начавшаяся в окне начала обеда
  (truck_lunch_from…truck_lunch_to), — тревога, только если длиннее обеда (truck_lunch_min) + live_stop_min;
- нет связи: «день открыт» (с первой связи за день до day_closed или до возвращения на склад со всеми закрытыми
  точками) и с последней связи прошло больше live_no_contact_min; в журнале — и перерывы между получениями событий;
- GPS выключен или нет разрешения (track.device, APK 2.2.0): от такого состояния до следующего «on»;
- машина без права въезда в малый центр (настройки машины) — в его границе: подряд не меньше CENTER_MIN_POINTS точек;
- отклонение от плановой линии (deviation): активно, пока последняя точка (свежая, не старше STALE_S) вне линии плана и
  отклонение ещё идёт, и только не «փոքր շեղում» (перепробег участка не меньше live_detour_min_km) и не объяснено;
- пропущенные магазины рейса (sequence): активна, пока есть пропущенные (сегодня) и не объяснена.
Линия трека на карте (владелец 08.10) — track_line: стоянка — одна точка, езда — без дрожания и по дорогам (привязку
Valhalla делает фон views._LiveTracks); только отображение — все расчёты выше идут по своим точкам.
"""
from __future__ import annotations

import bisect
import math
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Collection, Mapping, Protocol, Sequence

from . import actuals as ac
from . import track_line as tl
from .geo import Fix, Point, haversine_km, in_city, in_polygon, track_steps
from .learning import _hhmm, effective_refuels, fuel_intervals, track_fixes
from .store import DEFAULT_SETTINGS, LIVE_ALERT_KINDS, LIVE_EXPLAIN_KINDS

YEREVAN = ac.YEREVAN
DONE = ('full', 'partial', 'refused', 'covered')   # точка закрыта водителем
OPEN = ('pending', 'in_progress')
STALE_S = 120                       # последняя точка старше — «давно» (серая)
MOVING_MS = ac.STOP_MS              # скорость терминала не ниже — машина едет (как стоянка у магазина, №60)
SPEED_GAP = timedelta(seconds=60)   # перерыв трека дольше — превышение скорости прерывается
CENTER_MIN_POINTS = 2               # в малом центре — не меньше 2 точек подряд (одна — может быть погрешность GPS)
NO_CONTACT_END_H = 20               # APK останавливает запись в 20:00 — после этого «нет связи» не тревога (№76, ревью)
OLD_APK_SILENT_MIN = 20              # старый APK (без device) шлёт пачками раз в 2–17 мин: «կապ չկա» — после 20 мин
NO_CONTACT_MAX = timedelta(hours=3)  # связи нет дольше — машина закончила день: состояние «կապ չկա», без тревоги
# «не успеет» (№87) — только от свежего положения: старый APK шлёт пачками до 20 мин; давнее — прогноз от места, где машины
# уже нет (телефон выключен), и ложные тревоги
LATE_FIX_MAX = timedelta(minutes=OLD_APK_SILENT_MIN)
# заезд к незакрытой точке короче — не посещение (пробка, светофор у магазина): как стоянка «не по плану» actuals; разгрузка
# по нормам — от 8 мин на точку
GPS_VISIT_MIN = ac.OTHER_DWELL.total_seconds() / 60.0
TRACK_LINE_POINTS = 1500            # линия трека на карте (track_line.line)
PLAN_LINE_POINTS = 1500             # плановая линия на карте (на все рейсы); отклонение считается по полной
DEVIATION_LINE_POINTS = 300         # линия одного отклонения на карте
DEVIATION_MIN_KM = 0.5              # отклонение — не меньше 0,5 км пути вне линии плана…
DEVIATION_MIN_S = 60.0              # …и не меньше минуты от первой до последней точки
LUNCH_DETOUR_MAX_KM = 3.0          # заезд к обеду и отъезд от него вне линии до 3 км — не отклонение (дальше — отклонение)
STRAIGHT_DETOUR = 1.5               # участок плана по прямой (дороги нет): путь машины не длиннее 1,5 × прямой — «по пути»
STATS_GAP = timedelta(minutes=5)    # перерыв трека дольше — «нет данных»: ни езда, ни стоянка
M_PER_DEG_LAT = 110540.0            # равнопромежуточная проекция (как actuals.simplify): метров на градус широты…
M_PER_DEG_LON = 111320.0            # …и долготы на экваторе (× cos широты)
ADHERENCE_MIN_KM = 1.0              # следование плану — от столько км езды в счёте дня (меньше — нечего оценивать)
LEG_LINE_POINTS = 200               # линия участка с перепробегом на карте
DEPOT_NODE = 'depot'                # узел склада в порядке плана (магазины — клиенты)
LEG_RATIO_MIN_KM = 0.2              # прогноз идущего участка: короче по прямой — извилистость модели (Road.detour)…
LEG_RATIO_MAX = 3.0                 # …а у длинного — своя (км плана / по прямой), не больше 3
EXPLAIN_FROM_TOL = timedelta(minutes=3)   # объяснение — к тревоге с тем же началом ± столько (пересчёт сдвигает точки)
HOVER_SPEED_S = 60.0                # подсказка линии трека: скорость терминала ближайшей по времени точки не дальше 60 с…
HOVER_STEP_S = 120.0                # …нет — смещение между соседними точками трека не дольше 2 мин
HOVER_DEV_MAX_M = 5000.0            # расстояние до плановой линии ищется до 5 км (дальше — -1: «больше 5 км»)…
GAP_S = tl.GAP_S                    # перерыв трека в подсказке линии — как разрыв кусков track_line
HOVER_DEV_CELL_M = 500.0            # …по индексу плана с ячейкой не меньше 500 м (свой, помнится в RouteGeometry)



class LiveFacts(Protocol):
    """Факт терминалов за день (courier.live.LiveSource; подключает app_v2 — attach_live_facts)."""

    def fleet(self, day: str) -> dict[str, dict[str, Any]]:
        """Машина → stops, track, drivers, last_contact, contacts, device, devices, closed_at (см. LiveSource.fleet)."""
        ...


@dataclass(frozen=True)
class Rules:
    """Пороги тревог (по умолчанию — ответ владельца №76: «Да, так»), обед (№61), малый центр, нормы разгрузки."""
    speed_kmh: float = 90.0
    speed_sec: float = 30.0
    stop_min: float = 15.0
    no_contact_min: float = 5.0
    lunch_min: float = 30.0
    lunch_window: tuple[float, float] = (750.0, 870.0)   # начало обеда, минуты от полуночи (12:30–14:30)
    center_zone: tuple[Point, ...] = ()
    unload_min_per_stop: float = 8.0
    unload_min_per_tonne: float = 6.0
    load_min: float = 0.0                # загрузка на складе перед рейсом: фиксированные минуты и на тонну (настройки)
    load_min_per_tonne: float = 0.0
    # тревоги в Telegram (live_alerts): какие виды слать, тихие часы (минуты от полуночи: с — до; None — нет), повтор
    alert_kinds: tuple[str, ...] = LIVE_ALERT_KINDS
    quiet: tuple[float, float] | None = (1200.0, 480.0)
    repeat_min: float = 30.0
    # «не успеет» (№87): магазин без окна — прогноз позже плана не меньше чем на late_nowin_min; машина — возврат на склад
    # позже work_end (конец рабочего дня машины, минуты от полуночи; принята переработка №32 — её предел, views)
    late_nowin_min: float = 30.0
    work_end: float = 1080.0
    deviation_m: float = 300.0   # отклонение от плановой линии дальше столько метров (08.10)
    detour_min_km: float = 1.0   # отклонение — тревога, если перепробег его участка не меньше (08.10, п. 5)
    # цена топлива, ֏/л, — как «Развоз» (fleet.TruckNorms: дизель настроек, нет — запасная, тогда estimated)
    fuel_price: float = 500.0
    fuel_price_estimated: bool = True

    @classmethod
    def from_settings(cls, s: Mapping[str, Any]) -> Rules:
        def hm(key: str, default: float) -> float:
            m = _hhmm(s.get(key))
            return float(m) if m is not None else default

        def num(key: str) -> float:
            v = s.get(key)
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else float(DEFAULT_SETTINGS[key])
        quiet = (hm('live_quiet_from', 1200.0), hm('live_quiet_to', 480.0))

        def opt(key: str) -> float:   # необязательная (null — «ещё не известно») норма — 0
            v = s.get(key)
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0
        return cls(num('live_speed_kmh'), num('live_speed_sec'), num('live_stop_min'), num('live_no_contact_min'),
                   num('truck_lunch_min'), (hm('truck_lunch_from', 750.0), hm('truck_lunch_to', 870.0)),
                   tuple((float(p[0]), float(p[1])) for p in s.get('center_zone') or ()),
                   num('unload_min_per_stop'), num('unload_min_per_tonne'),
                   opt('warehouse_load_fixed_min'), opt('warehouse_load_min_per_tonne'),
                   tuple(k for k in LIVE_ALERT_KINDS if k in (s.get('live_alert_kinds', LIVE_ALERT_KINDS))),
                   quiet if quiet[0] != quiet[1] else None, num('live_repeat_min'),
                   num('late_nowin_min'), hm('truck_work_end', 1080.0), num('live_deviation_m'),
                   num('live_detour_min_km'),
                   float(s.get('fuel_price_diesel') or s.get('fuel_price_fallback', 500)),
                   s.get('fuel_price_diesel') is None)


@dataclass(frozen=True)
class TruckSpec:
    """Нормы машины: грузоподъёмность, расход (л/100 км) пустой/полной (running_costs) и общий, право въезда в центр
    (None — неизвестно: тревоги «центр» нет)."""
    capacity_kg: float | None = None
    l100: float | None = None
    empty_l100: float | None = None
    full_l100: float | None = None
    center_ok: bool | None = None
    # магазины плана этой машины, куда она въезжает в малый центр по правилу магазина (№78): у них тревоги «центр» нет
    center_customers: frozenset[int] = frozenset()
    # износ, ֏/км (журнал гаража или ручной — store.Bundle.resolved_trucks) и нагрузочная часть — как running_costs
    wear_amd_per_km: float | None = None
    wear_load_amd_per_km: float | None = None

    @property
    def norm_l100(self) -> float | None:
        """Норма для сверки с заправками — как «Նորմ և փաստ» (views._fuel_norms): середина «пустой — полный», нет —
        расход машины."""
        if self.empty_l100 is not None and self.full_l100 is not None:
            return (self.empty_l100 + self.full_l100) / 2
        return self.empty_l100 if self.empty_l100 is not None else self.l100

    def rate(self, load_kg: float) -> float | None:
        """Л/100 км при грузе load_kg — как running_costs.route_cost: base + (full − base) × груз / грузоподъёмность;
        расход по загрузке не задан — l100; нормы нет — None."""
        if self.empty_l100 is None:
            return self.l100
        slope = (self.full_l100 - self.empty_l100) if self.full_l100 is not None else 0.0
        if slope and self.capacity_kg and self.capacity_kg > 0:
            return self.empty_l100 + slope * load_kg / self.capacity_kg
        return self.empty_l100

    def cost_amd(self, km: float, load_kg: float, fuel_price: float) -> float | None:
        """֏ за km с грузом load_kg — формула running_costs.route_cost (её total_amd, как «Развоз»): топливо по загрузке ×
        цена + износ (wear + wear_load × доля груза²). Расхода нет — None (только износ ввёл бы в заблуждение)."""
        rate = self.rate(load_kg)
        if rate is None:
            return None
        u = load_kg / self.capacity_kg if self.capacity_kg and self.capacity_kg > 0 else 0.0
        return km * (rate / 100.0 * fuel_price + (self.wear_amd_per_km or 0.0) + (self.wear_load_amd_per_km or 0.0) * u * u)


@dataclass(frozen=True)
class Road:
    """Модель пути: по прямой × извилистость, скорость города (оба конца в радиусе города) или области — запасная.
    Дорожная модель «Развоза» (legs — Valhalla или граф OSM с часовым профилем пробок и выученными нормами) и время у
    магазина (unload — введённое, выученное по GPS, иначе норма №50/№60) подключает views._live_road; нет их (карты нет,
    движок не готов) — запасная модель и нормы настроек."""
    detour: float = 1.3
    city_kmh: float = 25.0
    region_kmh: float = 45.0
    center: Point = (40.1792, 44.4991)
    radius_km: float = 12.0
    # (a, b, минута суток выезда, a — положение машины сейчас) → минуты или None (пары нет); положение машины в таблицы
    # дорог не кладут (меняется каждые 30 с), его участок считается отдельно
    legs: Callable[[Point, Point, float, bool], float | None] | None = field(default=None, compare=False, repr=False)
    unload: Callable[[Point, float], float] | None = field(default=None, compare=False, repr=False)   # (точка, кг) → мин
    # (a, b, минута суток выезда) → (км, минуты) по дорожной модели «Развоза» или None (пары нет) — план участка
    # перепробега (detour_legs); положения машины здесь нет: только склад и магазины дня
    pair: Callable[[Point, Point, float], tuple[float, float] | None] | None = field(default=None, compare=False,
                                                                                   repr=False)

    def minutes(self, a: Point, b: Point) -> float:
        km = haversine_km(a, b) * self.detour
        city = in_city(a, self.center, self.radius_km) and in_city(b, self.center, self.radius_km)
        return km / (self.city_kmh if city else self.region_kmh) * 60.0

    def leg(self, a: Point, b: Point, depart_min: float, here: bool = False) -> tuple[float, bool]:
        """(минуты, по дорожной модели?): дорожная модель, нет — запасная."""
        if self.legs is not None:
            m = self.legs(a, b, depart_min, here)
            if m is not None:
                return m, True
        return self.minutes(a, b), False

    def drive(self, a: Point, b: Point, depart_min: float) -> tuple[float, float, bool]:
        """(км, минуты, по дорожной модели?) участка a → b: дорожная модель (pair), нет пары — по прямой × извилистость."""
        if self.pair is not None:
            got = self.pair(a, b, depart_min)
            if got is not None:
                return got[0], got[1], True
        return haversine_km(a, b) * self.detour, self.minutes(a, b), False

    def stay(self, p: Point, kg: float, rules: Rules) -> float:
        """Минуты у магазина p с грузом kg (№50/№60), без обеда."""
        if self.unload is not None:
            return self.unload(p, kg)
        return rules.unload_min_per_stop + rules.unload_min_per_tonne * kg / 1000.0


@dataclass(frozen=True)
class PlanTrip:
    """Рейс плана машины: клиенты по порядку объезда, плановые ETA клиентов, плановый выезд."""
    customers: tuple[int, ...]
    etas: Mapping[int, datetime]
    depart: datetime | None = None
    preloaded: bool = False   # №78: загружен с вечера (вне сезона первый рейс машины, не рейс заказов дня) — без загрузки


def plan_trips(draft_trips: Sequence[Sequence[int]], prediction: Mapping[str, Any] | None, day: date,
               preloaded: bool = False) -> list[PlanTrip]:
    """Рейсы машины из черновика «Развоза» (клиенты по порядку) и прогноза сборки (prediction['trucks'][машина]:
    trips — depart «HH:MM», stops — [[клиент, ETA]]). ETA клиента — из прогноза (первое появление); выезд рейса — из
    прогноза, если число рейсов то же (логист не менял рейсы после сборки), иначе неизвестен. preloaded — первый рейс
    загружен с вечера (№78, правило — dispatch._timeline)."""
    midnight = datetime(day.year, day.month, day.day, tzinfo=YEREVAN)

    def at(text: Any) -> datetime | None:
        m = _hhmm(text)
        return midnight + timedelta(minutes=m) if m is not None else None
    pred = [t for t in (prediction or {}).get('trips') or () if isinstance(t, Mapping)]
    etas: dict[int, datetime] = {}
    for t in pred:
        for c in t.get('stops') or ():
            if isinstance(c, list) and len(c) == 2 and isinstance(c[0], int) and c[0] not in etas \
                    and (e := at(c[1])) is not None:
                etas[c[0]] = e
    same = len(pred) == len(draft_trips)
    return [PlanTrip(tuple(stops), {c: etas[c] for c in stops if c in etas}, at(pred[j].get('depart')) if same else None,
                     preloaded and j == 0)
            for j, stops in enumerate(draft_trips)]


def _iso(t: datetime | None) -> str | None:
    return t.astimezone(YEREVAN).isoformat(timespec='seconds') if t is not None else None


def _moment(raw: Any) -> datetime | None:
    try:
        t = datetime.fromisoformat(raw) if isinstance(raw, str) else None
    except ValueError:
        return None
    return t if t is not None and t.utcoffset() is not None else None


def _near(a: Point, b: Point | None, radius_m: float) -> bool:
    return b is not None and haversine_km(a, b) * 1000.0 <= radius_m


# --- рейсы и груз ---

def departures(pts: Sequence[Fix], actual: ac.DayActual, depot: Point | None) -> list[datetime]:
    """Выезды со склада по порядку: конец стоянки на складе, после которой есть точки (машина уехала), и начало трека,
    если он начался вне склада (склада нет — начало трека)."""
    if not pts:
        return []
    out = [s.leave for s in actual.stays if s.kind == 'depot' and s.leave < pts[-1].at]
    if depot is None or not _near(pts[0].point, depot, ac.DEPOT_RADIUS_M):
        out.append(pts[0].at)
    return sorted(set(out))


def trip_of(stops: Sequence[Mapping[str, Any]], plan: Sequence[PlanTrip]) -> dict[str, int]:
    """Точка → номер рейса плана (по клиенту, первое появление); вне плана и без плана — 0."""
    first: dict[int, int] = {}
    for j, t in enumerate(plan):
        for c in t.customers:
            first.setdefault(c, j)
    return {s['stop_id']: first.get(s.get('customer_id'), 0) for s in stops}   # type: ignore[arg-type]


def departed_trips(trips: Mapping[str, int], touches: Mapping[str, datetime], deps: Sequence[datetime],
                   start: datetime | None, open_stops: Collection[str] = ()) -> dict[int, datetime]:
    """Уехавшие рейсы → момент выезда (правило — в описании модуля). trips — точка → рейс, touches — точка → касание,
    deps — выезды по порядку, start — начало трека, open_stops — незакрытые точки. Рейс без касаний уехал, только если
    все точки предыдущего рейса закрыты (заезд на склад посреди рейса — не новый рейс) и позже рейса с касаниями нет
    (машина уже на другом рейсе — этот пропущен)."""
    n = max(trips.values(), default=-1) + 1
    touched: dict[int, list[datetime]] = {}
    for sid, k in trips.items():
        if sid in touches:
            touched.setdefault(k, []).append(touches[sid])
    last_touched = max(touched, default=-1)
    out: dict[int, datetime] = {}
    prev_last: datetime | None = None
    for k in range(n):
        if k in touched:
            first = min(touched[k])
            before = [d for d in deps if d <= first]
            out[k] = before[-1] if before else min(start or first, first)
            prev_last = max(touched[k])
            continue
        if k < last_touched:
            continue
        if k > 0 and any(trips[sid] == k - 1 for sid in open_stops if sid in trips):
            break
        after = [d for d in deps if prev_last is None or d > prev_last]
        if not after:
            break
        out[k] = after[-1]
        prev_last = after[-1]
    return out


@dataclass(frozen=True)
class Load:
    events: tuple[tuple[datetime, float], ...]   # (момент, ± кг) по возрастанию момента
    loaded_kg: float          # вес накладных уехавших рейсов
    delivered_kg: float       # доставлено по ним
    unweighed: int            # строк без веса в точках уехавших рейсов, ещё не доставленных полностью
    returned_kg: float = 0.0       # принято возвратов (все)
    unloaded_kg: float = 0.0       # выгружено на складе: недовезённое и возвраты
    trip_kg: float = 0.0           # загружено в последнем уехавшем рейсе
    refused_kg: float = 0.0        # недовезённое закрытых точек, ещё в машине
    returns_aboard_kg: float = 0.0  # возвраты, ещё не сданные на склад
    returns_unweighed: int = 0     # возвратов без известного веса

    def at(self, t: datetime) -> float:
        """Груз на борту в момент t (события строго до t), не меньше 0."""
        k = bisect.bisect_left([e[0] for e in self.events], t)
        return max(0.0, math.fsum(d for _, d in self.events[:k]))

    @property
    def remaining_kg(self) -> float:
        return max(0.0, math.fsum(d for _, d in self.events))


def depot_entries(pts: Sequence[Fix], depot: Point | None) -> list[datetime]:
    """Моменты входа в зону склада: первая точка трека в DEPOT_RADIUS_M после точки вне зоны (и первая точка, если трек
    начался в зоне). Нет склада — пусто."""
    if depot is None:
        return []
    out: list[datetime] = []
    inside = False
    for f in pts:
        now = _near(f.point, depot, ac.DEPOT_RADIUS_M)
        if now and not inside:
            out.append(f.at)
        inside = now
    return out


def _entry_after(entries: Sequence[datetime], t: datetime) -> datetime | None:
    """Первый вход в зону склада строго после t."""
    k = bisect.bisect_right(entries, t)
    return entries[k] if k < len(entries) else None


def load_of(stops: Sequence[Mapping[str, Any]], trips: Mapping[str, int], gone: Mapping[int, datetime],
            touches: Mapping[str, datetime], returns: Sequence[Mapping[str, Any]] = (),
            entries: Sequence[datetime] = ()) -> Load:
    """Груз по уехавшим рейсам gone (рейс → выезд): + вес рейса в момент выезда, − доставлено в момент выгрузки точки.
    Недовезённое закрытого рейса (все его точки закрыты) — − в момент первого входа в зону склада (entries) после его
    последнего касания; возврат (returns: at, kg) — + в его момент и − при первом входе в зону склада после него.
    Нет входа — груз остаётся в машине. Точка, посещённая по GPS, но не отмеченная (unmarked), — её вес тоже в машине
    (и в расходе топлива): сколько отдали, неизвестно, пока водитель не отметит."""
    events: list[tuple[datetime, float]] = []
    loaded = delivered = unloaded = returned = refused = 0.0
    unweighed = 0
    per_trip: dict[int, list[float]] = {}   # рейс → [вес, доставлено, недовезённое закрытых точек]
    closed = {k: True for k in gone}
    for s in stops:
        k = trips[s['stop_id']]
        if k not in gone:
            continue
        kg = float(s.get('weight_kg') or 0.0)
        loaded += kg
        events.append((gone[k], kg))
        share = s.get('share')
        if share is None or share < 1.0:
            unweighed += int(s.get('unweighed') or 0)
        done = kg * min(1.0, float(share)) if share is not None and share > 0 else 0.0
        unknown = False
        if done > 0:
            delivered += done
            events.append((max(touches.get(s['stop_id'], gone[k]), gone[k]), -done))
        elif share is None and s.get('status') not in OPEN:
            # закрыта, а сколько отдали — неизвестно (неизвестное ≠ 0): не считаем весь вес точки лежащим в машине
            events.append((max(touches.get(s['stop_id'], gone[k]), gone[k]), -kg))
            unknown = True
        if s.get('status') in OPEN:
            closed[k] = False
        acc = per_trip.setdefault(k, [0.0, 0.0, 0.0])
        acc[0] += kg
        acc[1] += kg if unknown else done
        if s.get('status') not in OPEN and not unknown:
            acc[2] += kg - done
    touched: dict[int, datetime] = {}
    for s in stops:
        k = trips[s['stop_id']]
        if k in gone and s['stop_id'] in touches:
            touched[k] = max(touched.get(k, touches[s['stop_id']]), touches[s['stop_id']])
    for k, (kg, done, left) in per_trip.items():
        if left <= 0:
            continue
        back = _entry_after(entries, touched[k]) if closed[k] and k in touched else None
        if back is not None:
            events.append((back, -left))
            unloaded += left
        else:
            refused += left
    aboard = 0.0
    no_weight = 0
    for r in returns:
        at, kg = _moment(r.get('at')), r.get('kg')
        if at is None:
            continue
        if not isinstance(kg, (int, float)) or kg <= 0:
            no_weight += 1
            continue
        returned += kg
        events.append((at, float(kg)))
        back = _entry_after(entries, at)
        if back is not None:
            events.append((back, -float(kg)))
            unloaded += kg
        else:
            aboard += kg
    events.sort(key=lambda e: e[0])
    last = max(gone, default=None)
    trip_kg = per_trip[last][0] if last in per_trip else 0.0
    return Load(tuple(events), loaded, delivered, unweighed, returned, unloaded, trip_kg, refused, aboard, no_weight)


def fuel_liters(moving: Sequence[Fix], load: Load, truck: TruckSpec) -> float | None:
    """Топливо ≈ Σ сегментов км дня км × расход при грузе в конце сегмента / 100; нормы нет — None."""
    if truck.rate(0.0) is None:
        return None
    total = onboard = 0.0
    i = 0
    for f, km in track_steps(moving):   # сегменты — по времени: груз сдвигается вместе с ними
        while i < len(load.events) and load.events[i][0] < f.at:
            onboard += load.events[i][1]
            i += 1
        total += km * truck.rate(max(0.0, onboard)) / 100.0   # type: ignore[operator]
    return total


def refuel_check(refuels: Sequence[Mapping[str, Any]], truck: TruckSpec, moving: Sequence[Fix], load: Load,
                 day: date) -> dict[str, Any] | None:
    """Сверка расчёта топлива с заправками машины (refuels — Store.refuels одной машины): последняя заправка; последний
    интервал «полный бак → полный бак» — залито фактически против км одометра × норма / 100 (calc_l, delta_pct — на
    сколько % факт выше расчёта); заправка сегодня — расчёт с её момента (since_l). Заправок нет — None."""
    items = next(iter(effective_refuels(refuels).values()), [])
    if not items:
        return None
    at, _, p = items[-1]
    lit = p.get('liters')
    out: dict[str, Any] = {
        'last': {'at': _iso(at), 'liters': float(lit) if isinstance(lit, (int, float)) and not isinstance(lit, bool)
                 else None, 'full': p.get('full_tank', True) is not False,
                 'today': at.astimezone(YEREVAN).date() == day},
        'interval': None, 'since_l': None}
    ivs = fuel_intervals(refuels)
    if ivs:
        iv, norm = ivs[-1], truck.norm_l100
        calc = iv.km * norm / 100.0 if norm is not None else None
        out['interval'] = {'from': _iso(iv.start), 'to': _iso(iv.end), 'km': round(iv.km), 'liters': round(iv.liters, 1),
                           'calc_l': round(calc, 1) if calc is not None else None,
                           'delta_pct': round((iv.liters / calc - 1.0) * 100.0, 1) if calc else None}
    if out['last']['today']:
        since = fuel_liters([f for f in moving if f.at >= at], load, truck)
        out['since_l'] = round(since, 1) if since is not None else None
    return out


# --- тревоги ---

def _alert(kind: str, start: datetime, end: datetime | None, active: bool, **extra: Any) -> dict[str, Any]:
    return {'kind': kind, 'from': _iso(start), 'to': _iso(end), 'active': active, **extra}


def speed_alerts(pts: Sequence[ac.TrackFix], rules: Rules, live: bool) -> list[dict[str, Any]]:
    """Превышения: подряд точки со скоростью терминала > speed_kmh, от первой до последней не меньше speed_sec."""
    out: list[dict[str, Any]] = []
    run: list[ac.TrackFix] = []

    def close(is_last: bool) -> None:
        if run and (run[-1].at - run[0].at).total_seconds() >= rules.speed_sec:
            top = max(run, key=lambda f: f.spd or 0.0)
            out.append(_alert('speed', run[0].at, run[-1].at, live and is_last, max_kmh=round((top.spd or 0) * 3.6),
                              lat=top.lat, lon=top.lon))
    for f in pts:
        fast = f.spd is not None and f.spd * 3.6 > rules.speed_kmh
        if run and (not fast or f.at - run[-1].at > SPEED_GAP):
            close(False)
            run = []
        if fast:
            run.append(f)
    close(True)
    return out


def _unplanned(actual: ac.DayActual, since: datetime, until: datetime | None) -> list[ac.Stay]:
    """Стоянки не по плану, начавшиеся после первого выезда (since) и до закрытия дня (until)."""
    return [s for s in actual.stays if s.kind == 'other' and s.arrive >= since and (until is None or s.arrive < until)]


def lunch_stay(stays: Sequence[ac.Stay], day: date, rules: Rules) -> ac.Stay | None:
    """Обед: самая длинная из стоянок не по плану (_unplanned), начавшихся в окне начала обеда; обеда в настройках нет —
    None."""
    lo, hi = rules.lunch_window
    window = [s for s in stays if lo <= ac.day_minutes(day, s.arrive) <= hi]
    return max(window, key=lambda s: (s.minutes, s.arrive)) if window and rules.lunch_min > 0 else None


def stop_alerts(actual: ac.DayActual, day: date, rules: Rules, since: datetime | None, until: datetime | None,
                last_at: datetime | None, live: bool) -> list[dict[str, Any]]:
    """Стоянки не по плану длиннее stop_min после первого выезда (since) и до закрытия дня (until); обед — самая
    длинная из начавшихся в окне обеда: тревога, только если длиннее обеда + stop_min."""
    if since is None:
        return []
    stays = _unplanned(actual, since, until)
    lunch = lunch_stay(stays, day, rules)
    out = []
    for s in stays:
        limit = rules.stop_min + (rules.lunch_min if s is lunch else 0.0)
        if s.minutes > limit:
            ongoing = live and last_at is not None and s.leave >= last_at
            out.append(_alert('stop', s.arrive, None if ongoing else s.leave, ongoing, minutes=round(s.minutes),
                              lunch=s is lunch, lat=s.center[0] if s.center else None,
                              lon=s.center[1] if s.center else None))
    return out


def contact_alerts(contacts: Sequence[datetime], last_contact: datetime | None, now: datetime | None,
                   start: datetime | None, end: datetime | None, rules: Rules) -> list[dict[str, Any]]:
    """Нет связи: перерывы между получениями событий длиннее no_contact_min в открытом дне [start, end]; now (день
    открыт сейчас) — и с последней связи до сейчас, но не после 20:00 по Еревану и не дольше NO_CONTACT_MAX."""
    if start is None:
        return []
    limit = timedelta(minutes=rules.no_contact_min)
    inside = sorted(t for t in contacts if t >= start and (end is None or t <= end))
    out = [_alert('no_contact', a, b, False, minutes=round((b - a).total_seconds() / 60))
           for a, b in zip(inside, inside[1:]) if b - a > limit]
    if (now is not None and last_contact is not None and limit < now - last_contact <= NO_CONTACT_MAX
            and now.astimezone(YEREVAN).hour < NO_CONTACT_END_H):
        out.append(_alert('no_contact', last_contact, None, True,
                          minutes=round((now - last_contact).total_seconds() / 60)))
    return out


def gps_alerts(devices: Sequence[tuple[datetime, Any]], live: bool) -> list[dict[str, Any]]:
    """GPS выключен / нет разрешения (track.device): от такого состояния до следующего «on»; не закончилось — сейчас."""
    out: list[dict[str, Any]] = []
    cur: tuple[datetime, str] | None = None
    for at, gps in devices:
        if gps in ('off', 'no_permission') and cur is None:
            cur = (at, gps)
        elif gps == 'on' and cur is not None:
            out.append(_alert('gps', cur[0], at, False, gps=cur[1]))
            cur = None
    if cur is not None:
        out.append(_alert('gps', cur[0], None, live, gps=cur[1]))
    return out


def center_alerts(pts: Sequence[Fix], rules: Rules, truck: TruckSpec, live: bool,
                  stops: Sequence[Mapping[str, Any]] = ()) -> list[dict[str, Any]]:
    """Машина без права въезда (center_ok False) в границе малого центра: подряд не меньше CENTER_MIN_POINTS точек. Заезд,
    в котором машина была у магазина с правилом «в центр машинам допуска» (№78, truck.center_customers; в STOP_RADIUS_M его
    точки), — по правилу, без тревоги: заезд — все точки подряд в зоне, т.е. и подъезд по центру к магазину, и выезд из
    него; другие заезды в центр в тот же день — тревога."""
    if truck.center_ok is not False or len(rules.center_zone) < 3:
        return []
    ok = [(s['lat'], s['lon']) for s in stops if s.get('customer_id') in truck.center_customers
          and s.get('lat') is not None and s.get('lon') is not None]
    out = []
    run: list[Fix] = []
    for i, f in enumerate(pts + [None]):   # type: ignore[operator]
        if f is not None and in_polygon(f.point, rules.center_zone):
            run.append(f)
            continue
        if ok and any(_near(x.point, p, ac.STOP_RADIUS_M) for x in run for p in ok):
            run = []
            continue
        if len(run) >= CENTER_MIN_POINTS:
            out.append(_alert('center', run[0].at, run[-1].at, live and f is None, lat=run[0].lat, lon=run[0].lon))
        run = []
    return out


# --- плановая линия, отклонение от неё, показатели дня (владелец 08.10) ---

@dataclass(frozen=True)
class PlanRoute:
    """Плановая линия машины на день (правило — в описании модуля; собирает views._live_plan_routes): geo — линии рейсов
    (RouteGeometry: по дорогам и участки по прямой); stops — (клиент, точка) по порядку плана за день; km — км плана
    (прогноз сборки «Развоза», нет — длина линий по дорогам; None — неизвестно); trip_stops — клиенты каждой линии
    geo.trips по порядку (подсказка участка «склад → магазин → …» на карте; пусто — неизвестно)."""
    geo: RouteGeometry
    stops: tuple[tuple[int, Point], ...] = ()
    km: float | None = None
    trip_stops: tuple[tuple[int, ...], ...] = ()


def _seg_xy(x: float, y: float, x1: float, y1: float, x2: float, y2: float) -> float:
    """Расстояние на плоскости от (x, y) до отрезка (x1, y1)–(x2, y2)."""
    dx, dy = x2 - x1, y2 - y1
    d2 = dx * dx + dy * dy
    t = 0.0 if d2 == 0 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / d2))
    return math.hypot(x - x1 - t * dx, y - y1 - t * dy)


def segment_m(p: Point, a: Point, b: Point) -> float:
    """Расстояние, м, от точки p до отрезка a–b: равнопромежуточная проекция у широты p (для Армении достаточно)."""
    kx = M_PER_DEG_LON * math.cos(math.radians(p[0]))
    return _seg_xy(p[1] * kx, p[0] * M_PER_DEG_LAT, a[1] * kx, a[0] * M_PER_DEG_LAT, b[1] * kx, b[0] * M_PER_DEG_LAT)


def polyline_m(p: Point, line: Sequence[Point]) -> float:
    """Расстояние, м, от точки p до ломаной line (одна точка — до неё; пусто — бесконечность)."""
    if len(line) < 2:
        return segment_m(p, line[0], line[0]) if line else math.inf
    return min(segment_m(p, a, b) for a, b in zip(line, line[1:]))


class RouteIndex:
    """Ломаные плана в сетке ячеек cell_m × cell_m — чтобы не мерить каждую точку трека до каждого участка (трек дня —
    тысячи точек, линии по дорогам — тысячи участков, карточки флота — раз в 10 с). near(p, r) при r ≤ cell_m — то же, что
    min(polyline_m) ≤ r: ближайшая к p точка участка не дальше r лежит в ячейке p или соседней, а участок записан во все
    ячейки своего охватывающего прямоугольника. Проекция — у широты первой точки линий (в пределах Армении ошибка — доли
    процента расстояния)."""

    def __init__(self, lines: Sequence[Sequence[Point]], cell_m: float):
        first = next((p for line in lines for p in line), (40.18, 44.5))
        self._kx = M_PER_DEG_LON * math.cos(math.radians(first[0]))
        self._cell = float(cell_m)
        self._grid: dict[tuple[int, int], list[tuple[float, float, float, float]]] = {}
        for line in lines:
            xy = [self._xy(p) for p in line]
            for (x1, y1), (x2, y2) in (zip(xy, xy[1:]) if len(xy) > 1 else [(xy[0], xy[0])] if xy else ()):
                seg = (x1, y1, x2, y2)
                for i in range(math.floor(min(x1, x2) / self._cell), math.floor(max(x1, x2) / self._cell) + 1):
                    for j in range(math.floor(min(y1, y2) / self._cell), math.floor(max(y1, y2) / self._cell) + 1):
                        self._grid.setdefault((i, j), []).append(seg)

    def _xy(self, p: Point) -> tuple[float, float]:
        return p[1] * self._kx, p[0] * M_PER_DEG_LAT

    def near(self, p: Point, radius_m: float) -> bool:
        """Есть участок линий не дальше radius_m (не больше cell_m) от p."""
        x, y = self._xy(p)
        i, j = math.floor(x / self._cell), math.floor(y / self._cell)
        r = min(radius_m, self._cell)
        return any(_seg_xy(x, y, *seg) <= r for di in (-1, 0, 1) for dj in (-1, 0, 1)
                   for seg in self._grid.get((i + di, j + dj), ()))

    def distance(self, p: Point, limit_m: float) -> float | None:
        """Расстояние, м, от p до ближайшего участка линий; дальше limit_m (или линий нет) — None. Кольца ячеек вокруг
        ячейки p по очереди: точки кольца r + 1 не ближе r × cell_m — найденное не дальше этого окончательно."""
        x, y = self._xy(p)
        i, j = math.floor(x / self._cell), math.floor(y / self._cell)
        best = math.inf
        for r in range(math.ceil(limit_m / self._cell) + 1):
            for di in range(-r, r + 1):
                for dj in (range(-r, r + 1) if abs(di) == r else (-r, r)):
                    for seg in self._grid.get((i + di, j + dj), ()):
                        best = min(best, _seg_xy(x, y, *seg))
            if best <= r * self._cell:
                break
        return best if best <= limit_m else None


class RouteGeometry:
    """Линии рейсов одной машины (views строит их в фоне и держит в кэше, пока не сменятся план машины или карта):
    trips — ломаные рейсов для карты (склад → магазины → склад); road_parts — участки, проложенные по дорогам;
    straight — участки (a, b), для которых дороги не нашлось (точка дальше roads.SNAP_MAX_KM от дороги, пути нет) — на
    карте по прямой, а вдоль них отклонение не считается (_along: машина между a и b едет не по прямой). road — есть хоть
    один участок по дорогам (нет — отклонение не считается вовсе). Индекс отклонения (на порог) и линии для карты
    (actuals.simplify) считаются один раз и помнятся — не на каждом пересчёте карточки. source — линии по прямой
    (склад → магазины → склад), из которых построены trips: пока линии перестраиваются, views отдаёт прежние — по source
    видно, того ли они плана. cuts — индексы концов участков (склад, магазины по порядку, склад) в каждой из trips; None —
    каждая точка trips — конец участка (линии по прямой). leg_km — км участка (a, b) плана по его линии по дорогам (план
    участка перепробега, detour_legs)."""

    def __init__(self, trips: Sequence[Sequence[Point]], road_parts: Sequence[Sequence[Point]] = (),
                 straight: Sequence[tuple[Point, Point]] = (), source: Sequence[Sequence[Point]] = (),
                 cuts: Sequence[Sequence[int]] | None = None, leg_km: Mapping[tuple[Point, Point], float] | None = None):
        self.trips = tuple(tuple(t) for t in trips)
        self.road_parts = tuple(tuple(x) for x in road_parts)
        self.straight = tuple(straight)
        self.source = tuple(tuple(x) for x in source)
        self.cuts = (tuple(tuple(c) for c in cuts) if cuts is not None
                     else tuple(tuple(range(len(t))) for t in self.trips))
        self.leg_km = dict(leg_km or {})
        self._lock = threading.Lock()
        self._index: dict[float, RouteIndex] = {}
        self._shown: tuple[list[list[list[float]]], list[list[int]]] | None = None

    @property
    def road(self) -> bool:
        return bool(self.road_parts)

    @property
    def km(self) -> float | None:
        """Длина рейсов, если все участки — по дорогам; иначе неизвестна."""
        if not self.road or self.straight:
            return None
        return math.fsum(haversine_km(a, b) for t in self.trips for a, b in zip(t, t[1:]))

    def index(self, cell_m: float) -> RouteIndex:
        with self._lock:
            if cell_m not in self._index:
                self._index[cell_m] = RouteIndex(self.road_parts, cell_m)
            return self._index[cell_m]

    def shown(self) -> list[list[list[float]]]:
        """Линии рейсов для карты: не больше PLAN_LINE_POINTS точек на все рейсы (концы участков остаются всегда)."""
        return self._map()[0]

    def shown_cuts(self) -> list[list[int]]:
        """Индексы концов участков (склад, магазины, склад) в линиях shown() — подсказка участка «A → B» на карте."""
        return self._map()[1]

    def _map(self) -> tuple[list[list[list[float]]], list[list[int]]]:
        with self._lock:
            if self._shown is None:
                per = max(2, PLAN_LINE_POINTS // max(1, len(self.trips)))
                lines, cuts = [], []
                for t, cut in zip(self.trips, self.cuts):
                    keep = {c for c in cut if 0 <= c < len(t)}
                    # Дуглас — Пекер track_line._fit с обязательными точками; третье поле — индекс точки в t
                    fit = tl._fit([(p[0], p[1], float(i)) for i, p in enumerate(t)], keep, per)
                    where = {int(p[2]): k for k, p in enumerate(fit)}
                    lines.append([[round(p[0], 6), round(p[1], 6)] for p in fit])
                    cuts.append([where[c] for c in cut if 0 <= c < len(t)])   # по порядку, с повторами (магазины в одной точке)
                self._shown = (lines, cuts)
            return self._shown


def _along(p: Point, legs: Sequence[tuple[Point, Point]], threshold_m: float) -> bool:
    """Точка «по пути» участка по прямой: путь a → p → b не длиннее STRAIGHT_DETOUR × прямой плюс порог с обеих сторон
    (эллипс с фокусами a и b) — дороги там нет в карте, судить об отклонении нельзя."""
    return any(haversine_km(p, a) + haversine_km(p, b) <= STRAIGHT_DETOUR * haversine_km(a, b) + 2 * threshold_m / 1000.0
               for a, b in legs)


def off_route(p: Point, index: RouteIndex, threshold_m: float, keep_out: Sequence[tuple[Point, float]],
              straight: Sequence[tuple[Point, Point]] = ()) -> bool:
    """Точка вне плановой линии: дальше threshold_m от участков по дорогам, вне keep_out и не «по пути» участка по прямой."""
    return (not index.near(p, threshold_m) and not any(_near(p, c, r) for c, r in keep_out)
            and not _along(p, straight, threshold_m))


def _path_km(run: Sequence[Fix]) -> float:
    """Км пути по точкам — тем же правилом, что км дня (geo.track_steps)."""
    return math.fsum(km for _, km in track_steps(run))


def deviation_runs(moving: Sequence[Fix], index: RouteIndex | None, threshold_m: float,
                   keep_out: Sequence[tuple[Point, float]], since: datetime | None, until: datetime | None,
                   lunch: tuple[datetime, datetime] | None = None,
                   straight: Sequence[tuple[Point, Point]] = ()) -> list[list[Fix]]:
    """Отклонения от плановой линии (правило — в описании модуля): moving — точки км-трека (ac.moving_track), index —
    участки плана по дорогам (None — их нет: отклонений нет, не тревога), keep_out — (точка, радиус, м) склада и
    магазинов, since — первый настоящий выезд (None — машина не выезжала), until — конец счёта (возвращение последнего
    рейса на склад, закрытие дня; None — день идёт), lunch — (начало, конец) стоянки обеда: её точки — не отклонение, и
    заезд к обеду (отклонение, которое кончилось прямо перед стоянкой обеда) и отъезд от него (началось сразу после) не
    длиннее LUNCH_DETOUR_MAX_KM — тоже (кафе в стороне от линии; дальний объезд вокруг обеда — отклонение); straight —
    участки плана по прямой (_along). Перерыв трека дольше STATS_GAP со сменой места отклонение прерывает (что было между
    точками — неизвестно); стоянка (в km-треке — две точки в одном месте) — нет."""
    if index is None or since is None:
        return []
    out: list[list[Fix]] = []
    run: list[Fix] = []
    after_lunch = False   # отклонение началось сразу после стоянки обеда

    def close(before_lunch: bool = False) -> None:
        nonlocal after_lunch
        if len(run) >= 2 and (run[-1].at - run[0].at).total_seconds() >= DEVIATION_MIN_S:
            km = _path_km(run)
            if km >= DEVIATION_MIN_KM and not ((before_lunch or after_lunch) and km <= LUNCH_DETOUR_MAX_KM):
                out.append(list(run))
        after_lunch = False
    prev_lunch = False
    for f in moving:
        in_lunch = lunch is not None and lunch[0] <= f.at <= lunch[1]
        if run and f.at - run[-1].at > STATS_GAP and f.point != run[-1].point:   # стоянка в km-треке — две точки на месте
            close()
            run = []
        if (not in_lunch and f.at >= since and (until is None or f.at <= until)
                and off_route(f.point, index, threshold_m, keep_out, straight)):
            if not run:
                after_lunch = prev_lunch
            run.append(f)
        else:
            close(in_lunch)
            run = []
        prev_lunch = in_lunch
    close()
    return out


def day_stats(pts: Sequence[Fix]) -> dict[str, Any]:
    """Показатели дня по очищенному треку (правило — в описании модуля): max_speed — {kmh, at, lat, lon} или None (нет
    скорости терминала); moving_min / stopped_min / nodata_min — минуты в движении, на месте и без данных; avg_kmh — км
    отрезков движения / время движения (движения меньше 5 минут — None). Отрезок между соседними точками — движение, если
    у его конца скорость терминала не ниже MOVING_MS (нет скорости — смещение быстрее MOVING_MS). Трека нет — всё None."""
    tops = [f for f in pts if getattr(f, 'spd', None) is not None and f.spd * 3.6 <= ac.MAX_SPEED_KMH]   # type: ignore[attr-defined]
    top = max(tops, key=lambda f: (f.spd, -f.at.timestamp()), default=None)   # type: ignore[attr-defined]
    out: dict[str, Any] = {
        'max_speed': {'kmh': round(top.spd * 3.6), 'at': _iso(top.at), 'lat': round(top.lat, 6),   # type: ignore[attr-defined]
                      'lon': round(top.lon, 6)} if top is not None else None,
        'moving_min': None, 'stopped_min': None, 'nodata_min': None, 'avg_kmh': None}
    if len(pts) < 2:
        return out
    moving = stopped = nodata = km = 0.0
    for a, b in zip(pts, pts[1:]):
        dt = (b.at - a.at).total_seconds()
        if dt <= 0:
            continue
        step_km = haversine_km(a.point, b.point)
        spd = getattr(b, 'spd', None)
        if dt > STATS_GAP.total_seconds():
            nodata += dt
        elif (spd >= MOVING_MS) if spd is not None else step_km * 1000.0 / dt >= MOVING_MS:
            moving += dt
            km += step_km
        else:
            stopped += dt
    out.update(moving_min=round(moving / 60), stopped_min=round(stopped / 60), nodata_min=round(nodata / 60),
               avg_kmh=round(km / (moving / 3600.0)) if moving >= 300 else None)
    return out


# --- порядок объезда, перепробег, следование плану, объяснения (владелец 08.10) ---

@dataclass(frozen=True)
class Anchor:
    """Опорная точка дня в фактическом порядке: стоянка на складе или обслуживающий GPS-визит магазина (магазинов одного
    места) — прибытие, отъезд (None — машина там сейчас), точка (склад или точка магазина у терминала), узлы плана
    (DEPOT_NODE или клиенты) и точки терминала (подпись)."""
    arrive: datetime
    leave: datetime | None
    point: Point
    nodes: frozenset[Any]
    stops: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class Leg:
    """Участок дня A → B (detour_legs): км по GPS и минуты в пути против плана, перепробег и его ≈ ֏."""
    a: Anchor
    b: Anchor
    start: datetime
    end: datetime
    km: float
    minutes: float
    plan_km: float
    plan_min: float
    approx: bool          # км плана — по прямой × извилистость (ни линии плана, ни дорожной модели)
    consecutive: bool     # A → B — соседние в плане (с промежуточными закрытыми без визита — все звенья соседние)
    cost_amd: float | None
    ongoing: bool = False  # идёт сейчас: B — следующий магазин (склад), end — последняя точка
    projected_km: float | None = None   # идущий: проехано + от последней точки до B по прямой × извилистость

    @property
    def excess_km(self) -> float:
        return self.km - self.plan_km

    @property
    def judged_km(self) -> float:
        """Перепробег для решения «тревога или փոքր շեղում»: идущий — прогноз (projected_km), кончившийся — факт."""
        return (self.projected_km if self.ongoing and self.projected_km is not None else self.km) - self.plan_km

    @property
    def excess_min(self) -> float:
        return self.minutes - self.plan_min


def _overlap_min(a: datetime, b: datetime, span: tuple[datetime, datetime] | None) -> float:
    """Минут пересечения [a, b] со span (None — 0)."""
    if span is None:
        return 0.0
    return max(0.0, (min(b, span[1]) - max(a, span[0])).total_seconds() / 60.0)


def detour_legs(moving: Sequence[Fix], anchors: Sequence[Anchor], pairs: Mapping[tuple[Any, Any], float | None],
                road: Road, truck: TruckSpec, load: Load, rules: Rules, day: date, since: datetime | None,
                until: datetime | None, lunch: tuple[datetime, datetime] | None = None,
                target: Anchor | None = None, pos: Point | None = None,
                vias: Callable[[Anchor, Anchor], Sequence[tuple[Any, Point]]] | None = None) -> list[Leg]:
    """Участки дня между соседними опорными точками фактического порядка (правило — в описании модуля). moving — точки
    км-трека; anchors — по прибытию; pairs — (узел, узел) соседних в плане → км по линии плана по дорогам (None — линии
    нет); since / until — счёт дня, как у отклонения (участок начинается не раньше since и раньше until); lunch — стоянка
    обеда (её минуты — не перепробег); target — следующий магазин (склад) идущего участка: от последнего отъезда до
    последней точки трека (target.arrive), pos — последняя точка (прогноз идущего участка); vias — (A, B) → точки плана
    между ними (узел, точка), закрытые водителем без своего GPS-визита: план A → B идёт через них. Км — geo.track_steps
    (как км дня), минуты — от отъезда до прибытия."""
    if since is None:
        return []
    times = [f.at for f in moving]

    def plan_of(a: Anchor, b: Anchor, minute: float) -> tuple[float, float, bool, bool]:
        """(км, минуты плана, approx, все звенья соседние в плане) A → [промежуточные] → B."""
        chain = [(a.nodes, a.point), *((frozenset({n}), p) for n, p in (vias(a, b) if vias is not None else ())),
                 (b.nodes, b.point)]
        km = minutes = 0.0
        approx, consecutive = False, True
        for (xs, p), (ys, q) in zip(chain, chain[1:]):
            known = [pairs[(x, y)] for x in xs for y in ys if (x, y) in pairs]
            geo = next((k for k in known if k is not None), None)
            leg_km, leg_min, by_road = road.drive(p, q, minute)
            km += geo if geo is not None else leg_km
            minutes += leg_min
            approx = approx or (geo is None and not by_road)
            consecutive = consecutive and bool(known)
        return km, minutes, approx, consecutive

    def make(a: Anchor, b: Anchor, start: datetime, end: datetime, ongoing: bool) -> Leg:
        i, j = bisect.bisect_left(times, start), bisect.bisect_right(times, end)
        km = _path_km(moving[i:j])
        minutes = (end - start).total_seconds() / 60.0 - _overlap_min(start, end, lunch)
        plan_km, plan_min, approx, consecutive = plan_of(a, b, ac.day_minutes(day, start) % 1440.0)
        excess = km - plan_km
        cost = truck.cost_amd(max(0.0, excess), load.at(start), rules.fuel_price)
        projected = None
        if ongoing and pos is not None:   # остаток — с извилистостью самого плана участка (линия плана / прямая)
            direct = haversine_km(a.point, b.point)
            ratio = min(LEG_RATIO_MAX, max(1.0, plan_km / direct)) if direct >= LEG_RATIO_MIN_KM else road.detour
            projected = km + haversine_km(pos, b.point) * ratio
        return Leg(a, b, start, end, km, minutes, plan_km, plan_min, approx, consecutive, cost, ongoing, projected)

    mine = [x for x in anchors if x.leave is None or x.leave >= since]
    out = [make(a, b, a.leave, b.arrive, False) for a, b in zip(mine, mine[1:])
           if a.leave is not None and b.arrive >= a.leave and (until is None or a.leave < until)]
    if target is not None and mine and mine[-1].leave is not None and target.arrive > mine[-1].leave \
            and (until is None or mine[-1].leave < until):
        out.append(make(mine[-1], target, mine[-1].leave, target.arrive, True))
    return out


def leg_at(legs: Sequence[Leg], t: datetime) -> Leg | None:
    """Участок, в котором момент t (отклонение — по его первой точке)."""
    return next((x for x in legs if x.start <= t <= x.end), None)


def sequence_check(stops: Sequence[Mapping[str, Any]], trips: Mapping[str, int], plan: Sequence[PlanTrip],
                   touches: Mapping[str, datetime], nos: Mapping[int, int], live: bool
                   ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Порядок объезда внутри рейса (правило — в описании модуля): (тревоги sequence — по эпизоду, сводка {'skipped':
    точки, пропущенные сейчас, 'pairs': пары «обслужен раньше, хотя в плане позже»}). touches — касания точек (момент
    обслуживания: прибытие визита по GPS, нет — отметка доставки; ожидающая — засчитанный GPS-визит), nos — клиент →
    номер в плане машины за день (подпись)."""
    pos: dict[str, tuple[int, int]] = {}
    by_id = {s['stop_id']: s for s in stops}
    for s in stops:
        k = trips[s['stop_id']]
        if k < len(plan) and s.get('customer_id') in plan[k].customers:
            pos[s['stop_id']] = (k, plan[k].customers.index(s['customer_id']))
    # закрытая водителем без момента обслуживания — обслужена (когда — неизвестно): не «пропущена» и порядок не меняет
    untimed = {sid for sid in pos if sid not in touches and by_id[sid].get('status') in DONE}

    def label(sid: str) -> dict[str, Any]:
        s = by_id[sid]
        return {'stop_id': sid, 'name': s.get('name'), 'no': nos.get(s.get('customer_id'))}   # type: ignore[arg-type]

    def skipped_by(served: Collection[str]) -> list[str]:
        top: dict[int, int] = {}
        for sid in served:
            k, i = pos[sid]
            top[k] = max(top.get(k, -1), i)
        return sorted((sid for sid in pos if sid not in served and sid not in untimed
                       and pos[sid][1] < top.get(pos[sid][0], -1)), key=lambda sid: pos[sid])
    events = sorted((touches[sid], sid) for sid in pos if sid in touches)
    served: set[str] = set()
    alerts: list[dict[str, Any]] = []
    episode: tuple[datetime, dict[str, None], str] | None = None   # (начало, пропущенные за эпизод, кто «перепрыгнул»)
    skipped: list[str] = []
    i = 0
    while i < len(events):
        t = events[i][0]
        group = [sid for at, sid in events[i:] if at == t]
        i += len(group)
        served.update(group)
        skipped = skipped_by(served)
        if skipped and episode is None:
            episode = (t, dict.fromkeys(skipped), max(group, key=lambda sid: pos[sid][1]))
        elif episode is not None:
            episode[1].update(dict.fromkeys(skipped))
            if not skipped:
                alerts.append(_seq_alert(episode, t, False, by_id, label, set()))
                episode = None
    if episode is not None:
        alerts.append(_seq_alert(episode, None, live, by_id, label, set(skipped)))
    pairs = []
    for k in sorted({p[0] for p in pos.values()}):
        order = [sid for _, sid in events if pos[sid][0] == k]
        pairs += [{'trip': k + 1, 'first': label(a), 'then': label(b)}   # один визит (общее место) — не пара
                  for a, b in zip(order, order[1:]) if pos[b][1] < pos[a][1] and touches[b] > touches[a]]
    return alerts, {'skipped': [label(sid) for sid in skipped], 'pairs': pairs}


def _seq_alert(episode: tuple[datetime, dict[str, None], str], end: datetime | None, active: bool,
               by_id: Mapping[str, Mapping[str, Any]], label: Callable[[str], dict[str, Any]],
               still: Collection[str]) -> dict[str, Any]:
    start, skipped, jump = episode
    first = by_id[next(iter(skipped))]
    return _alert('sequence', start, end, active, jump=label(jump),
                  skipped=[{**label(sid), 'open': sid in still} for sid in skipped],
                  lat=first.get('lat'), lon=first.get('lon'))


def explains(e: Mapping[str, Any], a: Mapping[str, Any]) -> bool:
    """Объяснение e относится к тревоге a (правило — в описании модуля): тот же вид и то же начало ± EXPLAIN_FROM_TOL
    (отклонение между пересчётами сдвигается на несколько точек; новое — начинается позже, это другой случай); у порядка
    объезда — ещё и все пропущенные эпизода (a.skipped) есть в объяснении: эпизод разросся новыми пропусками — объяснять
    заново."""
    if e.get('kind') != a.get('kind') or not a.get('from') or not e.get('from'):
        return False
    if abs(datetime.fromisoformat(e['from']) - datetime.fromisoformat(a['from'])) > EXPLAIN_FROM_TOL:
        return False
    return a['kind'] != 'sequence' or {x['stop_id'] for x in a.get('skipped') or ()} <= set(e.get('stops') or ())


def apply_explanations(alerts: Sequence[dict[str, Any]], explained: Sequence[Mapping[str, Any]]) -> None:
    """Объяснения диспетчера (store.live_explanations: машина, день) → тревоги deviation и sequence (explains). Подходит
    несколько — последнее записанное. Объяснённая тревога: explained {id, reason, note, at, by}, active false (не
    «сейчас», не Telegram)."""
    for a in alerts:
        if a.get('kind') not in LIVE_EXPLAIN_KINDS:
            continue
        hit = None
        for e in explained:
            if explains(e, a):
                hit = e
        if hit is not None:
            a['explained'] = {k: hit.get(k) for k in ('id', 'reason', 'note', 'at', 'by')}
            a['active'] = False


def adherence(moving: Sequence[Fix], since: datetime | None, until: datetime | None,
              off_km: float) -> tuple[float | None, float]:
    """(следование плану, % — или None, км езды в счёте дня): доля км км-трека в счёте дня (since — until, как у
    отклонения) внутри коридора плана = 100 × (1 − off_km / км); off_km — км отклонений без объяснённых. Меньше
    ADHERENCE_MIN_KM езды — None."""
    if since is None:
        return None, 0.0
    counted = _path_km([f for f in moving if f.at >= since and (until is None or f.at <= until)])
    if counted < ADHERENCE_MIN_KM:
        return None, counted
    return max(0.0, min(100.0, 100.0 * (1.0 - off_km / counted))), counted


def _leg_end(x: Anchor, at: datetime | None, nos: Mapping[int, int]) -> dict[str, Any]:
    if DEPOT_NODE in x.nodes:
        return {'kind': 'depot', 'name': None, 'no': None, 'at': _iso(at)}
    nums = sorted(n for c in x.nodes if (n := nos.get(c)) is not None)
    return {'kind': 'stop', 'name': x.stops[0].get('name') if x.stops else None, 'no': nums[0] if nums else None,
            'stops': len(x.stops), 'at': _iso(at)}


def track_hover(line: Sequence[tl.TPoint], parts: Sequence[tl.Chunk], pts: Sequence[Fix], moving: Sequence[Fix],
                index: RouteIndex | None, runs: Sequence[Sequence[Fix]]) -> dict[str, Any]:
    """Данные точек линии трека для подсказки на карте (владелец 08.10: «при наведении на линию покажи данные на этой
    точке — скорость и другие»). Вершина линии — не точка GPS (у привязанной к дорогам момент — интерполяция), поэтому:
    - track_v (1:1 с track) — км/ч: точка стоянки (track_line: середина стоянки, дважды) — 0; иначе скорость терминала
      ближайшей по времени точки трека pts не дальше HOVER_SPEED_S (больше ac.MAX_SPEED_KMH — сбой GPS, мимо); нет —
      смещение между соседними по времени точками не дольше HOVER_STEP_S; нет и его — None («տվյալ չկա»);
    - track_km (1:1) — км дня к моменту вершины: те же сегменты, что км карточки (geo.track_steps по km-треку moving), между
      концами сегментов — по времени; последняя вершина — км карточки;
    - track_dev_m — только вершины внутри отклонений (deviation_runs, по моменту): [индекс вершины, м до участков плана по
      дорогам] (-1 — дальше HOVER_DEV_MAX_M); линии по дорогам нет (отклонение не считается) — None. Не 1:1: вне
      отклонений расстояние не нужно, а 1 500 «null» — лишние 7 КБ каждого опроса;
    - track_gaps — индексы i участков (i, i + 1) линии дольше GAP_S без единой точки трека pts внутри («տվյալ չկա»);
      машина стояла в пробке (точки есть, в линию не попали как дрожание) — не перерыв.
    Стоянка (где, с какого по какое время) — у страницы из самой линии (две одинаковые точки подряд) и точек дня."""
    stays: dict[tuple[float, float], list[tuple[float, float]]] = {}
    for c in parts:
        if c.stay:
            stays.setdefault((c.points[0][0], c.points[0][1]), []).append((c.points[0][2], c.points[-1][2]))
    at = [f.at.timestamp() for f in pts]

    def speed(p: tl.TPoint) -> int | None:
        if any(a <= p[2] <= b for a, b in stays.get((p[0], p[1]), ())):
            return 0
        j = bisect.bisect_left(at, p[2])
        best: tuple[float, float] | None = None   # (|Δt|, м/с)
        for k in range(j - 1, -1, -1):
            if p[2] - at[k] > HOVER_SPEED_S:
                break
            spd = getattr(pts[k], 'spd', None)
            if spd is not None and spd * 3.6 <= ac.MAX_SPEED_KMH:
                best = (p[2] - at[k], spd)
                break
        for k in range(j, len(at)):
            if at[k] - p[2] > HOVER_SPEED_S or (best is not None and at[k] - p[2] >= best[0]):
                break
            spd = getattr(pts[k], 'spd', None)
            if spd is not None and spd * 3.6 <= ac.MAX_SPEED_KMH:
                best = (at[k] - p[2], spd)
                break
        if best is not None:
            return round(best[1] * 3.6)
        if 0 < j < len(at) and 0 < at[j] - at[j - 1] <= HOVER_STEP_S:
            kmh = haversine_km(pts[j - 1].point, pts[j].point) / ((at[j] - at[j - 1]) / 3600.0)
            return round(kmh) if kmh <= ac.MAX_SPEED_KMH else None
        return None

    steps = list(track_steps(moving))
    st = [moving[0].at.timestamp()] if steps else []
    cum = [0.0] if steps else []
    for f, km in steps:
        st.append(f.at.timestamp())
        cum.append(cum[-1] + km)

    def km_at(t: float) -> float:
        j = bisect.bisect_right(st, t)
        if j == 0:
            return 0.0
        if j == len(st):
            return cum[-1]
        return cum[j - 1] + (cum[j] - cum[j - 1]) * (t - st[j - 1]) / (st[j] - st[j - 1])

    dev: list[list[int]] | None = None
    if index is not None:
        spans = [(r[0].at.timestamp(), r[-1].at.timestamp()) for r in runs]
        dev = []
        for i, p in enumerate(line):
            if any(a <= p[2] <= b for a, b in spans):
                d = index.distance((p[0], p[1]), HOVER_DEV_MAX_M)
                dev.append([i, round(d) if d is not None else -1])
    # перерывы трека: участок линии дольше GAP_S без единой точки трека внутри (стоял в пробке — точки есть, выброшены как
    # дрожание: не перерыв)
    gaps = [i for i, (p, q) in enumerate(zip(line, line[1:]))
            if q[2] - p[2] > GAP_S and bisect.bisect_right(at, p[2]) >= bisect.bisect_left(at, q[2])]
    km = [round(km_at(p[2]), 1) for p in line]
    if km and cum:
        km[-1] = round(cum[-1], 1)   # последняя вершина — км карточки (стоянка в конце дня может кончиться раньше сегмента)
    return {'track_v': [speed(p) for p in line], 'track_km': km, 'track_dev_m': dev, 'track_gaps': gaps}


# --- карточка машины ---

def _left(stops: Sequence[Mapping[str, Any]], visited: Collection[str]) -> list[Mapping[str, Any]]:
    """Оставшиеся точки: незакрытые, с координатой, без кончившегося GPS-визита (visited — unmarked)."""
    return [s for s in stops if s.get('status') in OPEN and s.get('lat') is not None and s.get('lon') is not None
            and s['stop_id'] not in visited]


def _next_stop(stops: Sequence[Mapping[str, Any]], trips: Mapping[str, int], plan: Sequence[PlanTrip],
               current: int, visited: Collection[str] = ()) -> Mapping[str, Any] | None:
    """Следующий магазин: незакрытая точка с координатой рейса current, затем следующих рейсов, затем прежних; внутри —
    in_progress, затем порядок плана (вне плана — порядок терминала). visited — точки с кончившимся GPS-визитом: уже
    посещены, не следующие."""
    pos = {c: i for t in plan for i, c in enumerate(t.customers)}
    left = _left(stops, visited)

    def key(s: Mapping[str, Any]) -> tuple[Any, ...]:
        k = trips[s['stop_id']]
        group = 0 if k == current else (1 if k > current else 2)
        return (group, k, s.get('status') != 'in_progress', pos.get(s.get('customer_id'), math.inf),
                s.get('seq') if isinstance(s.get('seq'), int) else math.inf, s['stop_id'])
    return min(left, key=key) if left else None


@dataclass(frozen=True)
class EtaPlan:
    """ETA по очереди: точка → (прибытие, участки до неё — все по дорожной модели), возвращение на склад после рейса, на
    котором машина сейчас (так же), и когда вставлен обед; end — возвращение на склад после всех оставшихся рейсов
    (конец дня машины, №87; склада нет — None)."""
    arrive: Mapping[str, tuple[datetime, bool]]
    back: tuple[datetime, bool] | None
    lunch_at: datetime | None = None
    end: datetime | None = None


def lunch_taken(actual: ac.DayActual, day: date, rules: Rules) -> bool:
    """Обед уже был: стоянка не по плану не короче половины обеда, начавшаяся в окне начала обеда ± час (обед могли
    взять раньше или позже — окно только правило сборки). Идущая сейчас стоянка считается так же — когда простояла не меньше
    половины обеда (короткая остановка в окне — ещё не обед). Обеда в настройках нет — считается взятым."""
    if rules.lunch_min <= 0:
        return True
    lo, hi = rules.lunch_window
    for s in actual.stays:
        if s.kind != 'other':
            continue
        m = ac.day_minutes(day, s.arrive)
        if s.minutes >= rules.lunch_min / 2 and lo - 60 <= m <= hi + 60:
            return True
    return False


def eta_plan(day: date, now: datetime, pos: Point, queue: Sequence[tuple[int, Sequence[Mapping[str, Any]]]],
             gone: Collection[int], current: int | None, plan: Sequence[PlanTrip], depot: Point | None,
             road: Road, rules: Rules, lunch_pending: bool, at_depot: bool,
             here: tuple[Mapping[str, Any], float] | None = None,
             windows: Mapping[int, tuple[float, float]] | None = None) -> EtaPlan:
    """Прибытия к оставшимся точкам по очереди queue (рейс, точки по порядку; рейс, где машина сейчас, — первым, даже
    пустой) и возвращение на склад после него — правило в описании модуля. here — (точка, минут уже стоит): разгрузка
    идёт, её ETA — сейчас, дальше — после остатка стоянки. windows — окна приёма клиентов (не раньше, не позже; минуты
    от полуночи): у следующих магазинов разгрузка — не раньше начала окна."""
    midnight = datetime(day.year, day.month, day.day, tzinfo=YEREVAN)
    t, live_pos, by_road = now, True, True
    arrive: dict[str, tuple[datetime, bool]] = {}
    back: tuple[datetime, bool] | None = None
    lo, hi = rules.lunch_window
    pending, lunch_at = lunch_pending and ac.day_minutes(day, now) <= hi, None   # окно кончилось — обеда уже не будет
    left_at = ac.day_minutes(day, now)   # когда выехали на текущий участок

    def move(a: Point, b: Point) -> None:
        nonlocal t, live_pos, by_road, left_at
        left_at = ac.day_minutes(day, t)
        m, ok = road.leg(a, b, ac.day_minutes(day, t) % 1440.0, live_pos)
        by_road = by_road and ok
        live_pos = False
        t += timedelta(minutes=m)

    def rest(boundary: bool) -> None:
        """Обед: после разгрузки/на складе не раньше начала окна; в дороге — если прибытие позже конца окна."""
        nonlocal t, pending, lunch_at
        if not pending:
            return
        m = ac.day_minutes(day, t)
        if (lo <= m <= hi) if boundary else (m > hi and left_at <= hi):   # в дороге — только если выехали до конца окна
            lunch_at, pending = t, False
            t += timedelta(minutes=rules.lunch_min)

    if here is not None:
        s, stayed = here
        p = (s['lat'], s['lon'])
        arrive[s['stop_id']] = (now, True)
        stay = road.stay(p, float(s.get('weight_kg') or 0.0), rules)
        opens = (windows or {}).get(s.get('customer_id'), (-math.inf, math.inf))[0]
        if math.isfinite(opens) and midnight + timedelta(minutes=opens) > now:   # ждёт окна: разгрузка — с его начала
            t = midnight + timedelta(minutes=opens + stay)
        else:
            t = now + timedelta(minutes=max(0.0, stay - stayed))
        rest(True)
        pos, live_pos, at_depot = p, False, False
    for k, stops in queue:
        if k not in gone and depot is not None:   # рейс не уехал: на склад, загрузка, не раньше планового выезда
            if not at_depot and not _near(pos, depot, ac.DEPOT_RADIUS_M):
                move(pos, depot)
            pos, at_depot = depot, True
            kg = math.fsum(float(x.get('weight_kg') or 0.0) for x in stops)
            # №78: рейс, загруженный с вечера (вне сезона первый рейс машины), — без загрузки
            load = 0.0 if k < len(plan) and plan[k].preloaded else rules.load_min + rules.load_min_per_tonne * kg / 1000.0
            ready = t + timedelta(minutes=load)
            dep = plan[k].depart if k < len(plan) else None
            t = max(ready, dep) if dep is not None else ready
            rest(True)
        for x in stops:
            p = (x['lat'], x['lon'])
            move(pos, p)
            rest(False)
            arrive[x['stop_id']] = (t, by_road)
            opens = (windows or {}).get(x.get('customer_id'), (-math.inf, math.inf))[0]
            if math.isfinite(opens):   # окно ещё не открылось — ждёт (прибытие то же)
                t = max(t, midnight + timedelta(minutes=opens))
            t += timedelta(minutes=road.stay(p, float(x.get('weight_kg') or 0.0), rules))
            rest(True)
            pos, at_depot = p, False
        if depot is None:
            continue
        if not at_depot and not _near(pos, depot, ac.DEPOT_RADIUS_M):
            move(pos, depot)
        if k == current and back is None and not (at_depot and not stops):
            back = (t, by_road)
        pos, at_depot = depot, True
    return EtaPlan(arrive, back, lunch_at, t if depot is not None and queue else None)


def _queue(stops: Sequence[Mapping[str, Any]], trips: Mapping[str, int], plan: Sequence[PlanTrip], current: int,
           nxt: Mapping[str, Any] | None, departed: bool,
           visited: Collection[str] = ()) -> list[tuple[int, list[Mapping[str, Any]]]]:
    """Оставшиеся точки по очереди объезда: рейс, где машина сейчас (первым, даже без точек — домой), затем следующие
    рейсы, затем пропущенные раньше; внутри — следующий магазин (nxt), затем порядок плана (вне плана — терминала).
    visited — точки с кончившимся GPS-визитом: в очередь не идут."""
    pos = {c: i for t in plan for i, c in enumerate(t.customers)}
    left = _left(stops, visited)
    by_trip: dict[int, list[Mapping[str, Any]]] = {}
    for s in left:
        by_trip.setdefault(trips[s['stop_id']], []).append(s)
    for xs in by_trip.values():
        xs.sort(key=lambda s: (nxt is None or s['stop_id'] != nxt['stop_id'], pos.get(s.get('customer_id'), math.inf),
                               s.get('seq') if isinstance(s.get('seq'), int) else math.inf, s['stop_id']))
    first = [current] if departed or current in by_trip else []
    later = sorted((k for k in by_trip if k != current), key=lambda k: (k < current, k))
    return [(k, by_trip.get(k, [])) for k in [*first, *later]]


def late_forecast(day: date, stops: Sequence[Mapping[str, Any]], arrive: Mapping[str, tuple[datetime, bool]],
                  planned: Mapping[str, datetime], windows: Mapping[int, tuple[float, float]], end: datetime | None,
                  rules: Rules, here: str | None = None) -> list[dict[str, Any]]:
    """«Не успеет» (ответ владельца №87, п.2) по прогнозу eta_plan: arrive — точка → (прибытие, по дорогам), planned —
    точка → плановое ETA её клиента в её рейсе, windows — клиент → окно приёма (не раньше, не позже; минуты от полуночи,
    store.CustomerWindow.span), end — возвращение на склад после всех оставшихся рейсов, here — точка, у которой машина
    стоит (её прибытие — факт, не прогноз). Только ожидающие точки (pending) с прогнозом — in_progress водитель открывает
    у магазина, прибытие уже было:
    - магазин с окном — прибытие позже конца окна (как window_miss «Развоза»: раньше начала — машина ждёт, не
      опоздание); late_kind window, over_min — на сколько позже конца;
    - без окна и с окном без конца («не раньше HH:MM») — прибытие позже планового ETA не меньше чем на
      rules.late_nowin_min; late_kind plan, over_min — насколько позже плана; нет планового ETA — не оценивается;
    - машина — возвращение на склад позже rules.work_end (конец рабочего дня; возврат в запасе конца дня №78 — не
      опоздание, как в «Развозе»); late_kind return.
    Опоздание, округлённое до минут, меньше 1 — не опоздание. Магазин с несколькими накладными — одна строка (самое
    большое опоздание); target — ключ строки на день (cКЛИЕНТ, без клиента — точка; return). Магазины — по прибытию,
    машина — последней."""
    midnight = datetime(day.year, day.month, day.day, tzinfo=YEREVAN)
    worst: dict[str, dict[str, Any]] = {}
    for s in stops:
        sid, cid = s['stop_id'], s.get('customer_id')
        if s.get('status') != 'pending' or sid == here or sid not in arrive:
            continue
        at = arrive[sid][0]
        span = windows.get(cid) if isinstance(cid, int) else None
        if span is not None and math.isfinite(span[1]):
            limit = midnight + timedelta(minutes=span[1])
            kind = 'window'
        else:
            limit = planned.get(sid)   # type: ignore[assignment]
            if limit is None or (at - limit).total_seconds() / 60.0 < rules.late_nowin_min:
                continue
            kind = 'plan'
        over = round((at - limit).total_seconds() / 60.0)
        target = f'c{cid}' if isinstance(cid, int) else str(sid)
        if over >= 1 and (target not in worst or over > worst[target]['over_min']):
            worst[target] = {'target': target, 'late_kind': kind, 'stop_id': sid, 'customer_id': cid,
                             'name': s.get('name'), 'eta': _iso(at), 'limit': _iso(limit), 'over_min': over}
    out = sorted(worst.values(), key=lambda x: (x['eta'], x['target']))
    if end is not None:
        limit = midnight + timedelta(minutes=rules.work_end)
        over = round((end - limit).total_seconds() / 60.0)
        if over >= 1:
            out.append({'target': 'return', 'late_kind': 'return', 'stop_id': None, 'customer_id': None, 'name': None,
                        'eta': _iso(end), 'limit': _iso(limit), 'over_min': over})
    return out


def car_view(day: date, now: datetime, facts: Mapping[str, Any], plan: Sequence[PlanTrip], truck: TruckSpec,
             depot: Point | None, rules: Rules, road: Road, detail: bool = False,
             windows: Mapping[int, tuple[float, float]] | None = None,
             route: PlanRoute | None = None,
             track_snap: Callable[[Sequence[tl.Chunk]], Mapping[Any, Sequence[tl.TPoint]]] | None = None,
             explained: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Карточка машины (detail — ещё линия трека, точки дня, журнал тревог, плановая линия и линии отклонений). now —
    сейчас (Ереван); день не сегодня — без ETA и тревог «сейчас». windows — окна приёма клиентов (late_forecast), route —
    плановая линия машины (None — машине план не отправлен). track_snap — куски линии трека → уже привязанные к дорогам
    (ключ Chunk.key → линия; views._LiveTracks: чего нет — привязывается в фоне); None — линия без привязки. Линия трека
    (track_line) — только отображение: ни один показатель карточки от неё не зависит. explained — объяснения диспетчера
    этой машины за день (store.live_explanations; apply_explanations)."""
    live = now.astimezone(YEREVAN).date() == day
    stops = list(facts.get('stops') or ())
    raw = list(facts.get('track') or ())
    fixes = track_fixes(raw)
    pts = ac.clean_track(fixes)
    plan_stops = [ac.PlanStop(s['stop_id'], s.get('customer_id'),
                              (s['lat'], s['lon']) if s.get('lat') is not None and s.get('lon') is not None else None,
                              float(s.get('weight_kg') or 0.0), delivered_at=_moment(s.get('delivered_at')))
                  for s in stops]
    actual = ac.reconstruct(fixes, plan_stops, depot)
    visited = actual.visited
    last = pts[-1] if pts else None
    last_contact = _moment(facts.get('last_contact'))
    device = facts.get('device')
    # данные до: последняя связь или последняя точка GPS, что позже; прогноз — только при свежей связи и включённом GPS
    data_until = max((t for t in (last_contact, last.at if last is not None else None) if t is not None), default=None)
    silent = timedelta(minutes=rules.no_contact_min if device is not None else OLD_APK_SILENT_MIN)
    gps_off = isinstance(device, Mapping) and device.get('gps') in ('off', 'no_permission')
    forecast = live and data_until is not None and now - data_until <= silent and not gps_off

    # визиты точки по GPS (все заезды, не только обслуживающий): идёт сейчас — стоянка до последней точки трека (сегодня);
    # долгий — не короче GPS_VISIT_MIN (короче — пробка, светофор у магазина) или идёт сейчас
    visits: dict[str, list[ac.Visit]] = {}
    for v in actual.visits:
        for k in v.keys:
            visits.setdefault(k, []).append(v)
    ongoing = {k: v for k, vs in visits.items() for v in vs if live and last is not None and v.leave >= last.at}
    long = {k: [v for v in vs if v.minutes >= GPS_VISIT_MIN or ongoing.get(k) is v] for k, vs in visits.items()}
    # касания: закрытые — визит или отметка доставки; открытые водителем у магазина (in_progress) — долгий визит;
    # ожидающие — долгий визит после выезда своего рейса (ниже, по рейсам)
    touches: dict[str, datetime] = {}
    for s in stops:
        sid = s['stop_id']
        if s.get('status') in DONE:
            t = visited.get(sid) or _moment(s.get('delivered_at'))
        elif s.get('status') == 'in_progress':
            t = min((v.arrive for v in long.get(sid, ())), default=None)
        else:
            continue
        if t is not None:
            touches[sid] = t
    trips = trip_of(stops, plan)
    deps = departures(pts, actual, depot)
    start = pts[0].at if pts else None
    open_ids = {s['stop_id'] for s in stops if s.get('status') in OPEN}
    seen: dict[str, list[ac.Visit]] = {}   # точка → засчитанные заезды
    unmarked: set[str] = set()
    for k in sorted(set(trips.values())):   # рейс уехал — с тем, что известно о прежних рейсах (их визиты уже засчитаны)
        gone = departed_trips(trips, touches, deps, start, open_ids - unmarked)
        if k not in gone:
            continue
        # заезд засчитывается не раньше первого выезда после последнего касания прежних рейсов (выезд рейса — последний
        # такой: заезд на склад посреди рейса не отменяет визиты до него)
        prev = max((t for sid, t in touches.items() if trips[sid] < k), default=None)
        bound = min([d for d in deps if prev is None or d > prev][:1] + [gone[k]])
        for s in stops:
            sid = s['stop_id']
            if trips[sid] != k or s.get('status') not in OPEN or s.get('lat') is None or s.get('lon') is None:
                continue   # закрытая точка — визит показывается как есть (ниже), правило заездов — только для незакрытых
            mine = [v for v in long.get(sid, ()) if v.arrive >= bound]
            if not mine:
                continue
            seen[sid] = mine
            if s.get('status') == 'pending':
                touches.setdefault(sid, mine[0].arrive)
            inside = live and last is not None and _near(last.point, (s['lat'], s['lon']), ac.STOP_RADIUS_M)
            if sid not in ongoing and not inside:
                unmarked.add(sid)   # машина была и уехала, водитель не отметил — точка посещена
    gone = departed_trips(trips, touches, deps, start, open_ids - unmarked)   # посещённые по GPS не держат следующий рейс
    # «на месте» — одно правило для карточки (next.here) и таблицы: прогноз есть, последняя точка в STOP_RADIUS_M точки и её
    # последний заезд идёт или кончился не раньше JITTER_BREAK до неё (стоянку оборвало дрожание скорости — машина там же)
    served = dict(actual.served)

    def at_stop(x: Mapping[str, Any]) -> ac.Visit | None:
        vs = visits.get(x['stop_id'])
        if not (forecast and last is not None and vs and x.get('lat') is not None and x.get('lon') is not None
                and _near(last.point, (x['lat'], x['lon']), ac.STOP_RADIUS_M)):   # type: ignore[union-attr]
            return None
        return vs[-1] if vs[-1].leave >= last.at - ac.JITTER_BREAK else None   # type: ignore[union-attr]
    gps: dict[str, dict[str, Any]] = {}
    serving: dict[str, tuple[ac.Visit, datetime | None]] = {}   # точка → её визит и отъезд (None — машина там) — опоры дня
    for s in stops:
        sid = s['stop_id']
        cur = at_stop(s)
        if cur is not None:
            gps[sid] = {'arrive': _iso(cur.arrive), 'leave': None, 'here': True,
                        'minutes': round(max(0.0, (now - cur.arrive).total_seconds() / 60.0))}
            serving[sid] = (cur, None)
            continue
        if s.get('status') in OPEN:   # незакрытая — засчитанный заезд (долгий, после выезда её рейса)
            v = (ongoing.get(sid) if ongoing.get(sid) in seen.get(sid, ()) else seen[sid][0]) if sid in seen else None
        else:   # закрытая — обслуживающий визит, нет — первый заезд, как бы короток ни был
            v = actual.visits[served[sid]] if sid in served else next(iter(visits.get(sid, ())), None)
        if v is not None:
            gps[sid] = {'arrive': _iso(v.arrive), 'leave': None if v is ongoing.get(sid) else _iso(v.leave),
                        'minutes': round(v.minutes), 'here': False}
            serving[sid] = (v, None if v is ongoing.get(sid) else v.leave)
    entries = depot_entries(pts, depot)
    load = load_of(stops, trips, gone, touches, facts.get('returns') or (), entries)
    moving = ac.moving_track(fixes, actual)
    fuel = fuel_liters(moving, load, truck) if pts else (0.0 if truck.rate(0) is not None else None)
    refuel = refuel_check(facts.get('refuels') or (), truck, moving, load, day)

    brg = next((p[5] for p in reversed(raw) if last is not None and p[0] == round(last.at.timestamp() * 1000)
                and len(p) > 5), None)
    contacts = sorted(t for t in (_moment(x) for x in facts.get('contacts') or ()) if t is not None)
    closed_at = _moment(facts.get('closed_at'))
    done = sum(1 for s in stops if s.get('status') in DONE)
    at_depot = last is not None and _near(last.point, depot, ac.DEPOT_RADIUS_M)
    finished = closed_at is not None or (bool(stops) and done == len(stops) and at_depot)
    started = contacts[0] if contacts else (pts[0].at if pts else None)
    end = closed_at or (last.at if finished and last is not None else None)
    open_now = live and started is not None and not finished
    age = (now - last.at).total_seconds() if last is not None else None

    # следующий магазин, ETA, опоздание, возвращение
    current = max(gone) if gone else 0
    nxt = _next_stop(stops, trips, plan, current, unmarked) if live and stops else None
    next_out = None
    return_eta = None
    return_source = None
    etas: dict[str, tuple[datetime, bool]] = {}
    late: list[dict[str, Any]] = []
    own = {s['stop_id']: e for s in stops if trips[s['stop_id']] < len(plan)
           and (e := plan[trips[s['stop_id']]].etas.get(s.get('customer_id'))) is not None}   # плановое ETA её рейса
    if nxt is not None and not (forecast and last is not None) and not finished:   # прогноза нет: магазин и план — да
        k = trips[nxt['stop_id']]
        planned = plan[k].etas.get(nxt.get('customer_id')) if k < len(plan) else None   # type: ignore[arg-type]
        next_out = {'stop_id': nxt['stop_id'], 'name': nxt.get('name'), 'here': False, 'eta': None, 'eta_source': None,
                    'planned_eta': _iso(planned), 'delay_min': None, 'eta_unknown': True}
    if (forecast and last is not None and not finished
            and (nxt is not None or (gone and not at_depot and depot is not None))):
        queue = _queue(stops, trips, plan, current, nxt, bool(gone), unmarked)
        here = None
        if nxt is not None and _near(last.point, (nxt['lat'], nxt['lon']), ac.STOP_RADIUS_M):
            cur = at_stop(nxt)   # стоит у него — разгрузка идёт с прибытия этого заезда (только подъехал — с нуля)
            here = (nxt, max(0.0, (now - cur.arrive).total_seconds() / 60.0) if cur is not None else 0.0)
            queue = [(k, [x for x in xs if x['stop_id'] != nxt['stop_id']]) for k, xs in queue]
        eta = eta_plan(day, now, last.point, queue, gone, current if gone else None, plan, depot, road, rules,
                       not lunch_taken(actual, day, rules), at_depot, here, windows)
        etas = dict(eta.arrive)
        if now - last.at <= LATE_FIX_MAX:   # давнее положение — прогноз «не успеет» не строится (нет GPS — нет тревоги)
            late = late_forecast(day, stops, etas, own, windows or {}, eta.end, rules,
                                 nxt['stop_id'] if here is not None else None)   # type: ignore[index]
        if eta.back is not None and gone and not at_depot:
            return_eta, return_source = eta.back[0], 'road' if eta.back[1] else 'model'
        if nxt is not None:
            k = trips[nxt['stop_id']]
            arrival, by_road = etas[nxt['stop_id']]
            planned = plan[k].etas.get(nxt.get('customer_id')) if k < len(plan) else None   # type: ignore[arg-type]
            next_out = {'stop_id': nxt['stop_id'], 'name': nxt.get('name'), 'here': here is not None,
                        'eta': _iso(arrival), 'eta_source': None if here is not None else ('road' if by_road else 'model'),
                        'planned_eta': _iso(planned),
                        'delay_min': round((arrival - planned).total_seconds() / 60) if planned is not None else None,
                        'eta_unknown': False}

    # тревоги
    devices = [(t, gps) for t, gps in ((_moment(a), g) for a, g in facts.get('devices') or ()) if t is not None]
    first_dep = deps[0] if deps else None
    fresh = live and age is not None and age <= STALE_S
    speeds = speed_alerts(pts, rules, fresh)   # type: ignore[arg-type]

    # отклонение от плановой линии: только по линиям по дорогам; склад и магазины дня (терминала и плана) — не отклонение
    geo = route.geo if route is not None else None
    index = geo.index(rules.deviation_m) if geo is not None and geo.road else None
    straight = geo.straight if geo is not None else ()
    keep = [(depot, ac.DEPOT_RADIUS_M)] if depot is not None else []
    keep += [((s['lat'], s['lon']), ac.STOP_RADIUS_M) for s in stops if s.get('lat') is not None and s.get('lon') is not None]
    keep += [(p, ac.STOP_RADIUS_M) for _, p in (route.stops if route is not None else ())]
    lunch = lunch_stay(_unplanned(actual, first_dep, end), day, rules) if first_dep is not None else None
    # счёт — с первого настоящего выезда (конец стоянки на складе; трек начался вне склада — с первого магазина: дорога
    # из дома не отклонение) до возвращения на склад после последнего магазина (все точки закрыты или посещены) — дорога
    # домой без закрытия дня не отклонение; закрыт день раньше — до закрытия
    out_at = min([s.leave for s in actual.stays if s.kind == 'depot' and pts and s.leave < pts[-1].at]
                 + ([min(touches.values())] if touches else []), default=None)
    home = None
    if stops and touches and not any(s.get('status') in OPEN and s['stop_id'] not in unmarked for s in stops):
        home = _entry_after(entries, max(touches.values()))
    until = min((t for t in (end, home) if t is not None), default=None)
    lunch_span = (lunch.arrive, lunch.leave) if lunch is not None else None
    runs = deviation_runs(moving, index, rules.deviation_m, keep, out_at, until, lunch_span, straight)
    where: dict[int, Point] = {}   # клиент → точка, по порядку плана машины за день (первое появление)
    for c, p in (route.stops if route is not None else ()):
        where.setdefault(c, p)
    nos = {c: n for n, c in enumerate(where, 1)}   # номер магазина в плане за день (1…N по всем рейсам)

    # перепробег по участкам фактического порядка (только при плане): опоры — стоянки на складе и обслуживающие визиты
    legs: list[Leg] = []
    if plan:
        anchors = [Anchor(x.arrive, None if live and last is not None and x.leave >= last.at else x.leave, depot,
                          frozenset({DEPOT_NODE})) for x in actual.stays if x.kind == 'depot' and depot is not None]
        by_visit: dict[tuple[datetime, datetime], list[Mapping[str, Any]]] = {}
        for x in stops:
            if x['stop_id'] in serving and x.get('lat') is not None and x.get('lon') is not None:
                v = serving[x['stop_id']][0]
                by_visit.setdefault((v.arrive, v.leave), []).append(x)
        for (arrive, _), xs in by_visit.items():
            anchors.append(Anchor(arrive, serving[xs[0]['stop_id']][1], (xs[0]['lat'], xs[0]['lon']),
                                  frozenset(x.get('customer_id') for x in xs), tuple(xs)))
        anchors.sort(key=lambda x: x.arrive)
        pt = {DEPOT_NODE: depot, **where}
        pairs: dict[tuple[Any, Any], float | None] = {}
        for t in plan:
            seq = [DEPOT_NODE, *t.customers, DEPOT_NODE]
            for a, b in zip(seq, seq[1:]):
                if a != b:
                    pa, pb = pt.get(a), pt.get(b)
                    known = geo is not None and pa is not None and pb is not None
                    pairs[(a, b)] = geo.leg_km.get((pa, pb)) if known else None   # type: ignore[union-attr]
        target = None   # идущий участок: к следующему магазину своего рейса, иначе — на склад
        if live and until is None and last is not None and not finished:
            if nxt is not None and trips[nxt['stop_id']] == current:
                target = Anchor(last.at, None, (nxt['lat'], nxt['lon']), frozenset({nxt.get('customer_id')}), (nxt,))
            elif gone and depot is not None:
                target = Anchor(last.at, None, depot, frozenset({DEPOT_NODE}))
        anchored = {n for x in anchors for n in x.nodes}
        by_cid: dict[Any, list[Mapping[str, Any]]] = {}
        for x in stops:
            by_cid.setdefault(x.get('customer_id'), []).append(x)

        def vias(a: Anchor, b: Anchor) -> list[tuple[Any, Point]]:
            """Точки рейса плана между A и B, закрытые водителем без своего GPS-визита (координата магазина неточна —
            визит не найден; отметка — в пределах участка или без момента): машина там была, план A → B — через них."""
            for t in plan:
                cs = list(t.customers)
                ia = -1 if DEPOT_NODE in a.nodes else min((cs.index(c) for c in a.nodes if c in cs), default=None)
                ib = len(cs) if DEPOT_NODE in b.nodes else min((cs.index(c) for c in b.nodes if c in cs), default=None)
                if ia is None or ib is None or ia >= ib or (ia == -1 and ib == len(cs)):
                    continue
                out_v = []
                for c in cs[ia + 1:ib]:
                    mark = [x for x in by_cid.get(c, ()) if x.get('status') in DONE]
                    at = [m for x in mark if (m := _moment(x.get('delivered_at'))) is not None]
                    inside = not at or any(a.leave is not None and a.leave <= m <= b.arrive for m in at)
                    p = where.get(c) or next(((x['lat'], x['lon']) for x in mark
                                              if x.get('lat') is not None and x.get('lon') is not None), None)
                    if c not in anchored and mark and inside and p is not None:
                        out_v.append((c, p))
                return out_v
            return []
        legs = detour_legs(moving, anchors, pairs, road, truck, load, rules, day, out_at, until, lunch_span, target,
                           last.point if last is not None else None, vias)
    # итог дня (км, мин, ֏) — только кончившиеся участки: у идущего перепробег — прогноз (решение «тревога или нет»)
    over = [x for x in legs if not x.ongoing and x.excess_km >= rules.detour_min_km]

    deviations = []
    for k, run in enumerate(runs):   # идёт сейчас — последнее отклонение, и последняя свежая точка всё ещё вне линии
        ongoing = (fresh and k == len(runs) - 1 and run[-1].at == moving[-1].at and last is not None
                   and until is None
                   and off_route(last.point, index, rules.deviation_m, keep, straight))   # type: ignore[arg-type]
        # тревога — только при перепробеге своего участка не меньше detour_min_km (идущего — прогноз; участка нет — по
        # длине отклонения); меньше — «փոքր շեղում»: в журнале и на карте, не «сейчас» и не Telegram
        leg = leg_at(legs, run[0].at)
        km = _path_km(run)
        minor = (leg.judged_km if leg is not None else km) < rules.detour_min_km
        deviations.append(_alert('deviation', run[0].at, None if ongoing else run[-1].at, ongoing and not minor,
                                 km=round(km, 1), lat=round(run[0].lat, 6), lon=round(run[0].lon, 6), minor=minor,
                                 excess_km=round(leg.judged_km, 1) if leg is not None else None,
                                 projected=bool(leg is not None and leg.ongoing)))
    # порядок объезда: обслужена — касание; незакрытая (ожидающая или открытая водителем), у которой был кончившийся
    # GPS-визит не короче ac.MIN_DWELL после выезда её рейса (короткая разгрузка без отметки), — тоже «посещена»
    seq_touches = dict(touches)
    for x in stops:
        sid = x['stop_id']
        if x.get('status') in OPEN and sid not in seq_touches and trips[sid] in gone:
            brief = [v for v in visits.get(sid, ()) if v.arrive >= gone[trips[sid]] and v.leave - v.arrive >= ac.MIN_DWELL
                     and not (live and last is not None and v.leave >= last.at)]
            if brief:
                seq_touches[sid] = brief[0].arrive
    sequence, seq_summary = sequence_check(stops, trips, plan, seq_touches, nos, live) if plan else ([], None)
    apply_explanations(deviations + sequence, explained)
    off_km = math.fsum(_path_km(r) for r, a in zip(runs, deviations) if 'explained' not in a)
    adherence_pct, counted_km = adherence(moving, out_at, until, off_km) if index is not None else (None, 0.0)
    alerts = (speeds
              + stop_alerts(actual, day, rules, first_dep, end, last.at if last else None, open_now)
              + (contact_alerts(contacts, last_contact, now if open_now else None, started, end, rules)
                 if facts.get('device') is not None else [])   # старый APK (<2.2.0) шлёт пачками раз в 2–17 мин
              + gps_alerts(devices, open_now)
              + center_alerts(pts, rules, truck, fresh, stops)
              + deviations
              + sequence
              + [_alert('late', now, None, True, **x) for x in late])   # прогноз «не успеет» (№87) — пока он такой
    alerts.sort(key=lambda a: a['from'] or '')
    active = sorted({a['kind'] for a in alerts if a['active']})
    # начало идущей тревоги вида (последней из идущих): «Տեսա» страницы отмечает случай, а не вид; «не успеет» — без него
    # (её from — момент расчёта)
    since: dict[str, str] = {}
    for a in alerts:
        if a['active'] and a['kind'] != 'late' and a['from'] and a['from'] > since.get(a['kind'], ''):
            since[a['kind']] = a['from']

    if not pts and not contacts:
        state = 'nodata'
    elif finished:
        state = 'closed'
    elif any(k not in ('no_contact', 'late') for k in active):
        state = 'alert'   # другая активная тревога важнее «կապ չկա»; «не успеет» — прогноз, не событие машины: своя строка
    elif 'no_contact' in active or (open_now and last_contact is not None and now - last_contact > timedelta(
            minutes=rules.no_contact_min if facts.get('device') is not None else OLD_APK_SILENT_MIN)):
        state = 'offline'   # «կապ չկա»; тревогой — только у APK 2.2.0 и в пределах дня (contact_alerts)
    elif last is not None and age is not None and age <= STALE_S and last.spd is not None \
            and last.spd >= MOVING_MS:   # type: ignore[attr-defined]
        state = 'moving'
    else:
        state = 'standing'

    # груз по плану: рейс, который машина повезёт следующим (первый с точками после последнего уехавшего)
    ahead = sorted(k for k in set(trips.values()) if not gone or k > max(gone))
    planned_kg = (math.fsum(float(s.get('weight_kg') or 0.0) for s in stops if trips[s['stop_id']] == ahead[0])
                  if ahead else None)

    out: dict[str, Any] = {
        'position': ({'lat': round(last.lat, 6), 'lon': round(last.lon, 6), 'at': _iso(last.at),
                      'age_s': round(age) if age is not None else None,
                      'speed_kmh': round(last.spd * 3.6) if last.spd is not None else None,   # type: ignore[attr-defined]
                      'heading': round(brg) if isinstance(brg, (int, float)) else None,
                      'acc': round(last.accuracy) if last.accuracy is not None else None}
                     if last is not None else None),
        'state': state,
        'stores': {'done': done, 'total': len(stops),
                   'in_progress': sum(1 for s in stops if s.get('status') == 'in_progress'),
                   'gps_visited': len(gps), 'unmarked': len(unmarked)},
        'km': round(actual.km_gps, 1),
        'fuel_l': round(fuel, 1) if fuel is not None else None,
        'fuel_check': refuel,
        'load': {'remaining_kg': round(load.remaining_kg), 'loaded_kg': round(load.loaded_kg),
                 'delivered_kg': round(load.delivered_kg), 'unweighed_lines': load.unweighed,
                 'trip_kg': round(load.trip_kg), 'refused_kg': round(load.refused_kg),
                 'returns_kg': round(load.returns_aboard_kg), 'returns_unweighed': load.returns_unweighed,
                 'unloaded_kg': round(load.unloaded_kg),
                 'trips_gone': len(gone), 'trips': max(len(plan), 1 if stops else 0),
                 'planned_kg': round(planned_kg) if planned_kg is not None else None},
        'next': next_out,
        'forecast': forecast,
        'data_until': _iso(data_until),
        'return_eta': _iso(return_eta),
        'return_source': return_source,
        'device': dict(device) if isinstance(device, Mapping) else None,
        'offline_reason': device.get('exit') if state == 'offline' and isinstance(device, Mapping) else None,   # 'closed' | 'shutdown' (APK 2.2.5)
        'drivers': list(facts.get('drivers') or ()),
        'last_contact': _iso(last_contact),
        'contact_age_s': round((now - last_contact).total_seconds()) if last_contact is not None and live else None,
        'closed': finished,
        # «փոքր շեղում» — не тревога; since — с какого момента идёт активная тревога вида
        'alerts': {'active': active, 'count': sum(1 for a in alerts if not a.get('minor')), 'since': since},
        # идущие тревоги, которые диспетчер может объяснить («Բացատրել» в «Խնդիրներ հիմա»; журнала API флота не отдаёт)
        'explainable': [{'kind': a['kind'], 'from': a['from']} for a in alerts
                        if a['active'] and a['kind'] in LIVE_EXPLAIN_KINDS],
        'late': late,   # «не успеет» (№87, late_forecast): магазины и возврат на склад
        'alerts_log': alerts,   # журнал тревог дня: API флота его не отдаёт (views), Telegram и карточка машины — да
        # плановая линия (None — плана машине не отправляли) и отклонения от неё (None — линии по дорогам нет: не считали)
        'route': ({'road': route.geo.road, 'km': round(route.km, 1) if route.km is not None else None,
                   'trips': len(route.geo.trips), 'stops': len({c for c, _ in route.stops}),
                   'straight': len(route.geo.straight)} if route is not None else None),
        # count / km — все отклонения; alerts — тревоги (не փոքր, не объяснённые), minor, explained; следование плану —
        # adherence_pct (km — езда в счёте дня, off_km — отклонения без объяснённых)
        'deviation': ({'threshold_m': rules.deviation_m, 'count': len(runs),
                       'km': round(math.fsum(_path_km(r) for r in runs), 1),
                       'active': any(a['active'] for a in deviations),
                       'alerts': sum(1 for a in deviations if not a['minor'] and 'explained' not in a),
                       'minor': sum(1 for a in deviations if a['minor'] and 'explained' not in a),
                       'explained': sum(1 for a in deviations if 'explained' in a),
                       'detour_min_km': rules.detour_min_km,
                       'counted_km': round(counted_km, 1), 'off_km': round(off_km, 1),
                       'adherence_pct': round(adherence_pct, 1) if adherence_pct is not None else None}
                      if index is not None else None),
        # перепробег (только при плане): сумма по участкам с перепробегом не меньше detour_min_km
        'detour': ({'threshold_km': rules.detour_min_km, 'legs': len(legs), 'legs_over': len(over),
                    'excess_km': round(math.fsum(x.excess_km for x in over), 1),
                    'excess_min': round(math.fsum(max(0.0, x.excess_min) for x in over)),
                    'cost_amd': (round(math.fsum(x.cost_amd for x in over))   # type: ignore[misc]
                                 if all(x.cost_amd is not None for x in over) else None),
                    'approx': any(x.approx for x in over), 'fuel_price_estimated': rules.fuel_price_estimated}
                   if plan else None),
        'sequence': seq_summary,   # порядок объезда (None — плана нет): пропущенные сейчас и пары не по порядку
        'stats': {**day_stats(pts),
                  'adherence_pct': round(adherence_pct, 1) if adherence_pct is not None else None,
                  'overspeed': {'count': len(speeds), 'minutes': round(math.fsum(
                      (datetime.fromisoformat(a['to']) - datetime.fromisoformat(a['from'])).total_seconds()
                      for a in speeds) / 60)} if pts else None},
    }
    if detail and route is not None:
        out['route'].update({
            'lines': route.geo.shown(),
            'points': [{'customer_id': c, 'no': nos[c], 'lat': round(p[0], 6), 'lon': round(p[1], 6)}
                      for c, p in where.items()],
            # номера магазинов каждой линии рейса по порядку (подсказка участка плана); не сходится с линиями — пусто
            'trip_nos': ([[nos[c] for c in t if c in nos] for t in route.trip_stops]
                         if len(route.trip_stops) == len(route.geo.trips) else []),
            # индексы концов участков (склад, магазины по порядку, склад) в каждой из lines — 1:1 с ними
            'trip_cuts': route.geo.shown_cuts()})
    if detail and index is not None:
        out['deviation']['runs'] = [
            {'from': a['from'], 'to': a['to'], 'km': a['km'], 'active': a['active'], 'minor': a['minor'],
             'explained': a.get('explained'), 'excess_km': a['excess_km'],
             'line': [[round(p[0], 6), round(p[1], 6)] for p in ac.simplify([f.point for f in run], DEVIATION_LINE_POINTS)]}
            for a, run in zip(deviations, runs)]
    if detail and plan:
        times = [f.at for f in moving]

        def leg_line(x: Leg) -> list[list[float]]:
            got = moving[bisect.bisect_left(times, x.start):bisect.bisect_right(times, x.end)]
            return [[round(p[0], 6), round(p[1], 6)] for p in ac.simplify([f.point for f in got], LEG_LINE_POINTS)]
        out['detour']['items'] = [
            {'from': _leg_end(x.a, x.start, nos), 'to': _leg_end(x.b, None if x.ongoing else x.end, nos),
             'km': round(x.km, 1), 'plan_km': round(x.plan_km, 1), 'excess_km': round(x.excess_km, 1),
             'minutes': round(x.minutes), 'plan_min': round(x.plan_min), 'excess_min': round(x.excess_min),
             'cost_amd': round(x.cost_amd) if x.cost_amd is not None else None, 'approx': x.approx,
             'consecutive': x.consecutive, 'ongoing': x.ongoing, 'over': x.judged_km >= rules.detour_min_km,
             'projected_excess_km': round(x.judged_km, 1) if x.ongoing else None,
             'line': leg_line(x) if x.judged_km >= rules.detour_min_km else None}
            for x in legs]
    if detail:
        # линия трека (владелец 08.10): стоянка — одна точка, езда — без дрожания, по дорогам (track_line); момент точки
        # едет вместе с ней (track_t — 1:1 с track)
        parts = tl.chunks(pts, actual.stays)
        line = tl.line(parts, track_snap(parts) if track_snap is not None else {}, TRACK_LINE_POINTS)
        marks = {k: v for k, v in visited.items()}
        out.update({
            'track': [[round(p[0], 6), round(p[1], 6)] for p in line],
            'track_t': [round(p[2]) for p in line],
            # подсказка точки линии (track_v, track_km, track_dev_m); расстояние до плана — по своему индексу с ячейкой не
            # меньше HOVER_DEV_CELL_M (порог 100 м — ячейки 100 м: поиск до 5 км шёл бы по 10 тыс. ячеек на точку)
            **track_hover(line, parts, pts, moving,
                          geo.index(max(rules.deviation_m, HOVER_DEV_CELL_M)) if geo is not None and index is not None else None,
                          runs),
            'stops': [{'stop_id': s['stop_id'], 'customer_id': s.get('customer_id'), 'name': s.get('name'),
                       'lat': s.get('lat'), 'lon': s.get('lon'),
                       'status': s.get('status'), 'seq': s.get('seq'), 'trip': trips[s['stop_id']] + 1,
                       'weight_kg': round(float(s.get('weight_kg') or 0.0), 1),
                       'planned_eta': _iso(own.get(s['stop_id'])),
                       'eta': _iso(etas[s['stop_id']][0]) if s['stop_id'] in etas else None,
                       'eta_source': ('road' if etas[s['stop_id']][1] else 'model') if s['stop_id'] in etas else None,
                       'arrive': _iso(marks.get(s['stop_id'])), 'delivered_at': s.get('delivered_at'),
                       'gps': gps.get(s['stop_id']), 'unmarked': s['stop_id'] in unmarked,
                       'plan_no': nos.get(s.get('customer_id'))}   # type: ignore[arg-type]
                      for s in sorted(stops, key=lambda s: (trips[s['stop_id']],
                                                            s.get('seq') if isinstance(s.get('seq'), int) else 0))],
        })
    return out
