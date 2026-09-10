# -*- coding: utf-8 -*-
"""시그널방 도우미 - 창.

화면 셋:
  1) 메인       : 핫키 버튼을 누르면 여러 계정이 시간차를 두고 올립니다.
  2) 핫키 설정  : 버튼(핫키)을 만들고 문구/사진/보내는 방식을 정합니다.
  3) 계정 설정  : 텔레그램 사람 계정 로그인(전화번호 → 코드 → 2단계 비밀번호).

고객이 손대야 하는 값은 전부 이 창 안에 있습니다. 설정 파일을 직접 고칠 일도,
검은 콘솔 창도 없습니다.
"""
import os
import queue
import random
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import burst
import card
import config
import hotkeys as hotkeys_mod
import paths
import reporter
import runlog
import store
import tg

PAD = 8
BUTTON_COLORS = ["#2f6fb5", "#2e8b57", "#c0653a", "#7a4fa3", "#b5432f", "#3a7d7d"]


def safe_handler(function):
    """버튼 하나가 터져도 프로그램은 살아 있어야 합니다. --noconsole 이라 예외는
    콘솔이 아니라 대화상자로 보여줘야 보입니다."""
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except tg.TgError as exc:
            runlog.log("안내: %s" % exc)
            messagebox.showwarning("확인해 주세요", str(exc))
        except Exception as exc:  # noqa: BLE001
            runlog.log("예외: %r" % (exc,))
            try:
                reporter.error("GUI 예외: %r" % (exc,), run_log=runlog.text())
            except Exception:  # noqa: BLE001
                pass
            messagebox.showerror("오류", "처리 중 문제가 생겼습니다.\n\n%r" % (exc,))
    return wrapper


class App(object):
    def __init__(self, root, demo=False):
        self.root = root
        self.demo = demo
        self.settings = store.load()
        self.manager = tg.Manager(lambda: self.settings)
        self.manager.start()
        self.events = queue.Queue()
        self.controller = None
        self.busy = False
        self.editing = None

        root.title("%s v%s" % (config.APP_TITLE, config.APP_VERSION))
        root.geometry("1120x820")
        root.minsize(1040, 760)
        self._build_style()
        self._build_widgets()
        runlog.add_sink(lambda line: self.events.put({"event": "log", "line": line}))
        self.root.after(150, self._drain_events)
        self._refresh_all()
        runlog.log("프로그램을 시작했습니다. (v%s)" % config.APP_VERSION)
        if not demo:
            reporter.startup({"settings": store.redacted(self.settings),
                              "accounts": len(self.settings.get("accounts", [])),
                              "hotkeys": [item.get("name") for item
                                          in self.settings.get("hotkeys", [])]})

    # ------------------------------------------------------------- layout
    def _build_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:  # noqa: BLE001
            pass
        base = ("맑은 고딕", 10)
        self.root.option_add("*Font", base)
        style.configure(".", font=base)
        style.configure("Head.TLabel", font=("맑은 고딕", 11, "bold"))
        style.configure("Room.TLabel", font=("맑은 고딕", 11, "bold"), foreground="#1f5fa8")
        style.configure("Sub.TLabel", foreground="#777777")

    def _build_widgets(self):
        outer = ttk.Frame(self.root, padding=PAD)
        outer.pack(fill="both", expand=True)
        self.tabs = ttk.Notebook(outer)
        self.tabs.pack(fill="both", expand=True)

        self.tab_main = ttk.Frame(self.tabs, padding=PAD)
        self.tab_hotkeys = ttk.Frame(self.tabs, padding=PAD)
        self.tab_accounts = ttk.Frame(self.tabs, padding=PAD)
        self.tabs.add(self.tab_main, text="   메인   ")
        self.tabs.add(self.tab_hotkeys, text="   핫키 설정   ")
        self.tabs.add(self.tab_accounts, text="   계정 설정   ")

        self.status = tk.StringVar(value="준비되었습니다.")
        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(PAD, 0))
        ttk.Label(bar, textvariable=self.status).pack(side="left")

        self._build_main_tab()
        self._build_hotkeys_tab()
        self._build_accounts_tab()

    # ---------------------------------------------------------- 메인 화면
    def _build_main_tab(self):
        top = ttk.LabelFrame(self.tab_main, text=" 보낼 방 ", padding=PAD)
        top.pack(fill="x")
        self.room_var = tk.StringVar(value="(방을 선택해 주세요)")
        ttk.Label(top, textvariable=self.room_var, style="Room.TLabel").pack(side="left")
        ttk.Button(top, text="방 선택", command=safe_handler(self.on_pick_room)).pack(side="right")
        ttk.Button(top, text="계정 확인",
                   command=safe_handler(self.on_check_members)).pack(side="right", padx=6)
        self.account_summary = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.account_summary,
                  style="Sub.TLabel").pack(side="right", padx=12)

        body = ttk.Frame(self.tab_main)
        body.pack(fill="both", expand=True, pady=PAD)

        left = ttk.LabelFrame(body, text=" 핫키 (누르면 바로 나갑니다) ", padding=PAD)
        left.pack(side="left", fill="both", expand=True)
        self.hotkey_frame = ttk.Frame(left)
        self.hotkey_frame.pack(fill="both", expand=True)

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(PAD, 0))
        progress = ttk.LabelFrame(right, text=" 진행 상황 ", padding=PAD)
        progress.pack(fill="both", expand=True)
        head = ttk.Frame(progress)
        head.pack(fill="x")
        self.progress_var = tk.StringVar(value="대기중")
        ttk.Label(head, textvariable=self.progress_var, style="Head.TLabel").pack(side="left")
        self.stop_button = tk.Button(head, text="중지", state="disabled", width=8,
                                     bg="#c0392b", fg="white", relief="flat",
                                     activebackground="#e05545", activeforeground="white",
                                     command=safe_handler(self.on_stop))
        self.stop_button.pack(side="right")
        self.progress_bar = ttk.Progressbar(progress, mode="determinate")
        self.progress_bar.pack(fill="x", pady=6)
        self.log_box = tk.Text(progress, height=20, wrap="none", bg="#fbfbfb")
        self.log_box.pack(fill="both", expand=True)
        self.log_box.configure(state="disabled")

        one = ttk.LabelFrame(right, text=" 선택 계정으로 한 마디 ", padding=PAD)
        one.pack(fill="x", pady=(PAD, 0))
        self.one_account = ttk.Combobox(one, state="readonly", width=20)
        self.one_account.pack(side="left")
        self.one_text = tk.StringVar()
        entry = ttk.Entry(one, textvariable=self.one_text)
        entry.pack(side="left", fill="x", expand=True, padx=6)
        entry.bind("<Return>", lambda _event: self.on_one_shot())
        ttk.Button(one, text="보내기", command=safe_handler(self.on_one_shot)).pack(side="right")

    def _refresh_hotkey_buttons(self):
        for child in self.hotkey_frame.winfo_children():
            child.destroy()
        items = self.settings.get("hotkeys", [])
        columns = 2
        for index, hotkey in enumerate(items):
            color = BUTTON_COLORS[index % len(BUTTON_COLORS)]
            cell = ttk.Frame(self.hotkey_frame, padding=4)
            cell.grid(row=index // columns, column=index % columns,
                      sticky="nsew", padx=4, pady=4)
            button = tk.Button(cell, text=hotkey.get("name", "이름없음"),
                               bg=color, fg="white", relief="flat",
                               font=("맑은 고딕", 15, "bold"), height=2,
                               activebackground=color, activeforeground="white",
                               wraplength=260,
                               command=safe_handler(
                                   lambda item=hotkey: self.fire_hotkey(item)))
            button.pack(fill="both", expand=True)
            ttk.Label(cell, text=hotkeys_mod.summary(hotkey),
                      style="Sub.TLabel").pack(anchor="w")
        for column in range(columns):
            self.hotkey_frame.columnconfigure(column, weight=1)
        for row in range((len(items) + columns - 1) // columns):
            self.hotkey_frame.rowconfigure(row, weight=1)
        if not items:
            ttk.Label(self.hotkey_frame,
                      text="핫키 설정 탭에서 버튼을 만들어 주세요.").pack(pady=20)

    # ------------------------------------------------------- 핫키 설정 화면
    def _build_hotkeys_tab(self):
        left = ttk.Frame(self.tab_hotkeys)
        left.pack(side="left", fill="y")
        ttk.Label(left, text="핫키 목록", style="Head.TLabel").pack(anchor="w")
        self.hotkey_list = tk.Listbox(left, width=26, height=18, exportselection=False)
        self.hotkey_list.pack(fill="y", expand=True, pady=6)
        self.hotkey_list.bind("<<ListboxSelect>>", lambda _event: self._load_hotkey_editor())
        row = ttk.Frame(left)
        row.pack(fill="x")
        ttk.Button(row, text="추가", width=6,
                   command=safe_handler(self.on_hotkey_add)).pack(side="left")
        ttk.Button(row, text="복사", width=6,
                   command=safe_handler(self.on_hotkey_copy)).pack(side="left", padx=4)
        ttk.Button(row, text="삭제", width=6,
                   command=safe_handler(self.on_hotkey_delete)).pack(side="left")
        row2 = ttk.Frame(left)
        row2.pack(fill="x", pady=4)
        ttk.Button(row2, text="▲ 위로", command=safe_handler(lambda: self.on_hotkey_move(-1))).pack(side="left")
        ttk.Button(row2, text="▼ 아래로", command=safe_handler(lambda: self.on_hotkey_move(1))).pack(side="left", padx=4)

        right = ttk.Frame(self.tab_hotkeys, padding=(PAD, 0, 0, 0))
        right.pack(side="left", fill="both", expand=True)

        head = ttk.Frame(right)
        head.pack(fill="x")
        ttk.Label(head, text="버튼 이름").pack(side="left")
        self.hk_name = tk.StringVar()
        ttk.Entry(head, textvariable=self.hk_name, width=24).pack(side="left", padx=6)
        ttk.Label(head, text="보내는 방식").pack(side="left", padx=(PAD, 0))
        self.hk_mode = ttk.Combobox(head, state="readonly", width=22,
                                    values=[hotkeys_mod.MODE_LABELS[key] for key in
                                            (hotkeys_mod.MODE_TEXT,
                                             hotkeys_mod.MODE_IMAGE,
                                             hotkeys_mod.MODE_CARD)])
        self.hk_mode.pack(side="left", padx=6)

        middle = ttk.Frame(right)
        middle.pack(fill="both", expand=True, pady=PAD)

        text_box = ttk.LabelFrame(middle, text=" 문구 (계정마다 무작위로 하나씩) ", padding=PAD)
        text_box.pack(side="left", fill="both", expand=True)
        self.hk_texts = tk.Listbox(text_box, height=14, exportselection=False)
        self.hk_texts.pack(fill="both", expand=True)
        self.hk_texts.bind("<<ListboxSelect>>", lambda _event: self._text_selected())
        entry_row = ttk.Frame(text_box)
        entry_row.pack(fill="x", pady=(6, 0))
        self.hk_text_var = tk.StringVar()
        entry = ttk.Entry(entry_row, textvariable=self.hk_text_var)
        entry.pack(fill="x")
        entry.bind("<Return>", lambda _event: self.on_text_add())
        button_row = ttk.Frame(text_box)
        button_row.pack(fill="x", pady=(4, 0))
        ttk.Button(button_row, text="추가", width=6,
                   command=safe_handler(self.on_text_add)).pack(side="left")
        ttk.Button(button_row, text="수정", width=6,
                   command=safe_handler(self.on_text_edit)).pack(side="left", padx=4)
        ttk.Button(button_row, text="삭제", width=6,
                   command=safe_handler(self.on_text_delete)).pack(side="left")

        image_box = ttk.LabelFrame(middle, text=" 사진 (계정마다 무작위로 한 장) ", padding=PAD)
        image_box.pack(side="left", fill="both", expand=True, padx=(PAD, 0))
        self.hk_images = tk.Listbox(image_box, height=14, exportselection=False)
        self.hk_images.pack(fill="both", expand=True)
        button_row = ttk.Frame(image_box)
        button_row.pack(fill="x", pady=(6, 0))
        ttk.Button(button_row, text="사진 추가",
                   command=safe_handler(self.on_image_add)).pack(side="left")
        ttk.Button(button_row, text="미리보기",
                   command=safe_handler(self.on_image_preview)).pack(side="left", padx=4)
        ttk.Button(button_row, text="삭제",
                   command=safe_handler(self.on_image_delete)).pack(side="left")

        timing = ttk.LabelFrame(right, text=" 보내는 방법 ", padding=PAD)
        timing.pack(fill="x")
        self.hk_count_min = tk.StringVar()
        self.hk_count_max = tk.StringVar()
        self.hk_gap_min = tk.StringVar()
        self.hk_gap_max = tk.StringVar()
        self.hk_spread_min = tk.StringVar()
        self.hk_spread_max = tk.StringVar()
        rows = (("한 번에 말하는 인원", self.hk_count_min, self.hk_count_max, "명"),
                ("글 사이 간격", self.hk_gap_min, self.hk_gap_max, "초"),
                ("전체 걸리는 시간", self.hk_spread_min, self.hk_spread_max, "초"))
        for index, (label, low, high, unit) in enumerate(rows):
            ttk.Label(timing, text=label).grid(row=index, column=0, sticky="e",
                                               padx=(0, 6), pady=3)
            ttk.Entry(timing, textvariable=low, width=8).grid(row=index, column=1)
            ttk.Label(timing, text="~").grid(row=index, column=2, padx=4)
            ttk.Entry(timing, textvariable=high, width=8).grid(row=index, column=3)
            ttk.Label(timing, text=unit).grid(row=index, column=4, sticky="w", padx=4)

        self.card_box = ttk.LabelFrame(right, text=" 수익카드 자동생성 ", padding=PAD)
        self.card_box.pack(fill="x", pady=(PAD, 0))
        self.hk_card = {}
        fields = (("심볼", "symbol", 12), ("기준 랏", "base_lot", 8),
                  ("기준 수익금", "base_profit", 10), ("랏 최소", "lot_min", 8),
                  ("랏 최대", "lot_max", 8))
        for index, (label, key, width) in enumerate(fields):
            ttk.Label(self.card_box, text=label).grid(row=index // 3, column=(index % 3) * 2,
                                                      sticky="e", padx=(0, 4), pady=3)
            var = tk.StringVar()
            self.hk_card[key] = var
            ttk.Entry(self.card_box, textvariable=var, width=width).grid(
                row=index // 3, column=(index % 3) * 2 + 1, sticky="w")
        ttk.Label(self.card_box,
                  text="진입가/청산가와 바이·셀은 버튼을 누를 때 물어봅니다. 계정마다 랏이 달라지고 "
                       "수익금은 랏에 맞춰 자동 계산됩니다.",
                  style="Sub.TLabel").grid(row=2, column=0, columnspan=6, sticky="w",
                                           pady=(6, 0))

        bottom = ttk.Frame(right)
        bottom.pack(fill="x", pady=(PAD, 0))
        ttk.Button(bottom, text="이 핫키 저장",
                   command=safe_handler(self.on_hotkey_save)).pack(side="right")
        ttk.Label(bottom, text="{금액} 을 넣으면 그 계정 수익금으로 바뀝니다.",
                  style="Sub.TLabel").pack(side="left")

    # ------------------------------------------------------ 계정 설정 화면
    def _build_accounts_tab(self):
        api = ttk.LabelFrame(self.tab_accounts,
                             text=" 텔레그램 API (my.telegram.org 에서 한 번만 발급) ", padding=PAD)
        api.pack(fill="x")
        self.api_id = tk.StringVar(value=self.settings.get("api_id", ""))
        self.api_hash = tk.StringVar(value=self.settings.get("api_hash", ""))
        ttk.Label(api, text="API ID").grid(row=0, column=0, sticky="e", padx=(0, 6), pady=4)
        ttk.Entry(api, textvariable=self.api_id, width=18).grid(row=0, column=1, sticky="w")
        ttk.Label(api, text="API HASH").grid(row=0, column=2, sticky="e", padx=(PAD, 6))
        ttk.Entry(api, textvariable=self.api_hash, width=42).grid(row=0, column=3, sticky="w")
        ttk.Button(api, text="저장", command=safe_handler(self.on_save_api)).grid(
            row=0, column=4, padx=PAD)
        ttk.Label(api, text="my.telegram.org 로그인 → API development tools → 앱 하나 만들고 "
                            "거기 나오는 api_id / api_hash 를 넣어 주세요.",
                  style="Sub.TLabel").grid(row=1, column=0, columnspan=5, sticky="w",
                                           pady=(6, 0))

        ttk.Label(self.tab_accounts,
                  text="방에 있는 계정을 전화번호로 로그인해 두면, 버튼 한 번에 여러 계정이 돌아가며 "
                       "글을 올립니다. 로그인 정보는 이 PC 안에만 저장됩니다.",
                  style="Sub.TLabel").pack(anchor="w", pady=(PAD, 4))

        columns = ("use", "name", "phone", "state")
        self.account_tree = ttk.Treeview(self.tab_accounts, columns=columns,
                                         show="headings", height=16)
        for column, title, width in (("use", "사용", 60), ("name", "이름", 320),
                                     ("phone", "전화번호", 180), ("state", "상태", 340)):
            self.account_tree.heading(column, text=title)
            self.account_tree.column(column, width=width,
                                     anchor="center" if column == "use" else "w")
        self.account_tree.pack(fill="both", expand=True, pady=PAD)
        self.account_tree.bind("<Double-1>", lambda _event: self.on_toggle_account())

        row = ttk.Frame(self.tab_accounts)
        row.pack(fill="x")
        add = tk.Button(row, text="＋ 전화번호로 계정 추가", bg="#2f6fb5", fg="white",
                        relief="flat", font=("맑은 고딕", 11, "bold"), padx=10, pady=6,
                        activebackground="#4a86c8", activeforeground="white",
                        command=safe_handler(self.on_add_user))
        add.pack(side="left")
        ttk.Button(row, text="사용/해제",
                   command=safe_handler(self.on_toggle_account)).pack(side="left", padx=6)
        ttk.Button(row, text="이름 바꾸기",
                   command=safe_handler(self.on_rename_account)).pack(side="left")
        ttk.Button(row, text="삭제(로그아웃)",
                   command=safe_handler(self.on_delete_account)).pack(side="right")
        ttk.Label(self.tab_accounts, text="저장 위치: %s" % paths.app_dir(),
                  style="Sub.TLabel").pack(anchor="w", pady=(PAD, 0))

    # --------------------------------------------------------- refreshers
    def _refresh_all(self):
        self._refresh_accounts()
        self._refresh_room_label()
        self._refresh_hotkey_list()
        self._refresh_hotkey_buttons()

    def accounts(self):
        return self.settings.setdefault("accounts", [])

    def enabled_accounts(self):
        return [account for account in self.accounts() if account.get("enabled", True)]

    def _refresh_accounts(self):
        self.account_tree.delete(*self.account_tree.get_children())
        for account in self.accounts():
            self.account_tree.insert(
                "", "end", iid=account["key"],
                values=("O" if account.get("enabled", True) else "-",
                        account.get("label", account["key"]),
                        account.get("phone", ""),
                        account.get("state", "로그인됨")))
        labels = [account.get("label", account["key"]) for account in self.accounts()]
        self.one_account["values"] = labels
        if labels and not self.one_account.get():
            self.one_account.current(0)
        self.account_summary.set("사용중인 계정 %d개 / 전체 %d개"
                                 % (len(self.enabled_accounts()), len(self.accounts())))

    def _refresh_room_label(self):
        target = self.settings.get("target")
        if target:
            self.room_var.set("%s" % target.get("title"))
        else:
            self.room_var.set("(방을 선택해 주세요)")

    def hotkeys(self):
        return self.settings.setdefault("hotkeys", [])

    def _refresh_hotkey_list(self, select=None):
        self.hotkey_list.delete(0, "end")
        for hotkey in self.hotkeys():
            self.hotkey_list.insert("end", hotkey.get("name", "이름없음"))
        if self.hotkeys():
            index = select if select is not None else 0
            index = max(0, min(index, len(self.hotkeys()) - 1))
            self.hotkey_list.selection_set(index)
            self._load_hotkey_editor()

    def _selected_hotkey_index(self):
        selection = self.hotkey_list.curselection()
        if not selection:
            raise tg.TgError("왼쪽 목록에서 핫키를 먼저 선택해 주세요.")
        return selection[0]

    def _load_hotkey_editor(self):
        selection = self.hotkey_list.curselection()
        if not selection:
            return
        hotkey = self.hotkeys()[selection[0]]
        self.editing = hotkey
        self.hk_name.set(hotkey.get("name", ""))
        mode = hotkey.get("mode", hotkeys_mod.MODE_TEXT)
        self.hk_mode.set(hotkeys_mod.MODE_LABELS.get(mode, hotkeys_mod.MODE_LABELS[hotkeys_mod.MODE_TEXT]))
        self.hk_texts.delete(0, "end")
        for text in hotkey.get("texts", []):
            self.hk_texts.insert("end", text)
        self.hk_images.delete(0, "end")
        for image in hotkey.get("images", []):
            self.hk_images.insert("end", os.path.basename(image))
        self.hk_count_min.set(hotkey.get("count_min", 5))
        self.hk_count_max.set(hotkey.get("count_max", 12))
        self.hk_gap_min.set(hotkey.get("gap_min", 3))
        self.hk_gap_max.set(hotkey.get("gap_max", 40))
        self.hk_spread_min.set(hotkey.get("spread_min", 60))
        self.hk_spread_max.set(hotkey.get("spread_max", 180))
        data = dict(hotkeys_mod.DEFAULT_CARD)
        data.update(hotkey.get("card") or {})
        for key, var in self.hk_card.items():
            var.set(data.get(key, ""))

    def _mode_key(self):
        label = self.hk_mode.get()
        for key, value in hotkeys_mod.MODE_LABELS.items():
            if value == label:
                return key
        return hotkeys_mod.MODE_TEXT

    # ------------------------------------------------------- 핫키 편집
    def on_hotkey_add(self):
        dialog = TextPrompt(self.root, "핫키 추가", "버튼에 보일 이름을 적어 주세요.", "새 핫키")
        if not dialog.value:
            return
        self.hotkeys().append(hotkeys_mod.make(dialog.value.strip(), ["안녕하세요"]))
        store.save(self.settings)
        self._refresh_hotkey_list(len(self.hotkeys()) - 1)
        self._refresh_hotkey_buttons()

    def on_hotkey_copy(self):
        index = self._selected_hotkey_index()
        import copy as copy_mod
        clone = copy_mod.deepcopy(self.hotkeys()[index])
        clone["id"] = hotkeys_mod.new_id()
        clone["name"] = clone.get("name", "") + " 복사"
        clone["images"] = []
        self.hotkeys().insert(index + 1, clone)
        store.save(self.settings)
        self._refresh_hotkey_list(index + 1)
        self._refresh_hotkey_buttons()

    def on_hotkey_delete(self):
        index = self._selected_hotkey_index()
        hotkey = self.hotkeys()[index]
        if not messagebox.askyesno("삭제", "'%s' 핫키를 지울까요?" % hotkey.get("name")):
            return
        del self.hotkeys()[index]
        store.save(self.settings)
        self._refresh_hotkey_list(index - 1)
        self._refresh_hotkey_buttons()

    def on_hotkey_move(self, delta):
        index = self._selected_hotkey_index()
        target = index + delta
        if target < 0 or target >= len(self.hotkeys()):
            return
        items = self.hotkeys()
        items[index], items[target] = items[target], items[index]
        store.save(self.settings)
        self._refresh_hotkey_list(target)
        self._refresh_hotkey_buttons()

    def on_hotkey_save(self):
        if self.editing is None:
            raise tg.TgError("저장할 핫키를 선택해 주세요.")
        hotkey = self.editing
        name = self.hk_name.get().strip()
        if not name:
            raise tg.TgError("버튼 이름을 적어 주세요.")
        hotkey["name"] = name
        hotkey["mode"] = self._mode_key()
        hotkey["texts"] = list(self.hk_texts.get(0, "end"))
        if not hotkey["texts"]:
            raise tg.TgError("문구가 하나도 없습니다. 최소 한 줄은 있어야 합니다.")
        if hotkey["mode"] == hotkeys_mod.MODE_IMAGE and not hotkey.get("images"):
            raise tg.TgError("사진 + 글 방식인데 사진이 없습니다. 사진을 추가해 주세요.")
        for key, var, caster in (("count_min", self.hk_count_min, int),
                                 ("count_max", self.hk_count_max, int),
                                 ("gap_min", self.hk_gap_min, float),
                                 ("gap_max", self.hk_gap_max, float),
                                 ("spread_min", self.hk_spread_min, float),
                                 ("spread_max", self.hk_spread_max, float)):
            try:
                hotkey[key] = caster(str(var.get()).strip())
            except Exception:  # noqa: BLE001
                raise tg.TgError("숫자 칸에 숫자가 아닌 값이 있습니다. 확인해 주세요.")
        card_data = dict(hotkeys_mod.DEFAULT_CARD)
        card_data.update(hotkey.get("card") or {})
        for key, var in self.hk_card.items():
            value = str(var.get()).strip()
            if key in ("lot_min", "lot_max"):
                try:
                    value = float(value)
                except ValueError:
                    raise tg.TgError("랏 최소/최대는 숫자로 적어 주세요.")
            card_data[key] = value
        hotkey["card"] = card_data
        store.save(self.settings)
        self._refresh_hotkey_list(self.hotkeys().index(hotkey))
        self._refresh_hotkey_buttons()
        self.status.set("'%s' 핫키를 저장했습니다." % name)

    def _text_selected(self):
        selection = self.hk_texts.curselection()
        if selection:
            self.hk_text_var.set(self.hk_texts.get(selection[0]))

    def on_text_add(self):
        text = self.hk_text_var.get().strip()
        if not text:
            return
        self.hk_texts.insert("end", text)
        self.hk_text_var.set("")

    def on_text_edit(self):
        selection = self.hk_texts.curselection()
        if not selection:
            raise tg.TgError("고칠 문구를 먼저 선택해 주세요.")
        text = self.hk_text_var.get().strip()
        if not text:
            return
        self.hk_texts.delete(selection[0])
        self.hk_texts.insert(selection[0], text)

    def on_text_delete(self):
        selection = self.hk_texts.curselection()
        if not selection:
            raise tg.TgError("지울 문구를 먼저 선택해 주세요.")
        self.hk_texts.delete(selection[0])

    def on_image_add(self):
        if self.editing is None:
            raise tg.TgError("핫키를 먼저 선택해 주세요.")
        files = filedialog.askopenfilenames(
            title="사진 고르기",
            filetypes=[("이미지", "*.png *.jpg *.jpeg *.gif *.webp"), ("모든 파일", "*.*")])
        for path in files:
            hotkeys_mod.import_image(self.editing, path)
        if files:
            store.save(self.settings)
            self._load_hotkey_editor()
            self.status.set("사진 %d장을 추가했습니다." % len(files))

    def on_image_delete(self):
        selection = self.hk_images.curselection()
        if not selection or self.editing is None:
            raise tg.TgError("지울 사진을 먼저 선택해 주세요.")
        path = self.editing.get("images", [])[selection[0]]
        hotkeys_mod.remove_image(self.editing, path)
        store.save(self.settings)
        self._load_hotkey_editor()

    def on_image_preview(self):
        selection = self.hk_images.curselection()
        if not selection or self.editing is None:
            raise tg.TgError("미리 볼 사진을 먼저 선택해 주세요.")
        ImagePreview(self.root, self.editing["images"][selection[0]])

    # -------------------------------------------------------------- 계정
    def on_save_api(self):
        self.settings["api_id"] = self.api_id.get().strip()
        self.settings["api_hash"] = self.api_hash.get().strip()
        store.save(self.settings)
        self.status.set("API 정보를 저장했습니다.")

    def _selected_account(self):
        selection = self.account_tree.selection()
        if not selection:
            raise tg.TgError("계정 목록에서 계정을 먼저 선택해 주세요.")
        for account in self.accounts():
            if account["key"] == selection[0]:
                return account
        raise tg.TgError("선택한 계정을 찾지 못했습니다.")

    def on_toggle_account(self):
        account = self._selected_account()
        account["enabled"] = not account.get("enabled", True)
        store.save(self.settings)
        self._refresh_accounts()

    def on_rename_account(self):
        account = self._selected_account()
        dialog = TextPrompt(self.root, "이름 바꾸기", "방에 보이는 이름과 같게 적어두시면 편합니다.",
                            account.get("label", ""))
        if dialog.value:
            account["label"] = dialog.value.strip()
            store.save(self.settings)
            self._refresh_accounts()

    def on_delete_account(self):
        account = self._selected_account()
        if not messagebox.askyesno("삭제", "%s 계정을 목록에서 지우고 로그아웃할까요?"
                                   % account.get("label", "")):
            return
        self.status.set("로그아웃 중입니다...")
        future = self.manager.submit(self.manager.logout(account))

        def done(_result):
            if account in self.accounts():
                self.accounts().remove(account)
            store.save(self.settings)
            self._refresh_accounts()
            self.status.set("계정을 삭제했습니다.")
            runlog.log("계정 삭제: %s" % account.get("label"))
        self._await(future, done)

    def on_add_user(self):
        LoginDialog(self)

    def _store_account(self, result):
        for account in self.accounts():
            if account["key"] == result["key"]:
                account.update(result)
                break
        else:
            record = dict(result)
            record.setdefault("enabled", True)
            record["kind"] = "user"
            record["state"] = "로그인됨"
            self.accounts().append(record)
        store.save(self.settings)
        self._refresh_accounts()
        runlog.log("계정 준비 완료: %s" % result.get("label"))

    # -------------------------------------------------------------- 방
    def on_pick_room(self):
        accounts = self.enabled_accounts() or self.accounts()
        if not accounts:
            raise tg.TgError("먼저 계정 설정에서 계정을 하나 이상 로그인해 주세요.")
        self.status.set("방 목록을 불러오는 중입니다...")
        future = self.manager.submit(self.manager.list_dialogs(accounts[0]))

        def done(rooms):
            self.status.set("방 %d개를 찾았습니다." % len(rooms))
            RoomDialog(self, rooms)
        self._await(future, done)

    def set_room(self, room):
        self.settings["target"] = {"id": room["id"], "title": room["title"]}
        store.save(self.settings)
        self._refresh_room_label()
        runlog.log("방을 설정했습니다: %s" % room["title"])

    def target_id(self):
        target = self.settings.get("target")
        if not target:
            raise tg.TgError("방을 먼저 선택해 주세요. (메인 화면 → 방 선택)")
        return int(target["id"])

    def on_check_members(self):
        chat_id = self.target_id()
        accounts = self.enabled_accounts()
        if not accounts:
            raise tg.TgError("사용중인 계정이 없습니다.")
        self.status.set("계정이 방에 들어가 있는지 확인하는 중입니다...")
        future = self.manager.submit(self.manager.check_members(accounts, chat_id))

        def done(result):
            ok, bad = result
            bad_names = {name for name, _ in bad}
            for account in accounts:
                account["state"] = ("이 방에 없음"
                                    if account.get("label") in bad_names else "로그인됨")
            store.save(self.settings)
            self._refresh_accounts()
            if bad:
                messagebox.showwarning(
                    "방에 없는 계정",
                    "아래 계정은 이 방에 들어가 있지 않습니다. 방에 초대한 뒤 다시 확인해 주세요.\n\n%s"
                    % "\n".join("· %s" % name for name in bad_names))
            else:
                messagebox.showinfo("확인 완료",
                                    "사용중인 %d개 계정 모두 이 방에 들어가 있습니다." % len(ok))
            self.status.set("계정 확인을 마쳤습니다.")
        self._await(future, done)

    # ------------------------------------------------------------ 버스트
    def fire_hotkey(self, hotkey):
        if self.busy:
            raise tg.TgError("이미 보내는 중입니다. 끝나거나 중지한 뒤에 다시 눌러 주세요.")
        chat_id = self.target_id()
        enabled = self.enabled_accounts()
        if not enabled:
            raise tg.TgError("사용중인 계정이 없습니다. 계정 설정에서 계정을 추가해 주세요.")
        overrides = None
        if hotkey.get("mode") == hotkeys_mod.MODE_CARD:
            dialog = CardPrompt(self.root, hotkey)
            if dialog.value is None:
                return
            overrides = dialog.value
            hotkey.setdefault("card", {}).update(overrides)
            store.save(self.settings)
        low = int(hotkey.get("count_min", 5))
        high = int(hotkey.get("count_max", 12))
        count = random.randint(min(low, high), max(low, high))
        accounts = burst.pick_accounts(enabled, count)
        self.busy = True
        self.controller = burst.Controller()
        self.stop_button.configure(state="normal")
        self.progress_bar.configure(value=0, maximum=len(accounts))
        self.progress_var.set("%s 준비중..." % hotkey.get("name"))
        runlog.log("[%s] %d개 계정으로 시작합니다." % (hotkey.get("name"), len(accounts)))
        threading.Thread(target=self._burst_worker,
                         args=(hotkey, accounts, chat_id, overrides),
                         daemon=True).start()

    def _burst_worker(self, hotkey, accounts, chat_id, overrides):
        controller = self.controller
        name = hotkey.get("name", "")
        try:
            ok, bad = self.manager.run(self.manager.check_members(accounts, chat_id),
                                       timeout=180)
            if bad:
                names = {label for label, _ in bad}
                runlog.log("[%s] 방에 없는 계정은 빼고 보냅니다: %s" % (name, ", ".join(names)))
                self.events.put({"event": "warn",
                                 "text": "이 방에 들어가 있지 않아 제외한 계정: %s" % ", ".join(names)})
                accounts = [account for account in accounts
                            if account.get("label", account["key"]) not in names]
            if not accounts:
                self.events.put({"event": "warn", "text": "보낼 수 있는 계정이 없습니다."})
                self.events.put({"event": "done", "sent": 0, "failed": 0, "skipped": 0,
                                 "kind": name, "elapsed": 0})
                return
            items = burst.build_items(hotkey, accounts, overrides)
            gaps = burst.plan_gaps(len(items), hotkey.get("gap_min", 3),
                                   hotkey.get("gap_max", 40),
                                   hotkey.get("spread_min", 60),
                                   hotkey.get("spread_max", 180))
            self.events.put({"event": "planned", "kind": name, "total": len(items),
                             "spread": round(sum(gaps))})
            future = self.manager.submit(burst.run(
                self.manager, controller, name, items, chat_id, gaps,
                lambda payload: self.events.put(payload)))
            future.result(timeout=60 * 40)
            self._report_burst(name, controller, items)
        except Exception as exc:  # noqa: BLE001
            runlog.log("[%s] 실패: %r" % (name, exc))
            self.events.put({"event": "error", "text": str(exc)[:200]})
            self.events.put({"event": "done", "sent": getattr(controller, "sent", 0),
                             "failed": getattr(controller, "failed", 0),
                             "skipped": 0, "kind": name, "elapsed": 0})
            try:
                reporter.error("[%s] 버스트 실패: %r" % (name, exc), run_log=runlog.text())
            except Exception:  # noqa: BLE001
                pass

    def _report_burst(self, name, controller, items):
        try:
            sample = next((item.get("image") for item in items if item.get("image")), None)
            detail = {
                "hotkey": name,
                "counts": {"planned": len(items), "sent": controller.sent,
                           "failed": controller.failed, "skipped": controller.skipped},
                "elapsedSeconds": round(time.time() - controller.started_at, 1),
                "posts": controller.detail,
                "settings": store.redacted(self.settings),
            }
            reporter.burst_report(name, "[%s] 계획 %d / 전송 %d / 실패 %d / 취소 %d"
                                  % (name, len(items), controller.sent,
                                     controller.failed, controller.skipped),
                                  detail, run_log=runlog.text(), card_png=sample)
        except Exception:  # noqa: BLE001
            pass

    def on_stop(self):
        if self.controller:
            self.controller.stop()
            self.progress_var.set("중지하는 중입니다...")
            runlog.log("중지를 눌렀습니다. 남은 예약을 취소합니다.")

    def on_one_shot(self):
        text = self.one_text.get().strip()
        if not text:
            return
        label = self.one_account.get()
        account = next((item for item in self.accounts()
                        if item.get("label", item["key"]) == label), None)
        if not account:
            raise tg.TgError("보낼 계정을 골라 주세요.")
        chat_id = self.target_id()
        future = self.manager.submit(self.manager.send_text(account, chat_id, text))

        def done(_result):
            runlog.log("[%s] %s" % (label, text))
            self.one_text.set("")
            self.status.set("보냈습니다.")
        self._await(future, done)

    # ------------------------------------------------------------ 이벤트
    def _append_log(self, line):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _drain_events(self):
        try:
            while True:
                self._handle_event(self.events.get_nowait())
        except queue.Empty:
            pass
        self.root.after(150, self._drain_events)

    def _handle_event(self, payload):
        event = payload.get("event")
        if event == "log":
            self._append_log(payload["line"])
        elif event == "planned":
            self.progress_var.set("%s: %d명, 약 %d초에 걸쳐 올립니다."
                                  % (payload["kind"], payload["total"], payload["spread"]))
            self.progress_bar.configure(maximum=max(1, payload["total"]), value=0)
        elif event == "sent":
            self.progress_bar.configure(value=payload["index"])
            self.progress_var.set("%d / %d 전송" % (payload["index"], payload["total"]))
            runlog.log("  → [%s] %s" % (payload["label"], payload["text"]))
        elif event == "flood":
            self.progress_var.set("[%s] 텔레그램 제한 %d초 대기"
                                  % (payload["label"], payload["seconds"]))
        elif event == "failed":
            runlog.log("  × [%s] 실패: %s" % (payload["label"], payload["error"]))
        elif event == "stopped":
            self.progress_var.set("중지했습니다. (%d/%d)" % (payload["index"], payload["total"]))
        elif event == "warn":
            self.status.set(payload["text"])
            messagebox.showwarning("확인해 주세요", payload["text"])
        elif event == "error":
            self.status.set(payload["text"])
        elif event == "done":
            self.busy = False
            self.stop_button.configure(state="disabled")
            summary = ("%s 완료: 보냄 %d, 실패 %d, 취소 %d (%.0f초)"
                       % (payload.get("kind", ""), payload.get("sent", 0),
                          payload.get("failed", 0), payload.get("skipped", 0),
                          payload.get("elapsed", 0)))
            self.progress_var.set(summary)
            self.status.set(summary)
            runlog.log(summary)

    def _await(self, future, on_ok, on_err=None):
        def poll():
            if not future.done():
                self.root.after(150, poll)
                return
            try:
                result = future.result()
            except tg.TgError as exc:
                self.status.set(str(exc))
                if on_err:
                    on_err(exc)
                else:
                    messagebox.showwarning("확인해 주세요", str(exc))
                return
            except Exception as exc:  # noqa: BLE001
                runlog.log("실패: %r" % (exc,))
                if on_err:
                    on_err(exc)
                else:
                    messagebox.showerror("오류", "%r" % (exc,))
                return
            on_ok(result)
        self.root.after(120, poll)

    def on_exit(self):
        try:
            store.save(self.settings)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.manager.shutdown()
        except Exception:  # noqa: BLE001
            pass
        self.root.destroy()


class TextPrompt(object):
    def __init__(self, parent, title, message, initial=""):
        self.value = None
        top = tk.Toplevel(parent)
        top.title(title)
        top.transient(parent)
        top.grab_set()
        top.resizable(False, False)
        ttk.Label(top, text=message, padding=PAD).pack(anchor="w")
        var = tk.StringVar(value=initial)
        entry = ttk.Entry(top, textvariable=var, width=52)
        entry.pack(padx=PAD, fill="x")
        entry.focus_set()
        row = ttk.Frame(top, padding=PAD)
        row.pack(fill="x")

        def ok(*_args):
            self.value = var.get()
            top.destroy()
        ttk.Button(row, text="확인", command=ok).pack(side="right")
        ttk.Button(row, text="취소", command=top.destroy).pack(side="right", padx=6)
        entry.bind("<Return>", ok)
        parent.wait_window(top)


class CardPrompt(object):
    """수익카드 핫키를 누르면 이번 시그널의 값만 물어봅니다."""

    def __init__(self, parent, hotkey):
        self.value = None
        data = dict(hotkeys_mod.DEFAULT_CARD)
        data.update(hotkey.get("card") or {})
        top = tk.Toplevel(parent)
        top.title("%s - 이번 시그널" % hotkey.get("name", ""))
        top.transient(parent)
        top.grab_set()
        top.resizable(False, False)
        frame = ttk.Frame(top, padding=PAD)
        frame.pack(fill="both", expand=True)

        direction = tk.StringVar(value=data.get("direction", "buy"))
        entry = tk.StringVar(value=str(data.get("entry", "")))
        exit_price = tk.StringVar(value=str(data.get("exit", "")))
        symbol = tk.StringVar(value=str(data.get("symbol", "XAUUSDe")))

        ttk.Label(frame, text="심볼").grid(row=0, column=0, sticky="e", padx=(0, 6), pady=4)
        ttk.Entry(frame, textvariable=symbol, width=14).grid(row=0, column=1, sticky="w")
        row = ttk.Frame(frame)
        row.grid(row=0, column=2, columnspan=2, sticky="w", padx=PAD)
        ttk.Radiobutton(row, text="바이", value="buy", variable=direction).pack(side="left")
        ttk.Radiobutton(row, text="셀", value="sell", variable=direction).pack(side="left", padx=6)
        ttk.Label(frame, text="진입가").grid(row=1, column=0, sticky="e", padx=(0, 6), pady=4)
        ttk.Entry(frame, textvariable=entry, width=14).grid(row=1, column=1, sticky="w")
        ttk.Label(frame, text="청산가").grid(row=1, column=2, sticky="e", padx=(PAD, 6))
        ttk.Entry(frame, textvariable=exit_price, width=14).grid(row=1, column=3, sticky="w")
        ttk.Label(frame, text="계정마다 랏과 수익금은 자동으로 다르게 만들어집니다.",
                  foreground="#777777").grid(row=2, column=0, columnspan=4, sticky="w",
                                             pady=(6, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, columnspan=4, sticky="e", pady=(PAD, 0))

        def preview():
            try:
                self._preview(top, hotkey, symbol.get(), direction.get(),
                              float(entry.get()), float(exit_price.get()))
            except ValueError:
                messagebox.showwarning("확인해 주세요", "진입가와 청산가는 숫자로 넣어 주세요.")

        def ok():
            try:
                self.value = {"symbol": symbol.get().strip() or "XAUUSDe",
                              "direction": direction.get(),
                              "entry": float(entry.get()),
                              "exit": float(exit_price.get())}
            except ValueError:
                messagebox.showwarning("확인해 주세요", "진입가와 청산가는 숫자로 넣어 주세요.")
                return
            top.destroy()

        ttk.Button(buttons, text="카드 미리보기", command=preview).pack(side="left", padx=6)
        ttk.Button(buttons, text="보내기", command=ok).pack(side="left")
        ttk.Button(buttons, text="취소", command=top.destroy).pack(side="left", padx=6)
        parent.wait_window(top)

    def _preview(self, parent, hotkey, symbol, direction, entry, exit_price):
        data = dict(hotkeys_mod.DEFAULT_CARD)
        data.update(hotkey.get("card") or {})
        lot = card.random_lots(1, data.get("lot_min", 0.5), data.get("lot_max", 6.0),
                               data.get("lot_step", 0.5))[0]
        delta = abs(exit_price - entry)
        base_profit = float(data.get("base_profit") or 0)
        base_lot = float(data.get("base_lot") or 1)
        contract = (base_profit / (delta * base_lot)) if (base_profit and delta and base_lot) else 100.0
        profit = card.profit_for(lot, entry, exit_price, direction, contract)
        path = os.path.join(paths.cards_dir(), "preview.png")
        when = time.gmtime() if data.get("time_utc", True) else time.localtime()
        card.render(path, symbol, direction, lot, entry, exit_price, profit, when=when)
        ImagePreview(parent, path, "계정마다 랏과 수익금이 다르게 만들어집니다.")


class ImagePreview(object):
    def __init__(self, parent, path, note=""):
        top = tk.Toplevel(parent)
        top.title("미리보기")
        top.transient(parent)
        try:
            from PIL import Image, ImageTk
            image = Image.open(path)
            if image.width > 900:
                ratio = 900.0 / image.width
                image = image.resize((int(image.width * ratio), int(image.height * ratio)))
            self.photo = ImageTk.PhotoImage(image)
        except Exception:  # noqa: BLE001
            self.photo = tk.PhotoImage(file=path)
        tk.Label(top, image=self.photo).pack(padx=PAD, pady=PAD)
        if note:
            ttk.Label(top, text=note, foreground="#777777").pack(pady=(0, PAD))
        ttk.Button(top, text="닫기", command=top.destroy).pack(pady=(0, PAD))


class RoomDialog(object):
    def __init__(self, app, rooms):
        self.app = app
        top = tk.Toplevel(app.root)
        self.top = top
        top.title("방 선택")
        top.transient(app.root)
        top.grab_set()
        top.geometry("520x460")
        ttk.Label(top, text="시그널을 올릴 방을 고르세요.", padding=PAD).pack(anchor="w")
        self.listbox = tk.Listbox(top)
        self.listbox.pack(fill="both", expand=True, padx=PAD)
        for room in rooms:
            self.listbox.insert("end", "%s   (id %s)" % (room["title"], room["id"]))
        self.rooms = rooms
        row = ttk.Frame(top, padding=PAD)
        row.pack(fill="x")
        ttk.Button(row, text="이 방으로 지정", command=self.choose).pack(side="right")
        ttk.Button(row, text="닫기", command=top.destroy).pack(side="right", padx=6)
        self.listbox.bind("<Double-1>", lambda _event: self.choose())

    def choose(self):
        selection = self.listbox.curselection()
        if not selection:
            return
        self.app.set_room(self.rooms[selection[0]])
        self.top.destroy()


class LoginDialog(object):
    """전화번호 → 인증코드 → (필요하면) 2단계 비밀번호."""

    def __init__(self, app):
        self.app = app
        self.key = None
        top = tk.Toplevel(app.root)
        self.top = top
        top.title("전화번호로 계정 추가")
        top.transient(app.root)
        top.grab_set()
        top.resizable(False, False)

        frame = ttk.Frame(top, padding=PAD)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="1) 전화번호 (국가번호부터, 예: +821012345678)").grid(
            row=0, column=0, columnspan=2, sticky="w")
        self.phone = tk.StringVar()
        ttk.Entry(frame, textvariable=self.phone, width=28).grid(row=1, column=0,
                                                                 sticky="w", pady=4)
        self.code_button = ttk.Button(frame, text="인증코드 받기",
                                      command=safe_handler(self.request_code))
        self.code_button.grid(row=1, column=1, sticky="w", padx=6)

        ttk.Label(frame, text="2) 텔레그램 앱으로 온 코드").grid(row=2, column=0, columnspan=2,
                                                        sticky="w", pady=(PAD, 0))
        self.code = tk.StringVar()
        self.code_entry = ttk.Entry(frame, textvariable=self.code, width=28, state="disabled")
        self.code_entry.grid(row=3, column=0, sticky="w", pady=4)
        self.submit_button = ttk.Button(frame, text="확인", state="disabled",
                                        command=safe_handler(self.submit_code))
        self.submit_button.grid(row=3, column=1, sticky="w", padx=6)

        ttk.Label(frame, text="3) 2단계 비밀번호 (설정한 계정만)").grid(row=4, column=0,
                                                             columnspan=2, sticky="w",
                                                             pady=(PAD, 0))
        self.password = tk.StringVar()
        self.password_entry = ttk.Entry(frame, textvariable=self.password, width=28,
                                        show="*", state="disabled")
        self.password_entry.grid(row=5, column=0, sticky="w", pady=4)
        self.password_button = ttk.Button(frame, text="확인", state="disabled",
                                          command=safe_handler(self.submit_password))
        self.password_button.grid(row=5, column=1, sticky="w", padx=6)

        self.message = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.message, foreground="#1f5fa8",
                  wraplength=380).grid(row=6, column=0, columnspan=2, sticky="w",
                                       pady=(PAD, 0))
        ttk.Button(frame, text="닫기", command=top.destroy).grid(row=7, column=1,
                                                              sticky="e", pady=(PAD, 0))

    def request_code(self):
        phone = self.phone.get().strip()
        if len(tg.digits(phone)) < 8:
            raise tg.TgError("전화번호를 국가번호까지 넣어 주세요. 예: +821012345678")
        self.message.set("인증코드를 요청하는 중입니다...")
        self.code_button.configure(state="disabled")
        future = self.app.manager.submit(self.app.manager.request_code(phone))

        def done(result):
            self.key = result["key"]
            self.code_button.configure(state="normal")
            if result.get("already"):
                self.message.set("이미 로그인된 계정입니다.")
                self.app._store_account({"key": result["key"], "kind": "user",
                                         "label": result["label"],
                                         "user_id": result["user_id"],
                                         "phone": result["phone"]})
                self.top.destroy()
                return
            self.code_entry.configure(state="normal")
            self.submit_button.configure(state="normal")
            self.code_entry.focus_set()
            self.message.set("텔레그램 앱으로 받은 코드를 넣고 확인을 눌러 주세요.")

        def failed(exc):
            self.code_button.configure(state="normal")
            self.message.set(str(exc))
        self.app._await(future, done, failed)

    def submit_code(self):
        if not self.key:
            raise tg.TgError("먼저 인증코드를 받아 주세요.")
        self.message.set("확인하는 중입니다...")
        future = self.app.manager.submit(
            self.app.manager.submit_code(self.key, self.code.get().strip()))

        def done(result):
            if result.get("needs_password"):
                self.password_entry.configure(state="normal")
                self.password_button.configure(state="normal")
                self.password_entry.focus_set()
                self.message.set("2단계 비밀번호를 넣어 주세요.")
                return
            result["phone"] = self.phone.get().strip()
            self.app._store_account(result)
            self.message.set("완료되었습니다.")
            self.top.destroy()

        def failed(exc):
            self.message.set(str(exc))
        self.app._await(future, done, failed)

    def submit_password(self):
        future = self.app.manager.submit(
            self.app.manager.submit_password(self.key, self.password.get()))

        def done(result):
            result["phone"] = self.phone.get().strip()
            self.app._store_account(result)
            self.top.destroy()

        def failed(exc):
            self.message.set(str(exc))
        self.app._await(future, done, failed)


def demo_fill(app):
    """--guidemo: 텔레그램에 연결하지 않고 창을 채워 화면을 찍는 내부용 모드."""
    names = ["스타벅스", "황금열쇠", "단타왔다가", "독불장군", "달려사냥꾼", "이수정",
             "고수개미", "김운용", "요이땅", "휴게소 종무님", "강대공(낚시왕)", "장전꾼 과장"]
    app.settings["accounts"] = [
        {"key": "user_demo%02d" % index, "kind": "user", "label": name,
         "phone": "+8210****%02d" % index, "enabled": True, "state": "로그인됨"}
        for index, name in enumerate(names)]
    app.settings["target"] = {"id": -1002345678901, "title": "마린투자그룹 해외선물 시그널방"}
    app._refresh_all()
    app.progress_bar.configure(maximum=9, value=9)
    summary = "청산 (수익인증) 완료: 보냄 9, 실패 0, 취소 0 (118초)"
    app.progress_var.set(summary)
    app.status.set(summary)
    for line in ["[청산 (수익인증)] 9개 계정으로 시작합니다.",
                 "  → [스타벅스] 수익 감사합니다",
                 "  → [단타왔다가] 222 불 수익 나이스~",
                 "  → [이수정] 수익청산 나이스",
                 "  → [요이땅] 3연승 나이스~👍👍",
                 "  → [김운용] 행복합니다^^",
                 "  → [고수개미] 수익 감사드립니다",
                 "  → [황금열쇠] 1332 불 먹고 갑니다~",
                 "  → [독불장군] 깔끔하게 수익청산했습니다",
                 "  → [달려사냥꾼] 청산 완료~ 감사합니다",
                 summary]:
        app._append_log("%s  %s" % (time.strftime("%H:%M:%S"), line))
    try:
        path = os.path.join(paths.cards_dir(), "preview.png")
        card.render(path, "XAUUSDe", "buy", 1.00, 4435.95, 4438.17, 222.00)
        ImagePreview(app.root, path, "계정마다 랏과 수익금이 다르게 만들어집니다.")
    except Exception:  # noqa: BLE001
        pass


def run(demo=False, hold_ms=0):
    root = tk.Tk()
    app = App(root, demo=demo)
    root.protocol("WM_DELETE_WINDOW", app.on_exit)
    if demo:
        root.after(600, lambda: demo_fill(app))
    if hold_ms:
        root.after(hold_ms, root.destroy)
    root.mainloop()
    return app
