const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const scripts = JSON.parse(fs.readFileSync(0, 'utf8'));

class Loop {
  constructor() {
    this.coreHp = 20;
    this.running = true;
    this.config = {battle: {baseCoreHp: 20}};
    this.hpUpdates = [];
    this.calls = [];
    this.enemies = [];
    this.callbacks = {onCoreHpChanged: (...args) => this.hpUpdates.push(args)};
  }
  _dealDamage(enemy, damage, card, crit) {
    this.calls.push([damage, card, crit]);
    enemy.currentHp -= Math.min(damage, enemy.currentHp);
    return 'native-result';
  }
  _checkEnemiesReachedCore() {
    this.coreHp = Math.max(0, this.coreHp - 1);
    if (this.coreHp === 0) this.running = false;
  }
}

function harness() {
  const loop = new Loop();
  const ref = {current: {loop}};
  const window = {__REACT_DEVTOOLS_GLOBAL_HOOK__: {__roots: {
    test: {current: {memoizedState: {memoizedState: ref}, type: {name: 'Game'}}}
  }}};
  let timer = null;
  const math = Object.create(Math);
  const context = vm.createContext({window, Math: math, Date,
    document: {querySelector: () => null},
    setInterval(fn) { timer = fn; return 1; }, clearInterval() { timer = null; }});
  const run = (key, arg) => {
    const value = vm.runInContext(scripts[key], context);
    return typeof value === 'function' ? value(arg) : value;
  };
  run('watch');
  return {loop, ref, window, math, run, tick: () => { if (timer) timer(); }, hasTimer: () => !!timer};
}

// All strategies, including an initially unloaded damage hook, must yield to ending.
for (const mode of ['off', 'full', 'probability', 'multiplier']) {
  const h = harness();
  h.run('cfg', {hpGuard: true, oneHit: mode !== 'off', oneHitMode: mode,
                oneHitProbability: 100, attackMultiplier: 10});
  h.tick();
  h.loop.coreHp = 2;
  h.tick();
  assert.equal(h.loop.coreHp, 20, 'hp guard active before ending');
  assert.equal(h.run('end').ok, true);
  assert.equal(h.loop.coreHp, 1);
  assert.deepEqual(h.loop.hpUpdates.at(-1), [1, 20]);
  for (const random of [0, 0.495, 0.999999]) {
    h.math.random = () => random;
    const enemy = {currentHp: 1000000};
    assert.equal(h.loop._dealDamage(enemy, 10000000, 'card', true), 'native-result');
    assert.deepEqual(h.loop.calls.at(-1), [1 + Math.floor(random * 100), 'card', true]);
  }
  h.tick();
  assert.equal(h.loop.coreHp, 1, 'guard cannot heal while ending');
  h.loop.coreHp = 20;
  h.tick();
  assert.equal(h.loop.coreHp, 1, 'ending survives a stage resetting the same engine');
  h.loop._checkEnemiesReachedCore();
  h.tick();
  assert.equal(h.loop.coreHp, 0, 'never revive a dead core');
  assert.equal(h.loop.running, false, 'native death remains intact');

  // Replacing the engine at a stage transition must not escape ending mode.
  const next = new Loop();
  h.ref.current.loop = next;
  h.tick();
  assert.equal(next.coreHp, 1);
  next._dealDamage({currentHp: 1000000}, 10000, 'next', false);
  assert.equal(next.calls.at(-1)[0], 100);
  h.run('unload');
  assert.equal(h.hasTimer(), false);
  for (const loop of [h.loop, next]) {
    assert.equal(loop._dealDamage, Loop.prototype._dealDamage);
    assert.equal(loop._checkEnemiesReachedCore, Loop.prototype._checkEnemiesReachedCore);
    assert.equal(Object.hasOwn(loop, '_dealDamage'), false);
  }
  assert.equal(h.window.__GF, undefined);
}

// A request before engine discovery remains pending and takes effect once available.
{
  const h = harness(), manager = h.ref.current;
  h.ref.current = null;
  assert.equal(h.run('end').pending, true);
  h.ref.current = manager;
  h.tick();
  assert.equal(h.loop.coreHp, 1);
  h.loop._dealDamage({currentHp: 1000000}, 99999);
  assert.ok(h.loop.calls.at(-1)[0] >= 1 && h.loop.calls.at(-1)[0] <= 100);
}

// Original strategy semantics, including overkill damage, remain unchanged.
{
  const h = harness();
  h.run('cfg', {oneHit: true, oneHitMode: 'multiplier', attackMultiplier: 2.5});
  h.tick();
  h.loop._dealDamage({currentHp: 100}, 101);
  assert.equal(h.loop.calls.at(-1)[0], 252, 'do not pre-clamp amplified damage to HP');
  h.run('cfg', {oneHitMode: 'probability', oneHitProbability: 0});
  h.loop._dealDamage({currentHp: 100}, 10);
  assert.equal(h.loop.calls.at(-1)[0], 10);
  h.run('cfg', {oneHitProbability: 100});
  h.loop._dealDamage({currentHp: 100}, 10);
  assert.equal(h.loop.calls.at(-1)[0], 100);
  h.run('end');
  h.run('unload');
  h.run('watch');
  h.run('cfg', {oneHit: true, oneHitMode: 'multiplier', attackMultiplier: 3});
  h.tick();
  h.loop._dealDamage({currentHp: 1000}, 10);
  assert.equal(h.loop.calls.at(-1)[0], 30, 'next battle restores desired controls');
}

// Calling an original method which throws must never apply damage a second time.
{
  const h = harness();
  let calls = 0;
  const original = () => { calls++; throw new Error('native failure'); };
  h.loop._dealDamage = original;
  h.run('cfg', {oneHit: true});
  h.tick();
  assert.throws(() => h.loop._dealDamage({currentHp: 100}, 10), /native failure/);
  assert.equal(calls, 1);
  h.run('unload');
  assert.equal(h.loop._dealDamage, original, 'restore original own method');
}
console.log('runtime controls passed');
