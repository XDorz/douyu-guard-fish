"""左对齐的实时攻速/分系编辑器，数字在失焦或回车后提交。"""
import math
import tkinter as tk
from tkinter import ttk

from combat_controls import EFFECTS, EFFECT_LABELS, PARAMETERS, default_controls, normalize_controls


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
        # 数字输入只在回车、失焦或点击其它区域时提交，避免输入 100
        # 时依次把 1、10、100 发送到游戏。
        self.root.bind_all('<Button-1>', self._click_outside_editor, add='+')

    def _group(self, key, parent, title):
        group = {
            'enabled': tk.BooleanVar(value=False), 'mode': tk.StringVar(value='倍率'),
            'value': tk.StringVar(value='1'), 'last_mode': 'multiplier',
            'cache': {'fixed': str(PARAMETERS[key][2]) if key in PARAMETERS else
                     '1' if key == 'attack_speed' else ('40' if key == 'ice' else '50'),
                      'multiplier': '1'},
        }
        self.groups[key] = group
        first, second = ttk.Frame(parent), ttk.Frame(parent)
        first.pack(anchor='w', fill='x')
        compact = key not in EFFECTS
        if compact:
            second = first
        else:
            second.pack(anchor='w', fill='x', pady=(5, 0))
        ttk.Checkbutton(first, text=title, variable=group['enabled'],
                        command=self.flush).pack(side='left', padx=(0, 4))
        box = ttk.Combobox(first, textvariable=group['mode'], values=('倍率', '固定'),
                           width=5, state='readonly', font=self.font)
        box.pack(side='left', padx=(0, 4))
        box.bind('<<ComboboxSelected>>', lambda _e: self._mode_changed(key))
        group['label'] = ttk.Label(second, text='倍率', font=self.small_font)
        if not compact:
            group['label'].pack(side='left')
        entry = ttk.Entry(second, textvariable=group['value'], width=5, font=self.font)
        entry.pack(side='left', padx=(4, 3))
        entry.bind('<Return>', lambda _e: self.flush())
        entry.bind('<FocusOut>', lambda _e: self.flush())
        group['unit'] = ttk.Label(second, text='倍', font=self.small_font)
        group['unit'].pack(side='left')
        if key == 'wind':
            group['cd'] = tk.StringVar(value='-1')
            ttk.Label(second, text='CD', font=self.small_font).pack(side='left', padx=(8, 4))
            cd = ttk.Entry(second, textvariable=group['cd'], width=4, font=self.font)
            cd.pack(side='left')
            cd.bind('<Return>', lambda _e: self.flush())
            cd.bind('<FocusOut>', lambda _e: self.flush())
            ttk.Label(second, text='秒', font=self.small_font).pack(side='left', padx=(3, 0))
        return first

    def _click_outside_editor(self, event):
        if self.loading:
            return
        cls = event.widget.winfo_class()
        if cls not in ('Entry', 'TEntry', 'Combobox', 'TCombobox'):
            self.flush()
        if cls in ('Tk', 'Toplevel', 'Frame', 'TFrame', 'Label', 'TLabel',
                   'Labelframe', 'TLabelframe', 'PanedWindow', 'TPanedwindow', 'TSeparator'):
            try:
                self.root.focus_set()
            except tk.TclError:
                pass

    def build_speed(self, parent):
        row = ttk.Frame(parent)
        row.pack(anchor='w', pady=(0, 6))
        speed = ttk.Frame(row)
        speed.pack(side='left', padx=(0, 16))
        self._group('attack_speed', speed, '全局攻速')
        repeat = ttk.Frame(row)
        repeat.pack(side='left')
        ttk.Checkbutton(repeat, text='冰可重复', variable=self.repeat_ice,
                        command=self.flush).pack(side='left', padx=(0, 12))
        ttk.Checkbutton(repeat, text='土可重复', variable=self.repeat_earth,
                        command=self.flush).pack(side='left')
        earth = ttk.Frame(parent)
        earth.pack(anchor='w', pady=(0, 2))
        self._group('earth_slow', earth, PARAMETERS['earth_slow'][0])

    def build_effects(self, parent):
        ttk.Separator(parent, orient='horizontal').pack(fill='x', pady=(7, 10))
        panel = ttk.Frame(parent)
        panel.pack(anchor='nw', fill='x')
        panel.columnconfigure(0, weight=1)
        panel.columnconfigure(2, weight=1)
        ttk.Separator(panel, orient='vertical').grid(row=0, column=1, rowspan=3, sticky='ns', padx=12)
        ttk.Separator(panel, orient='horizontal').grid(row=1, column=0, columnspan=3, sticky='ew', pady=8)
        for index, name in enumerate(EFFECTS):
            frame = ttk.Frame(panel)
            frame.grid(row=(index // 2) * 2, column=(index % 2) * 2, sticky='nw')
            self._group(name, frame, EFFECT_LABELS[name])
            key = {'thunder': 'thunder_count', 'fire': 'fire_crit', 'ice': 'ice_duration'}.get(name)
            if key:
                extra = ttk.Frame(frame)
                extra.pack(anchor='w', pady=(6, 0))
                self._group(key, extra, PARAMETERS[key][0])
        notes = ttk.Label(parent, text='风系 CD：-1 原速，最低 0.5 秒。时间单位为游戏秒。\n数字失焦或回车后应用；各项独立勾选生效。',
                          font=self.small_font, foreground='#666', justify='left', anchor='nw')
        notes.pack(anchor='nw', pady=(9, 0))

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
        unit = PARAMETERS[key][1] if key in PARAMETERS else '秒' if key == 'attack_speed' else '%'
        group['unit'].config(text=unit if fixed else '倍')
        group['label'].config(text='概率' if fixed else '倍率')

    def values(self):
        raw = {'effects': {}, 'parameters': {}, 'repeat_ice': self.repeat_ice.get(), 'repeat_earth': self.repeat_earth.get()}
        for key, group in self.groups.items():
            mode = self._mode(group)
            group['cache'][mode] = group['value'].get()
            values = {'enabled': group['enabled'].get(), 'mode': mode,
                      'multiplier': group['cache']['multiplier'],
                      'probability' if key in EFFECTS else 'fixed': group['cache']['fixed']}
            if key == 'attack_speed':
                raw[key] = values
            elif key in PARAMETERS:
                raw['parameters'][key] = values
            else:
                # GUI 不再施加雷/火/冰的人工 CD，清除旧配置遗留的隐藏限制。
                values['cd'] = group['cd'].get() if 'cd' in group else -1
                raw['effects'][key] = values
        return normalize_controls(raw, self.current)

    @staticmethod
    def _rule(controls, key):
        return (controls['effects'][key] if key in EFFECTS else
                controls['parameters'][key] if key in PARAMETERS else controls[key])

    def _apply(self, commit=False):
        self.job = None
        values = self.values()
        self.loading = True
        try:
            for key, group in self.groups.items():
                normalized = self._rule(values, key)
                field = ('probability' if key in EFFECTS else 'fixed') if self._mode(group) == 'fixed' else 'multiplier'
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
                data = self._rule(self.current, key)
                group['enabled'].set(data['enabled'])
                group['mode'].set('固定' if data['mode'] == 'fixed' else '倍率')
                group['last_mode'] = data['mode']
                group['cache'] = {'fixed': format(data['probability' if key in EFFECTS else 'fixed'], '.12g'),
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
