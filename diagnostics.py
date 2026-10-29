"""可回传的诊断文件。采集器不调用游戏方法、不安装计时器、不更改对象。"""
import datetime
import json
import os
import platform
import re
import sys


_PRIVATE = re.compile(r"cookie|token|auth|secret|password|user|nickname|account|session|uid|rid|url|header|storage", re.I)


def safe_api_data(value, depth=0):
    """只采集接口结构和数值，省略玩家身份、长文本、请求参数和认证信息。"""
    if depth > 5:
        return "[depth limit]"
    if isinstance(value, dict):
        return {str(k): "[redacted]" if _PRIVATE.search(str(k)) else safe_api_data(v, depth + 1)
                for k, v in list(value.items())[:100]}
    if isinstance(value, list):
        return {"length": len(value), "sample": [safe_api_data(v, depth + 1) for v in value[:10]]}
    if isinstance(value, str):
        return "[text:%d]" % len(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return "[unsupported]"


class DebugRecorder:
    def __init__(self, directory, version):
        os.makedirs(directory, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.path = os.path.join(directory, "debug_%s.jsonl" % stamp)
        self._file = open(self.path, "x", encoding="utf-8")
        self.write("session", version=version, python=sys.version.split()[0],
                   platform=platform.system(), schema_version=1)

    def write(self, kind, **data):
        self._file.write(json.dumps({"at": datetime.datetime.now().isoformat(timespec="milliseconds"),
                                     "kind": kind, **data}, ensure_ascii=False) + "\n")
        self._file.flush()

    def close(self):
        self._file.close()


JS_ENGINE_SNAPSHOT = r"""
(options) => {
  const full = !!(options && options.sources);
  const started = performance.now();
  const own = (o, k) => {
    if (!o || (typeof o !== 'object' && typeof o !== 'function')) return undefined;
    try { const d = Object.getOwnPropertyDescriptor(o, k); return d && 'value' in d ? d.value : undefined; }
    catch (_) { return undefined; }
  };
  const target = o => {
    if (!o || typeof o !== 'object') return null;
    const config = own(o, 'config');
    if (typeof own(o, 'coreHp') === 'number' && config && own(config, 'battle'))
      return {loop: o, manager: null};
    const loop = own(o, 'loop');
    const lc = own(loop, 'config');
    if (loop && typeof own(loop, 'coreHp') === 'number' && lc && own(lc, 'battle'))
      return {loop, manager: o};
    return null;
  };
  let found = null, via = '', fibers = 0;
  const gf = own(window, '__GF');
  if (gf) {
    found = target(own(own(gf, 'mgrRef'), 'current')) || target(own(gf, 'loopRef'));
    if (found) via = '__GF';
  }
  if (!found) {
    const queue = [];
    const hook = own(window, '__REACT_DEVTOOLS_GLOBAL_HOOK__');
    const roots = own(hook, '__roots');
    if (roots) for (const k of Object.keys(roots)) {
      const f = own(own(roots, k), 'current');
      if (f) queue.push(f);
    }
    if (!queue.length) {
      const el = document.querySelector('[class*="GameHUD-module_container"], [class*="CardLanes"], canvas');
      if (el) {
        const key = Object.keys(el).find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
        let f = own(el, key), hops = 0;
        while (f && own(f, 'return') && hops++ < 200) f = own(f, 'return');
        if (f) queue.push(f);
      }
    }
    const seen = new Set();
    for (let i = 0; i < queue.length && fibers < 12000; i++) {
      if (i % 128 === 0 && performance.now() - started > 30) break;
      const f = queue[i];
      if (!f || seen.has(f)) continue;
      seen.add(f); fibers++;
      let h = own(f, 'memoizedState');
      for (let n = 0; h && n < 300; n++, h = own(h, 'next')) {
        found = target(own(own(h, 'memoizedState'), 'current'));
        if (found) break;
      }
      if (found) { via = 'react-fiber'; break; }
      const child = own(f, 'child'), sibling = own(f, 'sibling');
      if (child) queue.push(child);
      if (sibling) queue.push(sibling);
    }
  }
  const out = {found: !!found, via, fibers, pageTime: Date.now(), performanceTime: performance.now()};
  if (!found) return out;

  const privateKey = /cookie|token|auth|secret|password|user|nickname|account|session|uid|rid|url|header|storage/i;
  const heavyKey = /texture|sprite|renderer|container|parent|children|canvas|fiber|memoized/i;
  const interesting = /attack|shoot|fire|effect|skill|cool|damage|crit|proc|trigger|tick|update|step|speed|spawn|wave|enem|core|start|pause|resume|loop|advance|time|hit|Original/i;
  let nodes = 0, sourceChars = 0;
  const sourceLimit = 160000;
  const ctorName = o => {
    try { const p = Object.getPrototypeOf(o); const c = own(p, 'constructor'); return typeof c === 'function' ? c.name : ''; }
    catch (_) { return ''; }
  };
  const sample = (o, depth, seen) => {
    if (o === null || typeof o === 'boolean') return o;
    if (typeof o === 'number') return Number.isFinite(o) ? o : String(o);
    if (typeof o === 'string') return o.slice(0, 180);
    if (typeof o === 'function') return '[function]';
    if (typeof o !== 'object') return '[' + typeof o + ']';
    if (seen.has(o)) return '[cycle]';
    if (nodes++ > 5000 || performance.now() - started > 100) return '[budget limit]';
    if (depth <= 0) return {type: ctorName(o), keys: Object.keys(o).slice(0, 80)};
    seen.add(o);
    let r;
    if (Array.isArray(o)) {
      r = {type: 'Array', length: o.length, sample: []};
      for (let i = 0; i < Math.min(o.length, 10); i++) r.sample.push(sample(own(o, String(i)), depth - 1, seen));
    } else if (o instanceof Map) {
      r = {type: 'Map', size: o.size, sample: []};
      for (const [k, v] of Map.prototype.entries.call(o)) {
        if (r.sample.length >= 10) break;
        r.sample.push(privateKey.test(String(k)) ? '[redacted]' :
          [String(k).slice(0, 80), sample(v, depth - 1, seen)]);
      }
    } else {
      r = {type: ctorName(o), fields: {}};
      const keys = Object.keys(o);
      // 优先时间/卡牌/特效字段，避免画面对象把采集预算用光。
      keys.sort((a, b) => Number(interesting.test(b)) - Number(interesting.test(a)));
      for (const key of keys.slice(0, 120)) {
        if (privateKey.test(key)) { r.fields[key] = '[redacted]'; continue; }
        if (heavyKey.test(key)) { r.fields[key] = '[render object omitted]'; continue; }
        const d = Object.getOwnPropertyDescriptor(o, key);
        r.fields[key] = d && 'value' in d ? sample(d.value, depth - 1, seen) : '[accessor not invoked]';
      }
    }
    seen.delete(o);
    return r;
  };
  const describe = o => {
    if (!o) return null;
    const methods = {};
    let p = o;
    for (let level = 0; p && p !== Object.prototype && level < 5; level++, p = Object.getPrototypeOf(p)) {
      for (const key of Object.getOwnPropertyNames(p).slice(0, 200)) {
        if (key === 'constructor' || key in methods || privateKey.test(key)) continue;
        const d = Object.getOwnPropertyDescriptor(p, key);
        if (!d || typeof d.value !== 'function') continue;
        if (full && interesting.test(key) && sourceChars < sourceLimit) {
          const src = Function.prototype.toString.call(d.value);
          const limit = Math.min(12000, sourceLimit - sourceChars);
          methods[key] = {source: src.slice(0, limit), truncated: src.length > limit};
          sourceChars += Math.min(src.length, limit);
        } else methods[key] = '[function]';
      }
    }
    return {type: ctorName(o), methods, state: sample(o, full ? 5 : 3, new Set())};
  };
  out.loop = describe(found.loop);
  out.manager = describe(found.manager);
  out.collections = {};
  for (const [label, obj] of [['loop', found.loop], ['manager', found.manager]]) {
    if (!obj) continue;
    for (const key of Object.keys(obj).filter(k => /tower|card|weapon|effect|skill|enem|ticker/i.test(k) && !privateKey.test(k)).slice(0, 15)) {
      const value = own(obj, key);
      if (Array.isArray(value)) out.collections[label + '.' + key] = value.slice(0, 3).map(describe);
      else if (value instanceof Map) {
        const items = [];
        for (const item of Map.prototype.values.call(value)) { items.push(describe(item)); if (items.length >= 3) break; }
        out.collections[label + '.' + key] = items;
      }
      else if (value && typeof value === 'object') out.collections[label + '.' + key] = describe(value);
    }
  }
  out.sourceChars = sourceChars;
  out.captureMs = performance.now() - started;
  return out;
}
"""
