# -*- coding: utf-8 -*-
"""Reproduce the 2026-10-12 "database is locked" failure against the app's real
tg.Manager, offline (tests/fake_telegram.py replaces only the network).

Customer timeline being replayed (run.log, DESKTOP-89N9I0J):
  app running -> account B: 인증코드 받기 fails with PhonePasswordFloodError twice
  -> third try works -> code -> "계정 준비 완료" -> every burst after that:
  "[B] 전송 실패: OperationalError('database is locked')", other accounts fine.

usage: python tests/repro_session_lock.py <app dir> [seconds]
exit 0 = account B sent every time, exit 1 = it failed (the bug).
"""
import asyncio
import os
import random
import shutil
import sys
import threading
import tempfile
import time

APP = os.path.abspath(sys.argv[1])
SECONDS = float(sys.argv[2]) if len(sys.argv) > 2 else 40
HERE = os.path.dirname(os.path.abspath(__file__))
home = tempfile.mkdtemp(prefix="srb-lock-")
os.environ["SIGNALROOM_HOME"] = home
os.environ["SIGNALROOM_NO_UPLOAD"] = "1"
sys.path[:0] = [APP, HERE]

import fake_telegram  # noqa: E402
fake_telegram.install(scale=0.1)
import tg  # noqa: E402

SETTINGS = {"api_id": "12345", "api_hash": "0123456789abcdef0123456789abcdef"}
CHAT = -1000000000000 - fake_telegram.CHAT_ID


def login(manager, phone, floods=0):
    fake_telegram.FLOOD_PLAN[phone.lstrip("+")] = floods
    for attempt in range(floods + 1):
        try:
            result = manager.run(manager.request_code(phone), 30)
        except Exception as exc:  # noqa: BLE001 - the GUI logs "실패: %r" and stays up
            print("  login %s attempt %d: 실패: %r" % (phone, attempt + 1, exc))
            continue
        account = manager.run(manager.submit_code(result["key"], "12345"), 30)
        account["phone"] = phone
        print("  login %s attempt %d: 계정 준비 완료 (%s)" % (phone, attempt + 1, account["key"]))
        return account
    raise SystemExit("login never succeeded")


def probe_write(key):
    import sqlite3
    path = os.path.join(home, "sessions", key + ".session")
    if not os.path.exists(path):
        return "no sqlite file (in-memory session)"
    conn = sqlite3.connect(path, timeout=0.2, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("ROLLBACK")
        return "free"
    except Exception as exc:  # noqa: BLE001
        return "HELD by another connection (%s)" % exc
    finally:
        conn.close()


def main():
    manager = tg.Manager(lambda: SETTINGS)
    manager.start()
    stop = threading.Event()
    manager.submit(fake_telegram.room_chatter(0.5, stop, blip_every=20))
    print("app under test:", APP)
    a = login(manager, "+821000000001")
    b = login(manager, "+821000000002", floods=2)
    sessions = sorted(os.listdir(os.path.join(home, "sessions")))
    print("  session files:", sessions)

    # Bursts like the customer's: each account posts once per burst, a few
    # (scaled) seconds apart, bursts a few (scaled) minutes apart.
    rng = random.Random(7)
    results = {a["key"]: [0, 0], b["key"]: [0, 0]}
    errors, tail = {}, {a["key"]: [], b["key"]: []}
    end = time.time() + SECONDS
    n = 0
    while time.time() < end:
        for account in (a, b):
            n += 1
            try:
                manager.run(manager.send_text(account, CHAT, "burst line %d" % n), 30)
                results[account["key"]][0] += 1
                tail[account["key"]].append("ok")
            except Exception as exc:  # noqa: BLE001
                results[account["key"]][1] += 1
                tail[account["key"]].append("FAIL")
                errors.setdefault(account["key"], repr(exc))
            time.sleep(rng.uniform(0.3, 2.0))
        time.sleep(rng.uniform(2.0, 9.0))
    for key, (ok, bad) in results.items():
        print("  %-18s sent %3d  failed %3d  last 6: %s  %s"
              % (key, ok, bad, " ".join(tail[key][-6:]), errors.get(key, "")))
    clients = len(fake_telegram.LIVE)
    print("  live Telegram connections at the end: %d (2 accounts)" % clients)
    for account in (a, b):
        print("  write lock on %s.session: %s" % (account["key"], probe_write(account["key"])))
    print("  Telethon clients alive on each session file:")
    import gc
    from telethon import TelegramClient
    for obj in gc.get_objects():
        if isinstance(obj, TelegramClient) and obj.session is not None:
            name = getattr(obj.session, "filename", None) or getattr(obj.session, "_path", "?")
            conn = getattr(obj.session, "_conn", None)
            keep = getattr(obj, "_keepalive_handle", None)
            upd = getattr(obj, "_updates_handle", None)
            print("    %-28s connected=%-5s open_write_txn=%-5s keepalive=%s updates=%s" % (
                os.path.basename(name), bool(obj.is_connected()),
                bool(conn is not None and conn.in_transaction),
                "dead" if keep is None or keep.done() else "running",
                "dead" if upd is None or upd.done() else "running"))
    stop.set()
    manager.shutdown()
    shutil.rmtree(home, ignore_errors=True)
    failed = sum(bad for _ok, bad in results.values())
    print("RESULT:", "FAIL (bug reproduced)" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
