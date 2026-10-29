const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const defaults = () => JSON.parse(JSON.stringify(input.defaults));

function card(id, camp, chance = 0.3) {
  return {cardInstanceId: id, camp, attack: 50, attackSpeed: 2000,
          attrVal: chance, campVal1: camp === 4 ? chance : 0, campVal2: camp === 5 ? 8 : 1,
          campVal3: 0};
}
class Loop {
  constructor(stage = 121) {
    this.config = {configHash: `stage-${stage}-test`, battle: {baseCoreHp: 20}};
    this.coreHp = 20; this.running = true; this.lastTickTime = 1000;
    this.cards = [card('wind-a', 5, .5), card('wind-b', 5, .1), card('fire', 3),
                  card('thunder', 1), card('ice', 4), card('earth', 2)];
    this.cardCooldowns = this.cards.map(() => 0);
    this.pendingHits = new Map(); this._nextProjectileId = 0;
    this._floorCardStats = new Map(this.cards.map(c => [c.cardInstanceId, {skills: 0}]));
    this._iceFrozenThisFloor = new Set(); this._earthSlowedThisFloor = new Set();
    this.windRowCooldownMs = 0; this.enemies = []; this.pendingSpawnQueue = [];
    this.shots = []; this.hpUpdates = []; this.removals = []; this.rates = [];
    this.skillSpeed = 1; this.random = () => .25;
    this.callbacks = {onCoreHpChanged: (...args) => this.hpUpdates.push(args),
      onEnemyKilled: id => this.removals.push(id),
      onProjectileLaunched: (id, target, camp, projectile) => this.shots.push({...this.pendingHits.get(projectile)})};
  }
  _resolveAttackSpeedMul() { return this.skillSpeed; }
  _updateCardAttacks(dt) {
    for (let i = 0; i < this.cards.length; i++) {
      this.cardCooldowns[i] -= dt;
      if (this.cardCooldowns[i] > 0) continue;
      const c = this.cards[i], id = 'proj-' + this._nextProjectileId;
      const crit = c.camp === 3 && this.random() < c.attrVal + c.campVal1;
      this.pendingHits.set(id, {card: c, isCrit: crit});
      this.callbacks.onProjectileLaunched(c.cardInstanceId, 'enemy', c.camp, id);
      this._nextProjectileId++;
      this.cardCooldowns[i] = Math.max(.2, c.attackSpeed / this._resolveAttackSpeedMul(c.camp) / 1000);
    }
  }
  _incSkillStat(id) { this._floorCardStats.get(id).skills++; }
  _applyProjectileHit(c, target, damage, primary, crit) {
    this._dealDamage(target, damage);
    const rate = c.camp === 1 ? c.attrVal + c.campVal3 : c.camp === 4 ? c.campVal1 : c.attrVal + c.campVal1;
    this.rates.push([c.cardInstanceId, rate]);
    if (c.camp === 5 && this.windRowCooldownMs <= 0 && this.random() < rate) {
      this.windRowCooldownMs = Math.max(500, c.campVal2 * 1000);
      this._incSkillStat(c.cardInstanceId);
    } else if (c.camp === 1 && primary && this.random() < rate) {
      this._incSkillStat(c.cardInstanceId);
    } else if (c.camp === 4 && !this._iceFrozenThisFloor.has(target.id) && this.random() < rate) {
      this._iceFrozenThisFloor.add(target.id); this._incSkillStat(c.cardInstanceId);
    } else if (c.camp === 2 && !this._earthSlowedThisFloor.has(target.id)) {
      this._earthSlowedThisFloor.add(target.id); this._incSkillStat(c.cardInstanceId);
    }
    if (c.camp === 3 && crit) this._incSkillStat(c.cardInstanceId);
  }
  _dealDamage(enemy, damage) {
    if (!this.enemies.includes(enemy)) return;
    enemy.currentHp -= Math.min(enemy.currentHp, damage);
    if (enemy.currentHp <= 0) this._killEnemy(enemy);
  }
  _killEnemy(enemy) {
    this.callbacks.onEnemyKilled(enemy.id);
    this.enemies = this.enemies.filter(e => e !== enemy);
    return 'killed';
  }
  _checkEnemiesReachedCore() {
    for (const e of this.enemies.filter(e => e.pathProgress >= 1)) {
      this.callbacks.onEnemyKilled(e.id);
      this.enemies = this.enemies.filter(x => x !== e);
      this.coreHp = Math.max(0, this.coreHp - 1);
      if (!this.coreHp) this.running = false;
    }
  }
}
function harness() {
  const loop = new Loop(), ref = {current: {loop}}, timers = new Map();
  const window = {__WM: {stage: {curStage: 999}}, __REACT_DEVTOOLS_GLOBAL_HOOK__: {__roots: {
    test: {current: {memoizedState: {memoizedState: ref}, type: {name: 'Game'}}}}}};
  const context = vm.createContext({window, document: {querySelector: () => null}, Date,
    setInterval(fn) { timers.set(1, fn); return 1; }, clearInterval(id) { timers.delete(id); }});
  const run = (key, arg) => {
    const fn = vm.runInContext(input[key], context);
    return typeof fn === 'function' ? fn(arg === undefined ? arg : JSON.parse(JSON.stringify(arg))) : fn;
  };
  const tick = () => { for (const fn of timers.values()) fn(); };
  run('watch'); run('install');
  const configure = options => {
    run('cfg', {monitor: true, monitorEpoch: 1, hpGuard: false, oneHit: false, combat: options});
    tick();
  };
  configure(defaults());
  return {loop, ref, window, timers, run, tick, configure,
    stats: () => JSON.parse(JSON.stringify(run('snapshot'))),
    enemy(id = 'target') { const e = {id, currentHp: 100000, pathProgress: .5}; ref.current.loop.enemies.push(e); return e; }};
}

// Observing kills does not install combat-changing methods. Base arrivals are not kills.
{
  const h = harness(), l = h.loop;
  assert.equal(h.timers.size, 1, 'reuse the existing watcher; do not add polling timers');
  assert.equal(l._updateCardAttacks, Loop.prototype._updateCardAttacks);
  assert.equal(l._applyProjectileHit, Loop.prototype._applyProjectileHit);
  assert.equal(l._dealDamage, Loop.prototype._dealDamage);
  const e = h.enemy('killed');
  l._dealDamage(e, 100000);
  l._killEnemy(e); // A repeated native notification should not duplicate the counter.
  const reached = h.enemy('reached'); reached.pathProgress = 1;
  l._checkEnemiesReachedCore();
  assert.equal(h.stats().totalKills, 1);
  assert.equal(h.stats().stages[0].stage, 121, 'engine config wins over stale network stage');
  assert.equal(h.stats().stages[0].partial, false);
  const next = new Loop(122); h.ref.current.loop = next; h.tick();
  assert.equal(l._killEnemy, Loop.prototype._killEnemy, 'old engines are released at stage transitions');
  next._dealDamage(h.enemy('next-stage'), 100000);
  assert.equal(h.stats().totalKills, 2);
  assert.deepEqual(h.stats().stages.map(x => x.kills), [1, 1]);
  assert.equal(h.window.__GF.wrappedLoops.length, 1, 'do not retain all previous game engines');
  h.run('unload');
  assert.equal(next._killEnemy, Loop.prototype._killEnemy);
  assert.equal(h.timers.size, 0);
}

// Global speed is independent of game skill bonuses and respects the native floor.
{
  const h = harness(), l = h.loop, cfg = defaults();
  l.skillSpeed = 2;
  const original = l.cards.map(c => c.attackSpeed);
  Object.assign(cfg.attack_speed, {enabled: true, mode: 'fixed', fixed: .2});
  h.configure(cfg); l._updateCardAttacks(.1);
  assert.ok(l.cardCooldowns.every(x => Math.abs(x - .2) < 1e-9));
  assert.deepEqual(l.cards.map(c => c.attackSpeed), original);
  Object.assign(cfg.attack_speed, {mode: 'multiplier', multiplier: -2});
  h.configure(cfg);
  assert.ok(l.cardCooldowns.every(x => Math.abs(x - 2) < 1e-9), 'changing speed rescales remaining cooldown');
  l.cardCooldowns.fill(0); l._updateCardAttacks(.1);
  assert.ok(l.cardCooldowns.every(x => Math.abs(x - 2) < 1e-9));
  cfg.attack_speed.multiplier = 0; h.configure(cfg);
  l.cardCooldowns.fill(0); l._updateCardAttacks(.1);
  assert.ok(l.cardCooldowns.every(x => Math.abs(x - 1) < 1e-9));
  h.configure(defaults());
  assert.equal(l._updateCardAttacks, Loop.prototype._updateCardAttacks);
}

// Fixed wind probability applies to different cards equally; CD is fixed and shared.
{
  const h = harness(), l = h.loop, cfg = defaults();
  Object.assign(cfg.effects.wind, {enabled: true, mode: 'fixed', probability: 70, cd: .5});
  h.configure(cfg);
  const saved = l.cards.slice(0, 2).map(c => ({...c}));
  l._applyProjectileHit(l.cards[0], h.enemy(), 1, true, false);
  l._applyProjectileHit(l.cards[1], h.enemy('second'), 1, true, false);
  assert.deepEqual(l.rates.slice(-2).map(r => r[1]), [.7, .7]);
  assert.equal(l.windRowCooldownMs, 500);
  assert.equal(l._floorCardStats.get('wind-b').skills, 0, 'second card shares wind cooldown');
  assert.deepEqual(l.cards.slice(0, 2), saved, 'restore actual game card values after native call');
  cfg.effects.wind.mode = 'multiplier'; cfg.effects.wind.multiplier = 2;
  h.configure(cfg); l.windRowCooldownMs = 0;
  l._applyProjectileHit(l.cards[0], h.enemy('third'), 1, true, false);
  l._applyProjectileHit(l.cards[1], h.enemy('fourth'), 1, true, false);
  assert.deepEqual(l.rates.slice(-2).map(r => r[1]), [1, .2]);
  assert.equal(l.windRowCooldownMs, 500, 'probability multiplier must not scale CD');
  h.configure(defaults());
  assert.equal(l._applyProjectileHit, Loop.prototype._applyProjectileHit);
}

// Custom proc cooldowns use simulated active time, independently per card.
for (const name of ['thunder', 'ice']) {
  const h = harness(), l = h.loop, cfg = defaults();
  const c = l.cards.find(x => x.camp === (name === 'ice' ? 4 : 1));
  Object.assign(cfg.effects[name], {enabled: true, mode: 'fixed', probability: 100, cd: 2});
  h.configure(cfg);
  l._applyProjectileHit(c, h.enemy('first'), 1, true, false);
  l.lastTickTime += 100000; // Pausing the game must not make proc cooldowns elapse.
  l._applyProjectileHit(c, h.enemy('second'), 1, true, false);
  assert.equal(l._floorCardStats.get(c.cardInstanceId).skills, 1);
  l._updateCardAttacks(2.1);
  l._applyProjectileHit(c, h.enemy('third'), 1, true, false);
  assert.equal(l._floorCardStats.get(c.cardInstanceId).skills, 2);
}

// Fire starts its cooldown when a critical projectile launches, without waiting for a hit.
{
  const h = harness(), l = h.loop, cfg = defaults();
  Object.assign(cfg.effects.fire, {enabled: true, mode: 'fixed', probability: 100, cd: 2});
  h.configure(cfg);
  const originalCard = {...l.cards[2]};
  l._updateCardAttacks(.1);
  l.cardCooldowns.fill(0); l._updateCardAttacks(.1);
  assert.deepEqual(l.shots.filter(s => s.card.camp === 3).map(s => s.isCrit), [true, false]);
  assert.deepEqual(l.cards[2], originalCard);
  cfg.effects.fire.cd = 0;
  cfg.effects.fire.probability = 0;
  h.configure(cfg); l.cardCooldowns.fill(0); l._updateCardAttacks(.1);
  assert.equal(l.shots.filter(s => s.card.camp === 3).at(-1).isCrit, false, 'live config replaces captured old probability');
  cfg.effects.fire.probability = 100;
  h.configure(cfg); l.cardCooldowns.fill(0); l._updateCardAttacks(.1);
  assert.equal(l.shots.filter(s => s.card.camp === 3).at(-1).isCrit, true);
  h.configure(defaults());
  assert.equal(l._updateCardAttacks, Loop.prototype._updateCardAttacks);
}

// Repeated control only bypasses the current target check; disabling preserves history.
for (const [camp, key, setName] of [[4, 'repeat_ice', '_iceFrozenThisFloor'], [2, 'repeat_earth', '_earthSlowedThisFloor']]) {
  const h = harness(), l = h.loop, cfg = defaults(), c = l.cards.find(x => x.camp === camp);
  const e = h.enemy();
  l._applyProjectileHit(c, e, 1, true, false);
  l._applyProjectileHit(c, e, 1, true, false);
  assert.equal(l._floorCardStats.get(c.cardInstanceId).skills, 1);
  cfg[key] = true; h.configure(cfg);
  l._applyProjectileHit(c, e, 1, true, false);
  l._applyProjectileHit(c, e, 1, true, false);
  assert.equal(l._floorCardStats.get(c.cardInstanceId).skills, 3);
  assert.ok(l[setName].has(e.id));
  if (camp === 4) {
    Object.assign(cfg.effects.ice, {enabled: true, mode: 'fixed', probability: 0});
    h.configure(cfg); l._applyProjectileHit(c, e, 1, true, false);
    assert.ok(l[setName].has(e.id), 'failed repeat roll must not erase original once-per-stage history');
  }
  h.configure(defaults()); l._applyProjectileHit(c, e, 1, true, false);
  assert.equal(l._floorCardStats.get(c.cardInstanceId).skills, 3);
}

// Ending wins over all settings, stops new attacks and reduces already airborne damage.
{
  const h = harness(), l = h.loop, cfg = defaults();
  cfg.repeat_ice = cfg.repeat_earth = true;
  cfg.attack_speed.enabled = true; cfg.attack_speed.multiplier = 100;
  for (const rule of Object.values(cfg.effects)) Object.assign(rule, {enabled: true, mode: 'fixed', probability: 100});
  h.configure(cfg); l._updateCardAttacks(.1);
  const fired = l._nextProjectileId;
  assert.equal(h.run('end').ok, true);
  for (let i = 0; i < 2000; i++) l._updateCardAttacks(.1);
  assert.equal(l._nextProjectileId, fired, '180-game-second hold must not expire during a long ending');
  assert.equal(l.coreHp, 1);
  const e = h.enemy('airborne'); l._applyProjectileHit(l.cards[0], e, 900000, true, true);
  assert.ok(e.currentHp >= 99900 && e.currentHp < 100000);
  h.run('unload');
  assert.ok(l.cardCooldowns.every(x => x < 180), 'unloading must release the forced attack cooldown');
  assert.equal(l._updateCardAttacks, Loop.prototype._updateCardAttacks);
  assert.equal(l._applyProjectileHit, Loop.prototype._applyProjectileHit);
}

// Unexpected native exceptions must not leave card stat overrides behind.
{
  const h = harness(), l = h.loop, cfg = defaults();
  l._updateCardAttacks = () => { throw new Error('native error'); };
  const before = l.cards.map(c => ({...c}));
  cfg.attack_speed.enabled = true;
  cfg.effects.fire.enabled = true;
  h.configure(cfg);
  assert.throws(() => l._updateCardAttacks(.1), /native error/);
  assert.deepEqual(l.cards, before);
}
console.log('combat controls: native delegation, live overrides, counters, repeats and ending passed');
