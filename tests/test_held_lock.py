# -*- coding: utf-8 -*-
"""Force the 2026-10-12 failure on purpose and check the app still sends.

Offline: tests/fake_telegram.py replaces only Telethon's network sender. No real
Telegram account is used.

Setup, exactly what a customer PC upgraded from 1.0.1 has on disk:
  two accounts logged in with a 1.0.1-style sqlite <key>.session each. Account B's
  file is then locked by ANOTHER connection that never lets go (what the leaked
  login client did on her PC), once with a RESERVED write lock (an open write
  transaction) and once with an EXCLUSIVE lock (readers blocked too).

Checks, against tg.Manager of the app dir given:
  1. both accounts send several times while B's file stays locked
  2. (1.0.2+) a "database is locked" raised inside a send is recovered: the
     account's client is closed, a fresh one opened, the send retried once
  3. (1.0.2+) only one Telegram connection per account at the end

usage: python tests/test_held_lock.py <app dir>
exit 0 = PASS, 1 = FAIL
"""
import asyncio
import os
import shutil
import sqlite3
import sys
import tempfile

APP = os.path.abspath(sys.argv[1])
HERE = os.path.dirname(os.path.abspath(__file__))
home = tempfile.mkdtemp(prefix="srb-held-")
os.environ["SIGNALROOM_HOME"] = home
os.environ["SIGNALROOM_NO_UPLOAD"] = "1"
sys.path[:0] = [APP, HERE]

import fake_telegram  # noqa: E402
fake_telegram.install(scale=0.1)
import paths  # noqa: E402
import runlog  # noqa: E402
import tg  # noqa: E402
from telethon import TelegramClient  # noqa: E402

API_ID, API_HASH = 12345, "0123456789abcdef0123456789abcdef"
SETTINGS = {"api_id": str(API_ID), "api_hash": API_HASH}
CHAT = -1000000000000 - fake_telegram.CHAT_ID
LINES = []
runlog.add_sink(LINES.append)


def legacy_login(phone):
    """A 1.0.1-style login: Telethon on a sqlite .session file, then closed."""
    key = "user_" + tg.digits(phone)
    path = os.path.join(paths.sessions_dir(), key)

    async def go():
        client = TelegramClient(path, API_ID, API_HASH)
        await client.connect()
        sent = await client.send_code_request(phone)
        await client.sign_in(phone, code="12345", phone_code_hash=sent.phone_code_hash)
        await client.disconnect()
    asyncio.run(go())
    return {"key": key, "label": key, "kind": "user", "phone": phone}


def hold_lock(key, mode):
    conn = sqlite3.connect(os.path.join(paths.sessions_dir(), key + ".session"),
                           timeout=0, isolation_level=None)
    conn.execute("BEGIN %s" % mode)
    conn.execute("insert or replace into version values (8)")
    return conn


def lock_is_held(key):
    probe = sqlite3.connect(os.path.join(paths.sessions_dir(), key + ".session"),
                            timeout=0, isolation_level=None)
    try:
        probe.execute("BEGIN IMMEDIATE")
        probe.execute("ROLLBACK")
        return False
    except sqlite3.OperationalError:
        return True
    finally:
        probe.close()


def send_round(manager, accounts, rounds, tag):
    ok = {a["key"]: 0 for a in accounts}
    errors = {}
    for n in range(rounds):
        for account in accounts:
            try:
                manager.run(manager.send_text(account, CHAT, "%s %d" % (tag, n)), 30)
                ok[account["key"]] += 1
            except Exception as exc:  # noqa: BLE001
                errors.setdefault(account["key"], repr(exc))
    return ok, errors


def main():
    failures = []
    a = legacy_login("+821000000001")
    b = legacy_login("+821000000002")
    for mode in ("IMMEDIATE", "EXCLUSIVE"):
        # every mode starts from a fresh 1.0.1 install: the migration itself has
        # to get through the held lock
        for name in os.listdir(paths.sessions_dir()):
            if name.endswith(".str"):
                os.unlink(os.path.join(paths.sessions_dir(), name))
        holder = hold_lock(b["key"], mode)
        print("[%s] lock on %s.session held by another connection: %s"
              % (mode, b["key"], lock_is_held(b["key"])))
        manager = tg.Manager(lambda: SETTINGS)
        manager.start()
        ok, errors = send_round(manager, [a, b], 4, mode)
        for account in (a, b):
            print("  %-18s sent %d/4  %s" % (account["key"], ok[account["key"]],
                                            errors.get(account["key"], "")))
            if ok[account["key"]] != 4:
                failures.append("%s: %s sent %d/4" % (mode, account["key"],
                                                       ok[account["key"]]))
        print("  lock still held during the whole run: %s" % lock_is_held(b["key"]))
        manager.shutdown()
        holder.rollback()
        holder.close()

    if hasattr(tg, "FileSession"):
        print("[recovery] 'database is locked' raised inside B's next send")
        manager = tg.Manager(lambda: SETTINGS)
        manager.start()
        manager.run(manager.send_text(b, CHAT, "warm up"), 30)
        # (a) the error hits while resolving the room, before anything is sent:
        #     the client is recycled and the line is sent once.
        # (b) the error hits after Telegram accepted the line (where 1.0.1 raised
        #     it): the client is recycled and the line is NOT posted twice.
        original_resolve = tg.Manager.resolve
        original_entities = tg.FileSession.process_entities
        state = {"resolve": 0, "entities": 0}

        async def flaky_resolve(self, account, chat_id):
            if state["resolve"] == 0:
                state["resolve"] += 1
                raise sqlite3.OperationalError("database is locked")
            return await original_resolve(self, account, chat_id)

        def flaky_entities(self, tlo):
            if state["entities"] == 0 and getattr(tlo, "updates", None):
                state["entities"] += 1
                raise sqlite3.OperationalError("database is locked")
            return original_entities(self, tlo)

        for case, patch in (("before send", "resolve"), ("after send", "entities")):
            if patch == "resolve":
                tg.Manager.resolve = flaky_resolve
            else:
                tg.FileSession.process_entities = flaky_entities
            before = len(fake_telegram.SENT)
            del LINES[:]
            try:
                manager.run(manager.send_text(b, CHAT, "after lock " + case), 30)
                print("  [%s] send returned OK" % case)
            except Exception as exc:  # noqa: BLE001
                failures.append("recovery %s: %r" % (case, exc))
                print("  [%s] send FAILED: %r" % (case, exc))
            finally:
                tg.Manager.resolve = original_resolve
                tg.FileSession.process_entities = original_entities
            delivered = len(fake_telegram.SENT) - before
            for line in LINES:
                if "세션 잠김" in line:
                    print("    run.log: %s" % line)
            print("    copies delivered to the room: %d" % delivered)
            if state[patch] != 1 or not any("세션 잠김" in line for line in LINES):
                failures.append("recovery %s not exercised" % case)
            if delivered != 1:
                failures.append("recovery %s delivered %d copies" % (case, delivered))
        # and the account keeps working afterwards
        ok, errors = send_round(manager, [b], 3, "after recovery")
        print("  after recovery: %s sent %d/3 %s" % (b["key"], ok[b["key"]],
                                                    errors.get(b["key"], "")))
        if ok[b["key"]] != 3:
            failures.append("account did not keep sending after recovery")
        manager.run(manager.send_text(a, CHAT, "a"), 30)
        live = {}
        for sender in fake_telegram.LIVE:
            live[sender._uid()] = live.get(sender._uid(), 0) + 1
        print("  live connections per account: %s" % live)
        if any(count > 1 for count in live.values()):
            failures.append("more than one connection per account: %s" % live)
        manager.shutdown()
        left = sorted(os.listdir(paths.sessions_dir()))
        print("  session dir: %s" % left)

    shutil.rmtree(home, ignore_errors=True)
    print("RESULT:", "FAIL " + "; ".join(failures) if failures else "PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
