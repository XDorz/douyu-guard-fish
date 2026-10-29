分析依据为用户提供的 2026-10-29 第 121 关 Debug 引擎快照。这里只记录去除账号数据后的方法路径与结论；原始诊断文件不进入仓库。这些结论对应当次游戏版本，未通过真实账号验证加速修改。

第 121 关没有卡死。在约 226.953 秒现实时间内，`currentWave` 从 0 变到 9，累计生成敌人数从 6 变到 47。页面 `Date.now()` 和 `performance.now()` 同样前进约 227 秒，但 `BattleLoop.lastTickTime` 只前进 44.867 秒，比例约 0.198。原助手仅以关卡、血量和弹窗判断进展，四分钟不变就刷新，因此误中断。

这是传给引擎的时钟与现实时间存在差异的直接证据。现有采样没有包含外层 `GameEngineProvider` 调用 `manager.tick` 的闭包和 ticker 设置，无法确认差异来自游戏设计、节流还是外部工具。不能仅凭这份数据断言是 Cheat Engine 导致，也不能据此保证改某个全局时钟就能正确加速。

| 项目 | 实际路径与限制 | 可行性 |
| --- | --- | --- |
| 整体加速 | `BattleManager.tick(nowMs, dtSec)` 同时驱动逻辑循环、弹丸、特效、塔与敌人的显示；`BattleLoop.tick(nowMs)` 按 100ms 步进，累计时间最多保留 500ms；波次超时比较 `lastTickTime - waveStartMs` | 有统一入口，适合用局部虚拟时钟和同步的 `dtSec` 倍率做实验。不能只放大 `_doTick` 的参数或只改 `Date.now()`；需要处理暂停、切关、500ms 截断和弹丸碰撞，并验证服务端接受结算 |
| 普攻攻速 | `_updateCardAttacks` 使用 `card.attackSpeed / _resolveAttackSpeedMul(camp) / 1000`，再取 `max(0.2, interval)`；当前冷却存在 `cardCooldowns` | 可以在原速率倍率结果上再乘用户倍率，保留技能增益。原路径最低间隔 0.2 个游戏秒；还受目标是否可攻击、在途弹丸数量限制，攻速不等于固定每秒出手数 |
| 风系横扫 CD | `_applyProjectileHit` 检查共享的 `windRowCooldownMs`；触发后设为 `max(500, card.campVal2 * 1000)`；由 `_updateWindCooldown` 扣减 | 可以缩短，原路径最低 500 个游戏毫秒。它是全战斗的风系共享冷却，不是每张风卡独立 CD；该样本卡牌配置为 8 秒 |
| 雷系传导概率 | 主弹丸命中后比较随机数与 `card.attrVal + card.campVal3` | 可以独立调整；已捕获的方法中没有单独的时间 CD |
| 火系暴击概率 | 发射攻击时比较随机数与 `card.attrVal + card.campVal1` | 可以独立调整；已捕获的方法中没有单独的时间 CD |
| 风系横扫概率 | 主弹丸命中且 CD 已结束时，比较随机数与 `card.attrVal + card.campVal1` | 可以独立调整，但提高概率不会绕过共享 CD |
| 冰系冰冻概率 | 比较随机数与 `card.campVal1`，并检查 `_iceFrozenThisFloor` | 可以调概率，但命中过冰冻的同一只怪本关不再触发；清空集合将改变原生“一怪一次”的规则，不是单纯减 CD |
| 土系减速/眩晕 | 命中后检查 `_earthSlowedThisFloor`，时长来自 `attrVal * (1 + campVal1)`，减速比例来自 `campVal2` | 这条路径没有随机概率或时间 CD，也是同一只怪本关一次；可调整时长/强度，但不应包装成统一“特效概率” |

卡牌中虽然有 `skillTriggerRate` 字段，已捕获的这些触发方法没有读取它，不能把这个字段当作所有特效的统一概率入口。上述时间单位都是引擎内的游戏时间；当游戏内部时钟与现实时间不同，现实耗时会相应改变。

改血量方面，`_checkEnemiesReachedCore` 只有发现至少一只 `pathProgress >= 1` 的敌人时才继续执行扣血、击杀/击败回调、基地受伤回调、血条更新，最后在血量归零时设置 `running = false` 并调用 `onBattleEnded(false, currentFloor)`。单独设置 `coreHp = 0` 再调用血条更新，甚至手动调用这次终点检查，在没有敌人到达时都不会直接执行结算分支。强行调用 `onBattleEnded` 会跳过前面的原生事件，服务端上报和奖励是否一致尚无依据。因此保留 1 血让怪物走完整条原生死亡链路更稳妥。

收尾的随机 1–100 伤害在既有 `_dealDamage` 入口生效，保留原生事件顺序。冰冻/减速并未取消，所以仍可能等不到三分钟内结算；此时 GUI 暂停等待人工恢复，不把这场误算为完成。

后续实现顺序建议为：先独立攻速倍率，再风系 CD 和各系概率，最后做整体加速的同步时钟实验。当前版本没有引入这些新开关。
