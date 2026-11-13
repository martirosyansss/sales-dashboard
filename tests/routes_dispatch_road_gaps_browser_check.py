"""Actual dispatch page: no straight planned line before, after a failed, or after a gapped road reply."""
import json,logging,tempfile,threading
from datetime import datetime
from pathlib import Path
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright
from routes_dispatch_browser_check import build_app,FakeClient,DAY,TILE_PNG

OUT=Path(__file__).resolve().parents[1]/'output'/'dispatch-road-gaps'

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    checks=[]
    with tempfile.TemporaryDirectory(prefix='dispatch-road-gaps-') as temporary:
        app=build_app(temporary,FakeClient())
        from route_optimizer import views
        views._clock=lambda:datetime(2026,10,1,8,0)
        state=app.extensions['route_optimizer']
        state.crew_days_loader=lambda *args:[]
        state.driver_list_loader=lambda *args:[]
        client=app.test_client()
        day=client.get('/api/routes/dispatch?date='+DAY).get_json()
        built=client.post('/api/routes/dispatch/build',json={'date':DAY,'rev':day['rev'],'trucks':['CAR1','CAR2']})
        assert built.status_code==200,built.get_json()
        server=make_server('127.0.0.1',8774,app,threaded=True)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True)
                for name,size in [('desktop',{'width':1440,'height':950}),('mobile',{'width':320,'height':568})]:
                    page=browser.new_page(viewport=size)
                    errors=[];held=[]
                    page.on('pageerror',lambda e:errors.append(str(e)))
                    page.add_init_script("""(() => {
                        (() => { const m = matchMedia.bind(window); window.matchMedia = q => m(q === '(min-width: 1101px)' ? '(min-width: 99999px)' : q); })();
                        let library; window.observedPolylines=[];
                        Object.defineProperty(window,'L',{configurable:true,get:()=>library,set:lib=>{
                            library=lib; const original=lib.polyline;
                            lib.polyline=function(points,options){
                                const line=original(points,options);
                                observedPolylines.push({line,initial:JSON.parse(JSON.stringify(points))}); return line;
                            };
                        }});
                    })();""")
                    for pattern in ('https://tiles.api-maps.yandex.ru/**','https://*.tile.openstreetmap.org/**'):
                        page.route(pattern,lambda r:r.fulfill(status=200,content_type='image/png',body=TILE_PNG))
                    page.route('**/api/routes/road-lines',lambda r:held.append(r))
                    page.goto(f'http://127.0.0.1:8774/routes/dispatch?date={DAY}')
                    page.wait_for_function('observedPolylines.length>0')
                    assert page.evaluate('observedPolylines.every(x=>x.initial.length===0)')
                    checks.append(name+': real dispatch renders no raw straight line while roads are pending')
                    page.wait_for_timeout(150)
                    assert held
                    for request in held[:]: request.abort()
                    held.clear()
                    page.wait_for_timeout(150)
                    assert page.evaluate('observedPolylines.every(x=>x.line.getLatLngs().length===0)')
                    checks.append(name+': failed road request retains only markers')
                    page.screenshot(path=str(OUT/(name+'.png')))
                    assert not errors,errors
                    page.close()
                browser.close()
        finally: server.shutdown();server.server_close()
    report={'passed':len(checks),'checks':checks,'production_browser_used':False,'production_data_written':False}
    (OUT/'results.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
