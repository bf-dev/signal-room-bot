# -*- coding: utf-8 -*-
"""A fake Telegram network for Telethon, for reproducing session-file bugs offline.

Only MTProtoSender is replaced. Everything above it is real Telethon 1.44: the
TelegramClient, its update loop and keepalive loop, and its SQLiteSession on a real
.session file. That is what the "database is locked" bug lives in, so it is what has
to run for real. No socket is opened and no real account is involved.

Scripted per phone: how many SendCodeRequest calls fail with PhonePasswordFloodError
before one succeeds (what the customer hit on 2026-10-12).
"""
import asyncio
import datetime
import inspect
import itertools
import os
import random
import textwrap
import time

from telethon import errors
from telethon.crypto import AuthKey
from telethon.tl import functions, types

import telethon.client.telegrambaseclient as _tbc
import telethon.client.updates as _upd

CHAT_ID = 4385880988            # bare channel id; the app uses -100<id>
AUTHORIZED = {}                 # auth key bytes -> user id
FLOOD_PLAN = {}                 # phone -> remaining PhonePasswordFlood failures
SENT = []                       # (user_id, text) actually accepted by the "server"
LIVE = []                       # every connected FakeSender, like sockets on Telegram's side
_ids = itertools.count(100)


def now():
    return datetime.datetime.now(tz=datetime.timezone.utc)


def _user(uid, is_self=True):
    return types.User(id=uid, access_hash=uid * 7, first_name="lab%d" % uid,
                      is_self=is_self, phone="99966%d" % uid)


def _channel():
    return types.Channel(id=CHAT_ID, title="lab room", photo=types.ChatPhotoEmpty(),
                         date=now(), access_hash=4242, megagroup=True)


def _unwrap(request):
    while isinstance(request, (functions.InvokeWithLayerRequest,
                               functions.InitConnectionRequest,
                               functions.InvokeWithoutUpdatesRequest)):
        request = request.query
    return request


class FakeSender(object):
    def __init__(self, auth_key, *, loggers, auth_key_callback=None,
                 updates_queue=None, auto_reconnect_callback=None, **_kw):
        self.auth_key = auth_key or AuthKey(None)
        self._auth_key_callback = auth_key_callback
        self._auto_reconnect_callback = auto_reconnect_callback
        self._user_connected = False
        self._disc = None
        self._updates_queue = updates_queue
        self.subscribed = False     # Telegram pushes updates after an authorized call

    async def connect(self, connection):
        if self._user_connected:
            return False
        if not self.auth_key.key:
            self.auth_key.key = os.urandom(256)
            if self._auth_key_callback:
                await self._auth_key_callback(self.auth_key)
        self._user_connected = True
        self._disc = asyncio.get_running_loop().create_future()
        LIVE.append(self)
        return True

    def is_connected(self):
        return self._user_connected

    def _transport_connected(self):
        return self._user_connected

    def _keepalive_ping(self, _rnd):
        return None

    async def disconnect(self):
        self._user_connected = False
        if self in LIVE:
            LIVE.remove(self)
        if self._disc and not self._disc.done():
            self._disc.set_result(None)

    @property
    def disconnected(self):
        if self._disc is None:
            self._disc = asyncio.get_running_loop().create_future()
            self._disc.set_result(None)
        return asyncio.shield(self._disc)

    def send(self, request, ordered=False):
        if isinstance(request, list):
            return [self.send(r) for r in request]
        future = asyncio.get_running_loop().create_future()
        try:
            future.set_result(self._answer(_unwrap(request)))
        except Exception as exc:  # noqa: BLE001
            future.set_exception(exc)
        return future

    def _uid(self):
        return AUTHORIZED.get(bytes(self.auth_key.key or b""))

    def _answer(self, r):
        uid = self._uid()
        if isinstance(r, functions.help.GetConfigRequest):
            return None
        if isinstance(r, functions.auth.SendCodeRequest):
            phone = r.phone_number.lstrip("+")
            left = FLOOD_PLAN.get(phone, 0)
            if left > 0:
                FLOOD_PLAN[phone] = left - 1
                raise errors.PhonePasswordFloodError(request=r)
            return types.auth.SentCode(type=types.auth.SentCodeTypeApp(length=5),
                                       phone_code_hash="hash-" + r.phone_number)
        if isinstance(r, functions.auth.SignInRequest):
            new_uid = int(r.phone_number.lstrip("+")[-6:])
            AUTHORIZED[bytes(self.auth_key.key)] = new_uid
            return types.auth.Authorization(user=_user(new_uid))
        # Telegram keeps a session per connection that has talked to it, authorized
        # or not; once the auth key is signed in, every such connection gets pushes.
        self.subscribed = True
        if uid is None:
            raise errors.AuthKeyUnregisteredError(request=r)
        if isinstance(r, functions.updates.GetStateRequest):
            return types.updates.State(pts=1, qts=0, date=now(), seq=1, unread_count=0)
        if isinstance(r, functions.updates.GetDifferenceRequest):
            return types.updates.DifferenceEmpty(date=now(), seq=1)
        if isinstance(r, functions.updates.GetChannelDifferenceRequest):
            return types.updates.ChannelDifferenceEmpty(pts=r.pts or 1, final=True)
        if isinstance(r, functions.users.GetUsersRequest):
            return [_user(uid)]
        if isinstance(r, functions.channels.GetChannelsRequest):
            return types.messages.Chats(chats=[_channel()])
        if isinstance(r, functions.messages.GetDialogsRequest):
            msg = types.Message(id=1, peer_id=types.PeerChannel(CHAT_ID), date=now(),
                                message="hi")
            dialog = types.Dialog(peer=types.PeerChannel(CHAT_ID), top_message=1,
                                  read_inbox_max_id=1, read_outbox_max_id=1,
                                  unread_count=0, unread_mentions_count=0,
                                  unread_reactions_count=0, unread_poll_votes_count=0,
                                  notify_settings=types.PeerNotifySettings())
            return types.messages.Dialogs(dialogs=[dialog], messages=[msg],
                                          chats=[_channel()], users=[_user(uid)])
        if isinstance(r, functions.messages.SendMessageRequest):
            SENT.append((uid, r.message))
            mid = next(_ids)
            msg = types.Message(id=mid, peer_id=types.PeerChannel(CHAT_ID), date=now(),
                                message=r.message, out=True,
                                from_id=types.PeerUser(uid))
            # Every member of the room gets the new message pushed, on every
            # connection that is authorized for that member. That includes a stale
            # login client left on the same .session file: it shares the auth key,
            # so once the account is signed in, Telegram treats it as signed in too.
            for other in list(LIVE):
                if (other is not self and other.subscribed and other._uid() is not None
                        and other._updates_queue):
                    other._updates_queue.put_nowait(types.Updates(
                        updates=[types.UpdateNewChannelMessage(message=msg, pts=mid,
                                                               pts_count=1)],
                        users=[_user(uid, is_self=False)], chats=[_channel()],
                        date=now(), seq=0))
            return types.Updates(
                updates=[types.UpdateMessageID(id=mid, random_id=r.random_id),
                         types.UpdateNewChannelMessage(message=msg, pts=mid, pts_count=1)],
                users=[_user(uid)], chats=[_channel()], date=now(), seq=0)
        if isinstance(r, functions.auth.LogOutRequest):
            AUTHORIZED.pop(bytes(self.auth_key.key), None)
            return types.auth.LoggedOut()
        return None


def _push_room_message(sender_uid, text, skip=None):
    mid = next(_ids)
    msg = types.Message(id=mid, peer_id=types.PeerChannel(CHAT_ID), date=now(),
                        message=text, from_id=types.PeerUser(sender_uid))
    for other in list(LIVE):
        if (other is not skip and other.subscribed and other._uid() is not None
                and other._updates_queue):
            other._updates_queue.put_nowait(types.Updates(
                updates=[types.UpdateNewChannelMessage(message=msg, pts=mid, pts_count=1)],
                users=[_user(sender_uid, is_self=False)], chats=[_channel()],
                date=now(), seq=0))
    return mid


async def room_chatter(every, stop, blip_every=0):
    """Other people in the room (the signal provider, real members) keep talking, and
    now and then Telegram drops a TCP connection, which Telethon answers with an
    automatic reconnect plus a get_me() (that is what an idle PC does all day)."""
    n = 0
    next_blip = time.time() + blip_every if blip_every else None
    while not stop.is_set():
        n += 1
        _push_room_message(777000 + n % 50, "room chatter %d" % n)
        if next_blip and time.time() >= next_blip:
            next_blip = time.time() + blip_every * random.uniform(0.5, 1.5)
            for sender in list(LIVE):
                if sender._auto_reconnect_callback:
                    asyncio.get_running_loop().create_task(sender._auto_reconnect_callback())
        await asyncio.sleep(every * random.uniform(0.3, 1.7))


def install(scale=0.1):
    """Swap the network out and run Telethon's clocks faster, keeping their ratio.

    Telethon commits a .session only from its keepalive loop (every 60 s) and Python's
    sqlite3 waits 5 s on a locked file before raising "database is locked". Both are
    multiplied by `scale` (default 6 s and 0.5 s) so an hour of the customer's day runs
    in minutes with the same 12:1 race window. The keepalive body is Telethon's own
    source with only the interval changed."""
    import sqlite3
    import telethon.sessions.sqlite as _sq
    _tbc.MTProtoSender = FakeSender
    busy = 5.0 * scale

    class _Sqlite3(object):
        def __getattr__(self, name):
            return getattr(sqlite3, name)

        @staticmethod
        def connect(*args, **kwargs):
            kwargs.setdefault("timeout", busy)
            return sqlite3.connect(*args, **kwargs)
    _sq.sqlite3 = _Sqlite3()
    keepalive_seconds = 60.0 * scale
    source = textwrap.dedent(inspect.getsource(_upd.UpdateMethods._keepalive_loop))
    source = source.replace("timeout=60", "timeout=%r" % float(keepalive_seconds))
    namespace = {}
    exec(compile(source, _upd.__file__, "exec"), vars(_upd), namespace)
    _upd.UpdateMethods._keepalive_loop = namespace["_keepalive_loop"]
