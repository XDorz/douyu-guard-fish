# -*- coding: utf-8 -*-
"""保卫鱼娘 · 自动挂机助手（桌面 GUI）

用法：python yu_gui.py  或双击生成的 exe
"""
import os
import re
import sys
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import bot as botmod  # noqa: E402
import settings
from combat_ui import CombatWidgets

LOG_Q = queue.Queue()
FONT = ("Microsoft YaHei UI", 10)
FONT_B = ("Microsoft YaHei UI", 10, "bold")
FONT_S = ("Microsoft YaHei UI", 9)
ONE_HIT_MODE_LABELS = (("全额秒杀", "full"), ("概率秒杀", "probability"),
                       ("攻击倍率", "multiplier"), ("攻击力固定", "fixed_attack"))
ONE_HIT_MODE_KEYS = {label: key for label, key in ONE_HIT_MODE_LABELS}


class App:
    def __init__(self, root, debug=False):
        self.root = root
        self.debug = bool(debug)
        self._save_job = None
        self._config_error_logged = False
        self._settings = dict(settings.DEFAULTS)
        self._mode_values = {"probability": "50", "multiplier": "2", "fixed_attack": "100000"}
        self.running = False
        self.thread = None
        self.bot = None
        root.title("保卫鱼娘 · 自动挂机助手 v%s%s" %
                   (botmod.APP_VERSION, " · Debug" if self.debug else ""))
        root.geometry("1240x820")
        root.minsize(1100, 740)
        self._build()
        self._load_config()
        self._log("[系统] 就绪。首次使用请先点「登录斗鱼」登录账号（登录态保存在本机）。")
        self._log("[系统] 数据目录：%s" % botmod.DATA_DIR)
        self._log("[系统] 配置自动保存：%s" % botmod.CONFIG_PATH, "dim")
        try:
            monitor = botmod.ApiMonitor(botmod.baseline_path(botmod.DATA_DIR))
            self._log('[系统] API 监控基准：%s' % monitor.path, 'dim')
            if monitor.error:
                self._log('[系统] ' + monitor.error + '；修复后才能挂机。', 'api_alert')
        except OSError as exc:
            self._log('[系统] API 基准准备失败：%s' % type(exc).__name__, 'api_alert')
        if self.debug:
            self._log("[系统] Debug 模式：开始挂机后自动生成 logs/debug_*.jsonl 诊断文件。")
        self._log("[系统] 说明：只执行免费操作；绝技/加速/技能刷新等付费功能不会自动点击。")
        cands = botmod.chrome_candidates()
        if not cands:
            self._log("[警告] 未检测到 Chrome/Edge，请先安装 Google Chrome", "warn")
        else:
            self._log("[系统] 使用浏览器：%s" % cands[0][0], "dim")
        self.root.after(150, self._pump)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- UI ----------
    def _build(self):
        body = ttk.Frame(self.root, padding=10)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)

        top = ttk.Frame(body)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=3)
        top.columnconfigure(2, weight=2)
        task = ttk.Frame(top)
        task.grid(row=0, column=0, sticky="nw")
        ttk.Separator(top, orient="vertical").grid(row=0, column=1, sticky="ns", padx=12)
        game = ttk.Frame(top)
        game.grid(row=0, column=2, sticky="new")
        self._build_task_controls(task)
        self._build_game_controls(game)

        self.status = tk.StringVar(value="状态：未运行")
        status_label = ttk.Label(body, textvariable=self.status, anchor="w",
                                 font=FONT_B, justify="left", wraplength=780)
        status_label.grid(row=1, column=0, sticky="ew", pady=(10, 7))
        body.bind("<Configure>", lambda e: status_label.configure(wraplength=max(300, e.width - 20)))

        log_frame = ttk.Frame(body)
        log_frame.grid(row=2, column=0, sticky="nsew")
        self.text = tk.Text(log_frame, wrap="word", height=22, width=1, font=("Consolas", 10))
        sb = ttk.Scrollbar(log_frame, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        for tag, color in (("warn", "#ef6c00"), ("error", "#c62828"),
                           ("win", "#2e7d32"), ("dim", "#888")):
            self.text.tag_config(tag, foreground=color)
        self.text.tag_config("api_alert", foreground="#c62828", font=("Microsoft YaHei UI", 11, "bold"))

    def _build_task_controls(self, parent):
        parent.columnconfigure(0, weight=1)
        header = ttk.Frame(parent)
        header.grid(row=0, column=0, sticky="ew")
        self.rid = tk.StringVar(value=botmod.DEFAULT_RID)
        self.rounds = tk.StringVar(value="0")
        self.gap_min = tk.StringVar(value="5")
        self.gap_max = tk.StringVar(value="15")

        def field(row, title, var, width=5, suffix=""):
            group = ttk.Frame(row)
            group.pack(side="left", padx=(0, 12))
            ttk.Label(group, text=title, font=FONT).pack(side="left")
            ttk.Entry(group, textvariable=var, width=width, font=FONT).pack(side="left", padx=(4, 3))
            if suffix:
                ttk.Label(group, text=suffix, font=FONT_S, foreground="#666").pack(side="left")
            return group

        field(header, "房间号", self.rid, 8)
        field(header, "场数", self.rounds, 4, "0=不限")
        gap = field(header, "场间等待", self.gap_min, 4)
        ttk.Label(gap, text="~", font=FONT).pack(side="left")
        ttk.Entry(gap, textvariable=self.gap_max, width=4, font=FONT).pack(side="left", padx=3)
        ttk.Label(gap, text="秒", font=FONT_S, foreground="#666").pack(side="left")
        actions = ttk.Frame(parent)
        actions.grid(row=1, column=0, sticky="w", pady=(9, 9))
        ttk.Style(self.root).configure("Run.TButton", font=FONT_B, padding=(5, 3))
        buttons = (("start_btn", "开始挂机", self.start, "normal"),
                   ("end_btn", "本场正常结束", self.end_current, "disabled"),
                   ("pause_btn", "暂时停止", self.pause_or_resume, "disabled"),
                   ("stop_btn", "终止并退出", self.stop, "disabled"))
        for column, (name, text, command, state) in enumerate(buttons):
            button = ttk.Button(actions, text=text, command=command, state=state,
                                style="Run.TButton", width=-10)
            button.grid(row=0, column=column, sticky="w", padx=(0, 6 if column < 3 else 0))
            setattr(self, name, button)

        targets = ttk.Frame(parent)
        targets.grid(row=2, column=0, sticky="w")
        self.stop_stage = tk.StringVar(value="0")
        self.advance_stages = tk.StringVar(value="0")
        self.max_minutes = tk.StringVar(value="13")
        field(targets, "固定关卡", self.stop_stage, 5, "关")
        field(targets, "推进", self.advance_stages, 5, "关")
        field(targets, "单场最长", self.max_minutes, 5, "分钟")
        ttk.Label(targets, text="关卡目标 0=关闭", font=FONT_S, foreground="#666").pack(side="left")

        options = ttk.Frame(parent)
        options.grid(row=3, column=0, sticky="w", pady=(7, 0))
        self.headless_var = tk.BooleanVar(value=False)
        self.coin_var = tk.BooleanVar(value=True)
        self.finish_fast_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(options, text="隐藏浏览器", variable=self.headless_var).pack(side="left", padx=(0, 16))
        ttk.Checkbutton(options, text="免费用尽后银币续打", variable=self.coin_var).pack(side="left", padx=(0, 16))
        ttk.Checkbutton(options, text="尽快完成", variable=self.finish_fast_var,
                        command=self._runtime_options_changed).pack(side="left")
        tools = ttk.Frame(parent)
        tools.grid(row=4, column=0, sticky="w", pady=(10, 0))
        ttk.Button(tools, text="登录斗鱼", command=self.login).pack(side="left", padx=(0, 6))
        ttk.Button(tools, text="打开数据目录", command=self.open_dir).pack(side="left")

    def _build_game_controls(self, parent):
        common = ttk.Frame(parent)
        common.pack(anchor="w", fill="x")
        first = ttk.Frame(common)
        first.pack(anchor="w", pady=(0, 9))
        hp = ttk.Frame(first)
        hp.pack(side="left", padx=(0, 16))
        self.hp_var = tk.BooleanVar(value=True)
        self.hp_threshold = tk.StringVar(value="5")
        ttk.Checkbutton(hp, text="血量低于", variable=self.hp_var,
                        command=self._runtime_options_changed).pack(side="left")
        hp_entry = ttk.Entry(hp, textvariable=self.hp_threshold, width=4, font=FONT)
        hp_entry.pack(side="left", padx=3)
        hp_entry.bind("<Return>", lambda _e: self._runtime_options_changed())
        hp_entry.bind("<FocusOut>", lambda _e: self._runtime_options_changed())
        ttk.Label(hp, text="滴回满", font=FONT_S, foreground="#666").pack(side="left")

        damage = ttk.Frame(first)
        damage.pack(side="left")
        self.one_hit_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(damage, text="秒杀", variable=self.one_hit_var,
                        command=self._runtime_options_changed).pack(side="left")
        self.one_hit_mode = tk.StringVar(value="全额秒杀")
        self._last_one_hit_mode = "full"
        self.one_hit_mode_box = ttk.Combobox(
            damage, textvariable=self.one_hit_mode, width=10, state="readonly",
            values=[label for label, _ in ONE_HIT_MODE_LABELS], font=FONT)
        self.one_hit_mode_box.pack(side="left", padx=(4, 4))
        self.one_hit_mode_box.bind("<<ComboboxSelected>>", self._one_hit_mode_changed)
        self.one_hit_value = tk.StringVar(value="50")
        self.one_hit_value_entry = ttk.Entry(damage, textvariable=self.one_hit_value, width=7, font=FONT)
        self.one_hit_value_entry.pack(side="left", padx=(0, 2))
        self.one_hit_value_entry.bind("<Return>", lambda _e: self._runtime_options_changed())
        self.one_hit_value_entry.bind("<FocusOut>", lambda _e: self._runtime_options_changed())
        self.one_hit_unit = ttk.Label(damage, text="", font=FONT_S, foreground="#666", width=2)
        self.one_hit_unit.pack(side="left")
        self._update_one_hit_mode_ui()

        self.combat_widgets = CombatWidgets(self.root, self._combat_controls_changed, FONT, FONT_S)
        self.combat_widgets.build_speed(common)
        self.combat_widgets.build_effects(parent)

    # ---------- 配置（只在 GUI 线程读写 tkinter 变量） ----------
    def _config_vars(self):
        return {
            "rid": self.rid, "rounds": self.rounds, "gap_min": self.gap_min,
            "gap_max": self.gap_max, "hp_guard": self.hp_var,
            "hp_threshold": self.hp_threshold, "coin_play": self.coin_var,
            "finish_fast": self.finish_fast_var,
            "one_hit": self.one_hit_var, "headless": self.headless_var,
            "stop_stage": self.stop_stage, "advance_stages": self.advance_stages,
            "max_minutes": self.max_minutes,
        }

    def _load_config(self):
        self._settings, error = settings.load(botmod.CONFIG_PATH)
        for key, var in self._config_vars().items():
            var.set(self._settings[key])
        self.combat_widgets.set_controls(self._settings["combat"])
        self._desired_combat = self.combat_widgets.values()
        mode = self._settings["one_hit_mode"]
        self.one_hit_mode.set(dict((key, label) for label, key in ONE_HIT_MODE_LABELS)[mode])
        self._last_one_hit_mode = mode
        self._mode_values = {"probability": self._settings["one_hit_probability"],
                             "multiplier": self._settings["attack_multiplier"],
                             "fixed_attack": self._settings["attack_fixed"]}
        self.one_hit_value.set(self._mode_values.get(mode, "50"))
        self._update_one_hit_mode_ui()
        if error:
            self._log("[系统] 配置读取失败，使用默认配置：%s" % error, "warn")
        for var in list(self._config_vars().values()) + [self.one_hit_mode, self.one_hit_value]:
            var.trace_add("write", self._schedule_config_save)

    def _schedule_config_save(self, *_args):
        if self._save_job is not None:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(500, self._save_config)

    def _save_config(self):
        if self._save_job is not None:
            self.root.after_cancel(self._save_job)
            self._save_job = None
        values = {key: var.get() for key, var in self._config_vars().items()}
        values["combat"] = self.combat_widgets.values()
        mode = self._one_hit_mode_key()
        if mode in self._mode_values:
            self._mode_values[mode] = self.one_hit_value.get()
        values.update(one_hit_mode=mode,
                      one_hit_probability=self._mode_values["probability"],
                      attack_multiplier=self._mode_values["multiplier"],
                      attack_fixed=self._mode_values["fixed_attack"])
        # 输入框暂时为空或格式无效时保留上次有效值，不把半输入状态存到文件。
        self._settings = settings.normalize(values, self._settings)
        try:
            settings.save(botmod.CONFIG_PATH, self._settings)
            self._config_error_logged = False
        except OSError as exc:
            if not self._config_error_logged:
                self._log("[系统] 配置保存失败：%s" % exc, "warn")
                self._config_error_logged = True

    # ---------- 日志 ----------
    def _log(self, msg, tag=None):
        self.text.insert("end", msg + "\n", tag)
        self.text.see("end")
        try:
            if int(self.text.index("end-1c").split(".")[0]) > 3000:
                self.text.delete("1.0", "800.0")
        except Exception:
            pass

    def _pump(self):
        try:
            while True:
                item = LOG_Q.get_nowait()
                if isinstance(item, tuple):
                    line, level = item
                else:
                    line, level = item, None
                tag = None
                if level == "api_alert":
                    tag = "api_alert"
                elif level == "warn" or "!!" in line or "警告" in line:
                    tag = "warn"
                elif level == "error" or "异常" in line or "失败" in line:
                    tag = "error"
                elif "完成" in line or "结束" in line and "运行" in line:
                    tag = "win"
                self._log(line, tag)
                m = re.search(r"第 (\d+)/(\d+) 场", line)
                if m:
                    self._round = "%s/%s" % (m.group(1), m.group(2))
                m2 = re.search(r"进行中 t=(\d+)s 第(\S+)关 HP=(\S+) 免费剩余=(\S+)", line)
                if m2:
                    self._hud = ("进行中 %ss｜第%s关｜HP %s"
                                 % (m2.group(1), m2.group(2), m2.group(3)))
                m3 = re.search(r"战报：起始第(\S+)关 → 到达第(\S+)关，用时 (\d+) 秒", line)
                if m3:
                    self._hud = "上场：第%s→%s关，用时%ss" % (m3.group(1), m3.group(2), m3.group(3))
        except queue.Empty:
            pass
        except Exception:
            pass
        self._refresh_run_state()
        self.root.after(150, self._pump)

    def _refresh_run_state(self):
        b = self.bot
        active = bool(self.running and b and not b._stop.is_set())
        self.end_btn.config(state="normal" if active and b._battle_active
                            and not b._natural_end_requested and not b._paused else "disabled")
        paused = bool(b and b._paused)
        pending = bool(b and b._pause_requested and not paused)
        self.pause_btn.config(text="恢复战斗" if paused else ("正在收尾…" if pending else "暂时停止"),
                              state="normal" if active and not pending else "disabled")
        if b:
            st = b.status
            today = st.get("daily_count")
            def count_text(value):
                if isinstance(value, float) and value.is_integer():
                    return str(int(value))
                return str(value)
            today_text = ("%s/%s" % (count_text(today), count_text(st.get("daily_target", 30)))
                          if today is not None else "未读取")
            if today is None and st.get('battleRound') is not None:
                today_text += "（开战计数 %s）" % count_text(st['battleRound'])
            free_text = count_text(st["freeNum"]) if st.get("freeNum") is not None else "-"
            self.status.set("%s · 已完成 %s/%s 场｜免费剩余 %s｜今日场次 %s｜%s｜回血 %s 次｜银币 %s"
                            % ("正在收尾后暂停" if pending else st.get("state", "运行中"),
                               st.get("rounds_done", 0), b.rounds if b.rounds > 0 else "不限",
                               free_text, today_text, getattr(self, "_hud", "启动中"),
                               st.get("refills", 0), st.get("coin", "-")))

    # ---------- 控制 ----------
    def _one_hit_mode_key(self):
        return ONE_HIT_MODE_KEYS.get(self.one_hit_mode.get(), "full")

    def _one_hit_values(self):
        mode = self._one_hit_mode_key()
        try:
            value = float((self.one_hit_value.get() or "").strip())
        except ValueError:
            value = (50.0 if mode == "probability" else
                     2.0 if mode == "multiplier" else 100000.0)
        if mode == "probability":
            value = max(0.0, min(value, 100.0))
            self.one_hit_value.set("%g" % value)
            return mode, value, 2.0, 100000.0
        if mode == "multiplier":
            value = max(value, 0.01)
            self.one_hit_value.set("%g" % value)
            return mode, 50.0, value, 100000.0
        if mode == "fixed_attack":
            value = max(value, 1.0)
            self.one_hit_value.set("%g" % value)
            return mode, 50.0, 2.0, value
        return mode, 50.0, 2.0, 100000.0

    def _update_one_hit_mode_ui(self):
        mode = self._one_hit_mode_key()
        if mode == "probability":
            self.one_hit_unit.config(text="%")
            self.one_hit_value_entry.config(state="normal")
        elif mode == "multiplier":
            self.one_hit_unit.config(text="倍")
            self.one_hit_value_entry.config(state="normal")
        elif mode == "fixed_attack":
            self.one_hit_unit.config(text="攻")
            self.one_hit_value_entry.config(state="normal")
        else:
            self.one_hit_unit.config(text="")
            self.one_hit_value_entry.config(state="disabled")

    def _one_hit_mode_changed(self, _event=None):
        mode = self._one_hit_mode_key()
        if mode != self._last_one_hit_mode:
            if self._last_one_hit_mode in self._mode_values:
                self._mode_values[self._last_one_hit_mode] = self.one_hit_value.get()
            if mode in self._mode_values:
                self.one_hit_value.set(self._mode_values[mode])
            self._last_one_hit_mode = mode
        self._update_one_hit_mode_ui()
        self._runtime_options_changed()

    def _combat_controls_changed(self, values):
        self._desired_combat = values
        self._schedule_config_save()
        if self.running and self.bot:
            self.bot.set_runtime_controls(combat_controls=values)

    def _runtime_options_changed(self):
        """复选框点击后通知运行线程；Playwright 调用仍在机器人线程中执行。"""
        self._desired_hp_guard = bool(self.hp_var.get())
        self._desired_one_hit = bool(self.one_hit_var.get())
        mode, probability, multiplier, attack_fixed = self._one_hit_values()
        self._desired_one_hit_mode = mode
        self._desired_one_hit_probability = probability
        self._desired_attack_multiplier = multiplier
        self._desired_attack_fixed = attack_fixed
        self._desired_finish_fast = bool(self.finish_fast_var.get())
        if not (self.running and self.bot):
            return
        try:
            threshold = int((self.hp_threshold.get() or "5").strip() or 5)
        except ValueError:
            threshold = 5
        self.bot.set_runtime_controls(hp_guard=self.hp_var.get(),
                                      one_hit=self.one_hit_var.get(),
                                      hp_threshold=max(threshold, 1),
                                      one_hit_mode=mode,
                                      one_hit_probability=probability,
                                      attack_multiplier=multiplier,
                                      attack_fixed=attack_fixed,
                                      finish_fast=self.finish_fast_var.get())
        self._log("[系统] 已提交实时开关：回血=%s，秒杀=%s，尽快完成=%s（策略=%s，失焦/回车后生效）"
                  % ("开" if self.hp_var.get() else "关",
                     "开" if self.one_hit_var.get() else "关",
                     "开" if self.finish_fast_var.get() else "关",
                     botmod.one_hit_mode_text(mode, probability, multiplier, attack_fixed)), "dim")

    def start(self):
        if self.running:
            return
        if not botmod.chrome_candidates():
            messagebox.showerror("缺少浏览器",
                                 "未检测到 Chrome / Edge。\n请先安装 Google Chrome，"
                                 "或设置环境变量 CHROME_PATH 指向 chrome.exe")
            return
        try:
            rid = (self.rid.get() or botmod.DEFAULT_RID).strip()
            rounds = int((self.rounds.get() or "0").strip() or 0)
            gap_min = float((self.gap_min.get() or "0").strip() or 0)
            gap_max = float((self.gap_max.get() or "0").strip() or 0)
            hp_th = int((self.hp_threshold.get() or "5").strip() or 5)
            stop_stage = int((self.stop_stage.get() or "0").strip() or 0)
            advance_stages = int((self.advance_stages.get() or "0").strip() or 0)
            max_minutes = float((self.max_minutes.get() or "13").strip() or 13)
            one_hit_mode, one_hit_probability, attack_multiplier, attack_fixed = self._one_hit_values()
        except ValueError:
            messagebox.showwarning("输入错误", "房间号/场数/等待时间/回血阈值/关卡目标/时限/秒杀参数格式不对")
            return
        if rounds < 0:
            messagebox.showwarning("输入错误", "场数不能为负数（0=不限）")
            return
        if stop_stage < 0 or advance_stages < 0:
            messagebox.showwarning("输入错误", "关卡目标不能为负数（0=关闭）")
            return
        if max_minutes <= 0:
            messagebox.showwarning("输入错误", "单场最长时间必须大于 0 分钟")
            return
        if one_hit_mode == "probability" and not 0 <= one_hit_probability <= 100:
            messagebox.showwarning("输入错误", "概率秒杀必须填写 0~100%")
            return
        if one_hit_mode == "multiplier" and attack_multiplier <= 0:
            messagebox.showwarning("输入错误", "攻击倍率必须大于 0")
            return
        if one_hit_mode == "fixed_attack" and attack_fixed < 1:
            messagebox.showwarning("输入错误", "固定攻击力必须大于 0")
            return
        if hp_th < 1:
            hp_th = 1
        if gap_max < gap_min:
            gap_min, gap_max = gap_max, gap_min
            self.gap_min.set("%g" % gap_min)
            self.gap_max.set("%g" % gap_max)

        self.combat_widgets.flush()
        self._desired_combat = self.combat_widgets.values()
        self._save_config()
        self.bot = None
        self.running = True
        self._desired_hp_guard = bool(self.hp_var.get())
        self._desired_one_hit = bool(self.one_hit_var.get())
        self._desired_one_hit_mode = one_hit_mode
        self._desired_one_hit_probability = one_hit_probability
        self._desired_attack_multiplier = attack_multiplier
        self._desired_attack_fixed = attack_fixed
        self._desired_finish_fast = bool(self.finish_fast_var.get())
        self._hud = ""
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self._log("[系统] 开始挂机：房间 %s，场数 %s，场间等待 %.0f~%.0f 秒%s"
                  % (rid, rounds if rounds > 0 else "不限", gap_min, gap_max,
                     "（后台模式）" if self.headless_var.get() else ""))
        self._log("[系统] 单场最长时间：%.1f 分钟；绝对关卡目标：%s；推进目标：%s"
                  % (max_minutes, stop_stage or "关", advance_stages or "关"))
        if self.hp_var.get():
            self._log("[系统] 回血守护：开（血量 < %d 自动回满）" % hp_th)
        self._log("[系统] 银币续打：%s" % ("开（免费用尽后银币≥10 自动继续）" if self.coin_var.get() else "关"))
        self._log("[系统] 尽快完成：%s" % ("开（每场开始即正常收尾，场次继续推进）"
                                          if self.finish_fast_var.get() else "关"))
        if self.one_hit_var.get():
            self._log("[系统] 秒杀模式：开（策略=%s）" %
                      botmod.one_hit_mode_text(one_hit_mode, one_hit_probability,
                                               attack_multiplier, attack_fixed))
        self.thread = threading.Thread(target=self._run,
                                       args=(rid, rounds, gap_min, gap_max, self.headless_var.get(),
                                             self.hp_var.get(), hp_th, self.coin_var.get(),
                                             self.one_hit_var.get(), max_minutes * 60,
                                             stop_stage, advance_stages,
                                             one_hit_mode, one_hit_probability,
                                             attack_multiplier, attack_fixed,
                                             self.finish_fast_var.get()),
                                       daemon=True)
        self.thread.start()

    def _run(self, rid, rounds, gap_min, gap_max, headless, hp_guard, hp_th, coin_play,
             one_hit=False, max_battle_seconds=botmod.MAX_BATTLE_SECONDS,
             stop_stage=0, advance_stages=0, one_hit_mode="full",
             one_hit_probability=50.0, attack_multiplier=2.0,
             attack_fixed=100000.0, finish_fast=False):
        try:
            self.bot = botmod.GuardFishBot(rid=rid, rounds=rounds, gap_min=gap_min,
                                           gap_max=gap_max, headless=headless,
                                           hp_guard=hp_guard, hp_threshold=hp_th,
                                           coin_play=coin_play, one_hit=one_hit,
                                           max_battle_seconds=max_battle_seconds,
                                           stop_stage=stop_stage,
                                           advance_stages=advance_stages,
                                           one_hit_mode=one_hit_mode,
                                           one_hit_probability=one_hit_probability,
                                           attack_multiplier=attack_multiplier,
                                           attack_fixed=attack_fixed,
                                           finish_fast=finish_fast,
                                           debug=self.debug, pause_on_timeout=True,
                                           combat_controls=self._desired_combat)
            # 如果用户在工作线程刚启动的瞬间点击了复选框，以当前 GUI 值为准。
            self.bot.set_runtime_controls(hp_guard=self._desired_hp_guard,
                                          one_hit=self._desired_one_hit,
                                          one_hit_mode=self._desired_one_hit_mode,
                                          one_hit_probability=self._desired_one_hit_probability,
                                          attack_multiplier=self._desired_attack_multiplier,
                                          attack_fixed=self._desired_attack_fixed,
                                          finish_fast=self._desired_finish_fast,
                                          combat_controls=self._desired_combat)
            self.bot.add_log_sink(lambda line, level=None: LOG_Q.put((line, level)))
            self.bot.start()
            self.bot._thread.join()
        except Exception as e:
            LOG_Q.put(("[异常] %s" % e, "error"))
        finally:
            self.running = False
            self.root.after(0, self._on_finished)

    def _on_finished(self):
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.end_btn.config(state="disabled")
        self.pause_btn.config(state="disabled", text="暂时停止")
        if self.bot:
            self._refresh_run_state()
        else:
            self.status.set("状态：已停止")
        self._log("[系统] 已停止")

    def login(self):
        if self.running:
            messagebox.showinfo("提示", "挂机运行中，请先停止再登录")
            return
        self._log("[系统] 正在打开登录窗口（登录完成后直接关掉浏览器窗口即可）…")

        def run():
            msg, tag = None, "dim"
            try:
                botmod.open_login(self.rid.get() or botmod.DEFAULT_RID)
                msg = "[系统] 登录窗口已关闭。"
            except Exception as e:
                msg, tag = "[异常] 登录失败：%s" % e, "error"
            self.root.after(0, lambda m=msg, t=tag: self._log(m, t))
        threading.Thread(target=run, daemon=True).start()

    def open_dir(self):
        try:
            os.makedirs(botmod.DATA_DIR, exist_ok=True)
            os.startfile(botmod.DATA_DIR)
        except Exception as e:
            messagebox.showwarning("打不开", str(e))

    def end_current(self):
        if self.running and self.bot and self.bot._battle_active:
            self.bot.request_natural_end("GUI 点击")
            self.end_btn.config(state="disabled")
            self._log("[系统] 已请求本场正常结束，等待游戏结算…", "warn")

    def pause_or_resume(self):
        if not (self.running and self.bot):
            return
        if self.bot._paused:
            self.bot.request_resume()
        else:
            self.bot.request_pause()
        self._refresh_run_state()

    def stop(self):
        if self.bot:
            self.bot.request_stop()
            self._log("[系统] 终止并退出中…（会关闭浏览器，当前场可能作废）", "warn")
        self.stop_btn.config(state="disabled")

    def on_close(self):
        if self.running and self.bot:
            if not messagebox.askokcancel("退出", "挂机还在运行，确定退出？\n（战斗中退出会导致当前一场作废）"):
                return
            self.bot.request_stop()
        self.combat_widgets.close()
        self._save_config()
        self.root.destroy()


def main(debug=False):
    argv = sys.argv[1:]
    debug = debug or "--debug" in argv
    # 有命令行参数时走 bot.py 的 CLI（--run / --login / --selftest）
    if any(a.startswith("-") and a != "--debug" for a in argv):
        botmod.main(default_debug=debug)
        return
    root = tk.Tk()
    try:
        root.call("tk", "scaling", 1.15)
    except Exception:
        pass
    App(root, debug=debug)
    root.mainloop()


if __name__ == "__main__":
    main()
