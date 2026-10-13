# 保卫鱼娘 · 自动挂机助手

斗鱼直播间 H5 互动游戏「保卫鱼娘」的自动化工具。Windows 桌面 GUI + 免安装单文件 exe。

> 仅供学习研究使用。使用本工具可能违反平台用户协议，账号风险自负。

## 功能

- **自动挂机**：自动「开启挑战」→ 自动打完一整场 → 自动开下一场，用完每日 **10 次免费次数**自动停止
- **自动选技能**：开局"跳过 BOSS 关"技能三选一 + 战中每 5 关的三选一，按阵容（土系主力 + SR 猛犸象仔）智能评分挑选
- **血条自动回满**：战斗基地血量低于阈值（默认 5）时自动回满（朝满值 20 回血，本地运行时直改，免费）。开启后单场可打到服务端 10 分钟上限（约 10~15 关）；服务端到点掐断时自动识别并刷新恢复、继续下一场
- **自动领宝箱**：战斗中宝箱随出随领，结算宝箱自动领取
- **数据抓包**：所有游戏接口响应落盘 `logs/api_*.jsonl`；每场战报 `logs/battles_*.jsonl`（起始关/结束关/用时/技能选择/回血次数/血量时间线）
- **安全红线**：**绝技、加速、技能刷新等一切需要鱼翅（付费）的功能一律不自动点击**；免费用尽后仅使用免费获取的**银币**续打（10 银币/局，余额不足自动停止）

## 快速开始（免安装）

1. 到 [Releases](../../releases) 下载 `guard-fish-assistant-v*-win64.zip`，解压
2. 运行 `保卫鱼娘助手.exe`
3. 首次使用点「登录斗鱼」登录账号（登录态保存在本机 `chrome_profile/`，有效期约 7 天）
4. 填房间号（默认 `12306`）→ 点「开始挂机」

**运行要求**：Windows 10/11 x64 + Google Chrome 或 Microsoft Edge（程序自动查找）。无需安装 Python。

## 命令行模式

```
保卫鱼娘助手.exe --login                    # 打开浏览器手动登录
保卫鱼娘助手.exe --run --rounds 10          # 挂机 10 场
保卫鱼娘助手.exe --run --rounds 10 --headless   # 后台模式（不显示浏览器）
保卫鱼娘助手.exe --selftest                 # 自检（driver / 浏览器可用性）
```

窗口程序没有控制台，命令行日志写入 exe 同级目录的 `run.log`。

## 从源码运行

```bash
pip install playwright
python yu_gui.py            # 图形界面
python bot.py --run --rounds 1   # 命令行
```

需要系统 Python 3.11+（打包用的 GUI 依赖 tkinter，需完整版 Python）。

## 打包

```bat
打包exe.bat
```

产物：`保卫鱼娘自动挂机\保卫鱼娘助手.exe`。需要完整版 Python（自带 tkinter）。

Playwright 官方没有 PyInstaller hook，`yu_gui.spec` 里手动把 `site-packages/playwright/driver`（node.exe + cli.js，约 102MB）打进了包。

## 项目结构

```
bot.py                    核心：Playwright 驱动 + 页面状态机 + 自动决策
yu_gui.py                 桌面 GUI + CLI 入口
yu_gui.spec               PyInstaller 打包配置
打包exe.bat               一键打包
启动桌面助手.bat           优先跑 exe，否则跑源码
重新登录.bat              打开浏览器重新登录斗鱼
probe/                    开发期探测脚本（探明机制过程，可忽略）
```

## 原理简述

- 直接打开游戏切片页 `douyu.com/pages/vibe-lab-act202606-game/mine?rid=<房间号>`，不依赖直播间入口
- 游戏接口全部在 `/japi/revenuenc/web/actgame/**`：`battleStart`（开战，消耗 1 次免费次数）→ `stageStart` → 战斗由页面引擎自动推进（`stageEventReport` 自动上报）→ `stageEnd` → `nextStage`（带技能选择）→ 循环至基地血量归零或 10 分钟上限 → `receiveBox` 领宝箱
- 页内注入 fetch/XHR hook 实时抓取接口数据（状态展示 + 落盘），Python 侧用状态机驱动 DOM 点击
- 技能评分：全卡攻击/血量 > 全卡攻速 > 土系（主力）> 雷/火 > 冰/风；「绝技+1」因不使用绝技评为最低
- 付费识别：绝技/加速/刷新确认弹窗均含"送出礼物（N鱼翅）"字样 —— 自动化代码**从不点击**这些入口（连入口按钮本身也不点）

## 数据目录

默认存在 exe 同级：`chrome_profile/`（登录态）、`logs/`（抓包 + 战报 + 异常截图）。  
exe 所在目录不可写（如装在 `Program Files`）时自动回退到 `%LOCALAPPDATA%\GuardFishBot`。

## 免责声明

本项目仅用于技术学习。游戏有每日 10 次免费挑战，自动化操作会产生异常行为特征，可能导致成绩无效或账号风控。请自行评估风险，勿用于商业用途。
