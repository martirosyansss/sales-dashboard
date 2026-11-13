"""Real Leaflet/Chromium check of common planned roads, independent of the user's browser and ERP."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from routes_basemap_browser_check import serve, PORT

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output'/'main-roads-browser'
HTML='''<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css">
<style>html,body,#map{height:100%;width:100%;margin:0}</style></head><body><div id="map"></div>
<script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="/js/routes_basemap.js?v=7"></script><script>
window.map=L.map('map').setView([40.195,44.505],13);
window.group=L.layerGroup().addTo(map);
window.raw=[[40.2,44.5],[40.18,44.51]];
window.road=[[40.2,44.5],[40.19,44.5],[40.18,44.51]];
</script></body></html>'''

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    server=serve()
    checks=[]
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            for name,size in [('desktop',{'width':1440,'height':900}),('mobile',{'width':320,'height':568})]:
                page=browser.new_page(viewport=size)
                errors=[]; requests=[]; held=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.route(f'http://127.0.0.1:{PORT}/__main-roads.html',lambda r:r.fulfill(status=200,content_type='text/html',body=HTML))
                def route(r):
                    data=r.request.post_data_json
                    requests.append(data)
                    lat=data['lines'][0][0][0]
                    if lat==40.21: held.append(r);return
                    if lat==40.22: r.abort();return
                    if lat==40.23:
                        r.fulfill(status=200,content_type='application/json',body=json.dumps({'success':True,'lines':[[]],
                            'segments':[[[[40.23,44.5],[40.231,44.501]],[[40.235,44.506],[40.24,44.51]]]],'line_status':['partial']}));return
                    if lat==40.24:
                        r.fulfill(status=200,content_type='application/json',body=json.dumps({'success':True,'lines':[[]],
                            'segments':[[]],'line_status':['unavailable']}));return
                    r.fulfill(status=200,content_type='application/json',body=json.dumps({'success':True,'routing_policy':'main_roads_v1',
                        'lines':[[[40.2,44.5],[40.19,44.5],[40.18,44.51]]]}))
                page.route('**/api/routes/road-lines',route)
                page.route('**/api/routes/garage/road-lines',route)
                page.goto(f'http://127.0.0.1:{PORT}/__main-roads.html')
                page.wait_for_load_state('networkidle')
                page.evaluate("""() => {
                    window.halo=RoutesBasemap.roadLine(raw,{color:'#fff',weight:9},group);
                    window.line=RoutesBasemap.roadLine(raw,{color:'#147652',weight:5},group);
                }""")
                page.wait_for_function('line.getLatLngs().length===3')
                assert len(requests)==1
                assert page.evaluate('line.getLatLngs()[1].lat')==40.19
                assert page.evaluate('halo.getLatLngs().length')==3
                page.screenshot(path=str(OUT/(name+'.png')))
                checks.append(name+': actual road geometry, casing and request deduplication')
                page.evaluate("""() => { window.center=RoutesBasemap.roadLine(raw,{color:'blue'},group,true); }""")
                page.wait_for_function('center.getLatLngs().length===3')
                assert requests[-1]['avoid_center'] is True
                checks.append(name+': center bypass option retained')
                page.evaluate("""() => { window.garage=RoutesBasemap.roadLine(raw,{},group,true,'/api/routes/garage/road-lines'); }""")
                page.wait_for_function('garage.getLatLngs().length===3')
                assert len(requests)==3 and requests[-1]['avoid_center'] is True
                checks.append(name+': garage uses its permitted endpoint and separate request cache')
                page.evaluate("""() => { window.stale=RoutesBasemap.roadLine([[40.21,44.5],[40.18,44.51]],{},group); }""")
                page.wait_for_timeout(100)
                assert len(held)==1
                page.evaluate('group.clearLayers()')
                held[0].fulfill(status=200,content_type='application/json',body=json.dumps({'success':True,'lines':[[[40.21,44.5],[40.18,44.51]]]}))
                page.wait_for_timeout(100)
                assert page.evaluate('group.getLayers().length')==0
                assert page.evaluate('stale.getLatLngs().length')==0
                checks.append(name+': delayed response cannot restore removed route')
                page.evaluate("""() => { window.failed=RoutesBasemap.roadLine([[40.22,44.5],[40.18,44.51]],{},group); }""")
                page.wait_for_timeout(200)
                assert page.evaluate('failed.getLatLngs().length')==0
                assert not errors,errors
                checks.append(name+': network failure draws no invented shortcut; no JavaScript errors')
                page.evaluate("""() => { window.partial=RoutesBasemap.roadLine([[40.23,44.5],[40.24,44.51]],{},group); }""")
                page.wait_for_function('partial.getLatLngs().length===2')
                assert page.evaluate('partial.getLatLngs().every(part=>part.length===2)')
                assert page.evaluate('partial.getLatLngs()[0][1].lat===40.231 && partial.getLatLngs()[1][0].lat===40.235')
                checks.append(name+': confirmed parts stay separate across an unknown road gap')
                page.evaluate("""() => { window.absent=RoutesBasemap.roadLine([[40.24,44.5],[40.25,44.51]],{},group); }""")
                page.wait_for_timeout(150)
                assert page.evaluate('absent.getLatLngs().length')==0
                assert not errors,errors
                checks.append(name+': explicit unavailable-road response draws no straight line')
                page.close()
            browser.close()
    finally:
        server.shutdown();server.server_close()
    result={'passed':len(checks),'checks':checks,'production_browser_used':False}
    (OUT/'results.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))

if __name__=='__main__': main()
