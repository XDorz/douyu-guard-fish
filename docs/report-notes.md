本记录分析用户提供的 2026-10-30 完整单场日志，以及同日读取到的[公开游戏脚本](https://shark2.douyucdn.cn/front-publish/vibe-lab-master/act202606-game/assets/index-DIjj-A7p.js)和[游戏配置](https://wconf.douyucdn.cn/resource/common/activity/actgame_w.json)。没有使用用户的账号凭证，也没有向游戏提交测试战斗。原始压缩包和解压数据移至本项目 `logs/diagnostics/debug-20261030/`，不进入仓库。

日志中有 15 次 `stageEventReport` 成功响应，以及一次 `stageEnd` 成功响应（`status=20`）。旧采集器没有保存请求体，因此日志本身无法给出本场真实提交的伤害统计。引擎快照中的卡牌伤害合计 394,946，基地为 0 血、已停止，发射计数为 41；这些是本地采样值，不能直接当作请求内容。

公开前端确认了以下请求结构：

| 请求 | 主要字段与含义 |
| --- | --- |
| `stageEventReport` | 对局身份、`stage`、`logId`、`eventType`、`eventJson`、`timestamp`、签名。当前 `eventJson` 为空；事件类型为出怪波次、小怪事件、迷你 Boss 事件 |
| `stageEnd` | 对局身份、`stage`、`result`、`curHp`、`logIdList`、`timestamp`、签名；`stat` 是 JSON 字符串，包含 `totalDmg`、`cardDmg`、`skillDmg`、`invalidDmg` |
| `nextStage` | 对局身份、关卡、技能选择、时间及签名 |

`totalDmg` 不是屏幕显示的实际累计伤害：原生结算把它归到本关已生成敌人的总血量，并调整差额，使卡牌伤害、绝技伤害及未造成的伤害合计与它一致。`cardDmg` 是按卡汇总的伤害；`skillDmg` 对应清屏绝技；`invalidDmg` 包含未打掉的部分。助手保留原生统计和提交链路，不重写这些字段或签名。

事件上报也不是完整的实际杀敌列表：小怪首次事件后按原生概率抽样；而前端监听的 `onEnemyDefeated` 在怪物抵达基地时也会触发。助手的实际杀敌统计使用 `_killEnemy`，两者不能直接等同。

已确认的结算请求中没有直接携带 `attackSpeed`、分系概率、CD、`_floorCardStats.attacks/skills` 或冰土控制次数。修改它们仍会改变各卡伤害、结果和事件时间，不能由“没有直接上传该字段”推出服务端不校验。服务端下发的卡牌、怪物、对局信息和次数均应以接口为准。当前没有服务端源码，不能给出“哪些值一定可以随意改”的保证。

冰和土再次触发时，原生方法把剩余控制时间与本次持续时间取较大值，不把两段时间相加。冰冻还会清除当前减速剩余时间。再次触发会增加本地技能计数；已确认的上报路径没有直接发送这个计数。具体影响仍需通过新版请求日志与服务端响应验证。

v1.2.1 的 Debug 增加 `api_request` 并保留 `api_response`，会解析 JSON、表单、查询参数及内嵌的 `stat` / `logIdList`，保留字段结构与数值；对局 token、签名、账号等信息脱敏。新日志能够区分“请求实际提交了什么”与“本地引擎持有什么”。对局过程中及结束请求均会记录，运行回到主界面后再结束助手即可。

每日次数方面，用户日志里的 `battleRound` 从开战前 27 增至开战后 28，结算后仍为 28。仅凭它无法证明每日清零口径。公开配置明确有 `taskType=4`、`cycle=2` 的每日挑战任务，当前最高档为任务 60392「每日发起挑战满30场」，另有 10、20 场档。前端从 `/actgame/userTask/panelInfo` 读取 `dayTasks`，按 `taskId` 与配置匹配。

助手使用同一任务接口，按配置选择最高档，读取其 `num` 进度并显示在免费次数旁。开局、结算和运行中最多每分钟查询，暂停期间不查询；未读取到目标任务时明确标为未知，`battleRound` 只作为另外标注的开战计数。未登录环境只能确认接口返回“用户未登录”；登录后的任务进度格式与数值需要本次 Debug 实测确认。
