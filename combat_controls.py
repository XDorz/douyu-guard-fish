"""独立的战斗配置、统计和原生方法适配；不改变游戏时钟或复制战斗算法。"""
from copy import deepcopy
import math


EFFECTS = ("thunder", "fire", "ice", "wind")
EFFECT_LABELS = {"thunder": "雷系传导", "fire": "火系暴击", "ice": "冰系冰冻", "wind": "风系横扫"}
CD_LIMITS = {name: (0.5 if name == "wind" else 0.0, 180.0) for name in EFFECTS}


def default_controls():
    return {
        "attack_speed": {"enabled": False, "mode": "multiplier", "multiplier": 1.0, "fixed": 1.0},
        "repeat_ice": False, "repeat_earth": False,
        "effects": {name: {"enabled": False, "mode": "multiplier", "multiplier": 1.0,
                           "probability": 40.0 if name == "ice" else 50.0,
                           # -1 means keep the native timing.  In particular, the
                           # native wind floor remains in force instead of turning
                           # an empty field into an accidental 0.5-second override.
                           "cd": -1.0} for name in EFFECTS},
    }


def _number(value, fallback, low, high):
    try:
        if isinstance(value, bool):
            return fallback
        value = float(value)
        return max(low, min(high, value)) if math.isfinite(value) else fallback
    except (TypeError, ValueError, OverflowError):
        return fallback


def normalize_controls(values, base=None):
    result = deepcopy(base if base is not None else default_controls())
    if not isinstance(values, dict):
        return result
    for key in ("repeat_ice", "repeat_earth"):
        if isinstance(values.get(key), bool):
            result[key] = values[key]
    pairs = [(values.get("attack_speed"), result["attack_speed"], None)]
    effects = values.get("effects")
    if isinstance(effects, dict):
        pairs += [(effects.get(name), result["effects"][name], name) for name in EFFECTS]
    for raw, target, effect in pairs:
        if not isinstance(raw, dict):
            continue
        if isinstance(raw.get("enabled"), bool):
            target["enabled"] = raw["enabled"]
        if raw.get("mode") in ("fixed", "multiplier"):
            target["mode"] = raw["mode"]
        if effect:
            target["probability"] = _number(raw.get("probability"), target["probability"], 0, 100)
            target["multiplier"] = _number(raw.get("multiplier"), target["multiplier"], 0, 100)
            raw_cd = raw.get("cd")
            try:
                parsed_cd = float(raw_cd)
                if parsed_cd == -1:
                    # Only -1 preserves native timing; all other values clamp.
                    target["cd"] = -1.0
                else:
                    target["cd"] = _number(raw_cd, target["cd"], *CD_LIMITS[effect])
            except (TypeError, ValueError, OverflowError):
                pass
        else:
            target["fixed"] = _number(raw.get("fixed"), target["fixed"], 0.2, 180)
            mult = _number(raw.get("multiplier"), target["multiplier"], -100, 100)
            # 负数只用于减速；-0.5 归到 -1，避免负值反而加速。
            target["multiplier"] = min(-1, mult) if mult < 0 else (max(0.01, mult) if mult else 0.0)
    return result


class KillStats:
    """合并各次页面模块的累计值，暂停/刷新不清空本场已观测的击杀。"""
    def __init__(self):
        self.seen = {}
        self.stages = {}

    def consume(self, snapshot):
        if not isinstance(snapshot, dict):
            return
        epoch = snapshot.get("epoch")
        for row in snapshot.get("stages") or []:
            stage, count = row.get("stage"), row.get("kills")
            if not isinstance(stage, int) or stage < 1 or not isinstance(count, int) or count < 0:
                continue
            key = (epoch, stage)
            before = self.seen.get(key, 0)
            self.seen[key] = max(before, count)
            target = self.stages.setdefault(stage, {"kills": 0, "kills_partial": False})
            target["kills"] += max(0, count - before)
            target["kills_partial"] |= bool(row.get("partial"))

    @property
    def total(self):
        return sum(row["kills"] for row in self.stages.values())

    def for_stage(self, stage):
        return dict(self.stages.get(stage, {"kills": None, "kills_partial": True}))


class ActivityWatch:
    """读取已有计数，避免遍历怪物/弹丸；只有可靠采样时才推进停滞计时。"""
    def __init__(self, limit=240):
        self.limit = limit
        self.signature = None
        self.last_tick = None
        self.last_sample = None
        self.idle_seconds = 0.0
        self.progressed = False

    def observe(self, snapshot, now, suspended=False):
        current = (snapshot or {}).get("current") or {}
        signature = (snapshot.get("epoch"), current.get("id"), current.get("projectiles"),
                     snapshot.get("totalKills")) if snapshot else None
        tick = current.get("tick")
        self.progressed = bool(self.signature and signature and signature[:2] == self.signature[:2]
                               and signature[2:] != self.signature[2:])
        available = (current.get("activityAvailable") and current.get("running")
                     and isinstance(tick, (int, float)))
        elapsed = max(0, now - self.last_sample) if self.last_sample is not None else 0
        # 刻意降低攻速后，正常倒计时不应触发停滞；时钟冻结仍会累计超时。
        cooling = current.get("cooling") and self.last_tick is not None and tick != self.last_tick
        if suspended or not available or signature != self.signature or cooling or elapsed > 5:
            self.idle_seconds = 0.0
        else:
            self.idle_seconds += elapsed
        self.signature, self.last_tick, self.last_sample = signature, tick, now
        return self.idle_seconds >= self.limit


JS_COMBAT_INSTALL = r"""
(() => {
  const GF = window.__GF;
  if (!GF || window.__gfCombat) return;
  let cfg = {attack_speed: {enabled: false}, effects: {}}, version = 0, epoch = 0, nextId = 0;
  let currentLoop = null;
  const metas = new WeakMap(), stages = new Map();
  const camps = {1: 'thunder', 3: 'fire', 4: 'ice', 5: 'wind'};
  const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
  const now = loop => Number(loop.lastTickTime) || 0;
  function stageOf(loop) {
    const match = /^stage-(\d+)(?:-|$)/.exec(String(loop.config && loop.config.configHash || ''));
    const n = match ? Number(match[1]) : Number((window.__WM && window.__WM.stage || {}).curStage);
    return Number.isInteger(n) && n > 0 ? n : null;
  }
  function markStage(loop, meta) {
    const stage = stageOf(loop);
    if (!stage) return null;
    if (!stages.has(stage)) stages.set(stage, {stage, kills: 0,
      partial: meta.stage === null && Number(loop._nextProjectileId) > 0});
    meta.stage = stage;
    return stages.get(stage);
  }
  function method(meta, object, key, enabled, create) {
    const slot = (object === meta.loop ? 'loop.' : 'cb.') + key;
    let saved = meta.methods.get(slot);
    if (enabled && !saved && object && typeof object[key] === 'function') {
      saved = {object, key, own: Object.prototype.hasOwnProperty.call(object, key), original: object[key]};
      saved.wrapper = create(saved.original);
      Object.defineProperty(saved.wrapper, '__gfOriginal', {value: saved.original});
      object[key] = saved.wrapper;
      meta.methods.set(slot, saved);
    } else if (!enabled && saved) {
      if (saved.object[saved.key] === saved.wrapper) {
        if (saved.own) saved.object[saved.key] = saved.original;
        else delete saved.object[saved.key];
      }
      meta.methods.delete(slot);
    }
  }
  function interval(loop, card, rule) {
    const mul = typeof loop._resolveAttackSpeedMul === 'function' ? loop._resolveAttackSpeedMul(card.camp) : 1;
    const base = Math.max(0.2, Number(card.attackSpeed) / Math.max(0.000001, mul) / 1000);
    if (!rule || !rule.enabled) return base;
    if (rule.mode === 'fixed') return clamp(Number(rule.fixed) || 0.2, 0.2, 180);
    const value = Number(rule.multiplier) || 0;
    const factor = value < 0 ? 1 / Math.max(1, -value) : value || 1;
    return clamp(base / factor, 0.2, 180);
  }
  function reconcileSpeed(loop, before, after) {
    if (!Array.isArray(loop.cards) || !Array.isArray(loop.cardCooldowns)) return;
    for (let i = 0; i < loop.cards.length; i++) {
      if (loop.cardCooldowns[i] > 0) {
        const old = interval(loop, loop.cards[i], before), next = interval(loop, loop.cards[i], after);
        if (old !== next) loop.cardCooldowns[i] = Math.min(next, loop.cardCooldowns[i] * next / old);
      }
    }
  }
  function holdAttacks(loop, meta, dt = 0) {
    if (!Array.isArray(loop.cardCooldowns)) return;
    if (!meta.held) meta.held = {at: now(loop), values: loop.cardCooldowns.slice()};
    for (let i = 0; i < (loop.cards || []).length; i++) loop.cardCooldowns[i] = 180 + Math.max(0, dt);
  }
  function releaseAttacks(loop, meta) {
    if (!meta.held || !Array.isArray(loop.cardCooldowns)) return;
    const elapsed = Math.max(0, now(loop) - meta.held.at) / 1000;
    for (let i = 0; i < loop.cardCooldowns.length; i++)
      loop.cardCooldowns[i] = Math.max(0, (meta.held.values[i] || 0) - elapsed);
    meta.held = null;
  }
  function probability(card, rule) {
    const base = card.camp === 4 ? card.campVal1 : card.attrVal + (card.camp === 1 ? card.campVal3 : card.campVal1);
    return clamp(rule.mode === 'fixed' ? Number(rule.probability) / 100 : base * Number(rule.multiplier), 0, 1);
  }
  function probabilityField(card, value) {
    return card.camp === 1 ? ['campVal3', value - card.attrVal] :
           ['campVal1', card.camp === 4 ? value : value - card.attrVal];
  }
  function skillCount(loop, card) {
    const stat = loop._floorCardStats && loop._floorCardStats.get(card.cardInstanceId);
    return stat && Number(stat.skills) || 0;
  }
  function ready(loop, meta, card, rule) {
    const last = meta.triggered.get(card.cardInstanceId);
    return last === undefined || meta.clock - last >= rule.cd * 1000;
  }
  function sync(loop) {
    if (!loop) return;
    currentLoop = loop;
    let meta = metas.get(loop);
    if (!meta) {
      meta = {loop, id: ++nextId, stage: null, methods: new Map(), counted: new WeakSet(),
              triggered: new Map(), clock: 0, applied: {attack_speed: {enabled: false}, effects: {}},
              version: -1, held: null, endWarmup: false};
      metas.set(loop, meta);
    }
    if (GF.wrappedLoops.indexOf(loop) < 0) GF.wrappedLoops.push(loop);
    if (typeof loop._killEnemy === 'function') markStage(loop, meta);
    method(meta, loop, '_killEnemy', true, original => function(enemy) {
      const row = markStage(this, meta);
      const fresh = enemy && typeof enemy === 'object' && !meta.counted.has(enemy);
      if (fresh) meta.counted.add(enemy);
      let result;
      try { result = original.apply(this, arguments); }
      catch (error) { if (fresh) meta.counted.delete(enemy); throw error; }
      if (fresh && row) row.kills++;
      return result;
    });
    if (meta.version !== version && !GF.finishing) {
      reconcileSpeed(loop, meta.applied.attack_speed, cfg.attack_speed);
      for (const card of loop.cards || []) {
        const name = camps[card.camp], before = meta.applied.effects[name], after = cfg.effects[name];
        if (!before || !before.enabled || !after || !after.enabled) meta.triggered.delete(card.cardInstanceId);
      }
      const wind = cfg.effects.wind;
      if (wind && wind.enabled && wind.cd >= 0 && typeof loop.windRowCooldownMs === 'number')
        loop.windRowCooldownMs = Math.min(loop.windRowCooldownMs, wind.cd * 1000);
      meta.applied = cfg;
      meta.version = version;
    }
    const fire = cfg.effects.fire;
    const customCD = ['thunder', 'ice'].some(k => cfg.effects[k] && cfg.effects[k].enabled && cfg.effects[k].cd > 0);
    const fixedAttack = !!(GF.oneHit && GF.oneHitMode === 'fixed_attack');
    const attacks = GF.finishing || fixedAttack || cfg.attack_speed.enabled || fire && fire.enabled || customCD;
    method(meta, loop, '_updateCardAttacks', !!attacks, original => function(dt) {
      meta.clock += Math.max(0, Number(dt) || 0) * 1000;
      if (GF.finishing) {
        // If the end request arrived before the first projectile of this
        // engine, allow one native attack update.  This avoids a zero-damage
        // battle when the request was made on the card-selection screen.
        let result;
        if (meta.endWarmup) {
          const beforeIds = Number(this._nextProjectileId);
          try {
            result = original.apply(this, arguments);
            const afterIds = Number(this._nextProjectileId);
            if (Number.isFinite(beforeIds) && Number.isFinite(afterIds) && afterIds > beforeIds) {
              meta.endWarmup = false;
              GF.endWarmup = false;
            }
          } finally {
            if (!meta.endWarmup) holdAttacks(this, meta, dt);
          }
          return result;
        }
        holdAttacks(this, meta, dt);
        return undefined;
      }
      const saved = [];
      const fire = cfg.effects.fire;
      const fixedNow = !!(GF.oneHit && GF.oneHitMode === 'fixed_attack');
      const fixedValue = Math.max(1, Number(GF.attackFixed) || 1);
      const set = (object, key, value) => { saved.push([object, key, object[key]]); object[key] = value; };
      try {
        for (const card of this.cards || []) {
          if (fixedNow) set(card, 'attack', fixedValue);
          if (cfg.attack_speed.enabled) {
            const mul = this._resolveAttackSpeedMul(card.camp);
            set(card, 'attackSpeed', interval(this, card, cfg.attack_speed) * mul * 1000);
          }
          if (card.camp === 3 && fire && fire.enabled) {
            const rate = ready(this, meta, card, fire) ? probability(card, fire) : 0;
            const [key, value] = probabilityField(card, rate);
            set(card, key, value);
          }
        }
        return original.apply(this, arguments);
      } finally {
        for (let i = saved.length - 1; i >= 0; i--) { const [object, key, value] = saved[i]; object[key] = value; }
      }
    });
    // 火系暴击在发射时判定，CD 也从这次发射开始，而不是等弹丸命中。
    method(meta, loop.callbacks, 'onProjectileLaunched', !!(fire && fire.enabled && !GF.finishing), original => function(cardId, targetId, camp, projectileId) {
      const result = original.apply(this, arguments);
      const fire = cfg.effects.fire;
      const hit = loop.pendingHits && loop.pendingHits.get(projectileId);
      if (camp === 3 && hit && hit.isCrit) {
        meta.triggered.set(cardId, meta.clock);
        if (fire.cd > 0 && hit.card) hit.card.campVal1 = -hit.card.attrVal;
      }
      return result;
    });
    const hitEnabled = fixedAttack || cfg.repeat_ice || cfg.repeat_earth || ['thunder', 'ice', 'wind'].some(k => cfg.effects[k] && cfg.effects[k].enabled);
    method(meta, loop, '_applyProjectileHit', !!hitEnabled && !GF.finishing, original => function(card, target) {
      const name = card && camps[card.camp], rule = cfg.effects[name];
      if (!card || GF.finishing) return original.apply(this, arguments);
      const changed = rule && rule.enabled && name !== 'fire';
      const rate = changed && (name === 'wind' || ready(this, meta, card, rule)) ? probability(card, rule) : 0;
      const [field, value] = probabilityField(card, rate), before = card[field], oldCD = card.campVal2;
      const skills = changed ? skillCount(this, card) : 0;
      const fixedNow = !!(GF.oneHit && GF.oneHitMode === 'fixed_attack');
      const oldAttack = card.attack;
      if (fixedNow) card.attack = Math.max(1, Number(GF.attackFixed) || 1);
      const seen = card.camp === 4 && cfg.repeat_ice ? this._iceFrozenThisFloor :
                   card.camp === 2 && cfg.repeat_earth ? this._earthSlowedThisFloor : null;
      const hadTarget = !!(seen && target && seen.has(target.id));
      if (hadTarget) seen.delete(target.id);
      if (changed) card[field] = value;
      if (changed && name === 'wind' && rule.cd >= 0) card.campVal2 = rule.cd;
      try { return original.apply(this, arguments); }
      finally {
        if (changed && skillCount(this, card) > skills) meta.triggered.set(card.cardInstanceId, meta.clock);
        if (changed) card[field] = before;
        if (changed && name === 'wind' && rule.cd >= 0) card.campVal2 = oldCD;
        if (fixedNow) card.attack = oldAttack;
        // 临时放行当前目标，关闭开关时仍保留其本关已经受过控制的历史。
        if (hadTarget) seen.add(target.id);
      }
    });
    if (GF.finishing) {
      meta.endWarmup = !!GF.endWarmup;
      if (meta.endWarmup) releaseAttacks(loop, meta);
      else holdAttacks(loop, meta);
    }
    else releaseAttacks(loop, meta);
  }
  function restore(loop) {
    const meta = metas.get(loop);
    if (!meta) return;
    releaseAttacks(loop, meta);
    reconcileSpeed(loop, meta.applied.attack_speed, {enabled: false});
    for (const saved of meta.methods.values()) {
      if (saved.object[saved.key] !== saved.wrapper) continue;
      if (saved.own) saved.object[saved.key] = saved.original;
      else delete saved.object[saved.key];
    }
    meta.methods.clear();
    meta.applied = {attack_speed: {enabled: false}, effects: {}};
    meta.version = -1;
    meta.endWarmup = false;
    if (currentLoop === loop) currentLoop = null;
  }
  function snapshot() {
    const loop = currentLoop, meta = loop && metas.get(loop);
    const rows = [...stages.values()].map(x => ({...x}));
    return {epoch, stages: rows, totalKills: rows.reduce((n, x) => n + x.kills, 0),
      current: meta ? {id: meta.id, stage: meta.stage, running: !!loop.running, tick: now(loop),
        projectiles: loop._nextProjectileId,
        activityAvailable: typeof loop._nextProjectileId === 'number',
        cooling: Array.isArray(loop.cardCooldowns) && loop.cardCooldowns.length > 0 &&
                 loop.cardCooldowns.every(x => x > 0),
        killAvailable: meta.methods.has('loop._killEnemy')} : null};
  }
  window.__gfCombat = {sync, restore, snapshot, configure(options, instance) {
    if (options) { cfg = options; version++; }
    if (instance !== undefined) epoch = instance;
    if (GF.loopRef) sync(GF.loopRef);
  }};
})()
"""

JS_COMBAT_SNAPSHOT = "() => window.__gfCombat ? window.__gfCombat.snapshot() : null"
