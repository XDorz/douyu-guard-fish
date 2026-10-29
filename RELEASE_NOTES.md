本版新增单关耗时统计、Debug 诊断文件和 GUI 配置保存，并提供直接下载的 Windows EXE。

- `guard-fish-assistant-v1.1.0-win64.exe`：普通版。
- `guard-fish-assistant-v1.1.0-debug-win64.exe`：Debug 版，开始挂机后自动收集战斗引擎信息。

两版均可直接运行，需本机已安装 Chrome/Edge。保留原目录的 `config.json` 和 `chrome_profile`，更新时将新版 EXE 放进原目录即可。Debug 与普通版共享配置，勿同时挂机。

每关通过后输出耗时，优先使用 `stageStart` / `stageEnd` 响应观测时间；缺少接口时标注估算或中途接管。宝箱不触发通关统计，最后未完成的关卡不标成通关。完整统计保存在 `logs/stages_日期.jsonl`，每场战报也带有 `stageTimes`。

Debug 版将引擎字段、方法源码、卡牌/特效样本、关卡接口结构写入 `logs/debug_时间戳.jsonl`。采集只读，每 15 秒及切关时采样，不增加攻速/加速/特效修改。反馈时提供这一份 Debug 文件即可，无需发送浏览器登录目录。

GUI 修改后自动保存至数据目录的 `config.json`，包括房间、场数、间隔、回血、秒杀策略及各策略参数、关卡目标、单场时限、后台模式；覆盖更新 EXE 后自动恢复。旧版本没有保存过的设置需在本版首次填写一次。

自然结束最长等待 180 秒，状态轮询仍为 0.5 秒；出现结算即按正常流程继续。

验证：Python/JavaScript 回归、GUI 配置重开恢复、Windows 打包。尚未使用真实游戏账号完成端到端验证。
