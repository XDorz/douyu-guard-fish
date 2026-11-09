"""手动集成检查：CHROME_PATH=/path/to/chrome python tests/browser_smoke.py。

仅访问本机 HTTP 测试页，不访问账号或游戏服务器。
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot
from api_monitor import ApiMonitor
from browser_compat import pick_driver, evaluate_main, fix_headless_ua

FIXTURE = b'''<html><body><div class="GameHUD-module_container" style="height:100px">battle</div>
<button class="challengeButton">challenge</button>
<div class="Starter-module_skillsPanel">
<button id="paid" class="Starter-module_btn-xxx">refresh</button>
<button id="auto" class="Starter-module_btn-xxx Starter-module_auto-yyy">random</button></div>
<script>
window.trustedClicks=0;window.randomClicks=0;window.paidClicks=0;
document.querySelector('.challengeButton').onclick=e=>{if(e.isTrusted)window.trustedClicks++};
document.querySelector('#auto').onclick=()=>window.randomClicks++;
document.querySelector('#paid').onclick=()=>window.paidClicks++;
class Loop {
 constructor(){this.coreHp=20;this.running=true;this.config={battle:{baseCoreHp:20}};
  this.enemies=[];this.towers=[];this._nextProjectileId=1;
  this.callbacks={onCoreHpChanged:()=>{}};}
 _dealDamage(enemy, damage){enemy.currentHp-=damage;return damage;}
 _checkEnemiesReachedCore(){this.coreHp--;}
 _killEnemy(){}
}
window.loop=new Loop();window.originalDamage=loop._dealDamage;window.originalCheck=loop._checkEnemiesReachedCore;
document.querySelector('.GameHUD-module_container').__reactFiber$local={
 memoizedState:{memoizedState:{current:{loop}}},type:{name:'LocalEngine'}};
</script></body></html>'''
paused, manual = threading.Event(), threading.Event()
results, holder = [], {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(FIXTURE)

    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        if self.path.endswith('/manualOperation'):
            manual.set()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"error":0,"data":{}}')


def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            template = json.loads(Path(bot.__file__).with_name('api_baseline.json').read_text())
            template['hosts'] = ['127.0.0.1']
            baseline = Path(tmp) / 'api_baseline.json'
            baseline.write_text(json.dumps(template))

            def worker():
                try:
                    name, factory, _ = pick_driver()
                    with factory() as pw, patch('bot.DATA_DIR', tmp), patch('bot.LOGDIR', tmp):
                        ctx = pw.chromium.launch_persistent_context(
                            str(Path(tmp) / 'profile'), executable_path=os.environ['CHROME_PATH'],
                            headless=True, args=['--no-sandbox'])
                        try:
                            fix_headless_ua(ctx)
                            page = ctx.pages[0]
                            b = bot.GuardFishBot(rounds=9, one_hit=True, one_hit_mode='multiplier',
                                                 attack_multiplier=3, hp_guard=True)
                            holder['bot'] = b
                            b.rounds_done, b._battle_active, b._page = 3, True, page
                            b._log = lambda *a: paused.set() if b._paused else None
                            b._api_write = lambda *a: None
                            b._api_monitor = ApiMonitor(baseline)
                            page.on('request', b._on_request)
                            page.on('response', b._on_response)
                            page.goto(f'http://127.0.0.1:{server.server_port}')
                            assert 'HeadlessChrome' not in page.evaluate('() => navigator.userAgent')
                            assert b._click_challenge()['ok']
                            assert evaluate_main(page, '() => window.trustedClicks') == 1
                            evaluate_main(page, "() => document.querySelector('.challengeButton').innerText='10鱼翅'")
                            assert not b._click_challenge()['ok']
                            assert b._click(bot.JS_CLICK_RANDOM_SKILL)['ok']
                            assert evaluate_main(page, '() => [window.randomClicks,window.paidClicks]') == [1, 0]
                            b._apply_runtime_controls(force=True)
                            b._network.observe('game/stageStart', {'error': 0, 'data': {
                                'stageInfo': {'curStage': 121, 'curHp': 20}}})
                            assert b._state()['wm']['stage']['curStage'] == 121
                            assert evaluate_main(page, '() => window.__GF.observedStage') == 121
                            page.wait_for_timeout(400)
                            assert evaluate_main(page, '() => loop._dealDamage({currentHp:1000},10)') == 30
                            evaluate_main(page, '() => loop.coreHp=2')
                            page.wait_for_timeout(300)
                            assert evaluate_main(page, '() => loop.coreHp') == 20
                            b.set_runtime_controls(one_hit_mode='gentle', hp_guard=False)
                            b._apply_runtime_controls()
                            page.wait_for_timeout(300)
                            assert evaluate_main(page, '() => loop._dealDamage({currentHp:1000},10)') >= 550
                            b._api_monitor.arm('战斗')
                            b._click('''async () => {
                                setTimeout(()=>fetch('/japi/revenuenc/web/actgame/game/manualOperation',
                                    {method:'POST',body:'manual=1'}),500);
                                await fetch('/japi/revenuenc/web/actgame/game/stageEnd',
                                    {method:'POST',body:'stat='+encodeURIComponent(JSON.stringify({newCounter:7}))});
                            }''')
                            restored = evaluate_main(page, '''() => !window.__GF &&
                                loop._dealDamage === originalDamage &&
                                loop._checkEnemiesReachedCore === originalCheck''')
                            b._api_observe_state({'inBattle': True})
                            evaluate_main(page, """() => fetch('/japi/revenuenc/web/actgame/game/stageEnd',
                                {method:'POST',body:'stat='+encodeURIComponent(JSON.stringify({
                                    cardDmg:{'999':200},totalDmg:500,skillDmg:0,invalidDmg:300}))})""")
                            assert restored and b.rounds_done == 3
                            assert len(list(Path(tmp).glob('api_change_*.json'))) == 1
                            assert b._api_monitor.pending is None
                            page.wait_for_timeout(200)
                            assert b._network.values['respCount'] >= 2, b._network.values
                            results.append({'driver': name, 'runtime_restored': restored,
                                            'rounds_preserved': b.rounds_done, 'trusted_click': True,
                                            'paid_refresh_untouched': True, 'api_pause_and_resume': True})
                        finally:
                            ctx.close()
                except BaseException as exc:
                    results.append({'error': repr(exc), 'traceback': traceback.format_exc()})
                    paused.set()

            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            try:
                assert paused.wait(20), 'did not pause'
                assert not results, results
                assert manual.wait(5), 'browser timers stopped during pause'
                assert holder['bot'].request_resume()
                thread.join(15)
                assert not thread.is_alive(), 'worker stuck'
                assert results and 'error' not in results[0], results
                print(json.dumps(results, ensure_ascii=False))
            finally:
                if 'bot' in holder:
                    holder['bot'].request_stop()
                thread.join(5)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
