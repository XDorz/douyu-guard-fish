# -*- coding: utf-8 -*-
"""
保卫鱼娘 · 自动挂机助手（核心）
=================================
斗鱼直播间互动小游戏「保卫鱼娘」的自动化工具：
  自动开局 → 自动选技能（含跳过BOSS关的前置三选一）→ 自动领周宝箱 → 自动领结算宝箱 → 多场循环

原则（用户红线）：
  - 只做免费操作；任何涉及鱼翅/礼物（绝技/加速/技能刷新/银币兑换/充值）一律不自动点击
  - 仅在持续无战斗活动的看门狗超时后刷新恢复；未确认结算不计场

数据抓包：
  - logs/api_<date>.jsonl     所有 /actgame/** 接口原始响应
  - logs/battles_<date>.jsonl 每场战报（起始关/结束关/用时/宝箱/技能选择）
"""

import os
import re
import sys
import json
import time
import random
import subprocess
import tempfile
import threading
import traceback
import datetime
from stage_timing import StageTimer, api_stage
from diagnostics import DebugRecorder, JS_ENGINE_SNAPSHOT, safe_api_data
from combat_controls import (JS_COMBAT_INSTALL, JS_COMBAT_SNAPSHOT, KillStats, ActivityWatch,
                             default_controls, normalize_controls, EFFECTS, EFFECT_LABELS)

APP_NAME = "保卫鱼娘挂机助手"
try:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION"), encoding="utf-8") as _vf:
        APP_VERSION = _vf.read().strip()
except OSError:
    APP_VERSION = "dev"
DEFAULT_RID = "12306"
GAME_URL = "https://www.douyu.com/pages/vibe-lab-act202606-game/mine?rid={rid}&isAnchorSide=0"
HOME_URL = "https://www.douyu.com/"

MAX_BATTLE_SECONDS = 13 * 60      # 默认单场监控时限，GUI / CLI 可调整
END_GRACE_SECONDS = 3 * 60         # 请求自然结束后等待结算的宽限时间（场上可能暂时没有接近基地的敌人）
TICK = 0.5                         # 状态轮询间隔
IDLE_STUCK_LIMIT = 90              # 空闲态卡住多久后尝试恢复（刷新页面）
PRE_BATTLE_LIMIT = 90              # 点挑战后"无进展"超时（每有进展会重新计时）
PRE_BATTLE_HARD_LIMIT = 300        # 点挑战后绝对上限（防止面板反复刷而无限等）


def log_msg(*a):
    s = " ".join(str(x) for x in a)
    try:
        if sys.stdout is not None:
            print(s, flush=True)
    except Exception:
        pass


# ---------------------------------------------------------------- 路径
IS_FROZEN = getattr(sys, "frozen", False)
if IS_FROZEN:
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _writable(d):
    try:
        t = os.path.join(d, ".wtest")
        with open(t, "w", encoding="utf-8") as f:
            f.write("x")
        os.remove(t)
        return True
    except Exception:
        return False


if IS_FROZEN and not _writable(APP_DIR):
    DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", APP_DIR), "GuardFishBot")
else:
    DATA_DIR = APP_DIR

PROFILE = os.path.join(DATA_DIR, "chrome_profile")
LOGDIR = os.path.join(DATA_DIR, "logs")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")


def ensure_dirs():
    try:
        os.makedirs(LOGDIR, exist_ok=True)
    except Exception:
        pass
    try:
        os.makedirs(PROFILE, exist_ok=True)
    except Exception:
        pass


# ---------------------------------------------------------------- Chrome 发现
def _reg_app_path(name):
    try:
        import winreg
    except Exception:
        return None
    subs = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\%s" % name,)
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for sub in subs:
            try:
                with winreg.OpenKey(hive, sub) as k:
                    v = winreg.QueryValue(k, None)
                    if v and os.path.isfile(v):
                        return v
            except Exception:
                pass
    return None


def portable_marks(exe):
    """识别 Chrome++ / 绿色版特征（官方 Chrome / Edge 都不带）"""
    d = os.path.dirname(exe)
    m = []
    try:
        if os.path.isfile(os.path.join(d, "version.dll")):
            m.append("version.dll(Chrome++)")
        if os.path.isfile(os.path.join(d, "chrome++.ini")):
            m.append("chrome++.ini")
        parent = os.path.dirname(d)
        if os.path.isdir(os.path.join(parent, "Data")):
            m.append("../Data")
    except Exception:
        pass
    return m


def chrome_candidates():
    """返回 [(exe, rank, name)]，rank 越小越优先：0=官方Chrome 1=Edge 2=便携改装版"""
    found = {}

    def add(p):
        if p and os.path.isfile(p):
            a = os.path.abspath(p).lower()
            if a not in found:
                found[a] = p

    add(os.environ.get("CHROME_PATH"))
    add(_reg_app_path("chrome.exe"))
    add(_reg_app_path("msedge.exe"))
    pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    pf86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    lad = os.environ.get("LOCALAPPDATA", "")
    for base in (pf, pf86, lad):
        if not base:
            continue
        add(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
        add(os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"))
    try:
        import shutil
        for n in ("chrome.exe", "msedge.exe"):
            w = shutil.which(n)
            add(w)
    except Exception:
        pass

    prio = os.environ.get("CHROME_PATH")
    lst = []
    for a, p in found.items():
        base = os.path.basename(p).lower()
        if "msedge" in base:
            rank, name = 1, "Microsoft Edge"
        elif "chrome" in base:
            marks = portable_marks(p)
            if marks:
                rank, name = 2, "便携版Chrome(%s)" % ",".join(marks)
            else:
                rank, name = 0, "Chrome"
        else:
            rank, name = 3, base
        if prio and a == os.path.abspath(prio).lower():
            rank = -1
        lst.append((p, rank, name))
    lst.sort(key=lambda x: (x[1], x[0]))
    return lst


def kill_stale_chrome(profile):
    """按 CommandLine 精确匹配 profile 路径杀残留 chrome（不误伤用户浏览器）"""
    try:
        ps1 = os.path.join(tempfile.gettempdir(), "kill_guardfish_chrome.ps1")
        with open(ps1, "w", encoding="utf-8-sig") as f:
            f.write("$p = Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                    "Where-Object { $_.CommandLine -like '*" + profile + "*' };\n"
                    "if ($p) { $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } }\n")
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1],
                       capture_output=True, timeout=30)
    except Exception:
        pass


def clean_profile_locks(profile):
    for lock in ("SingletonLock", "SingletonSocket", "SingletonCookie", "lockfile", "DevToolsActivePort"):
        fp = os.path.join(profile, lock)
        try:
            if os.path.exists(fp):
                os.remove(fp)
        except Exception:
            pass


# Chrome 启动参数。
# ⚠️ 不要加 --disable-gpu：本游戏是 PixiJS/WebGL 画面，禁用它会让 Chrome 退化为
# SwiftShader 软件渲染，1500x940@60fps 的战斗画面全部由 CPU 光栅化。
# 实测（2026-10-15，战斗中，浏览器进程树）：
#   带 --disable-gpu → 839% 单核（整机 52.4%），内存 1238→1802MB
#   硬件加速        → 82% 单核（整机 5.1%），内存 917→1165MB
# 即该项单独导致 CPU 高 10 倍。无显卡/驱动异常的环境由 launch_persistent 自动回退。
CHROME_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--mute-audio",
]
VIEWPORT = {"width": 1500, "height": 940}


def launch_persistent(pw, user_data_dir, headless=False):
    """逐候选启动持久化 Chrome；每个候选重试 2 次。返回 (ctx, used_path)

    GPU 回退：第一轮用硬件加速参数（CHROME_ARGS，不含 --disable-gpu）；
    若所有候选都起不来，再加 --disable-gpu 重试一轮，兼容无显卡/驱动异常的环境。
    """
    cands = chrome_candidates()
    if not cands:
        raise RuntimeError("未找到可用的 Chrome / Edge，请先安装 Google Chrome 或 Microsoft Edge")

    def _try(args):
        errs = []
        for exe, rank, name in cands:
            for attempt in (1, 2):
                try:
                    if attempt == 2:
                        kill_stale_chrome(user_data_dir)
                        clean_profile_locks(user_data_dir)
                        time.sleep(2)
                    ctx = pw.chromium.launch_persistent_context(
                        user_data_dir=user_data_dir, executable_path=exe, headless=headless,
                        viewport=VIEWPORT, args=args,
                        ignore_default_args=["--enable-automation"])
                    if ctx.pages:
                        page = ctx.pages[0]
                    else:
                        page = ctx.new_page()
                    try:
                        page.evaluate("1")
                    except Exception:
                        try:
                            ctx.close()
                        except Exception:
                            pass
                        raise RuntimeError("context 启动后不可用（可能被已有浏览器实例接管）")
                    return ctx, exe
                except Exception as e:
                    errs.append("%s(%s) 第%d次: %s" % (name, os.path.basename(exe), attempt, str(e)[:120]))
                    time.sleep(1)
        return None, errs

    # 第一轮：硬件加速（默认，CPU 友好）
    ctx, res = _try(list(CHROME_ARGS))
    if ctx is not None:
        return ctx, res
    errors = list(res)

    # 第二轮：软件渲染兜底（无显卡/驱动异常的环境）
    ctx, res = _try(list(CHROME_ARGS) + ["--disable-gpu"])
    if ctx is not None:
        return ctx, res
    errors += list(res)
    raise RuntimeError("浏览器启动失败：\n" + "\n".join(errors[-6:]))


# ---------------------------------------------------------------- 注入脚本
# 伪装 React DevTools hook（必须在页面脚本前注入）——用于拿到 fiber root、定位战斗引擎实例
FAKE_DEVTOOLS_HOOK = r"""
(() => {
  try {
    const hadHook = !!window.__REACT_DEVTOOLS_GLOBAL_HOOK__;
    let hook = window.__REACT_DEVTOOLS_GLOBAL_HOOK__;
    if (!hook) hook = window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = {};
    if (!hadHook) window.__GF_CREATED_REACT_HOOK__ = true;
    if (!hook.renderers) hook.renderers = new Map();
    if (typeof hook.inject !== 'function') {
      let nextId = 1;
      const roots = {};
      hook.inject = function(internals) {
        const id = nextId++;
        try { hook.renderers.set(id, internals); } catch (e) {}
        return id;
      };
      hook.onCommitFiberRoot = function(id, root) { try { roots[id] = root; hook.__roots = roots; } catch (e) {} };
      hook.onCommitFiberUnmount = function() {};
      hook.onPostCommitFiberRoot = function() {};
      hook.checkDCE = false;
      hook.isDisabled = false;
      hook.supportsFiber = true;
    }
  } catch (e) {}
})()
"""

# 血条守护：定位 BattleManager（coreHp），血量低于阈值自动回满（客户端引擎层，免费）
JS_HP_WATCH = r"""
(() => {
  if (window.__GF) return 'exists';
  const GF = window.__GF = {enabled: false, hpGuard: false, threshold: 5, refills: 0, hpEvents: [], hp: null,
                            mgrRef: null, loopRef: null, wrapped: false, foundOnce: false,
                            fullHp: 0,
                            battleT0: 0, maxRefillMs: 540000, capped: false,
                            oneHit: false, oneHitMode: 'full', oneHitProbability: 50,
                            attackMultiplier: 2, oneHitKills: 0, oneHitWrapped: false,
                            monitor: false, discover: false, endRequested: false, endApplied: false, finishing: false, endLoop: null,
                            intervalId: null, wrappedLoops: [],
                            scanCount: 0, scanInfo: null, diag: null, err: null, comp: ''};
  // 解析：ref.current 可能是 BattleLoop（直接含 coreHp）或 BattleManager（显示层，coreHp 在 .loop 里）
  function resolveTarget(c) {
    if (!c || typeof c !== 'object') return null;
    try {
      if (typeof c.coreHp === 'number' && c.config && c.config.battle && c.callbacks)
        return {loop: c, mgr: null, kind: 'loop'};
    } catch (e) {}
    try {
      const l = c.loop;
      if (l && typeof l === 'object' && typeof l.coreHp === 'number' && l.config && l.config.battle)
        return {loop: l, mgr: c, kind: 'manager'};
    } catch (e) {}
    try {
      const l = c.loop;
      if (l && typeof l === 'object' && (l.config || l.callbacks) && Array.isArray(c.towers))
        return {loop: l, mgr: c, kind: 'manager-pending'};
    } catch (e) {}
    return null;
  }
  function scanTree() {
    GF.scanCount++;
    const res = {fibers: 0, refs: 0, best: 0, comp: '', err: null};
    try {
      const hook = window.__REACT_DEVTOOLS_GLOBAL_HOOK__ || {};
      const roots = hook.__roots || {};
      const starts = [];
      for (const id of Object.keys(roots)) { const fr = roots[id]; if (fr && fr.current) starts.push(fr.current); }
      if (!starts.length) {
        const el = document.querySelector('[class*="GameHUD-module_container"], [class*="CardLanes"], canvas');
        if (el) {
          const k = Object.keys(el).find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
          if (k) { let f = el[k]; while (f && f.return) f = f.return; if (f) starts.push(f); }
        }
      }
      let best = null, bestScore = 0, bestName = '', bestKind = '';
      const seen = new Set();
      const queue = [...starts];
      // 索引游标而非 queue.shift()：大数组 shift() 是 O(n)，整棵树最坏 O(n^2)
      let qi = 0;
      let visited = 0;
      while (qi < queue.length && visited < 60000) {
        const f = queue[qi++]; visited++;
        if (!f || seen.has(f)) continue;
        seen.add(f);
        res.fibers++;
        try {
          let h = f.memoizedState, hc = 0;
          while (h && hc < 4000) {
            try {
              const v = h.memoizedState;
              if (v && typeof v === 'object' && 'current' in v) {
                res.refs++;
                const t = resolveTarget(v.current);
                if (t) {
                  const sc = t.kind === 'loop' ? 10 : (t.kind === 'manager' ? 9 : 5);
                  if (sc > bestScore) {
                    bestScore = sc; best = v; bestKind = t.kind;
                    bestName = (f.type && (f.type.name || f.type.displayName)) || 'fn';
                  }
                }
              }
            } catch (e) {}
            h = h.next; hc++;
          }
        } catch (e) {}
        if (f.child) queue.push(f.child);
        if (f.sibling) queue.push(f.sibling);
      }
      res.best = bestScore; res.comp = bestName;
      if (best) {
        GF.mgrRef = best; GF.foundOnce = true; GF.comp = bestName;
        const t = resolveTarget(best.current);
        GF.loopRef = t ? t.loop : null;
        try { GF.diag = {kind: bestKind, comp: bestName, coreHp: t ? t.loop.coreHp : null}; } catch (e) {}
      }
    } catch (e) { res.err = String(e); }
    GF.scanInfo = res;
    return res;
  }
  window.__gfScan = scanTree;
  function restoreCheck(loop) {
    try {
      if (loop.__gfCheckHadOwn) loop._checkEnemiesReachedCore = loop.__gfCheckOriginal;
      else if (loop.__gfWrapped) delete loop._checkEnemiesReachedCore;
      delete loop.__gfCheckOriginal;
      delete loop.__gfCheckHadOwn;
      delete loop.__gfWrapped;
    } catch (e) {}
  }
  function restoreDamage(loop) {
    try {
      if (loop.__gfDealHadOwn) loop._dealDamage = loop.__gfDealOriginal;
      else if (loop.__gfOneHitWrapped) delete loop._dealDamage;
      delete loop.__gfDealOriginal;
      delete loop.__gfDealHadOwn;
      delete loop.__gfOneHitWrapped;
    } catch (e) {}
  }
  function restoreLoop(loop) {
    if (!loop) return;
    restoreCheck(loop);
    restoreDamage(loop);
    if (window.__gfCombat) window.__gfCombat.restore(loop);
    GF.wrappedLoops = GF.wrappedLoops.filter(x => x !== loop);
  }
  window.__gfRestoreLoop = restoreLoop;
  // 只包装伤害入口；落点、击杀、掉落和结算仍由原引擎处理。
  function wrapDamage(loop) {
    if (loop.__gfOneHitWrapped) return;
    const original = loop._dealDamage;
    if (typeof original !== 'function') return;
    const hadOwn = Object.prototype.hasOwnProperty.call(loop, '_dealDamage');
    loop._dealDamage = function (enemy, damage, cardId, isCrit) {
      let nextDamage = damage;
      try {
        // 收尾优先级最高，覆盖普通攻击、暴击、传导、风系横扫和已发射的弹丸。
        if (GF.finishing) {
          nextDamage = 1 + Math.floor(Math.random() * 100);
        } else if (GF.oneHit && enemy && typeof enemy.currentHp === 'number' && enemy.currentHp > 0) {
          const mode = GF.oneHitMode || 'full';
          if (mode === 'multiplier') {
            const mult = Math.max(0.01, Number(GF.attackMultiplier) || 1);
            nextDamage = Math.max(1, Math.floor((Number(damage) || 0) * mult));
            if (nextDamage >= enemy.currentHp) GF.oneHitKills++;
          } else if (mode === 'full' || (mode === 'probability' &&
              Math.random() < Math.max(0, Math.min(100, Number(GF.oneHitProbability) || 0)) / 100)) {
            GF.oneHitKills++;
            nextDamage = enemy.currentHp;
          }
        }
      } catch (e) { GF.err = String(e); }
      // 原方法只调用一次，避免原引擎抛错时重复造成伤害。
      return original.call(this, enemy, nextDamage, cardId, isCrit);
    };
    loop.__gfDealHadOwn = hadOwn;
    loop.__gfDealOriginal = original;
    loop.__gfOneHitWrapped = true; GF.oneHitWrapped = true;
    if (GF.wrappedLoops.indexOf(loop) < 0) GF.wrappedLoops.push(loop);
  }
  function forceEnd(loop, tag) {
    try {
      if (!loop || typeof loop.coreHp !== 'number' || loop.running === false || loop.coreHp <= 0) return false;
      GF.hpGuard = false;
      GF.oneHit = false;
      GF.finishing = true;
      if (window.__gfCombat) window.__gfCombat.sync(loop);
      wrapDamage(loop);
      const before = loop.coreHp;
      loop.coreHp = 1;
      if (loop.callbacks && typeof loop.callbacks.onCoreHpChanged === 'function')
        loop.callbacks.onCoreHpChanged(1, loop.config.battle.baseCoreHp || 20);
      GF.endRequested = false;
      GF.endApplied = true;
      GF.endLoop = loop;
      GF.enabled = true;
      GF.hpEvents.push({t: Date.now(), type: 'end', from: before, to: 1, tag: tag || 'request'});
      return true;
    } catch (e) { GF.err = String(e); return false; }
  }
  window.__gfRequestEnd = () => {
    GF.hpGuard = false;
    GF.oneHit = false;
    GF.finishing = true;
    GF.endRequested = true;
    GF.discover = true;
    GF.enabled = true;
    let cur = GF.mgrRef && GF.mgrRef.current;
    let t = resolveTarget(cur);
    if (!t) { scanTree(); cur = GF.mgrRef && GF.mgrRef.current; t = resolveTarget(cur); }
    if (t && forceEnd(t.loop, 'request')) return {ok: true, applied: true};
    return {ok: false, pending: true, why: 'engine-not-found'};
  };
  function refill(loop, tag) {
    try {
      if (!GF.hpGuard || GF.finishing || GF.endRequested) return;
      if (!loop || typeof loop.coreHp !== 'number') return;
      if (loop.running === false) return;
      // 回血时间上限：按用户设置的单场时限停手，让战斗自然结束
      if (GF.battleT0 && (Date.now() - GF.battleT0) > GF.maxRefillMs) {
        if (!GF.capped) {
          GF.capped = true;
          GF.hpEvents.push({t: Date.now(), type: 'cap', mins: Math.round(GF.maxRefillMs / 60000)});
        }
        return;
      }
      if (loop.coreHp < GF.threshold) {
        // 「满血」目标 = 本次战斗见过的基础血量最大值（至少 20），
        // 不用当前关继承来的低血量（否则跨关后会只回到低值甚至原地打转）
        const stageMax = (loop.config && loop.config.battle && loop.config.battle.baseCoreHp) || 20;
        if (stageMax > (GF.fullHp || 0)) GF.fullHp = stageMax;
        const target = Math.max(GF.fullHp || 0, 20);
        if (target <= loop.coreHp) return;   // 防呆：无法提升就不再尝试
        const before = loop.coreHp;
        loop.coreHp = target;
        if (loop.callbacks && typeof loop.callbacks.onCoreHpChanged === 'function') loop.callbacks.onCoreHpChanged(target, target);
        GF.refills++;
        GF.hpEvents.push({t: Date.now(), type: 'refill', from: before, to: target, tag: tag});
      }
    } catch (e) { GF.err = String(e); }
  }
  window.__gfRefill = refill;
  let lastHp = null;
  let lastScan = 0;
  GF.intervalId = setInterval(() => {
    try {
      if (!GF.enabled && !GF.endRequested) return;
      let cur = GF.mgrRef && GF.mgrRef.current;
      let t = resolveTarget(cur);
      if (!t || typeof t.loop.coreHp !== 'number') {
        const now = Date.now();
        if (now - lastScan > 2000) { lastScan = now; scanTree(); }
        cur = GF.mgrRef && GF.mgrRef.current;
        t = resolveTarget(cur);
      }
      if (!t || typeof t.loop.coreHp !== 'number') return;
      const loop = t.loop;
      if (GF.loopRef && GF.loopRef !== loop) restoreLoop(GF.loopRef);
      GF.loopRef = loop;
      if (window.__gfCombat) window.__gfCombat.sync(loop);
      if (!GF.hpGuard) restoreCheck(loop);
      if (!GF.oneHit && !GF.finishing) restoreDamage(loop);
      if (GF.endRequested || (GF.finishing && (GF.endLoop !== loop || loop.coreHp > 1)))
        forceEnd(loop, 'watch');
      if (!GF.battleT0) { GF.battleT0 = Date.now(); GF.capped = false; }
      if (GF.hpGuard && !loop.__gfWrapped) {
        try {
          const proto = Object.getPrototypeOf(loop);
          const fn = loop._checkEnemiesReachedCore || (proto && proto._checkEnemiesReachedCore);
          if (typeof fn === 'function') {
            const hadOwn = Object.prototype.hasOwnProperty.call(loop, '_checkEnemiesReachedCore');
            const original = loop._checkEnemiesReachedCore;
            const orig = fn.bind(loop);
            loop._checkEnemiesReachedCore = function () {
              try { if (this.coreHp <= GF.threshold) refill(this, 'pre'); } catch (e) {}
              const r = orig();
              try { refill(this, 'post'); } catch (e) {}
              return r;
            };
            loop.__gfCheckHadOwn = hadOwn;
            loop.__gfCheckOriginal = original;
            loop.__gfWrapped = true; GF.wrapped = true;
            if (GF.wrappedLoops.indexOf(loop) < 0) GF.wrappedLoops.push(loop);
          }
        } catch (e) { GF.err = String(e); }
      }
      if (GF.oneHit || GF.finishing) wrapDamage(loop);
      const hp = loop.coreHp;
      const stageMax2 = (loop.config && loop.config.battle && loop.config.battle.baseCoreHp) || 20;
      if (stageMax2 > (GF.fullHp || 0)) GF.fullHp = stageMax2;
      const maxShown = Math.max(GF.fullHp || 0, 20);
      GF.hp = {cur: hp, max: maxShown, t: Date.now(), running: !!loop.running};
      if (lastHp !== null && hp < lastHp) GF.hpEvents.push({t: Date.now(), type: 'damage', from: lastHp, to: hp});
      lastHp = hp;
      refill(loop, 'watch');
    } catch (e) { GF.err = String(e); }
  }, 250);
  return 'installed';
})()
"""

JS_HP_CFG = r"""
(cfg) => {
  if (!window.__GF) return false;
  if (cfg && cfg.enabled !== undefined) window.__GF.hpGuard = !!cfg.enabled;   // 兼容旧参数名
  if (cfg && cfg.hpGuard !== undefined) window.__GF.hpGuard = !!cfg.hpGuard;
  if (cfg && cfg.threshold !== undefined) window.__GF.threshold = Number(cfg.threshold) || 5;
  if (cfg && cfg.oneHit !== undefined) window.__GF.oneHit = !!cfg.oneHit;
  if (cfg && cfg.oneHitMode !== undefined) {
    const mode = String(cfg.oneHitMode || 'full');
    window.__GF.oneHitMode = ['full', 'probability', 'multiplier'].indexOf(mode) >= 0 ? mode : 'full';
  }
  if (cfg && cfg.oneHitProbability !== undefined)
    window.__GF.oneHitProbability = Math.max(0, Math.min(100, Number(cfg.oneHitProbability) || 0));
  if (cfg && cfg.attackMultiplier !== undefined)
    window.__GF.attackMultiplier = Math.max(0.01, Number(cfg.attackMultiplier) || 1);
  if (cfg && cfg.discover !== undefined) window.__GF.discover = !!cfg.discover;
  if (cfg && cfg.monitor !== undefined) window.__GF.monitor = !!cfg.monitor;
  if (cfg && window.__gfCombat) window.__gfCombat.configure(cfg.combat, cfg.monitorEpoch);
  if (cfg && cfg.maxRefillMs !== undefined) window.__GF.maxRefillMs = Math.max(0, Number(cfg.maxRefillMs) || 0);
  // 守护循环（250ms）只要功能开启、正在寻找引擎或等待结束就运行
  window.__GF.enabled = !!(window.__GF.hpGuard || window.__GF.oneHit || window.__GF.monitor || window.__GF.discover || window.__GF.endRequested || window.__GF.finishing);
  return {hpGuard: window.__GF.hpGuard, enabled: window.__GF.enabled,
          threshold: window.__GF.threshold, oneHit: window.__GF.oneHit,
          oneHitMode: window.__GF.oneHitMode,
          oneHitProbability: window.__GF.oneHitProbability,
          attackMultiplier: window.__GF.attackMultiplier,
          discover: !!window.__GF.discover, endRequested: !!window.__GF.endRequested};
}
"""

JS_HP_END = r"""
() => {
  if (!window.__GF || typeof window.__gfRequestEnd !== 'function')
    return {ok: false, pending: false, why: 'runtime-not-loaded'};
  return window.__gfRequestEnd();
}
"""

JS_HP_UNLOAD = r"""
() => {
  const gf = window.__GF;
  if (!gf) return {ok: true, unloaded: false};
  try { gf.enabled = false; gf.endRequested = false; gf.finishing = false; } catch (e) {}
  try { if (gf.intervalId) clearInterval(gf.intervalId); } catch (e) {}
  try { for (const loop of [...(gf.wrappedLoops || [])]) { if (typeof window.__gfRestoreLoop === 'function') window.__gfRestoreLoop(loop); } } catch (e) {}
  try { if (window.__GF_CREATED_REACT_HOOK__) delete window.__REACT_DEVTOOLS_GLOBAL_HOOK__; delete window.__GF_CREATED_REACT_HOOK__; } catch (e) {}
  try { delete window.__gfScan; delete window.__gfRefill; delete window.__gfRequestEnd; delete window.__gfRestoreLoop; delete window.__gfCombat; delete window.__GF; } catch (e) {}
  return {ok: true, unloaded: true};
}
"""

JS_HP_DRAIN = r"""
() => {
  const gf = window.__GF;
  if (!gf) return null;
  const ev = gf.hpEvents ? gf.hpEvents.splice(0, 200) : [];
  const loop = gf.loopRef;
  const progress = loop ? [loop.currentFloor, loop.currentWave, loop.enemyInstanceCounter,
                          loop.enemies && loop.enemies.length, loop.pendingSpawnQueue && loop.pendingSpawnQueue.length] : null;
  return {refills: gf.refills || 0, wrapped: !!gf.wrapped, found: !!gf.mgrRef,
          comp: gf.comp || '', hp: gf.hp,
          hpGuard: !!gf.hpGuard, enabled: !!gf.enabled, discover: !!gf.discover,
          oneHit: !!gf.oneHit, oneHitMode: gf.oneHitMode || 'full',
          oneHitProbability: Number(gf.oneHitProbability) || 0,
          attackMultiplier: Number(gf.attackMultiplier) || 1,
          oneHitWrapped: !!gf.oneHitWrapped,
          oneHitKills: gf.oneHitKills || 0,
          endRequested: !!gf.endRequested, endApplied: !!gf.endApplied,
          finishing: !!gf.finishing, progress,
          combat: window.__gfCombat ? window.__gfCombat.snapshot() : null,
          scanInfo: gf.scanInfo, err: gf.err, events: ev};
}
"""

JS_HOOK = r"""
(() => {
  if (window.__WM) return;
  const W = window.__WM = {panelInfo:null, battleInfo:null, stage:null, maxStage:0,
                          freeNum:null, battleSkill:null, battleOver:false, last:null, respCount:0};
  const pick = (url, j) => {
    try {
      const d = j && j.data || {};
      W.last = {url: url, t: Date.now(), body: j};
      W.respCount++;
      if (url.indexOf('basics/panelInfo') >= 0) W.panelInfo = j;
      if (url.indexOf('game/battleInfo') >= 0) W.battleInfo = j;
      if (d.userInfo && d.userInfo.freeNum != null) W.freeNum = d.userInfo.freeNum;
      if (d.battleInfo && d.battleInfo.curStage != null) {
        W.stage = {curStage: d.battleInfo.curStage, curHp: d.battleInfo.baseHp, t: Date.now()};
        W.maxStage = Math.max(W.maxStage || 0, d.battleInfo.curStage);
      }
      if (d.stageInfo) {
        W.stage = {curStage: d.stageInfo.curStage, curHp: d.stageInfo.curHp, t: Date.now()};
        W.maxStage = Math.max(W.maxStage || 0, d.stageInfo.curStage);
      }
      if (d.battleSkill) W.battleSkill = d.battleSkill;
      if (d.status === 20) W.battleOver = true;
      if (d.status === 10) W.battleOver = false;
    } catch (e) {}
  };
  const of = window.fetch;
  window.fetch = function() {
    const args = arguments;
    return of.apply(this, args).then(resp => {
      try {
        const u = (typeof args[0] === 'string') ? args[0] : ((args[0] && args[0].url) || '');
        if (u.indexOf('/actgame/') >= 0) { resp.clone().json().then(j => pick(u, j)).catch(()=>{}); }
      } catch (e) {}
      return resp;
    });
  };
  const oo = XMLHttpRequest.prototype.open;
  const osend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(m, u) { this.__u = u; return oo.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function() {
    this.addEventListener('load', () => {
      try {
        if ((this.__u||'').indexOf('/actgame/') >= 0) pick(this.__u, JSON.parse(this.responseText));
      } catch (e) {}
    });
    return osend.apply(this, arguments);
  };
})()
"""

JS_STATE = r"""
(() => {
  const q = s => document.querySelector(s);
  const vis = el => { if (!el) return false; const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    return r.width > 1 && r.height > 1 && st.display !== 'none' && st.visibility !== 'hidden'; };
  const out = { t: Date.now() };

  out.inBattle = vis(q('[class*="GameHUD-module_container"]'));

  // 技能三选一面板（战斗中 SkillPanel / 开场后 Starter）
  const sp = q('[class*="SkillPanel-module_panel"]');
  const stp = q('[class*="Starter-module_skillsPanel"]');
  if (vis(sp) || vis(stp)) {
    const root = vis(sp) ? sp : stp;
    const cards = [...root.querySelectorAll('[class*="SkillPanel-module_skill-"], [class*="Starter-module_skill-"]')].filter(vis);
    out.skillPanel = cards.map((c, i) => ({ i: i, txt: (c.innerText || '').trim().replace(/\n/g, ' | ').slice(0, 160) }));
    out.skillPanelKind = vis(sp) ? 'stage' : 'starter';
  }

  // 已选技能确认列表（带开始按钮）
  const fl = q('[class*="Starter-module_skillList"]');
  if (vis(fl)) out.finalSkillList = (fl.innerText || '').trim().replace(/\n/g, ' | ').slice(0, 200);
  const la = q('[class*="Starter-module_limitAlter"]');
  if (vis(la)) out.limitAlter = true;

  out.confirmDlg = vis(q('[class*="finalSkillPayAlter"]'));   // 绝技付费确认（不会点，做哨兵）
  out.result = vis(q('[class*="Result-module_modal"]'));
  out.treasure = vis(q('[class*="TreasureResult-module_modal"]'));
  out.speedDlg = vis(q('[class*="GameHUD-module_payAlter"]')); // 加速付费弹窗（哨兵）

  const cb = q('[class*="challengeButton"]');
  out.canChallenge = vis(cb);
  if (vis(cb)) out.challengeTxt = (cb.innerText || '').replace(/\n/g, ' ').trim().slice(0, 60);

  // 每周宝箱「领取」（战斗宝箱领取按钮：固定 id js-target-treasure）
  if (out.inBattle) {
    const b = document.querySelector('#js-target-treasure button');
    let claimable = false;
    if (b) {
      const r = b.getBoundingClientRect();
      claimable = r.width > 1 && r.height > 1;
    }
    out.hudClaimable = claimable;
  }

  const w = window.__WM || {};
  out.wm = {respCount: w.respCount || 0, freeNum: w.freeNum, maxStage: w.maxStage || 0,
            panelReady: !!w.panelInfo,
            stage: w.stage, battleOver: !!w.battleOver,
            nick: (w.panelInfo && w.panelInfo.data && w.panelInfo.data.userInfo && w.panelInfo.data.userInfo.nn) || '',
            weekRank: (w.panelInfo && w.panelInfo.data && w.panelInfo.data.siteUserInfo && w.panelInfo.data.siteUserInfo.siteWeekRank) || 0,
            coin: (() => {
              try {
                const s = w.panelInfo && w.panelInfo.data && w.panelInfo.data.siteUserInfo;
                if (s && typeof s.coin === 'number') return s.coin;
              } catch (e) {}
              return null;
            })(),
            panelCode: (w.panelInfo && w.panelInfo.code) || 0};
  try {
    const ct = document.querySelector('[class*="TopBar-module_coinText"]');
    out.coinDom = ct ? (ct.innerText || '').trim() : null;
  } catch (e) { out.coinDom = null; }
  return out;
})()
"""

JS_CLICK_CHALLENGE = r"""
() => { const b = document.querySelector('[class*="challengeButton"]');
  if (!b) return null;
  const text = (b.innerText || '').replace(/\n/g, ' ').trim();
  if (/(鱼翅|送出礼物|充值|付费|支付|礼物)/.test(text))
    return {ok: false, why: 'paid-button-blocked', text: text.slice(0, 80)};
  b.click(); return {ok: true, text: text}; }
"""

JS_CLICK_SKILL = r"""
(i) => {
  const cards = [...document.querySelectorAll('[class*="SkillPanel-module_skill-"], [class*="Starter-module_skill-"]')]
    .filter(c => { const r = c.getBoundingClientRect(); return r.width > 1 && r.height > 1; });
  const c = cards[i]; if (!c) return null;
  c.click(); return (c.innerText || '').trim().slice(0, 60);
}
"""

JS_CLICK_FINAL_START = r"""
() => {
  const vis = el => { if (!el) return false; const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 1 && r.height > 1 && s.display !== 'none' && s.visibility !== 'hidden'; };
  const T = el => (el.innerText || '').replace(/\s+/g, '').trim();
  // 自身或内部真实按钮被禁用，都算不可点（外层 div 常是按钮的包装）
  const off = el => {
    if (el.disabled || el.getAttribute('aria-disabled') === 'true'
        || /disabled/i.test(String(el.className || ''))) return true;
    if (getComputedStyle(el).pointerEvents === 'none') return true;
    const inner = el.querySelector && el.querySelector('button, [role="button"]');
    return !!(inner && (inner.disabled || inner.getAttribute('aria-disabled') === 'true'));
  };
  const isBtn = el => el.matches('button, [role="button"]');
  const clickable = el => isBtn(el)
      || /btn|Btn|footer|Footer|confirm|Confirm|start|Start/.test(String(el.className || ''));
  const paid = el => /(鱼翅|送出礼物|充值|付费|支付|礼物)/.test(T(el));

  const list = document.querySelector('[class*="Starter-module_skillList"]');
  if (!list) return {ok: false, why: 'no-skillList'};

  // 技能卡本身要排除，其余可点元素都算候选（含 footer 容器，点击可冒泡）
  const cand = root => [...root.querySelectorAll('button, [role="button"], [class*="footer"], [class*="Footer"], [class*="btn"], [class*="Btn"]')]
      .filter(b => vis(b) && !b.closest('[class*="_skill-"]') && clickable(b));

  // 面板根：footer 常是 skillList 的兄弟节点，逐层上溯
  let scope = list, found = [], hops = 0;
  while (scope && hops < 6) {
    found = cand(scope);
    if (found.length) break;
    scope = scope.parentElement; hops++;
  }

  // 全页兜底（弹窗可能挂在 body 下）
  const all = [...document.querySelectorAll('button, [role="button"]')]
      .filter(b => vis(b) && !b.closest('[class*="_skill-"]'));

  // 前缀匹配优先；再退化到包含匹配（应对「点击开始」这类文案）
  const KEY = [/^开始/, /^确认/, /^确定/, /出战/, /进入战斗/, /^战斗/, /^立即/,
               /开始/, /确认/, /出战/, /进入/];

  // 优先真实 <button>；容器元素只在找不到按钮时兜底，避免选中包装层而漏判 disabled
  const choose = arr => {
    const safe = arr.filter(x => !paid(x));
    const real = safe.filter(isBtn);
    for (const re of KEY) { const b = real.find(x => re.test(T(x))); if (b) return {b: b, how: 'text'}; }
    const f = real.find(x => /footer/i.test(String(x.className || '')));
    if (f) return {b: f, how: 'footer'};
    for (const re of KEY) { const b = safe.find(x => re.test(T(x))); if (b) return {b: b, how: 'text-wrapper'}; }
    if (real.length === 1) return {b: real[0], how: 'single'};
    if (safe.length === 1) return {b: safe[0], how: 'single-wrapper'};
    return {b: null, how: null};
  };

  let {b: btn, how} = choose(found);
  if (!btn) { const p = choose(all); btn = p.b; how = p.how ? 'page-' + p.how : null; }

  const dump = arr => [...new Set(arr)].slice(0, 6).map(b =>
      b.tagName + '|' + T(b).slice(0, 16) + '|' + String(b.className || '').slice(0, 34));

  if (!btn) return {ok: false, why: found.concat(all).some(paid) ? 'paid-button-blocked' : 'no-button', hops: hops,
                    panelCls: String((scope && scope.className) || '').slice(0, 90),
                    seen: dump(found.concat(all))};
  if (off(btn)) return {ok: false, why: 'disabled', how: how, text: T(btn).slice(0, 24),
                        seen: dump(found.concat(all))};
  try { btn.click(); } catch (e) { return {ok: false, why: 'click-threw:' + String(e).slice(0, 60)}; }
  return {ok: true, how: how, text: T(btn).slice(0, 24) || 'ok'};
}
"""

JS_CLOSE_LIMIT_ALTER = r"""
() => {
  const m = document.querySelector('[class*="Starter-module_limitAlter"]');
  if (!m) return false;
  const c = m.querySelector('[class*="Starter-module_close"]');
  if (c) { c.click(); return true; }
  return false;
}
"""

JS_RESULT_BTN = r"""
() => {
  const b = document.querySelector('[class*="Result-module_btn"]');
  if (b) { b.click(); return 'result-btn'; }
  const c = document.querySelector('[class*="TreasureResult-module_close"]');
  if (c) { c.click(); return 'treasure-close'; }
  return null;
}
"""

JS_HUD_CLAIM = r"""
() => {
  const b = document.querySelector('#js-target-treasure button');
  if (!b) return null;
  const r = b.getBoundingClientRect();
  if (r.width < 1 || r.height < 1) return null;
  b.click(); return 'claimed';
}
"""

JS_RESULT_TEXT = r"""
() => {
  const m = document.querySelector('[class*="Result-module_modal"]');
  return m ? (m.innerText || '').trim().replace(/\n/g, ' | ').slice(0, 300) : '';
}
"""

JS_CLEAR_NO_TIP = r"""
() => {
  const cleared = [];
  ['act202606Game_clsSkillNoTip', 'act202606Game_speedNoTip2x', 'act202606Game_speedNoTip4x',
   'act202606Game_stageRefreshSkillTip', 'act202606Game_battleRefreshSkillTip'].forEach(k => {
    try { if (localStorage.getItem(k) != null) { localStorage.removeItem(k); cleared.push(k); } } catch (e) {}
  });
  return cleared;
}
"""

JS_HUD_HTML = r"""
() => { const h = document.querySelector('[class*="GameHUD-module_container"]');
  return h ? h.outerHTML.slice(0, 6000) : null; }
"""


# ---------------------------------------------------------------- 技能评分
def score_skill_text(txt):
    t = (txt or "").replace(" ", "").replace("\n", "")
    # 付费相关（绝技次数）无价值 —— 我们不用绝技
    if "绝技" in t:
        return 60
    if "全部出战卡" in t and "攻击力" in t:
        return 950
    if "基地血量" in t:
        return 900
    if "全部出战卡" in t and "攻速" in t:
        return 880
    if "土系出战卡" in t and "攻击力" in t:
        return 820
    if "土系出战卡" in t and "攻速" in t:
        return 760
    if "土系出战卡" in t:
        return 620          # 眩晕等
    if "雷系出战卡" in t and "攻击力" in t:
        return 700
    if "火系出战卡" in t and "攻击力" in t:
        return 680
    if "冰系出战卡" in t and "攻击力" in t:
        return 660
    if "风系出战卡" in t and "攻击力" in t:
        return 660
    if "雷系出战卡" in t:
        return 520
    if "火系出战卡" in t:
        return 500
    if "冰系出战卡" in t:
        return 480
    if "风系出战卡" in t:
        return 470
    if "攻击力" in t:
        return 430
    if "攻速" in t:
        return 420
    if any(k in t for k in ("传导", "暴击", "冰冻", "横扫", "眩晕")):
        return 400
    return 300


ONE_HIT_MODES = {"full", "probability", "multiplier"}


def normalize_one_hit_mode(mode):
    mode = str(mode or "full").strip().lower()
    return mode if mode in ONE_HIT_MODES else "full"


def one_hit_mode_text(mode, probability=50.0, multiplier=2.0):
    mode = normalize_one_hit_mode(mode)
    if mode == "probability":
        return "概率 %.1f%%" % float(probability)
    if mode == "multiplier":
        return "攻击 %.2fx" % float(multiplier)
    return "全额"


# ---------------------------------------------------------------- Bot
class GuardFishBot:
    def __init__(self, rid=DEFAULT_RID, rounds=10, gap_min=5, gap_max=15,
                 headless=False, claim_weekpass=True, on_log=None,
                 hp_guard=True, hp_threshold=5, coin_play=True, one_hit=False,
                 max_battle_seconds=MAX_BATTLE_SECONDS, stop_stage=0,
                 advance_stages=0, one_hit_mode="full",
                 one_hit_probability=50.0, attack_multiplier=2.0, debug=False,
                 pause_on_timeout=False, combat_controls=None):
        self.rid = str(rid)
        self.rounds = int(rounds)
        self.gap_min = float(gap_min or 0)
        self.gap_max = float(gap_max or 0)
        self.headless = bool(headless)
        self.claim_weekpass = bool(claim_weekpass)
        self.hp_guard = bool(hp_guard)
        self.hp_threshold = int(hp_threshold or 5)
        self.coin_play = bool(coin_play)          # 免费用尽后用银币续打（银币为免费获取）
        self.one_hit = bool(one_hit)              # 秒杀模式：我方卡牌造成的伤害直接击杀
        self.one_hit_mode = normalize_one_hit_mode(one_hit_mode)
        self.one_hit_probability = max(0.0, min(float(one_hit_probability or 0), 100.0))
        self.attack_multiplier = max(float(attack_multiplier or 1.0), 0.01)
        self.max_battle_seconds = max(float(max_battle_seconds or MAX_BATTLE_SECONDS), 1.0)
        self.stop_stage = max(int(stop_stage or 0), 0)       # 绝对关卡目标，0=关闭
        self.advance_stages = max(int(advance_stages or 0), 0)  # 从起始关推进的关卡数，0=关闭
        self.debug = bool(debug)
        self.pause_on_timeout = bool(pause_on_timeout)
        self.combat_controls = normalize_controls(combat_controls)
        self._runtime_epoch = 0
        self._kills = KillStats()
        self._pending_stage_records = []
        self._recovery_attempts = 0
        self._debug_recorder = None
        self._debug_failed = False
        self._debug_last_capture = None
        self._debug_last_stage = None
        self._debug_sources_collected = False
        self._debug_poll_stage = None
        self._stage_timer = None
        self._stage_fp = None
        self._one_hit_kills = 0
        self._last_runtime_kills = 0
        self._battle_paid = "free"                # free / coin

        self._stop = threading.Event()
        self._thread = None
        self._pw = None
        self._ctx = None
        self._page = None
        self._lock = threading.Lock()
        self._control_lock = threading.Lock()
        self._runtime_pending = True
        self._runtime_script_loaded = False
        self._runtime_applied = None
        self._natural_end_requested = False
        self._natural_end_sent = False
        self._natural_end_reason = ""
        self._battle_active = False
        self._pause_requested = False
        self._paused = False
        self._pause_wakeup = threading.Event()
        self.rounds_done = 0
        self._stage_target = None
        self._stage_stop_announced = False
        self._sinks = []
        if on_log:
            self._sinks.append(on_log)

        self._api_fp = None
        self._battle_fp = None
        self._battle_boxes = 0
        self._server_reject_ts = 0
        self._report_errs = 0
        self._date = datetime.date.today().strftime("%Y%m%d")

        # 对外可见的状态（GUI 读取）
        self.status = {
            "state": "空闲", "round": 0, "rounds": self.rounds, "rounds_done": 0, "stage": 0, "hp": 0,
            "freeNum": None, "maxStage": 0, "weekRank": 0, "nick": "", "coin": None,
        }

    # -------- 日志 --------
    def add_log_sink(self, fn):
        self._sinks.append(fn)

    def set_runtime_controls(self, hp_guard=None, one_hit=None, hp_threshold=None,
                             one_hit_mode=None, one_hit_probability=None,
                             attack_multiplier=None, combat_controls=None):
        """更新运行中的回血/秒杀开关；实际页面操作由机器人线程执行。"""
        with self._control_lock:
            if hp_guard is not None:
                self.hp_guard = bool(hp_guard)
            if one_hit is not None:
                self.one_hit = bool(one_hit)
            if hp_threshold is not None:
                self.hp_threshold = max(int(hp_threshold), 1)
            if one_hit_mode is not None:
                self.one_hit_mode = normalize_one_hit_mode(one_hit_mode)
            if one_hit_probability is not None:
                self.one_hit_probability = max(0.0, min(float(one_hit_probability), 100.0))
            if attack_multiplier is not None:
                self.attack_multiplier = max(float(attack_multiplier), 0.01)
            if combat_controls is not None:
                self.combat_controls = normalize_controls(combat_controls, self.combat_controls)
            self._runtime_pending = True

    def _reset_battle_controls(self):
        """开始新场前清除上一场的自然结束状态，并强制重新同步页面开关。"""
        # 解除上一场的低伤害包装；新场按用户选择重新加载，避免收尾状态泄漏。
        self._unload_runtime_script()
        with self._control_lock:
            self._natural_end_requested = False
            self._natural_end_sent = False
            self._natural_end_reason = ""
            self._runtime_pending = True
            self._runtime_applied = None

    def request_natural_end(self, reason="用户请求"):
        """请求本场把基地降到 1 血，让游戏自己的失败/结算流程收尾。"""
        with self._control_lock:
            if self._natural_end_requested:
                return
            self._natural_end_reason = str(reason)
            self._natural_end_requested = True
        self._log("已请求本场正常结束：%s（基地降到 1 血，停止新攻击，等待游戏结算）" % reason, "warn")

    def request_pause(self):
        """GUI 只提交请求，当前场的收尾和暂停都在工作线程执行。"""
        with self._control_lock:
            if self._stop.is_set() or self._pause_requested or self._paused:
                return False
            self._pause_requested = True
        self._log("已请求暂时停止：本场结算并回到主界面后暂停，保留场次和浏览器。", "warn")
        return True

    def request_resume(self):
        with self._control_lock:
            if self._stop.is_set() or not self._paused or not self._pause_requested:
                return False
            self._pause_requested = False
            self._pause_wakeup.set()
        return True

    def _wait_if_paused(self, unfinished=False):
        """保留调用栈和计数等待恢复；等待期间不查询页面、不执行游戏操作。"""
        with self._control_lock:
            requested = self._pause_requested
        if not requested or self._stop.is_set():
            return not self._stop.is_set()
        self._unload_runtime_script()
        with self._control_lock:
            self._pause_wakeup.clear()
            self._paused = True
        self.status["state"] = "已暂停（本场未结算）" if unfinished else "已暂停"
        self._debug_write("paused", unfinished=unfinished, rounds_done=self.rounds_done)
        self._log("%s，已完成 %d%s 场；点击「恢复战斗」继续。" %
                  (self.status["state"], self.rounds_done,
                   "/%d" % self.rounds if self.rounds > 0 else ""))
        while not self._stop.is_set():
            with self._control_lock:
                if not self._pause_requested:
                    break
            self._pause_wakeup.wait(0.5)
        with self._control_lock:
            self._paused = False
            self._runtime_pending = True
            # 暂停前卸载了页面模块。若本场未结束，恢复后要重新应用收尾请求。
            self._natural_end_sent = False
        if self._stop.is_set():
            return False
        self.status["state"] = "恢复监控" if unfinished else "准备开局"
        self._debug_write("resumed", unfinished=unfinished, rounds_done=self.rounds_done)
        self._log("已恢复挂机，继续%s（已完成 %d 场）。" %
                  ("当前场监控" if unfinished else "后续场次", self.rounds_done))
        return True

    def _log(self, msg, level="info"):
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
        log_msg(line)
        for fn in list(self._sinks):
            try:
                fn(line, level)
            except Exception:
                pass

    # -------- 停止 --------
    def request_stop(self):
        self._stop.set()
        self._pause_wakeup.set()
        self._log("收到停止指令…", "warn")

    def stop(self):
        self.request_stop()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=8)

    # -------- 浏览器 --------
    def _ensure_browser(self):
        if self._ctx is not None and self._page is not None:
            try:
                self._page.evaluate("1")
                return
            except Exception:
                self._close_browser()
        from playwright.sync_api import sync_playwright
        if self._pw is None:
            self._pw = sync_playwright().start()
        kill_stale_chrome(PROFILE)
        clean_profile_locks(PROFILE)
        ctx, exe = launch_persistent(self._pw, PROFILE, headless=self.headless)
        self._ctx = ctx
        self._page = ctx.pages[0] if ctx.pages else ctx.new_page()
        self._log("浏览器就绪：%s" % exe)
        try:
            self._page.add_init_script(JS_HOOK)
        except Exception as e:
            self._log("注入 hook 失败：%s" % str(e)[:120], "warn")
        try:
            self._page.on("response", self._on_response)
        except Exception:
            pass
        # 关闭付费「不再提示」标记（安全兜底）
        try:
            cleared = self._page.evaluate(JS_CLEAR_NO_TIP)
            if cleared:
                self._log("已清理付费免提示标记：%s" % cleared, "warn")
        except Exception:
            pass

    def _install_runtime_script(self):
        """运行时加载轻量击杀监听；伤害、攻速等方法仅在相关开关开启时包装。"""
        if self._page is None:
            return False
        if self._runtime_script_loaded:
            return True
        try:
            self._page.evaluate(FAKE_DEVTOOLS_HOOK)
            self._page.evaluate(JS_HP_WATCH)
            self._page.evaluate(JS_COMBAT_INSTALL)
            self._runtime_epoch += 1
            self._runtime_script_loaded = True
            self._last_runtime_kills = 0
            self._runtime_applied = None
            self._log("已加载战斗计数与实时控制模块", "dim")
            return True
        except Exception as e:
            self._log("动态加载回血/秒杀模块失败：%s" % str(e)[:120], "warn")
            return False

    def _unload_runtime_script(self):
        if self._page is None or not self._runtime_script_loaded:
            return
        self._collect_kills()
        try:
            self._page.evaluate(JS_HP_UNLOAD)
        except Exception as e:
            self._log("卸载回血/秒杀模块失败：%s" % str(e)[:120], "warn")
        self._runtime_script_loaded = False
        self._runtime_applied = None
        self._last_runtime_kills = 0

    def _apply_runtime_controls(self, force=False):
        """在机器人线程内同步实时开关，避免从 GUI 线程调用 Playwright。"""
        if self._page is None:
            return None
        with self._control_lock:
            hp_guard = bool(self.hp_guard)
            one_hit = bool(self.one_hit)
            threshold = max(int(self.hp_threshold or 5), 1)
            one_hit_mode = normalize_one_hit_mode(self.one_hit_mode)
            one_hit_probability = max(0.0, min(float(self.one_hit_probability), 100.0))
            attack_multiplier = max(float(self.attack_multiplier), 0.01)
            combat = self.combat_controls
            pending = bool(self._runtime_pending)
            natural_end = bool(self._natural_end_requested)
            natural_sent = bool(self._natural_end_sent)

        if natural_end:
            if not self._install_runtime_script():
                return None
            try:
                if not natural_sent:
                    self._page.evaluate(JS_HP_CFG, {
                        "hpGuard": False, "oneHit": False, "threshold": threshold,
                        "oneHitMode": one_hit_mode,
                        "oneHitProbability": one_hit_probability,
                        "attackMultiplier": attack_multiplier,
                        "discover": True, "monitor": True, "monitorEpoch": self._runtime_epoch,
                        "combat": default_controls(), "maxRefillMs": self.max_battle_seconds * 1000,
                    })
                    r = self._page.evaluate(JS_HP_END)
                    if isinstance(r, dict) and (r.get("ok") or r.get("pending")):
                        with self._control_lock:
                            self._natural_end_sent = True
                        if r.get("ok"):
                            self._log("已将基地降到 1 血，暂停新攻击并关闭强化；在途伤害限制为 1–100，等待自然结算", "warn")
                        else:
                            self._log("已请求自然结束，等待识别战斗引擎", "warn")
                        return r
                    self._log("自然结束请求暂未生效：%s" % (r or "未知原因"), "warn")
                    return r
                return None
            except Exception as e:
                self._log("同步自然结束请求失败：%s" % str(e)[:120], "warn")
                return None

        # 计数监听在整次挂机中保留；各个修改开关独立包装/恢复原生方法。
        if not self._install_runtime_script():
            return None
        desired = (hp_guard, one_hit, threshold, one_hit_mode, one_hit_probability,
                   attack_multiplier, combat)
        if pending or force or self._runtime_applied != desired:
            try:
                r = self._page.evaluate(JS_HP_CFG, {
                    "hpGuard": hp_guard, "oneHit": one_hit, "threshold": threshold,
                    "oneHitMode": one_hit_mode, "oneHitProbability": one_hit_probability,
                    "attackMultiplier": attack_multiplier, "combat": combat,
                    "monitor": True, "monitorEpoch": self._runtime_epoch,
                    "discover": False, "maxRefillMs": self.max_battle_seconds * 1000,
                })
                with self._control_lock:
                    self._runtime_pending = False
                self._runtime_applied = desired
                self._log("实时配置已同步：回血=%s｜伤害=%s｜攻速=%s｜分系覆盖=%s"
                          % ("开" if hp_guard else "关",
                             one_hit_mode_text(one_hit_mode, one_hit_probability, attack_multiplier) if one_hit else "关",
                             ("固定 %.3g 秒" % combat["attack_speed"]["fixed"]
                              if combat["attack_speed"]["mode"] == "fixed" else
                              "倍率 %.3g" % combat["attack_speed"]["multiplier"])
                             if combat["attack_speed"]["enabled"] else "关",
                             "/".join(EFFECT_LABELS[k] for k, v in combat["effects"].items() if v["enabled"]) or "关"), "dim")
                self._debug_write("runtime_config", combat=combat, hp_guard=hp_guard, one_hit=one_hit)
                return r
            except Exception as e:
                self._log("同步战斗设置失败：%s" % str(e)[:120], "warn")
        return None

    def _apply_hp_config(self):
        """兼容旧调用点：同步实时控制配置。"""
        return self._apply_runtime_controls(force=True)

    def _close_browser(self):
        try:
            self._unload_runtime_script()
        except Exception:
            pass
        try:
            if self._ctx is not None:
                self._ctx.close()
        except Exception:
            pass
        self._ctx = None
        self._page = None
        try:
            kill_stale_chrome(PROFILE)
            clean_profile_locks(PROFILE)
        except Exception:
            pass

    def _shutdown_pw(self):
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._pw = None

    # -------- 诊断与单关统计（不参与游戏控制） --------
    def _debug_write(self, kind, **data):
        if not self.debug or self._debug_failed:
            return
        try:
            if self._debug_recorder is None:
                self._debug_recorder = DebugRecorder(LOGDIR, APP_VERSION)
                self._log("Debug 诊断文件：%s" % self._debug_recorder.path)
            self._debug_recorder.write(kind, round=self.status.get("round", 0), **data)
        except Exception as exc:
            self._debug_failed = True
            self._log("Debug 写入失败，继续挂机：%s" % str(exc)[:120], "warn")

    def _debug_capture(self, st, force=False):
        if not self.debug or self._debug_failed or self._page is None:
            return
        now = time.monotonic()
        stage = ((st.get("wm") or {}).get("stage") or {}).get("curStage")
        if (not force and self._debug_last_capture is not None
                and now - self._debug_last_capture < 15 and stage == self._debug_last_stage):
            return
        self._debug_last_capture = now
        self._debug_last_stage = stage
        try:
            snapshot = self._page.evaluate(JS_ENGINE_SNAPSHOT, {"sources": not self._debug_sources_collected})
            if isinstance(snapshot, dict) and snapshot.get("found"):
                self._debug_sources_collected = True
            self._debug_write("engine_snapshot", monotonic_s=now, stage=stage,
                              state={k: bool(st.get(k)) for k in ("inBattle", "result", "treasure", "skillPanel", "canChallenge")},
                              snapshot=snapshot)
        except Exception as exc:
            # 不导出浏览器报错的 URL/堆栈，以免混入个人页面数据。
            self._debug_write("capture_error", error_type=type(exc).__name__)

    def _stage_record(self, record):
        # 接口回调只排队；下一次读取引擎计数后再落盘，包含通关的最后一次击杀。
        self._pending_stage_records.append(record)

    def _collect_kills(self):
        if self._page is not None and self._runtime_script_loaded:
            try:
                self._kills.consume(self._page.evaluate(JS_COMBAT_SNAPSHOT))
            except Exception:
                pass

    def _flush_stage_records(self):
        pending, self._pending_stage_records = self._pending_stage_records, []
        for record in pending:
            record.update(self._kills.for_stage(record["stage"]))
            record["kills_partial"] |= bool(record["partial"])
            self._write_stage_record(record)

    def _write_stage_record(self, record):
        obj = {"date": self._date, "version": APP_VERSION,
               "round": self.status.get("round", 0), **record}
        try:
            if self._stage_fp is None:
                path = os.path.join(LOGDIR, "stages_%s.jsonl" % self._date)
                self._stage_fp = open(path, "a", encoding="utf-8")
                self._log("单关耗时写入：%s" % path, "dim")
            self._stage_fp.write(json.dumps(obj, ensure_ascii=False) + "\n")
            self._stage_fp.flush()
        except OSError as exc:
            self._log("单关耗时写入失败：%s" % str(exc)[:100], "warn")
        self._debug_write("stage_timing", record=record)
        seconds = record["duration_s"]
        stage = record["stage"]
        count = record.get("kills")
        kills = "杀敌未采集" if count is None else "%s杀敌 %d" % (
            "已观测" if record.get("kills_partial") else "本关", count)
        if record["outcome"] == "cleared":
            label = ("中途接管，仅计观测时段" if record["partial"] else
                     "stageEnd 未带关卡号，按当前关估算" if record["end_stage_inferred"] else
                     "关卡切换估算，可能含选技能等待" if record["estimated"] else
                     "stageStart → stageEnd 响应间隔")
            self._log("第%s关通关耗时 %.1f 秒｜%s（%s）" % (stage, seconds, kills, label))
        elif record["outcome"] == "gap":
            self._log("第%s→%s关观测间隔 %.1f 秒（中间关卡数据缺失，不拆成单关耗时）"
                      % (stage, record["next_stage"], seconds), "warn")
        else:
            self._log("第%s关已观测 %.1f 秒｜%s（本场结束时未确认通关）" % (stage, seconds, kills), "dim")

    def _finish_stage_timing(self, reason):
        if self._stage_timer is not None:
            timer, self._stage_timer = self._stage_timer, None
            timer.finish(time.monotonic(), time.time(), reason)
            self._collect_kills()
            self._flush_stage_records()
            return timer.records
        return []

    # -------- 抓包 --------
    def _on_response(self, resp):
        try:
            url = resp.url
            if "/japi/revenuenc/" not in url or "/actgame/" not in url:
                return
            api = url.split("/actgame/")[-1].split("?")[0]
            received_mono, received_wall = time.monotonic(), time.time()
            try:
                body = resp.text()
            except Exception:
                body = ""
            if body:
                try:
                    j = json.loads(body)
                    if self._stage_timer is not None and 200 <= resp.status < 300:
                        self._stage_timer.on_api(api, j, received_mono, received_wall)
                    if self.debug and api != "game/stageEventReport":
                        self._debug_write("api_response", api=api, status=resp.status,
                                          monotonic_s=received_mono, stage=api_stage(j),
                                          data=safe_api_data(j))
                    # 战斗统计：宝箱领取累计
                    if api == "game/receiveBox" and isinstance(j, dict):
                        d = j.get("data") or {}
                        n = ((d.get("boxStat") or {}).get("total")) or len(d.get("list") or [])
                        with self._lock:
                            self._battle_boxes = (self._battle_boxes or 0) + int(n or 0)
                    # 服务端拒绝（如战斗超时 1042100「游戏参与异常」）
                    if api in ("game/stageEnd", "game/stageStart", "game/nextStage", "game/battleStart"):
                        err = j.get("error") if isinstance(j, dict) else None
                        try:
                            err = int(err or 0)
                        except Exception:
                            err = 0
                        if err:
                            if not self._server_reject_ts:
                                self._log("服务端对局限制：%s 返回 %s（%s）"
                                          % (api, err, str(j.get("msg") or "")[:60]), "warn")
                            self._server_reject_ts = time.time()
                    # 连续上报失败只触发收尾，不代表已经结算。
                    if api == "game/stageEventReport":
                        err2 = j.get("error") if isinstance(j, dict) else None
                        try:
                            err2 = int(err2 or 0)
                        except Exception:
                            err2 = 0
                        if err2:
                            self._report_errs += 1
                            if self._report_errs >= 3 and not self._server_reject_ts:
                                self._log("对局上报连续失败（%s），将尝试正常收尾并等待实际结算" % err2, "warn")
                                self._server_reject_ts = time.time()
                        else:
                            self._report_errs = 0
                    body = json.dumps(j, ensure_ascii=False)
                except Exception:
                    body = body[:4000]
            self._api_write({"t": time.strftime("%H:%M:%S"), "api": api, "status": resp.status, "body": body[:6000]})
        except Exception:
            pass

    def _api_write(self, obj):
        try:
            if self._api_fp is None:
                path = os.path.join(LOGDIR, "api_%s.jsonl" % self._date)
                self._api_fp = open(path, "a", encoding="utf-8")
                self._log("接口抓包写入：%s" % path)
            self._api_fp.write(json.dumps(obj, ensure_ascii=False) + "\n")
            self._api_fp.flush()
        except Exception:
            pass

    def _battle_write(self, obj):
        try:
            if self._battle_fp is None:
                path = os.path.join(LOGDIR, "battles_%s.jsonl" % self._date)
                self._battle_fp = open(path, "a", encoding="utf-8")
                self._log("战报写入：%s" % path)
            self._battle_fp.write(json.dumps(obj, ensure_ascii=False) + "\n")
            self._battle_fp.flush()
        except Exception:
            pass

    # -------- 页面动作 --------
    def _state(self):
        try:
            st = self._page.evaluate(JS_STATE)
            if self._stage_timer is not None and st and st.get("inBattle"):
                stage = ((st.get("wm") or {}).get("stage") or {}).get("curStage")
                self._stage_timer.observe(stage, time.monotonic(), time.time())
                if stage != self._debug_poll_stage:
                    self._debug_poll_stage = stage
                    self._debug_write("poll_stage_change", stage=stage, monotonic_s=time.monotonic())
            return st
        except Exception as e:
            self._log("状态读取失败：%s" % str(e)[:120], "warn")
            return None

    def _sync_status(self, st):
        try:
            wm = st.get("wm") or {}
            self.status["stage"] = (wm.get("stage") or {}).get("curStage") or self.status["stage"]
            self.status["hp"] = (wm.get("stage") or {}).get("curHp", 0)
            self.status["freeNum"] = wm.get("freeNum")
            self.status["maxStage"] = wm.get("maxStage") or self.status["maxStage"]
            self.status["weekRank"] = wm.get("weekRank") or self.status["weekRank"]
            self.status["nick"] = wm.get("nick") or self.status["nick"]
            coin = self._coin_balance(st)
            if coin is not None:
                self.status["coin"] = coin
        except Exception:
            pass

    @staticmethod
    def _coin_balance(st):
        """读取银币余额：优先接口数据，其次页面 TopBar 文本（formatUnit 可能带 万/亿）
        注意：页面数据未就绪（panelReady=false）时不可信任任何读数（DOM 可能是占位 0）"""
        try:
            wm = st.get("wm") or {}
            if not wm.get("panelReady"):
                return None
            v = wm.get("coin")
            if isinstance(v, (int, float)):
                return int(v)
        except Exception:
            pass
        try:
            dom = (st.get("coinDom") or "").replace(",", "").strip()
            if dom:
                mul = 1
                if "万" in dom:
                    mul = 10000
                elif "亿" in dom:
                    mul = 100000000
                m = re.match(r"^(\d+(?:\.\d+)?)", dom)
                if m:
                    return int(float(m.group(1)) * mul)
        except Exception:
            pass
        return None

    def _gate_challenge(self, st, wait_s=25):
        """开局闸门：免费 → 直接开；免费用尽 → 银币够则开（银币免费获取），否则停止。
        内部等待页面数据就绪（防止刷新后误判资源）。
        返回 (可开局: bool, 原因/说明: str)"""
        t0 = time.time()
        while True:
            wm = st.get("wm") or {}
            txt = st.get("challengeTxt") or ""
            hydrated = bool(wm.get("panelReady")) and wm.get("freeNum") is not None
            btn_ready = ("关" in txt) or bool(re.search(r"\d", txt))
            if hydrated and btn_ready:
                break
            if self._stop.is_set() or (time.time() - t0) > wait_s:
                return False, "等待游戏数据加载超时（页面未就绪），停止"
            time.sleep(1.0)
            st2 = self._state()
            if st2:
                st = st2
        txt = st.get("challengeTxt") or ""
        if any(k in txt for k in ("鱼翅", "送出礼物", "充值", "付费", "支付", "礼物")):
            return False, "挑战按钮疑似付费入口（%s），为安全起见停止" % txt[:80]
        if "关" in txt:                      # 免费局（副标题 第N关）
            self._battle_paid = "free"
            return True, txt
        # 免费用尽：解析按钮上的银币价（默认 10）
        nums = re.findall(r"\d+", txt)
        cost = int(nums[-1]) if nums else 10
        if not self.coin_play:
            return False, "免费次数已用尽（按钮显示：%s），未开启银币续打，停止" % (txt or "?")
        coin = self._coin_balance(st)
        if coin is None:
            return False, "免费次数已用尽，且无法读取银币余额，为安全起见停止"
        if coin < cost:
            return False, "免费次数与银币均不足（银币余额 %d < %d），停止" % (coin, cost)
        self._battle_paid = "coin"
        return True, "免费次数已用尽 → 消耗 %d 银币开启本场（银币余额 %d）" % (cost, coin)

    def _try_start_battle(self):
        """尝试开启一场并进入战场（数据就绪等待 + 点击重试 + 面板处理）。
        返回 (ok, info)：info = {"skills": [...]}"""
        info = {"skills": []}
        # a) 已在对局/面板中 → 直接进入
        st = self._state() or {}
        if st.get("inBattle") or st.get("skillPanel") or st.get("finalSkillList"):
            self._apply_runtime_controls()
            self._log("检测到战斗进行中，继续监控")
            return True, info
        if self._pause_requested and self._is_lobby(st):
            if not self._wait_if_paused():
                return False, info
            # 暂停期间用户可能手动开始了战斗，恢复时重新识别。
            return self._try_start_battle()
        self._apply_runtime_controls()

        # b) 门闸（内部会等待页面数据就绪）
        if not st.get("canChallenge"):
            self._log("未找到「开启挑战」按钮，尝试恢复页面…", "warn")
            if self._try_recover():
                st = self._state() or {}
        ok, why = self._gate_challenge(st)
        if not ok:
            self._log(why + "。", "warn")
            return False, info
        if self._pause_requested:
            if not self._wait_if_paused():
                return False, info
            return self._try_start_battle()
        if self._stop.is_set():
            return False, info
        r = self._click(JS_CLICK_CHALLENGE)
        if isinstance(r, dict) and not r.get("ok"):
            self._log("已拦截疑似付费挑战按钮：%s" % (r.get("text") or r.get("why") or "未知"), "warn")
            return False, info
        self._log("已点击「开启挑战」（%s%s）"
                  % (why, "，本场消耗银币" if self._battle_paid == "coin" else ""))
        if not r:
            return False, info
        # 标记本场开始时间，回血守护按用户设置的单场时限收手。
        try:
            self._page.evaluate("() => { if (window.__GF) { window.__GF.battleT0 = Date.now(); window.__GF.capped = false; window.__GF.oneHitKills = 0; } }")
        except Exception:
            pass
        last_click = time.time()
        clicks = 1

        # c) 开局等待：处理面板/弹窗；仍停在主页面则重试点击（点击可能落在按钮就绪前）
        #    超时用「无进展窗口」而非固定总时长：技能三选一/确认列表变化都算进展，
        #    避免技能多、面板刷新慢时被固定 90 秒误判失败。
        t0 = time.time()
        last_progress = t0
        last_final_txt = None
        last_final_click = 0.0
        final_fail_why = ""
        while True:
            if self._stop.is_set():
                return False, info
            if time.time() - t0 > PRE_BATTLE_HARD_LIMIT:
                self._log("开局超时（已达绝对上限 %ds）" % PRE_BATTLE_HARD_LIMIT, "warn")
                break
            if time.time() - last_progress > PRE_BATTLE_LIMIT:
                if last_final_txt is not None:
                    self._log("开局超时：技能确认列表停在本页 %ds 未能开始（%s）"
                              % (PRE_BATTLE_LIMIT, final_fail_why or "原因未知"), "warn")
                else:
                    self._log("开局超时：%ds 内无任何进展" % PRE_BATTLE_LIMIT, "warn")
                break
            time.sleep(0.8)
            st = self._state()
            if not st:
                continue
            self._sync_status(st)

            if st.get("limitAlter"):
                self._click(JS_CLOSE_LIMIT_ALTER)
                self._log("卡组不足提示：需要 9 张出战卡（请到编队里配满）", "warn")
                last_progress = time.time()
                time.sleep(1)
                continue

            cards = st.get("skillPanel")
            if cards:
                texts = [c.get("txt", "") for c in cards]
                scores = [score_skill_text(t) for t in texts]
                best = scores.index(max(scores))
                self._log("前置技能三选一 → 选 #%d（%d分）：%s" % (best + 1, max(scores), texts[best][:70]))
                got = self._click(JS_CLICK_SKILL, best)
                if got:
                    info["skills"].append({"text": texts[best][:80], "score": max(scores)})
                last_progress = time.time()
                time.sleep(2.0)
                continue

            if st.get("finalSkillList"):
                txt = st.get("finalSkillList") or ""
                if txt != last_final_txt:
                    last_final_txt = txt
                    last_progress = time.time()
                    self._log("技能确认列表：%s" % txt[:120])
                # 点击「开始战斗」：带冷却，避免每 0.8s 狂点；并校验返回结果
                if time.time() - last_final_click > 3.0:
                    last_final_click = time.time()
                    r = self._click(JS_CLICK_FINAL_START)
                    if isinstance(r, dict):
                        if r.get("ok"):
                            self._log("已点击开始战斗（%s · %s）" % (r.get("text") or "", r.get("how") or ""))
                            last_progress = time.time()
                        else:
                            final_fail_why = str(r.get("why") or "?")
                            seen = r.get("seen") or []
                            self._log("技能确认列表未能开始：%s；面板=%s；候选=%s"
                                      % (final_fail_why, (r.get("panelCls") or "")[:60], seen),
                                      "warn")
                    else:
                        final_fail_why = "no-skillList"
                        self._log("技能确认列表未能开始：页面未找到 Starter-module_skillList", "warn")
                time.sleep(1.0)
                continue

            if st.get("inBattle"):
                return True, info
            if st.get("result") or st.get("treasure"):
                self._log("战斗未开始即出现结算？", "warn")
                self._click(JS_RESULT_BTN)
                time.sleep(1.5)
                continue

            # 仍停在主页面：点击可能落空（按钮未就绪/被拦截）→ 重试
            if st.get("canChallenge") and (time.time() - last_click) > 15 and clicks < 6:
                if self._pause_requested and self._is_lobby(st):
                    if not self._wait_if_paused():
                        return False, info
                    return self._try_start_battle()
                r2 = self._click(JS_CLICK_CHALLENGE)
                if r2:
                    clicks += 1
                    last_click = time.time()
                    last_progress = time.time()
                    self._log("再次点击「开启挑战」（重试第 %d 次）" % clicks)

        return False, info

    def _goto_game(self):
        self._log("打开游戏页：房间 %s" % self.rid)
        # 页面导航会清掉动态引擎模块；下一步按当前开关决定是否重新装载。
        self._runtime_script_loaded = False
        self._runtime_applied = None
        try:
            self._page.goto(GAME_URL.format(rid=self.rid), wait_until="domcontentloaded", timeout=90000)
        except Exception as e:
            self._log("页面加载异常：%s" % str(e)[:150], "warn")
        # 兜底：确保 hook 存在（add_init_script 通常已注入）
        try:
            has = self._page.evaluate("() => !!window.__WM")
            if not has:
                self._page.evaluate(JS_HOOK)
        except Exception:
            pass
        self._apply_hp_config()
        self._wait_ready(60)
        # 等 panelInfo 真正返回后再判断登录态
        t0 = time.time()
        code = None
        while time.time() - t0 < 45:
            if self._stop.is_set():
                return False
            try:
                info = self._page.evaluate(
                    "() => window.__WM && window.__WM.panelInfo ? {code: window.__WM.panelInfo.code, "
                    "nick: (window.__WM.panelInfo.data && window.__WM.panelInfo.data.userInfo && "
                    "window.__WM.panelInfo.data.userInfo.nn) || ''} : null")
            except Exception:
                info = None
            if info:
                code = info.get("code")
                if info.get("nick"):
                    self.status["nick"] = info["nick"]
                    self._log("登录账号：%s" % info["nick"])
                    return True
                if code == 1002:
                    break
            time.sleep(1)
        st = self._state()
        if st:
            self._sync_status(st)
            if self.status.get("nick"):
                self._log("登录账号：%s" % self.status["nick"])
                return True
        self._log("似乎未登录（panelInfo code=%s）。请点「登录斗鱼」完成登录后再挂机。" % code, "error")
        return False

    def _wait_ready(self, timeout):
        """等待页面出现主界面按钮或战场（数据就绪才算数，防止刷新后过早操作）"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self._stop.is_set():
                return False
            st = self._state()
            if st:
                if st.get("canChallenge"):
                    wm = st.get("wm") or {}
                    txt = st.get("challengeTxt") or ""
                    if wm.get("panelReady") and (("关" in txt) or re.search(r"\d", txt)):
                        return True
                elif st.get("inBattle") or st.get("result") or st.get("treasure") \
                        or st.get("skillPanel") or st.get("finalSkillList"):
                    return True
            time.sleep(0.8)
        return False

    def _click(self, js, *args):
        try:
            return self._page.evaluate(js, args[0]) if args else self._page.evaluate(js)
        except Exception as e:
            self._log("点击执行失败：%s" % str(e)[:120], "warn")
            return None

    # -------- 主流程 --------
    def start(self):
        if self._thread and self._thread.is_alive():
            self._log("已在运行中", "warn")
            return
        self._stop.clear()
        with self._control_lock:
            self._natural_end_requested = False
            self._natural_end_sent = False
            self._natural_end_reason = ""
            self._runtime_pending = True
            self._runtime_applied = None
            self._pause_requested = False
            self._paused = False
        self.rounds_done = 0
        self._recovery_attempts = 0
        self.status["rounds_done"] = 0
        self._thread = threading.Thread(target=self._run, name="guardfish-bot", daemon=True)
        self._thread.start()

    def _run(self):
        ensure_dirs()
        self._debug_write("run_start", hp_guard=self.hp_guard, one_hit=self.one_hit,
                          one_hit_mode=self.one_hit_mode, one_hit_probability=self.one_hit_probability,
                          attack_multiplier=self.attack_multiplier, rounds=self.rounds,
                          stop_stage=self.stop_stage, advance_stages=self.advance_stages,
                          max_battle_seconds=self.max_battle_seconds)
        try:
            self._ensure_browser()
            if not self._goto_game():
                self.status["state"] = "未登录"
                return

            while True:
                if self._pause_requested and self._is_lobby(self._state() or {}):
                    if not self._wait_if_paused():
                        break
                i = self.rounds_done + 1
                if self.rounds > 0 and i > self.rounds:
                    break
                if self._stop.is_set():
                    break
                self.status["round"] = i
                r = self._one_battle(i)
                if r is None:
                    self._log("第%d场未能完成，停止。" % i, "error")
                    break
                completed = r.get("completed", True)
                if completed:
                    self.rounds_done += 1
                    self._recovery_attempts = 0
                self.status["rounds_done"] = self.rounds_done
                self._battle_write({
                    "date": self._date, "round": i,
                    "paid": r.get("paid"),
                    "start": r.get("t0"), "end": r.get("t1"), "dur_s": r.get("dur"),
                    "stage0": r.get("stage0"), "stageN": r.get("stageN"),
                    "freeNum": r.get("freeNum"), "boxes": r.get("boxes"),
                    "result": r.get("resultText"), "skills": r.get("skills"),
                    "refills": r.get("refills"), "hpMin": r.get("hpMin"),
                    "oneHitKills": r.get("oneHitKills"),
                    "kills": r.get("kills"), "completed": completed,
                    "ended": r.get("ended"),
                    "stageTimes": r.get("stageTimes", []),
                })
                self._log("战报：起始第%s关 → 到达第%s关，用时 %.0f 秒，回血 %s 次，%s%s"
                          % (r.get("stage0"), r.get("stageN"), r.get("dur") or 0,
                             r.get("refills") if r.get("refills") is not None else "-",
                             "（银币局）" if r.get("paid") == "coin" else "",
                             ("结算：" + (r.get("resultText") or "")) if r.get("resultText") else ""))
                if self._stop.is_set():
                    break
                if not completed:
                    self._log("本次中断未计入完成场数，继续第 %d 场任务。" % i, "warn")
                if self.rounds <= 0 or i < self.rounds:
                    self._sleep_gap()

            self.status["state"] = "已结束" if not self._stop.is_set() else "已停止"
            self._log("运行结束（完成 %d 场）" % self.rounds_done)
        except Exception as e:
            self._log("异常终止：%s" % str(e)[:300], "error")
            self._log(traceback.format_exc()[-800:], "error")
            self.status["state"] = "异常"
        finally:
            self._finish_stage_timing("运行终止")
            self._debug_write("run_end", state=self.status.get("state"))
            self._battle_active = False
            self._paused = False
            try:
                self._close_browser()
            except Exception:
                pass
            try:
                self._shutdown_pw()
            except Exception:
                pass
            for attr in ("_api_fp", "_battle_fp", "_stage_fp", "_debug_recorder"):
                writer = getattr(self, attr)
                if writer is not None:
                    try:
                        writer.close()
                    except Exception:
                        pass
                    setattr(self, attr, None)

    def _sleep_gap(self):
        if self._pause_requested:
            return
        lo, hi = min(self.gap_min, self.gap_max), max(self.gap_min, self.gap_max)
        if hi <= 0:
            return
        sec = random.uniform(max(lo, 1), max(hi, 2))
        self._log("场间等待 %.0f 秒…" % sec)
        t0 = time.time()
        while time.time() - t0 < sec:
            if self._stop.is_set() or self._pause_requested:
                return
            time.sleep(0.5)

    def _stage_target_for(self, stage0):
        targets = []
        if self.stop_stage > 0:
            targets.append(self.stop_stage)
        if self.advance_stages > 0 and stage0 is not None:
            # “推进 N 关”表示在本场起始关基础上再推进 N 个关卡。
            targets.append(int(stage0) + self.advance_stages)
        return max(targets) if targets else None

    @staticmethod
    def _is_lobby(st):
        return bool(st.get("canChallenge") and not any(
            st.get(k) for k in ("inBattle", "skillPanel", "finalSkillList", "result", "treasure")))

    def _one_battle(self, idx):
        """打一场，返回统计 dict 或 None"""
        # 自然结束只属于上一场；若不清理，下一场会刚进入战斗就再次被降到 1 血。
        self._reset_battle_controls()
        self._battle_active = False
        self._debug_last_capture = None
        self._debug_last_stage = None
        self._debug_poll_stage = None
        self._debug_sources_collected = False
        self.status["state"] = "准备开局"
        self._stage_target = None
        self._stage_stop_announced = False
        self._one_hit_kills = 0
        self._last_runtime_kills = 0
        self._kills = KillStats()
        self._pending_stage_records = []
        with self._lock:
            self._battle_boxes = 0
        self._server_reject_ts = 0
        self._report_errs = 0
        self._log("========== 第 %d%s 场 =========="
                  % (idx, ("/%d" % self.rounds) if self.rounds > 0 else ""))

        # 0) 清理可能残留的弹窗
        for _ in range(3):
            st = self._state()
            if not st:
                return None
            if st.get("result") or st.get("treasure"):
                r = self._click(JS_RESULT_BTN)
                self._log("清理残留弹窗：%s" % r)
                time.sleep(1.2)
                continue
            if st.get("finalSkillList"):
                r = self._click(JS_CLICK_FINAL_START)
                if isinstance(r, dict) and r.get("ok"):
                    self._log("残留的技能确认列表 → 已点开始战斗（%s）" % (r.get("text") or ""))
                else:
                    self._log("残留的技能确认列表 → 未能开始（%s）"
                              % ((r or {}).get("why") if isinstance(r, dict) else "no-skillList"), "warn")
                time.sleep(1.5)
                continue
            break

        st = self._state()
        if not st:
            return None

        # 1+2) 开局（含数据就绪等待、点击重试；失败自动恢复后重试一次）
        stage0 = None
        self._stage_timer = StageTimer(self._stage_record)
        started, info = self._try_start_battle()
        skills_chosen = list(info.get("skills") or [])
        if not started:
            if self._stop.is_set():
                return None
            self._log("开局未成功，刷新页面后重试一次…", "warn")
            self._snap("pre_battle_retry")
            try:
                self._runtime_script_loaded = False
                self._runtime_applied = None
                self._page.reload(wait_until="domcontentloaded", timeout=60000)
                self._wait_ready(60)
                self._apply_hp_config()
            except Exception as e:
                self._log("刷新失败：%s" % str(e)[:120], "error")
            started, info2 = self._try_start_battle()
            if not started:
                self._log("开局再次失败（未能进入战场），停止。", "error")
                self._snap("pre_battle_timeout")
                return None
            skills_chosen = list(info2.get("skills") or [])

        # 3) 战斗循环
        self.status["state"] = "战斗中"
        self._battle_active = True
        t_battle0 = time.time()
        ended = None
        completed = True
        result_text = ""
        boxes = None
        last_hud = 0
        last_claim = 0
        snap_done = False
        hp_timeline = []
        hp_min = None
        hp_refill_count = 0
        hp_found_logged = False
        activity_watch = ActivityWatch()
        end_started = None
        try:
            self._page.evaluate(JS_HP_DRAIN)      # 清掉上一场的残留事件
        except Exception:
            pass
        while True:
            if self._stop.is_set():
                return None
            # 运行中的开关和自然结束请求都在机器人线程执行，GUI 点击会在这里生效。
            elapsed = time.time() - t_battle0
            if self._pause_requested:
                self.request_natural_end("暂时停止")
            with self._control_lock:
                natural_requested = bool(self._natural_end_requested)
            if end_started is None and not natural_requested and elapsed >= self.max_battle_seconds:
                self.request_natural_end("达到单场监控时限 %.1f 分钟" % (self.max_battle_seconds / 60.0))
                natural_requested = True
            self._apply_runtime_controls()
            if end_started is None and natural_requested:
                end_started = time.time()
                self.status["state"] = "等待结算"
                self._log("进入自然结束宽限期（最多 %d 秒）" % END_GRACE_SECONDS, "warn")
            time.sleep(TICK)
            st = self._state()
            if not st and (end_started is None or time.time() - end_started <= END_GRACE_SECONDS):
                continue
            st = st or {}
            self._sync_status(st)
            self._debug_capture(st)
            wm = st.get("wm") or {}
            stage = (wm.get("stage") or {})
            if stage0 is None and stage.get("curStage"):
                stage0 = stage.get("curStage")
                self._stage_target = self._stage_target_for(stage0)
                if self._stage_target is not None:
                    self._log("本场关卡结束目标：第 %s 关（绝对目标=%s，推进=%s）"
                              % (self._stage_target, self.stop_stage or "关",
                                 self.advance_stages or "关"))
            cur_stage = stage.get("curStage")
            try:
                cur_stage_num = int(cur_stage) if cur_stage is not None else None
            except (TypeError, ValueError):
                cur_stage_num = None
            if (self._stage_target is not None and cur_stage_num is not None
                    and cur_stage_num >= int(self._stage_target)
                    and not self._stage_stop_announced):
                self._stage_stop_announced = True
                self.request_natural_end("达到第%s关目标" % cur_stage_num)

            # 血条守护：回收事件（回血/掉血）
            try:
                gf = self._page.evaluate(JS_HP_DRAIN)
            except Exception:
                gf = None
            if gf:
                if gf.get("found") and not hp_found_logged:
                    hp_found_logged = True
                    modes = "回血守护：%s｜秒杀模式：%s" % (
                        "开" if gf.get("hpGuard") else "关",
                        (one_hit_mode_text(gf.get("oneHitMode"),
                                           gf.get("oneHitProbability", 50),
                                           gf.get("attackMultiplier", 2))
                         if gf.get("oneHit") else "关"))
                    self._log("战斗引擎已识别（%s）｜%s" %
                              (gf.get("comp") or "engine", modes),
                              "dim" if not gf.get("hpGuard") else "info")
                if gf.get("err"):
                    self._log("回血守护异常：%s" % str(gf.get("err"))[:120], "warn")
                if gf.get("oneHitKills") is not None:
                    runtime_kills = int(gf.get("oneHitKills") or 0)
                    self._one_hit_kills += (runtime_kills - self._last_runtime_kills
                                           if runtime_kills >= self._last_runtime_kills else runtime_kills)
                    self._last_runtime_kills = runtime_kills
                    self.status["one_hit_kills"] = self._one_hit_kills
                for ev in (gf.get("events") or []):
                    if ev.get("type") == "refill":
                        hp_refill_count += 1
                        self._log("回血触发：血量 %s → %s（第 %d 次）"
                                  % (ev.get("from"), ev.get("to"), hp_refill_count))
                        if len(hp_timeline) < 500:
                            hp_timeline.append({"t": int(time.time() - t_battle0), "ev": "refill",
                                                "from": ev.get("from"), "to": ev.get("to")})
                    elif ev.get("type") == "damage":
                        v = ev.get("to")
                        if isinstance(v, (int, float)):
                            hp_min = v if hp_min is None else min(hp_min, v)
                        if len(hp_timeline) < 500:
                            hp_timeline.append({"t": int(time.time() - t_battle0), "ev": "hp", "to": v})
                    elif ev.get("type") == "cap":
                        self._log("回血守护：本场已到 %s 分钟，停止回血让战斗自然结束（避免服务端超时判异常）"
                                  % ev.get("mins", 9))

            combat = (gf or {}).get("combat")
            self._kills.consume(combat)
            self._flush_stage_records()
            self.status["kills"] = self._kills.total if self._kills.stages else None
            suspended = (not st.get("inBattle") or self._natural_end_requested or any(
                st.get(k) for k in ("skillPanel", "result", "treasure", "finalSkillList")))
            stalled = activity_watch.observe(combat, time.monotonic(), suspended=suspended)
            if activity_watch.progressed:
                self._recovery_attempts = 0
            stuck_by_server = self._server_reject_ts and (time.time() - self._server_reject_ts > 15)
            if not suspended:
                if stuck_by_server:
                    self.request_natural_end("服务端拒绝对局请求，尝试正常收尾")
                elif stalled:
                    self._log("连续 4 分钟未发射弹丸或击杀敌人，刷新页面恢复任务；不将本次刷新计为完成。", "warn")
                    recovered = self._recover_stalled_battle()
                    activity_watch = ActivityWatch()
                    if recovered == "lobby":
                        ended, completed = "停滞中断", False
                        break
                    if recovered == "stop":
                        return None
                    continue

            # 记录 HUD 结构一次（供诊断）
            if st.get("inBattle") and not snap_done:
                snap_done = True
                try:
                    html = self._page.evaluate(JS_HUD_HTML)
                    if html:
                        with open(os.path.join(LOGDIR, "hud_sample.html"), "w", encoding="utf-8") as f:
                            f.write(html)
                except Exception:
                    pass

            el = int(time.time() - t_battle0)
            self.status["refills"] = hp_refill_count
            engine_hp = ((gf or {}).get("hp") or {}).get("cur")
            shown_hp = engine_hp if isinstance(engine_hp, (int, float)) else stage.get("curHp", "?")
            self.status["hp"] = shown_hp
            if el - last_hud >= 60:
                last_hud = el
                self._log("进行中 t=%ss 第%s关 HP=%s 免费剩余=%s 回血=%d%s｜杀敌=%s"
                          % (el, stage.get("curStage", "?"), shown_hp,
                             wm.get("freeNum", "?"), hp_refill_count,
                             (" 秒杀=%d" % self._one_hit_kills) if self.one_hit else "",
                             self.status["kills"] if self.status["kills"] is not None else "未采集"))

            # 3.1 结算（顶部进度宝箱也可能使用 Result-module；必须确认战斗已结束）
            if st.get("result") or st.get("treasure"):
                modal_result = bool(st.get("result"))
                candidate_result_text = self._click(JS_RESULT_TEXT) if modal_result else ""
                r = self._click(JS_RESULT_BTN)
                self._log("%s弹窗 → %s %s" %
                          ("结算候选" if modal_result else "奖励",
                           r, ("｜" + candidate_result_text[:120]) if candidate_result_text else ""))
                time.sleep(2.0)
                st2 = self._state() or {}
                if st2.get("treasure"):
                    r2 = self._click(JS_RESULT_BTN)
                    self._log("奖励弹窗 → %s" % r2)
                    time.sleep(1.5)
                st3 = self._state() or {}
                wm3 = st3.get("wm") or {}
                battle_over_after = bool(wm3.get("battleOver"))
                returned_to_lobby = bool(st3.get("canChallenge"))
                actual_result = bool(modal_result and
                                     (battle_over_after or returned_to_lobby))
                if actual_result:
                    result_text = candidate_result_text or ""
                    if self._is_lobby(st3):
                        ended = "结算完成"
                        break
                if modal_result and not actual_result:
                    self._log("奖励弹窗已关闭，战斗仍在继续，忽略本次弹窗作为结束信号。", "dim")
                st = st3

            if self._is_lobby(st):
                self._log("战斗已结束（回到主界面）", "warn")
                ended = "结算完成" if result_text else "提前结束"
                break

            if end_started is not None and time.time() - end_started > END_GRACE_SECONDS:
                if not self.pause_on_timeout:
                    self._log("自然结束等待已达 %d 秒仍未结算，停止本次命令行运行；本场不计入完成场数。"
                              % END_GRACE_SECONDS, "error")
                    return None
                self._log("自然结束等待已达 %d 秒，尚未确认结算：暂停助手并保留本场，不计场、不刷新；"
                          "点击「恢复战斗」后重新识别当前战斗或主界面。" % END_GRACE_SECONDS, "warn")
                with self._control_lock:
                    self._pause_requested = True
                if not self._wait_if_paused(unfinished=True):
                    return None
                # 在原战斗循环继续，不重置起始关/推进目标、技能、战报和累计计数。
                end_started = time.time()
                activity_watch = ActivityWatch()
                continue

            if st.get("finalSkillList"):
                result = self._click(JS_CLICK_FINAL_START)
                if isinstance(result, dict) and result.get("ok"):
                    self._log("技能确认列表 → 已点击开始战斗，继续当前场监控")
                time.sleep(1.0)
                continue

            # 3.2 关卡技能三选一
            cards = st.get("skillPanel")
            if cards:
                texts = [c.get("txt", "") for c in cards]
                scores = [score_skill_text(t) for t in texts]
                best = scores.index(max(scores))
                self._log("第%s关技能三选一 → 选 #%d（%d分）：%s"
                          % (stage.get("curStage", "?"), best + 1, max(scores), texts[best][:70]))
                got = self._click(JS_CLICK_SKILL, best)
                if got:
                    skills_chosen.append({"text": texts[best][:80], "score": max(scores)})
                time.sleep(2.2)
                continue

            # 3.3 每周宝箱领取（免费）
            if self.claim_weekpass and st.get("hudClaimable") and time.time() - last_claim > 25:
                last_claim = time.time()
                r = self._click(JS_HUD_CLAIM)
                if r:
                    self._log("领取战斗宝箱")
                    time.sleep(1.5)
                    st2 = self._state() or {}
                    if st2.get("treasure"):
                        self._click(JS_RESULT_BTN)
                        self._log("宝箱奖励弹窗已关闭")
                        time.sleep(1.0)


        dur = time.time() - t_battle0
        self._battle_active = False
        stage_times = self._finish_stage_timing(ended or "监控结束")
        self._debug_capture(self._state() or {}, force=True)
        self._debug_write("battle_end", ended=ended, duration_s=dur)
        if ended is None:
            ended = "超时未结算"
            self._log("单场监控结束但未检测到结算，停止继续开新场。", "error")

        # 3.5 结束清理（可能还有弹窗）
        for _ in range(4):
            st = self._state() or {}
            if st.get("result") or st.get("treasure"):
                self._click(JS_RESULT_BTN)
                time.sleep(1.5)
            else:
                break

        final = self._state() or {}
        fwm = (final.get("wm") or {})
        fstage = (fwm.get("stage") or {})
        with self._lock:
            boxes = self._battle_boxes
        return {
            "t0": time.strftime("%H:%M:%S", time.localtime(t_battle0)),
            "t1": time.strftime("%H:%M:%S"),
            "dur": dur,
            "stage0": stage0,
            "stageN": fstage.get("curStage") or self.status.get("stage"),
            "freeNum": fwm.get("freeNum"),
            "boxes": boxes,
            "paid": self._battle_paid,
            "resultText": result_text,
            "skills": skills_chosen,
            "oneHitKills": self._one_hit_kills,
            "kills": self._kills.total if self._kills.stages else None,
            "completed": completed,
            "refills": hp_refill_count,
            "hpMin": hp_min,
            "hpTimeline": hp_timeline,
            "ended": ended,
            "stageTimes": stage_times,
        }

    def _recover_stalled_battle(self):
        """仅看门狗超时后使用；接回战斗保留当前场，回到大厅则记为中断重试。"""
        self._recovery_attempts += 1
        self._collect_kills()
        self._flush_stage_records()
        if self._recovery_attempts <= 3:
            try:
                self._snap("battle_inactive")
                self._runtime_script_loaded = False
                self._runtime_applied = None
                self._page.reload(wait_until="domcontentloaded", timeout=60000)
                self._wait_ready(60)
                self._apply_runtime_controls(force=True)
                st = self._state() or {}
                if self._is_lobby(st):
                    return "lobby"
                if any(st.get(k) for k in ("inBattle", "skillPanel", "finalSkillList", "result", "treasure")):
                    self._log("页面恢复，继续识别当前场；起始关与关卡目标保持不变。")
                    return "battle"
            except Exception as exc:
                self._log("停滞恢复失败：%s" % str(exc)[:120], "warn")
        if not self.pause_on_timeout:
            return "stop"
        self._log("自动恢复未能继续，暂停助手并保留当前任务，点击「恢复战斗」后重试。", "warn")
        with self._control_lock:
            self._pause_requested = True
        if not self._wait_if_paused(unfinished=True):
            return "stop"
        self._recovery_attempts = 0
        return "lobby" if self._is_lobby(self._state() or {}) else "battle"

    def _try_recover(self):
        """主页面卡住时的恢复：刷新页面（仅在非战斗中调用）"""
        try:
            self._log("刷新页面恢复…", "warn")
            self._runtime_script_loaded = False
            self._runtime_applied = None
            self._page.reload(wait_until="domcontentloaded", timeout=60000)
            ok = self._wait_ready(60)
            try:
                self._apply_hp_config()
            except Exception:
                pass
            return ok
        except Exception as e:
            self._log("刷新失败：%s" % str(e)[:120], "error")
            return False

    def _snap(self, tag):
        try:
            fp = os.path.join(LOGDIR, "err_%s_%s.png" % (tag, time.strftime("%H%M%S")))
            self._page.screenshot(path=fp)
            self._log("截图：%s" % fp)
        except Exception:
            pass


# ---------------------------------------------------------------- 登录
def open_login(rid=DEFAULT_RID):
    """拉起持久化浏览器到斗鱼首页，供用户手动登录；关闭窗口后返回"""
    ensure_dirs()
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError
    kill_stale_chrome(PROFILE)
    clean_profile_locks(PROFILE)
    log_msg("打开浏览器，请完成登录…")
    with sync_playwright() as pw:
        ctx, exe = launch_persistent(pw, PROFILE, headless=False)
        log_msg("浏览器：%s" % exe)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass
        t0 = time.time()
        while time.time() - t0 < 600:
            try:
                ctx.wait_for_event("close", timeout=5000)
                break
            except PWTimeoutError:
                continue
            except Exception:
                break
        try:
            ctx.close()
        except Exception:
            pass
    log_msg("登录窗口已关闭。登录态保存在：%s" % PROFILE)


# ---------------------------------------------------------------- selftest
def selftest():
    ensure_dirs()
    selftest_path = os.path.join(DATA_DIR, "selftest.txt")
    lines = []

    def emit(s):
        lines.append(str(s))
        try:
            with open(selftest_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception:
            pass
        log_msg(s)

    emit("selftest @ %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    emit("APP_DIR=%s" % APP_DIR)
    emit("DATA_DIR=%s" % DATA_DIR)
    emit("PROFILE=%s" % PROFILE)
    try:
        cands = chrome_candidates()
        emit("浏览器候选(%d):" % len(cands))
        for p, r, n in cands:
            emit("  rank=%d %s | %s" % (r, n, p))
    except Exception as e:
        emit("chrome_candidates FAIL: %s" % str(e)[:300])
    emit("import playwright ...")
    try:
        from playwright.sync_api import sync_playwright
        emit("playwright import: OK")
        emit("sync_playwright().start() ...")
        with sync_playwright() as pw:
            emit("driver started")
            ctx, exe = launch_persistent(pw, PROFILE, headless=True)
            emit("launch_persistent: OK (%s)" % exe)
            p = ctx.pages[0] if ctx.pages else ctx.new_page()
            p.goto("about:blank", timeout=15000)
            emit("about:blank: OK")
            ctx.close()
            emit("selftest: ALL OK")
    except Exception as e:
        emit("playwright/launch FAIL: %s" % str(e)[:400])
        emit(traceback.format_exc()[-600:])
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI
def main(default_debug=False):
    import argparse
    ap = argparse.ArgumentParser(description=APP_NAME)
    ap.add_argument("--run", action="store_true", help="命令行挂机")
    ap.add_argument("--login", action="store_true", help="打开浏览器登录斗鱼")
    ap.add_argument("--selftest", action="store_true", help="自检（driver/浏览器）")
    ap.add_argument("--debug", action="store_true", default=default_debug,
                    help="生成 logs/debug_*.jsonl 引擎与关卡诊断文件")
    ap.add_argument("--rid", default=DEFAULT_RID, help="房间号（默认 %s）" % DEFAULT_RID)
    ap.add_argument("--rounds", type=int, default=10, help="场数（0=不限，打完免费次数后银币够就继续）")
    ap.add_argument("--gap-min", type=float, default=5, help="场间最小间隔秒")
    ap.add_argument("--gap-max", type=float, default=15, help="场间最大间隔秒")
    ap.add_argument("--headless", action="store_true", help="无头模式")
    ap.add_argument("--no-claim", action="store_true", help="不自动领取战斗宝箱")
    ap.add_argument("--no-hp", action="store_true", help="关闭「血量低于阈值自动回满」")
    ap.add_argument("--hp-threshold", type=int, default=5, help="回血阈值（血量低于该值回满，默认 5）")
    ap.add_argument("--no-coin", action="store_true", help="免费用尽后不用银币续打（默认会用，银币为免费获取）")
    ap.add_argument("--one-hit", action="store_true", help="秒杀模式：我方卡牌伤害直接击杀怪物")
    ap.add_argument("--one-hit-mode", choices=("full", "probability", "multiplier"), default="full",
                    help="秒杀策略：full=全额，probability=概率，multiplier=攻击倍率")
    ap.add_argument("--one-hit-probability", type=float, default=50.0,
                    help="概率秒杀触发概率（0~100，默认 50）")
    ap.add_argument("--attack-multiplier", type=float, default=2.0,
                    help="攻击倍率策略的伤害倍数（默认 2）")
    speed_group = ap.add_mutually_exclusive_group()
    speed_group.add_argument("--attack-speed-multiplier", type=float,
                             help="独立攻速倍率；-2=原速的 1/2，0=原速")
    speed_group.add_argument("--attack-interval", type=float,
                             help="固定攻击间隔（游戏秒），自动限制到 0.2~180")
    ap.add_argument("--repeat-ice", action="store_true", help="允许同一敌人本关重复冰冻")
    ap.add_argument("--repeat-earth", action="store_true", help="允许同一敌人本关重复减速/眩晕")
    for effect in EFFECTS:
        chance_group = ap.add_mutually_exclusive_group()
        chance_group.add_argument("--%s-probability" % effect, type=float,
                                  help="%s固定概率（百分比）" % EFFECT_LABELS[effect])
        chance_group.add_argument("--%s-probability-multiplier" % effect, type=float,
                                  help="%s原生概率的倍率" % EFFECT_LABELS[effect])
        ap.add_argument("--%s-cd" % effect, type=float,
                        help="%s固定 CD（游戏秒），自动限制到允许范围" % EFFECT_LABELS[effect])
    ap.add_argument("--max-battle-minutes", type=float, default=MAX_BATTLE_SECONDS / 60,
                    help="单场最长监控时间（分钟，默认 13）")
    ap.add_argument("--stop-stage", type=int, default=0,
                    help="达到指定绝对关卡后自然结束（0=关闭）")
    ap.add_argument("--advance-stages", type=int, default=0,
                    help="从本场起始关推进指定关卡数后自然结束（0=关闭）")
    args = ap.parse_args()
    combat = default_controls()
    for field, value in (("multiplier", args.attack_speed_multiplier), ("fixed", args.attack_interval)):
        if value is not None:
            combat["attack_speed"].update(enabled=True, mode="multiplier" if field == "multiplier" else "fixed")
            combat["attack_speed"][field] = value
    combat.update(repeat_ice=args.repeat_ice, repeat_earth=args.repeat_earth)
    for effect in EFFECTS:
        rule = combat["effects"][effect]
        for suffix, field in (("probability", "probability"), ("probability_multiplier", "multiplier"), ("cd", "cd")):
            value = getattr(args, effect + "_" + suffix)
            if value is not None:
                rule["enabled"] = True
                rule[field] = value
                if field != "cd":
                    rule["mode"] = "fixed" if field == "probability" else "multiplier"
    combat = normalize_controls(combat)

    if not 0 <= args.one_hit_probability <= 100:
        ap.error("--one-hit-probability 必须在 0~100 之间")
    if args.attack_multiplier <= 0:
        ap.error("--attack-multiplier 必须大于 0")

    if args.selftest:
        selftest()
        return
    if args.login:
        open_login(args.rid)
        return
    if args.run:
        ensure_dirs()
        logf = open(os.path.join(DATA_DIR, "run.log"), "a", encoding="utf-8")

        def sink(line, level="info"):
            try:
                logf.write(line + "\n")
                logf.flush()
            except Exception:
                pass

        bot = GuardFishBot(rid=args.rid, rounds=args.rounds,
                           gap_min=args.gap_min, gap_max=args.gap_max,
                           headless=args.headless, claim_weekpass=not args.no_claim,
                           hp_guard=not args.no_hp, hp_threshold=args.hp_threshold,
                           coin_play=not args.no_coin, one_hit=args.one_hit,
                           max_battle_seconds=args.max_battle_minutes * 60,
                           stop_stage=args.stop_stage,
                           advance_stages=args.advance_stages,
                           one_hit_mode=args.one_hit_mode,
                           one_hit_probability=args.one_hit_probability,
                           attack_multiplier=args.attack_multiplier, debug=args.debug,
                           combat_controls=combat)
        bot.add_log_sink(sink)
        start_desc = "命令行挂机启动：房间=%s 场数=%d" % (args.rid, args.rounds)
        if args.headless:
            start_desc += " [headless]"
        if args.one_hit:
            start_desc += " [秒杀模式=%s]" % one_hit_mode_text(
                args.one_hit_mode, args.one_hit_probability, args.attack_multiplier)
        bot._log(start_desc)
        bot.start()
        try:
            while bot._thread and bot._thread.is_alive():
                bot._thread.join(timeout=1)
        except KeyboardInterrupt:
            bot.request_stop()
            bot.stop()
        logf.close()
        return

    print("保卫鱼娘 · 自动挂机助手")
    print("用法：")
    print("  --login          打开浏览器登录斗鱼（首次/掉登录后）")
    print("  --run            命令行挂机（--rounds N --rid 12306 --headless）")
    print("  --one-hit        秒杀模式：我方卡牌伤害直接击杀怪物")
    print("  --one-hit-mode full|probability|multiplier  秒杀策略")
    print("  --one-hit-probability N  概率秒杀触发概率（0~100）")
    print("  --attack-multiplier N  攻击倍率策略的伤害倍数")
    print("  --no-hp          关闭血量低于阈值自动回满")
    print("  --no-coin        免费用尽后不用银币续打")
    print("  --max-battle-minutes N  单场最长监控时间（分钟）")
    print("  --stop-stage N   达到第 N 关后自然结束")
    print("  --advance-stages N  从起始关推进 N 关后自然结束")
    print("  --selftest       环境自检")
    print("图形界面请运行「保卫鱼娘助手.exe」或 gui.py")


if __name__ == "__main__":
    main()
