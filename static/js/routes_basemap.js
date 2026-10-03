/* Подложка всех карт раздела «Маршруты» (решение владельца №47): Яндекс Карты через Tiles API —
   бесплатно, в том числе для закрытых систем, свои данные поверх можно (п. 6.5.1 https://yandex.ru/legal/maps_api/).
   Ключ сервер кладёт в data-yandex-key этого <script> (ROUTES_YANDEX_TILES_KEY). Без ключа, или если Яндекс
   не отдаёт ни одной плитки (неверный ключ), — OpenStreetMap, как было.
   Условия Яндекса: логотип в углу карты без своих отступов, ссылка на Яндекс Карты; до 30 запросов/с на ключ;
   плитки не изменять (п. 5.1.2) — тёмный фильтр routes.css действует только на слой OpenStreetMap (rt-tiles-osm).
   Проверка в браузере: tests/routes_basemap_browser_check.py. */
(function () {
    'use strict';

    const script = document.currentScript;
    const KEY = ((script && script.dataset.yandexKey) || '').trim();
    const LOGO_URL = script ? new URL('../img/yandex_maps_logo_ru.svg', script.src).href : '';
    // целое: повтор пишет масштаб как 2.0, 2.00 — у дробного (1.5) первый повтор совпал бы с исходным адресом
    const SCALE = window.devicePixelRatio > 1 ? 2 : 1;   // чёткая карта на экранах высокой плотности
    const RETRIES = 2;      // плитка с ошибкой (429 при всплеске запросов) — ещё 2 попытки, через ~1 и ~2 с
    const GIVE_UP = 3;      // столько плиток не загрузились совсем, а ни одна не загрузилась — ключ не работает
    // на странице бывает несколько карт («Развоз»), ключ у них общий — и счёт общий
    const yandexMaps = [];  // { map, layer, logo }
    let loaded = 0;
    let failed = 0;
    let broken = false;

    function addOsm(map) {
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            subdomains: 'abc', maxZoom: 19, className: 'rt-tiles-osm',
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>',
        }).addTo(map);
    }

    function yandexLogo() {
        const control = L.control({ position: 'bottomleft' });
        control.onAdd = () => {
            const a = L.DomUtil.create('a', 'rt-yandex-logo');
            a.href = 'https://yandex.ru/maps/';
            a.target = '_blank';
            a.rel = 'noopener';
            a.title = 'Яндекс Карты';
            a.style.margin = '0';           // отступы уже внутри картинки — свои добавлять нельзя
            a.style.lineHeight = '0';
            const img = L.DomUtil.create('img', '', a);
            img.src = LOGO_URL;
            img.alt = 'Яндекс';
            img.width = 88;
            img.height = 48;
            L.DomEvent.disableClickPropagation(a);
            return a;
        };
        return control;
    }

    /** Ключ не работает: все карты страницы — на OpenStreetMap, новые карты — сразу туда. */
    function giveUp() {
        broken = true;
        for (const { map, layer, logo } of yandexMaps) {
            if (!map.hasLayer(layer)) continue;         // карту уже убрали со страницы (map.remove())
            map.removeLayer(layer);
            map.removeControl(logo);
            addOsm(map);
        }
        yandexMaps.length = 0;
        console.warn('Яндекс Карты не отдают плитки (проверьте ROUTES_YANDEX_TILES_KEY) — подложка OpenStreetMap');
    }

    function addYandex(map) {
        const layer = L.tileLayer('https://tiles.api-maps.yandex.ru/v1/tiles/?x={x}&y={y}&z={z}'
            + '&lang=ru_RU&l=map&projection=web_mercator&scale={ymScale}&apikey={ymKey}', {
            maxZoom: 19,
            updateWhenIdle: true,           // при перетаскивании не грузить промежуточные плитки — меньше запросов
            ymScale: SCALE,
            ymKey: encodeURIComponent(KEY),
            attribution: '&copy; <a href="https://yandex.ru/legal/maps_termsofuse/" target="_blank" rel="noopener">Яндекс</a>',
        });
        const logo = yandexLogo();
        layer.on('tileload', () => { loaded += 1; });
        layer.on('tileerror', (e) => {
            const tile = e.tile;
            const attempt = (tile.rtRetry || 0) + 1;
            if (attempt <= RETRIES) {
                tile.rtRetry = attempt;
                // повторяем ровно ту плитку, что не загрузилась (зум карты к этому времени мог смениться);
                // тот же адрес браузер не запрашивает заново (берёт ошибку из кеша) — масштаб пишем иначе: 2.0, 2.00
                const first = tile.rtSrc || (tile.rtSrc = tile.getAttribute('src'));
                const url = first.replace(`&scale=${SCALE}&`, `&scale=${SCALE.toFixed(attempt)}&`);
                setTimeout(() => { if (tile.isConnected) tile.src = url; }, 1000 * attempt + Math.random() * 500);
                return;
            }
            failed += 1;
            if (!broken && loaded === 0 && failed >= GIVE_UP) giveUp();
        });
        layer.addTo(map);
        logo.addTo(map);
        yandexMaps.push({ map, layer, logo });
    }

    /** Положить подложку на карту Leaflet: Яндекс, если сервер дал ключ, иначе OpenStreetMap. */
    function add(map) {
        if (KEY && !broken) addYandex(map);
        else addOsm(map);
    }

    window.RoutesBasemap = { add };
})();
