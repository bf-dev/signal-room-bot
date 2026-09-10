# -*- coding: utf-8 -*-
"""시그널방 도우미 - 시작점.

고객이 쓰는 길은 하나뿐입니다: 실행하면 창이 뜹니다.
--selftest / --guidemo / --cardtest 는 우리 빌드 검증용입니다.
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config      # noqa: E402
import runlog      # noqa: E402


def _selftest():
    """창을 만들고 바로 닫습니다. tkinter 가 빌드에 들어갔는지 확인용."""
    import tkinter as tk
    import gui
    root = tk.Tk()
    app = gui.App(root, demo=True)
    root.after(1500, root.destroy)
    root.mainloop()
    app.manager.shutdown()
    return 0


def _cardtest():
    import card
    import paths
    path = os.path.join(paths.cards_dir(), "selftest-card.png")
    card.render(path, "XAUUSDe", "buy", 1.00, 4435.95, 4438.17, 222.00)
    size = os.path.getsize(path)
    if sys.stdout is not None:
        print("card ok: %s (%d bytes)" % (path, size))
    return 0 if size > 2000 else 1


def main():
    args = sys.argv[1:]
    if "--selftest" in args:
        return _selftest()
    if "--cardtest" in args:
        return _cardtest()
    if "--reporttest" in args:
        import reporter
        reporter.selftest()
        return 0

    demo = "--guidemo" in args
    hold_ms = int(os.environ.get("GUIDEMO_HOLD_MS", "0") or 0) if demo else 0
    if demo:
        os.environ.setdefault("SIGNALROOM_NO_UPLOAD", "1")

    import gui
    import updater
    app_holder = {}

    try:
        if not demo:
            updater.start(can_restart=lambda: not app_holder.get("busy", False))
    except Exception:  # noqa: BLE001
        pass
    try:
        app = gui.run(demo=demo, hold_ms=hold_ms)
        app_holder["app"] = app
    except Exception:  # noqa: BLE001
        runlog.log("치명적 오류: %s" % traceback.format_exc())
        try:
            import reporter
            reporter.error("시작 실패: %s" % traceback.format_exc(),
                           run_log=runlog.text())
        except Exception:  # noqa: BLE001
            pass
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(config.APP_TITLE,
                                 "프로그램을 시작하지 못했습니다.\n\n%s"
                                 % traceback.format_exc()[-800:])
        except Exception:  # noqa: BLE001
            pass
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
