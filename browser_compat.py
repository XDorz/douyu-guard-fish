"""上游浏览器适配：延迟加载驱动，明确区分主世界与隔离世界。"""
import base64
import inspect
from functools import lru_cache
from urllib.parse import urlsplit


GIF_1PX = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")


def pick_driver():
    try:
        from patchright.sync_api import sync_playwright, TimeoutError
        return "patchright", sync_playwright, TimeoutError
    except ImportError:
        from playwright.sync_api import sync_playwright, TimeoutError
        return "playwright", sync_playwright, TimeoutError


@lru_cache(maxsize=8)
def _main_world_keyword(page_type):
    if page_type.__module__.split('.')[0] != "patchright":
        return None
    parameters = inspect.signature(page_type.evaluate).parameters
    if 'isolated_context' in parameters:
        return 'isolated_context'
    if 'isolatedContext' in parameters:
        return 'isolatedContext'
    raise RuntimeError('Patchright 不支持指定主世界，请安装 requirements.txt 中的版本')


def evaluate_main(page, script, *args):
    # 显式选主世界：引擎、React fiber、实时开关和撤销操作必须在同一世界。
    # 不用捕获 TypeError 后重试，避免脚本已执行却因异常又执行一次。
    keyword = _main_world_keyword(type(page))
    if keyword:
        return page.evaluate(script, *args, **{keyword: False})
    return page.evaluate(script, *args)


def fix_headless_ua(ctx):
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    data = page.evaluate("""async () => ({ua: navigator.userAgent,
        platform: navigator.platform,
        metadata: navigator.userAgentData ? await navigator.userAgentData.getHighEntropyValues(
            ['architecture', 'bitness', 'model', 'platformVersion', 'fullVersionList', 'wow64']) : null})""")
    original = data['ua']
    if 'HeadlessChrome' not in original:
        return False
    # 保留实际平台、版本和硬件信息，不能把 Linux/Edge 硬编码成 Windows Chrome。
    params = {'userAgent': original.replace('HeadlessChrome', 'Chrome'),
              'platform': data['platform']}
    metadata = data.get('metadata')
    if metadata and metadata.get('brands'):
        params['userAgentMetadata'] = metadata
    session = ctx.new_cdp_session(page)
    try:
        session.send('Emulation.setUserAgentOverride', params)
    except Exception:
        session.detach()
        raise
    # Chromium 在会话 detach 后会撤销 Emulation 覆盖；保持到 context 关闭。
    ctx._guard_fish_ua_session = session
    return True


def is_stats_beacon(url):
    parsed = urlsplit(url)
    return parsed.hostname == 'dotserver.douyucdn.cn' and parsed.path == '/fish3/1.gif'


class NetworkState:
    """从既有响应监听获取状态，不再包装页面的 fetch / XMLHttpRequest。"""
    def __init__(self):
        self.values = {'respCount': 0, 'panelReady': False}

    def observe(self, api, payload):
        if not isinstance(payload, dict):
            return
        values = self.values
        values['respCount'] += 1
        if api == 'basics/panelInfo':
            values['panelCode'] = payload.get('code', payload.get('error', 0))
            values['panelReady'] = values['panelCode'] in (None, 0, '0')
        if any(payload.get(k) not in (None, 0, '0') for k in ('code', 'error')):
            return
        data = payload.get('data')
        if not isinstance(data, dict):
            return
        for group, fields in (
            ('userInfo', {'nn': 'nick', 'freeNum': 'freeNum',
                          'maxStage': 'maxStage', 'battleRound': 'battleRound'}),
            ('siteUserInfo', {'coin': 'coin', 'siteWeekRank': 'weekRank'}),
        ):
            source = data.get(group) or {}
            if isinstance(source, dict):
                for key, dest in fields.items():
                    if source.get(key) is not None:
                        values[dest] = source[key]
        for key in ('battleInfo', 'stageInfo'):
            stage = data.get(key)
            if isinstance(stage, dict) and stage.get('curStage') is not None:
                values['stage'] = {'curStage': stage['curStage'],
                                   'curHp': stage.get('curHp', stage.get('baseHp'))}
        if api == 'game/battleStart' or data.get('status') == 10:
            values['battleOver'] = False
        elif data.get('status') == 20:
            values['battleOver'] = True

    def merge(self, state):
        if state is not None:
            state['wm'] = dict(state.get('wm') or {}, **self.values)
        return state
