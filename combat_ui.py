"""左对齐的实时攻速/分系编辑器，统一处理模式缓存、边界和输入防抖。"""
import math
import tkinter as tk
from tkinter import ttk

from combat_controls import EFFECTS, EFFECT_LABELS, default_controls, normalize_controls


class CombatWidgets:
    def __init__(self, root, on_change, font, small_font):
        self.root, self.on_change = root, on_change
        self.font, self.small_font = font, small_font
        self.groups = {}
        self.repeat_ice = tk.BooleanVar(value=False)
        self.repeat_earth = tk.BooleanVar(value=False)
        self.current = default_controls()
        self.loading = False
        self.job = None
        for var in (self.repeat_ice, self.repeat_earth):
            var.trace_add('write', self._schedule)

    def _group(self, key, parent, title):
        group = {
            'enabled': tk.BooleanVar(value=False), 'mode': tk.StringVar(value='倍率'),
            'value': tk.StringVar(value='1'), 'last_mode': 'multiplier',
            'cache': {'fixed': '1' if key == 'attack_speed' else ('40' if key == 'ice' else '50'),
                      'multiplier': '1'},
        }
        self.groups[key] = group
        first, second = ttk.Frame(parent), ttk.Frame(parent)
        first.pack(anchor='w', fill='x')
        if key == 'attack_speed':
            second = first
        else:
            second.pack(anchor='w', fill='x', pady=(5, 0))
        ttk.Checkbutton(first, text=title, variable=group['enabled'],
                        command=self.flush).pack(side='left', padx=(0, 8))
        box = ttk.Combobox(first, textvariable=group['mode'], values=('倍率', '固定'),
                           width=5, state='readonly', font=self.font)
        box.pack(side='left', padx=(0, 8))
        box.bind('<<ComboboxSelected>>', lambda _e: self._mode_changed(key))
        group['label'] = ttk.Label(second, text='倍率', font=self.small_font)
        if key != 'attack_speed':
            group['label'].pack(side='left')
        entry = ttk.Entry(second, textvariable=group['value'], width=6, font=self.font)
        entry.pack(side='left', padx=(4, 3))
        entry.bind('<Return>', lambda _e: self.flush())
        entry.bind('<FocusOut>', lambda _e: self.flush())
        group['unit'] = ttk.Label(second, text='倍', font=self.small_font)
        group['unit'].pack(side='left')
        if key != 'attack_speed':
            group['cd'] = tk.StringVar(value='8' if key == 'wind' else '0')
            ttk.Label(second, text='CD', font=self.small_font).pack(side='left', padx=(12, 4))
            cd = ttk.Entry(second, textvariable=group['cd'], width=5, font=self.font)
            cd.pack(side='left')
            cd.bind('<Return>', lambda _e: self.flush())
            cd.bind('<FocusOut>', lambda _e: self.flush())
            ttk.Label(second, text='秒', font=self.small_font).pack(side='left', padx=(3, 0))
            group['cd'].trace_add('write', self._schedule)
        group['value'].trace_add('write', self._schedule)
        group['enabled'].trace_add('write', self._schedule)
        return first

    def build_speed(self, parent):
        speed = ttk.Frame(parent)
        speed.pack(side='left', padx=(22, 0))
        self._group('attack_speed', speed, '全局攻速')
        ttk.Checkbutton(parent, text='冰可重复', variable=self.repeat_ice,
                        command=self.flush).pack(side='left', padx=(16, 8))
        ttk.Checkbutton(parent, text='土可重复', variable=self.repeat_earth,
                        command=self.flush).pack(side='left')

    def build_effects(self, parent):
        panel = ttk.LabelFrame(parent, text='分系设置', padding=(10, 8))
        panel.pack(anchor='nw', fill='both', expand=True)
        for name in EFFECTS:
            frame = ttk.Frame(panel)
            frame.pack(anchor='w', fill='x', pady=(0, 12))
            self._group(name, frame, EFFECT_LABELS[name])
        notes = ttk.Label(panel, text='固定概率对同系所有卡生效。\n倍率按各卡原有概率计算。\n风系共享 CD，其余按卡计时。\nCD 0 表示不加额外冷却。\n固定攻速值为攻击间隔。\n负倍率减速，0 为原速。\n间隔与 CD 均为游戏内秒。',
                          font=self.small_font, foreground='#666', justify='left', anchor='nw', wraplength=290)
        notes.pack(anchor='nw', fill='x')

    def _schedule(self, *_args):
        if self.loading:
            return
        if self.job is not None:
            self.root.after_cancel(self.job)
        self.job = self.root.after(350, self._apply)

    @staticmethod
    def _mode(group):
        return 'fixed' if group['mode'].get() == '固定' else 'multiplier'

    def _mode_changed(self, key):
        group = self.groups[key]
        mode = self._mode(group)
        group['cache'][group['last_mode']] = group['value'].get()
        self.loading = True
        try:
            group['value'].set(group['cache'][mode])
            group['last_mode'] = mode
            self._update_unit(key)
        finally:
            self.loading = False
        self.flush()

    def _update_unit(self, key):
        group = self.groups[key]
        fixed = self._mode(group) == 'fixed'
        group['unit'].config(text=('秒' if key == 'attack_speed' else '%') if fixed else '倍')
        group['label'].config(text='概率' if fixed else '倍率')

    def values(self):
        raw = {'effects': {}, 'repeat_ice': self.repeat_ice.get(), 'repeat_earth': self.repeat_earth.get()}
        for key, group in self.groups.items():
            mode = self._mode(group)
            group['cache'][mode] = group['value'].get()
            values = {'enabled': group['enabled'].get(), 'mode': mode,
                      'multiplier': group['cache']['multiplier'],
                      'fixed' if key == 'attack_speed' else 'probability': group['cache']['fixed']}
            if key == 'attack_speed':
                raw[key] = values
            else:
                values['cd'] = group['cd'].get()
                raw['effects'][key] = values
        return normalize_controls(raw, self.current)

    def _apply(self, commit=False):
        self.job = None
        values = self.values()
        self.loading = True
        try:
            for key, group in self.groups.items():
                normalized = values[key] if key == 'attack_speed' else values['effects'][key]
                field = ('fixed' if key == 'attack_speed' else 'probability') if self._mode(group) == 'fixed' else 'multiplier'
                entries = [(group['value'], normalized[field])]
                if 'cd' in group:
                    entries.append((group['cd'], normalized['cd']))
                for var, value in entries:
                    try:
                        valid = math.isfinite(float(var.get()))
                    except (TypeError, ValueError, OverflowError):
                        valid = False
                    if valid or commit:
                        var.set(format(value, '.12g'))
        finally:
            self.loading = False
        if values != self.current:
            self.current = values
            self.on_change(values)

    def flush(self):
        if self.job is not None:
            self.root.after_cancel(self.job)
            self.job = None
        self._apply(commit=True)

    def set_controls(self, controls):
        self.current = normalize_controls(controls)
        self.loading = True
        try:
            self.repeat_ice.set(self.current['repeat_ice'])
            self.repeat_earth.set(self.current['repeat_earth'])
            for key, group in self.groups.items():
                data = self.current[key] if key == 'attack_speed' else self.current['effects'][key]
                group['enabled'].set(data['enabled'])
                group['mode'].set('固定' if data['mode'] == 'fixed' else '倍率')
                group['last_mode'] = data['mode']
                group['cache'] = {'fixed': format(data['fixed' if key == 'attack_speed' else 'probability'], '.12g'),
                                  'multiplier': format(data['multiplier'], '.12g')}
                group['value'].set(group['cache'][data['mode']])
                if 'cd' in group:
                    group['cd'].set(format(data['cd'], '.12g'))
                self._update_unit(key)
        finally:
            self.loading = False

    def close(self):
        if self.job is not None:
            self.root.after_cancel(self.job)
            self.job = None
