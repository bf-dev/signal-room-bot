# -*- coding: utf-8 -*-
"""텔레그램 계정 관리 (Telethon).

계정은 고객이 가진 '진짜 사용자 계정'입니다. 봇이 아니라 사람 계정이라 전화번호로
로그인하고(코드 -> 필요하면 2단계 비밀번호), 세션 파일은 %APPDATA% 안에만 저장합니다.
봇 토큰도 보조 수단으로 받을 수 있습니다.

Telethon is asyncio and tkinter is not, so exactly one asyncio loop runs on a
background thread for the whole program and the GUI hands it coroutines. Nothing
in the GUI thread ever awaits.
"""
import asyncio
import os
import re
import threading

import runlog

_TELETHON_ERROR = None
try:
    from telethon import TelegramClient, functions
    from telethon.errors import (FloodWaitError, PhoneCodeInvalidError,
                                 PhoneCodeExpiredError,
                                 SessionPasswordNeededError,
                                 PhoneNumberInvalidError)
    from telethon.tl.types import InputPeerChannel, InputPeerChat, InputPeerUser
except Exception as exc:  # noqa: BLE001 - reported in the GUI, never a crash
    _TELETHON_ERROR = exc
    TelegramClient = None

import paths


class TgError(Exception):
    """A message that is safe to show the customer, in Korean."""


def digits(value):
    return re.sub(r"\D", "", str(value or ""))


class Manager(object):
    def __init__(self, settings_getter):
        self._settings = settings_getter
        self._loop = None
        self._thread = None
        self._clients = {}
        self._pending = {}       # phone key -> {"client", "hash", "phone"}
        self._entity_cache = {}
        self._lock = threading.RLock()

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
            raise TgError("설정 탭에서 API ID 와 API HASH 를 먼저 입력해 주세요. "
                          "my.telegram.org 에서 받으실 수 있습니다.")
        return int(api_id), api_hash

    def _new_client(self, session_name):
        if TelegramClient is None:
            raise TgError("텔레그램 모듈을 불러오지 못했습니다: %r" % (_TELETHON_ERROR,))
        api_id, api_hash = self.api_credentials()
        path = os.path.join(paths.sessions_dir(), session_name)
        client = TelegramClient(path, api_id, api_hash,
                                device_model="Desktop", system_version="Windows",
                                app_version="1.0")
        if os.environ.get("SIGNALROOM_TEST_DC"):
            host, _, port = os.environ["SIGNALROOM_TEST_DC"].partition(":")
            client.session.set_dc(2, host, int(port or 443))
        return client

    async def _client_for(self, account):
        """Connected client for a stored account."""
        key = account["key"]
        with self._lock:
            client = self._clients.get(key)
        if client is not None and client.is_connected():
            return client
        client = self._new_client(key)
        await client.connect()
        if not await client.is_user_authorized():
            if account.get("kind") == "bot" and account.get("token"):
                await client.sign_in(bot_token=account["token"])
            else:
                await client.disconnect()
                raise TgError("[%s] 로그인이 풀렸습니다. 계정 탭에서 다시 로그인해 주세요."
                              % account.get("label", key))
        with self._lock:
            self._clients[key] = client
        return client

    async def _drop_client(self, key):
        with self._lock:
            client = self._clients.pop(key, None)
        if client:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------- login
    async def request_code(self, phone):
        """1단계: 전화번호로 인증코드를 보냅니다."""
        phone = phone.strip()
        if not phone.startswith("+"):
            phone = "+" + digits(phone)
        key = "user_" + digits(phone)
        client = self._new_client(key)
        await client.connect()
        if await client.is_user_authorized():
            me = await client.get_me()
            with self._lock:
                self._clients[key] = client
            return {"already": True, "key": key, "label": display_name(me),
                    "user_id": me.id, "phone": phone}
        try:
            sent = await client.send_code_request(phone)
        except PhoneNumberInvalidError:
            await client.disconnect()
            raise TgError("전화번호 형식이 올바르지 않습니다. 국가번호까지 넣어 주세요 (예: +821012345678)")
        except FloodWaitError as exc:
            await client.disconnect()
            raise TgError("텔레그램이 잠시 요청을 막았습니다. %d초 뒤에 다시 시도해 주세요."
                          % exc.seconds)
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
        return self._finish_login(key, client, me)

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
        return self._finish_login(key, client, me)

    def _finish_login(self, key, client, me):
        with self._lock:
            self._pending.pop(key, None)
            self._clients[key] = client
        return {"key": key, "kind": "user", "label": display_name(me),
                "user_id": me.id, "username": getattr(me, "username", None)}

    async def add_bot(self, token):
        token = token.strip()
        bot_id = token.split(":")[0]
        if not bot_id.isdigit():
            raise TgError("봇 토큰 형식이 올바르지 않습니다.")
        key = "bot_" + bot_id
        client = self._new_client(key)
        await client.connect()
        if not await client.is_user_authorized():
            try:
                await client.sign_in(bot_token=token)
            except Exception as exc:  # noqa: BLE001
                await client.disconnect()
                raise TgError("봇 로그인에 실패했습니다: %s" % type(exc).__name__)
        me = await client.get_me()
        with self._lock:
            self._clients[key] = client
        return {"key": key, "kind": "bot", "label": display_name(me),
                "user_id": me.id, "token": token,
                "username": getattr(me, "username", None)}

    async def logout(self, account):
        """세션 파일까지 지웁니다."""
        key = account["key"]
        try:
            client = await self._client_for(account)
            await client.log_out()
        except Exception:  # noqa: BLE001
            pass
        await self._drop_client(key)
        for suffix in (".session", ".session-journal", ""):
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
        if peer is None and account.get("kind") != "bot":
            try:
                async for dialog in client.iter_dialogs():
                    if dialog.id == int(chat_id):
                        peer = await client.get_input_entity(dialog.entity)
                        break
            except Exception:  # noqa: BLE001
                peer = None
        if peer is None and account.get("kind") == "bot":
            # A bot never has dialogs; it can address a channel it is a member
            # of by id with access_hash 0.
            raw = int(chat_id)
            try:
                if raw < 0:
                    marked = abs(raw)
                    if str(marked).startswith("100"):
                        peer = InputPeerChannel(int(str(marked)[3:]), 0)
                    else:
                        peer = InputPeerChat(marked)
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
        client = await self._client_for(account)
        peer = await self.resolve(account, chat_id)
        message = await client.send_message(peer, text)
        return getattr(message, "id", None)

    async def send_card(self, account, chat_id, image_path, caption):
        client = await self._client_for(account)
        peer = await self.resolve(account, chat_id)
        message = await client.send_file(peer, image_path, caption=caption,
                                         force_document=False)
        return getattr(message, "id", None)

    async def whoami(self, account):
        client = await self._client_for(account)
        me = await client.get_me()
        return {"id": me.id, "label": display_name(me)}


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
