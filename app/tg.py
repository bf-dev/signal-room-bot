# -*- coding: utf-8 -*-
"""텔레그램 계정 관리 (Telethon).

계정은 고객이 가진 '진짜 사용자 계정'입니다. 봇이 아니라 사람 계정이라 전화번호로
로그인하고(코드 -> 필요하면 2단계 비밀번호), 세션 파일은 %APPDATA% 안에만 저장합니다.
봇은 쓰지 않습니다: 방에서 사람처럼 보여야 하므로 사람 계정(MTProto user client)만 씁니다.

Telethon is asyncio and tkinter is not, so exactly one asyncio loop runs on a
background thread for the whole program and the GUI hands it coroutines. Nothing
in the GUI thread ever awaits.
"""
import asyncio
import os
import re
import shutil
import sqlite3
import tempfile
import threading

import runlog

_TELETHON_ERROR = None
try:
    from telethon import TelegramClient, functions
    from telethon.crypto import AuthKey
    from telethon.errors import (FloodWaitError, PhoneCodeInvalidError,
                                 PhoneCodeExpiredError,
                                 PhonePasswordFloodError,
                                 SessionPasswordNeededError,
                                 PhoneNumberInvalidError)
    from telethon.sessions import StringSession
except Exception as exc:  # noqa: BLE001 - reported in the GUI, never a crash
    _TELETHON_ERROR = exc
    TelegramClient = None
    StringSession = object

import paths


class TgError(Exception):
    """A message that is safe to show the customer, in Korean."""


def digits(value):
    return re.sub(r"\D", "", str(value or ""))


# ---------------------------------------------------------------- sessions
# 1.0.1 handed Telethon a path, so every client kept an sqlite connection open on
# <key>.session for its whole life and committed only every 60 s. Two clients on
# the same file (a login attempt that failed with PhonePasswordFloodError leaked
# its client) fought over the write lock until one of them held it for good, and
# every send of that account then failed with "database is locked" (2026-10-12).
#
# Since 1.0.2 a client never holds a file: the login lives in memory
# (StringSession) and is written to <key>.str with an atomic replace, only when
# the auth key or data center actually changes. A 1.0.1 <key>.session is read
# once, read-only, and migrated.

def _str_path(key):
    return os.path.join(paths.sessions_dir(), key + ".str")


def _legacy_path(key):
    return os.path.join(paths.sessions_dir(), key + ".session")


def _read_legacy_rows(path):
    conn = sqlite3.connect(path, timeout=3)
    try:
        row = conn.execute("select dc_id, server_address, port, auth_key "
                           "from sessions").fetchone()
        try:
            entities = conn.execute(
                "select id, hash, username, phone, name from entities").fetchall()
        except Exception:  # noqa: BLE001
            entities = []
        return row, entities
    finally:
        conn.close()


def read_legacy_session(path):
    """(dc_id, address, port, auth_key bytes, entity rows) from a 1.0.1 file.

    A file another process still has locked is copied first and the copy is read:
    the main file only changes at commit time, so the copy holds the last
    committed login."""
    try:
        row, entities = _read_legacy_rows(path)
    except Exception as exc:  # noqa: BLE001
        runlog.log("세션 파일을 바로 읽지 못해 사본으로 읽습니다 (%s): %r"
                   % (os.path.basename(path), exc))
        temp_dir = tempfile.mkdtemp(prefix="srb-session-")
        try:
            copy = os.path.join(temp_dir, "copy.session")
            shutil.copyfile(path, copy)
            row, entities = _read_legacy_rows(copy)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    if not row or not row[3]:
        return None
    return row[0], row[1], row[2], bytes(row[3]), entities


class FileSession(StringSession):
    """In-memory Telethon session that persists only the login, atomically."""

    def __init__(self, string=None, path=None):
        super().__init__(string)
        self._path = path
        self._written = string or None

    def save(self):
        encoded = super().save()
        if not self._path or not self.auth_key or encoded == self._written:
            return encoded
        try:
            temp = self._path + ".tmp"
            with open(temp, "w", encoding="ascii") as handle:
                handle.write(encoded)
            os.replace(temp, self._path)
            self._written = encoded
        except Exception as exc:  # noqa: BLE001 - the login is still valid in memory
            runlog.log("세션 저장 실패 (%s), 다음 저장 때 다시 씁니다: %r"
                       % (os.path.basename(self._path), exc))
        return encoded

    def delete(self):
        for path in (self._path, (self._path or "")[:-4] + ".session"):
            try:
                if path and os.path.isfile(path):
                    os.unlink(path)
            except Exception:  # noqa: BLE001
                pass
        return True


def load_session(key):
    """The account's login as a FileSession; nothing stays open on disk."""
    path = _str_path(key)
    if os.path.isfile(path):
        try:
            with open(path, encoding="ascii") as handle:
                text = handle.read().strip()
            if text:
                return FileSession(text, path)
        except Exception as exc:  # noqa: BLE001
            runlog.log("세션 파일 읽기 실패 (%s): %r" % (os.path.basename(path), exc))
    session = FileSession(None, path)
    legacy = _legacy_path(key)
    if os.path.isfile(legacy):
        data = None
        try:
            data = read_legacy_session(legacy)
        except Exception as exc:  # noqa: BLE001
            runlog.log("이전 세션 파일을 읽지 못했습니다 (%s): %r"
                       % (os.path.basename(legacy), exc))
        if data:
            dc_id, address, port, key_bytes, entities = data
            session.set_dc(dc_id, address, port)
            session.auth_key = AuthKey(data=key_bytes)
            try:
                session._entities |= set(tuple(row) for row in entities)
            except Exception:  # noqa: BLE001
                pass
            session.save()      # migrate: from now on the .session file is never opened
    return session


class Manager(object):
    def __init__(self, settings_getter):
        self._settings = settings_getter
        self._loop = None
        self._thread = None
        self._clients = {}
        self._pending = {}       # phone key -> {"client", "hash", "phone"}
        self._entity_cache = {}
        self._lock = threading.RLock()
        self._key_locks = {}     # key -> asyncio.Lock, one client per account

    # ------------------------------------------------------------- loop
    def start(self):
        if self._thread:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def submit(self, coro):
        """Run a coroutine on the telegram loop. Returns a concurrent Future."""
        if not self._loop:
            self.start()
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def run(self, coro, timeout=120):
        return self.submit(coro).result(timeout)

    def shutdown(self):
        async def _close():
            for client in list(self._clients.values()):
                try:
                    await client.disconnect()
                except Exception:  # noqa: BLE001
                    pass
        try:
            self.submit(_close()).result(10)
        except Exception:  # noqa: BLE001
            pass
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:  # noqa: BLE001
            pass

    # --------------------------------------------------------- credentials
    def api_credentials(self):
        settings = self._settings()
        api_id = str(settings.get("api_id") or "").strip()
        api_hash = str(settings.get("api_hash") or "").strip()
        if not api_id.isdigit() or len(api_hash) < 20:
            raise TgError("계정 설정에서 API ID 와 API HASH 를 먼저 입력해 주세요. "
                          "my.telegram.org 에서 받으실 수 있습니다.")
        return int(api_id), api_hash

    def _new_client(self, session_name):
        if TelegramClient is None:
            raise TgError("텔레그램 모듈을 불러오지 못했습니다: %r" % (_TELETHON_ERROR,))
        api_id, api_hash = self.api_credentials()
        client = TelegramClient(load_session(session_name), api_id, api_hash,
                                device_model="Desktop", system_version="Windows",
                                app_version="1.0")
        if os.environ.get("SIGNALROOM_TEST_DC"):
            host, _, port = os.environ["SIGNALROOM_TEST_DC"].partition(":")
            client.session.set_dc(2, host, int(port or 443))
        return client

    def _key_lock(self, key):
        with self._lock:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = self._key_locks[key] = asyncio.Lock()
            return lock

    async def _client_for(self, account):
        """Connected client for a stored account. Exactly one per account."""
        key = account["key"]
        async with self._key_lock(key):
            with self._lock:
                client = self._clients.get(key)
            if client is not None and client.is_connected():
                return client
            await self._close_all_for(key)
            client = self._new_client(key)
            try:
                await client.connect()
                authorized = await client.is_user_authorized()
            except Exception:
                await _quiet_disconnect(client)
                raise
            if not authorized:
                await _quiet_disconnect(client)
                raise TgError("[%s] 로그인이 풀렸습니다. 계정 설정에서 다시 로그인해 주세요."
                              % account.get("label", key))
            with self._lock:
                self._clients[key] = client
            return client

    async def _close_all_for(self, key):
        """Disconnect every client this app holds for one account (stored or a
        login still in progress)."""
        with self._lock:
            stored = self._clients.pop(key, None)
            pending = self._pending.pop(key, None)
        for client in (stored, (pending or {}).get("client")):
            if client is not None:
                await _quiet_disconnect(client)

    async def _adopt(self, key, client):
        """The login client becomes THE client of the account."""
        with self._lock:
            old = self._clients.get(key)
            self._clients[key] = client
            self._pending.pop(key, None)
        if old is not None and old is not client:
            await _quiet_disconnect(old)
        for cache_key in [k for k in self._entity_cache if k[0] == key]:
            self._entity_cache.pop(cache_key, None)

    async def _drop_client(self, key):
        await self._close_all_for(key)

    # ------------------------------------------------------------- login
    async def request_code(self, phone):
        """1단계: 전화번호로 인증코드를 보냅니다."""
        phone = phone.strip()
        if not phone.startswith("+"):
            phone = "+" + digits(phone)
        key = "user_" + digits(phone)
        # A second login for the same number must not open a second client on it.
        await self._close_all_for(key)
        client = self._new_client(key)
        try:
            await client.connect()
            if await client.is_user_authorized():
                me = await client.get_me()
                await self._adopt(key, client)
                return {"already": True, "key": key, "label": display_name(me),
                        "user_id": me.id, "phone": phone}
            sent = await client.send_code_request(phone)
        except PhoneNumberInvalidError:
            await _quiet_disconnect(client)
            raise TgError("전화번호 형식이 올바르지 않습니다. 국가번호까지 넣어 주세요 (예: +821012345678)")
        except FloodWaitError as exc:
            await _quiet_disconnect(client)
            raise TgError("텔레그램이 잠시 요청을 막았습니다. %d초 뒤에 다시 시도해 주세요."
                          % exc.seconds)
        except PhonePasswordFloodError as exc:
            await _quiet_disconnect(client)
            runlog.log("로그인 제한 (%s): %r, 이 시도의 연결은 닫았습니다." % (key, exc))
            raise TgError("이 번호로 로그인 시도가 너무 많아 텔레그램이 잠시 막았습니다. "
                          "몇 시간 뒤에 다시 시도해 주세요.")
        except BaseException:
            # 1.0.1 left the client connected here; it kept the session file
            # open next to the real client and locked it (2026-10-12).
            await _quiet_disconnect(client)
            raise
        with self._lock:
            self._pending[key] = {"client": client, "hash": sent.phone_code_hash,
                                  "phone": phone}
        return {"already": False, "key": key, "phone": phone}

    async def submit_code(self, key, code):
        """2단계: 텔레그램 앱으로 받은 코드를 넣습니다."""
        with self._lock:
            pending = self._pending.get(key)
        if not pending:
            raise TgError("인증 진행 정보가 없습니다. 전화번호부터 다시 입력해 주세요.")
        client = pending["client"]
        try:
            me = await client.sign_in(pending["phone"], code=digits(code),
                                      phone_code_hash=pending["hash"])
        except SessionPasswordNeededError:
            return {"needs_password": True, "key": key}
        except PhoneCodeInvalidError:
            raise TgError("인증코드가 올바르지 않습니다. 다시 확인해 주세요.")
        except PhoneCodeExpiredError:
            raise TgError("인증코드 유효시간이 지났습니다. 코드 다시 받기를 눌러 주세요.")
        return await self._finish_login(key, client, me)

    async def submit_password(self, key, password):
        """3단계(2단계 인증을 켠 계정만): 텔레그램 비밀번호."""
        with self._lock:
            pending = self._pending.get(key)
        if not pending:
            raise TgError("인증 진행 정보가 없습니다. 전화번호부터 다시 입력해 주세요.")
        client = pending["client"]
        try:
            me = await client.sign_in(password=password)
        except Exception as exc:  # noqa: BLE001
            raise TgError("2단계 비밀번호가 맞지 않습니다. (%s)" % type(exc).__name__)
        return await self._finish_login(key, client, me)

    async def _finish_login(self, key, client, me):
        try:
            client.session.save()
        except Exception:  # noqa: BLE001
            pass
        await self._adopt(key, client)
        return {"key": key, "kind": "user", "label": display_name(me),
                "user_id": me.id, "username": getattr(me, "username", None)}

    async def logout(self, account):
        """세션 파일까지 지웁니다."""
        key = account["key"]
        try:
            client = await self._client_for(account)
            await client.log_out()
        except Exception:  # noqa: BLE001
            pass
        await self._drop_client(key)
        for suffix in (".str", ".session", ".session-journal", ""):
            try:
                path = os.path.join(paths.sessions_dir(), key + suffix)
                if os.path.isfile(path):
                    os.unlink(path)
            except Exception:  # noqa: BLE001
                pass
        return True

    # -------------------------------------------------------------- rooms
    async def list_dialogs(self, account, limit=200):
        client = await self._client_for(account)
        rooms = []
        async for dialog in client.iter_dialogs(limit=limit):
            if dialog.is_group or dialog.is_channel:
                rooms.append({"id": dialog.id, "title": dialog.name or str(dialog.id),
                              "broadcast": bool(getattr(dialog.entity, "broadcast", False))})
        return rooms

    async def resolve(self, account, chat_id):
        """방을 이 계정이 실제로 보낼 수 있는지 확인하고 InputPeer 를 돌려줍니다."""
        cache_key = (account["key"], int(chat_id))
        peer = self._entity_cache.get(cache_key)
        if peer is not None:
            return peer
        client = await self._client_for(account)
        try:
            peer = await client.get_input_entity(int(chat_id))
        except Exception:  # noqa: BLE001
            peer = None
        if peer is None:
            try:
                async for dialog in client.iter_dialogs():
                    if dialog.id == int(chat_id):
                        peer = await client.get_input_entity(dialog.entity)
                        break
            except Exception:  # noqa: BLE001
                peer = None
        if peer is None:
            raise TgError("[%s] 계정이 이 방에 들어가 있지 않습니다. 방에 초대한 뒤 다시 확인해 주세요."
                          % account.get("label", account["key"]))
        self._entity_cache[cache_key] = peer
        return peer

    async def check_members(self, accounts, chat_id):
        """Returns (ok_labels, [ (label, reason) ]) for the enabled accounts."""
        ok, bad = [], []
        for account in accounts:
            try:
                await self.resolve(account, chat_id)
                ok.append(account.get("label", account["key"]))
            except TgError as exc:
                bad.append((account.get("label", account["key"]), str(exc)))
            except Exception as exc:  # noqa: BLE001
                bad.append((account.get("label", account["key"]), repr(exc)))
        return ok, bad

    # -------------------------------------------------------------- send
    async def send_text(self, account, chat_id, text):
        async def attempt(stage):
            client = await self._client_for(account)
            peer = await self.resolve(account, chat_id)
            stage["sending"] = True
            message = await client.send_message(peer, text)
            return getattr(message, "id", None)
        return await self._with_recovery(account, attempt)

    async def send_card(self, account, chat_id, image_path, caption):
        async def attempt(stage):
            client = await self._client_for(account)
            peer = await self.resolve(account, chat_id)
            stage["sending"] = True
            message = await client.send_file(peer, image_path, caption=caption,
                                             force_document=False)
            return getattr(message, "id", None)
        return await self._with_recovery(account, attempt)

    async def _with_recovery(self, account, attempt):
        """A session-storage error must never stall an account for good: close
        every client of that account, open a fresh one, try once more.

        Telethon stores entities only AFTER Telegram answered the send, so a
        storage error raised during the send itself means the message is already
        in the room: the client is recycled, but the line is not posted twice."""
        stage = {"sending": False}
        try:
            return await attempt(stage)
        except Exception as exc:  # noqa: BLE001
            if not is_storage_error(exc):
                raise
            label = account.get("label", account["key"])
            await self._close_all_for(account["key"])
            if stage["sending"]:
                runlog.log("[%s] 세션 잠김 감지 (%r): 메시지는 이미 보내져서 다시 보내지 않고, "
                           "연결만 새로 엽니다." % (label, exc))
                return None
            runlog.log("[%s] 세션 잠김 감지 (%r): 연결을 닫고 새로 열어 한 번 더 보냅니다."
                       % (label, exc))
            result = await attempt({"sending": False})
            runlog.log("[%s] 세션 잠김 복구 완료: 다시 보내기 성공." % label)
            return result

    async def whoami(self, account):
        client = await self._client_for(account)
        me = await client.get_me()
        return {"id": me.id, "label": display_name(me)}


async def _quiet_disconnect(client):
    try:
        await client.disconnect()
    except Exception:  # noqa: BLE001
        pass


def is_storage_error(exc):
    return (isinstance(exc, sqlite3.Error)
            or "database is locked" in str(exc).lower())


def display_name(entity):
    first = getattr(entity, "first_name", None) or ""
    last = getattr(entity, "last_name", None) or ""
    name = ("%s %s" % (first, last)).strip()
    if not name:
        name = getattr(entity, "username", None) or getattr(entity, "title", None) or ""
    return name or str(getattr(entity, "id", "?"))


def flood_seconds(exc):
    return int(getattr(exc, "seconds", 0) or 0)


def is_flood(exc):
    return _TELETHON_ERROR is None and isinstance(exc, FloodWaitError)
