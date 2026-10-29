import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import bot
from diagnostics import DebugRecorder, JS_ENGINE_SNAPSHOT, safe_api_data
from stage_timing import StageTimer


class DiagnosticsTests(unittest.TestCase):
    def test_file_is_readable_before_shutdown_and_has_no_identity_data(self):
        data = safe_api_data({"data": {"userInfo": {"uid": 1234, "nickname": "name"},
                                      "token": "private", "stageInfo": {"curStage": 113},
                                      "unknownText": "private value"}})
        with tempfile.TemporaryDirectory() as directory:
            writer = DebugRecorder(directory, "test")
            try:
                writer.write("api_response", data=data)
                with open(writer.path, encoding="utf-8") as f:
                    text = f.read()
                self.assertNotIn("private", text)
                self.assertNotIn("1234", text)
                rows = [json.loads(line) for line in text.splitlines()]
                self.assertEqual(rows[-1]["data"]["data"]["stageInfo"]["curStage"], 113)
            finally:
                writer.close()

    @unittest.skipUnless(shutil.which("node"), "Node required for JS diagnostic checks")
    def test_engine_snapshot_is_read_only_and_captures_original_methods(self):
        source = r'''
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const script = JSON.parse(fs.readFileSync(0, 'utf8')).script;
class Card {
  constructor() { this.attackInterval = 1.5; this.effect = {chance: 0.2, cooldown: 2}; }
  attack() { throw new Error('must not call game methods'); }
}
class Loop {
  constructor() {
    this.coreHp = 20; this.config = {battle: {baseCoreHp: 20}, speed: 1};
    this.towers = [new Card()]; this.running = true; this.token = 'must-be-redacted';
    this.self = this;
  }
  update(dt) { this.elapsed += dt * this.config.speed; }
  _dealDamage(enemy, amount) { enemy.currentHp -= amount; }
}
const loop = new Loop();
loop.__gfDealOriginal = loop._dealDamage;
Object.defineProperty(loop, 'evilGetter', {enumerable: true, get() { throw new Error('getter invoked'); }});
const fiber = {memoizedState: {memoizedState: {current: loop}}, type: {name: 'Game'}};
const el = {__reactFiber$test: fiber};
const window = {}; Object.freeze(window);
const before = Object.getOwnPropertyDescriptors(loop);
const ctx = vm.createContext({window, document: {querySelector: () => el}, performance,
                             Date, Object, Map, Set, Array, Number, String});
const read = vm.runInContext('(' + script + ')', ctx);
const result = read({sources: true});
assert.equal(result.found, true);
assert.equal(result.via, 'react-fiber');
assert.match(result.loop.methods.update.source, /speed/);
assert.match(result.loop.methods.__gfDealOriginal.source, /currentHp/);
assert.equal(result.loop.state.fields.evilGetter, '[accessor not invoked]');
assert.equal(result.loop.state.fields.token, '[redacted]');
assert.equal(result.collections['loop.towers'][0].state.fields.attackInterval, 1.5);
assert.deepEqual(Object.getOwnPropertyDescriptors(loop), before);
assert.deepEqual(Object.keys(window), []);
assert.equal(loop.coreHp, 20);
const compact = read({sources: false});
assert.equal(compact.sourceChars, 0);
console.log('read-only engine inspection passed');
'''
        result = subprocess.run([shutil.which("node"), "-e", source],
                                input=json.dumps({"script": JS_ENGINE_SNAPSHOT}),
                                text=True, encoding="utf-8", capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_api_timing_and_polling_share_one_stage_timer(self):
        class Response:
            status = 200

            def __init__(self, api, stage):
                self.url = "https://www.douyu.com/japi/revenuenc/web/actgame/" + api
                self.stage = stage

            def text(self):
                return json.dumps({"error": 0, "data": {"stageInfo": {"curStage": self.stage}}})

        b = bot.GuardFishBot()
        records = []
        b._stage_timer = StageTimer(records.append)
        b._api_write = lambda obj: None
        with patch("bot.time.monotonic", side_effect=[10, 60, 65]):
            b._on_response(Response("game/stageStart", 104))
            b._stage_timer.observe(104, 11, 1700000011)
            b._on_response(Response("game/stageEnd", 104))
            b._on_response(Response("game/nextStage", 105))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["duration_s"], 50)
        self.assertFalse(records[0]["partial"])
        self.assertIsNone(b._debug_recorder)


if __name__ == "__main__":
    unittest.main()
