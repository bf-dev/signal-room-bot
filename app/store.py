# -*- coding: utf-8 -*-
"""settings.json in the customer's own %APPDATA% folder.

Holds: their Telegram api_id/api_hash, the account list (label + session name +
enabled flag, never a password), the target room, the burst timing and the
phrase pools. Bot tokens ARE stored here because a bot token is the only way to
use that account again; the file lives under the customer's user profile.
"""
import json
import os
import threading

import config
import paths
import phrases

_LOCK = threading.RLock()

DEFAULT = {
    "api_id": "",
    "api_hash": "",
    "accounts": [],          # {key, kind: user|bot, label, phone, token, enabled}
    "target": None,          # {"id": int, "title": str}
    "burst": dict(config.DEFAULTS),
    "pools": {k: list(v) for k, v in phrases.DEFAULT_POOLS.items()},
    "last_close": {},        # last 청산 form values, so the form comes back filled
}


def _path():
    return paths.in_app_dir("settings.json")


def load():
    with _LOCK:
        data = json.loads(json.dumps(DEFAULT))  # deep copy
        try:
            with open(_path(), encoding="utf-8") as handle:
                stored = json.load(handle)
        except Exception:  # noqa: BLE001
            return data
        for key, value in stored.items():
            if key == "burst" and isinstance(value, dict):
                data["burst"].update(value)
            elif key == "pools" and isinstance(value, dict):
                for category, items in value.items():
                    if isinstance(items, list) and items:
                        data["pools"][category] = list(items)
            else:
                data[key] = value
        return data


def save(data):
    with _LOCK:
        tmp = _path() + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=1)
            os.replace(tmp, _path())
            return True
        except Exception:  # noqa: BLE001
            return False


def redacted(data):
    """A copy safe to put in an artifact: no api_hash, no bot tokens."""
    try:
        copy = json.loads(json.dumps(data))
        if copy.get("api_hash"):
            copy["api_hash"] = "***"
        for account in copy.get("accounts", []):
            if account.get("token"):
                account["token"] = "***"
            if account.get("phone"):
                digits = str(account["phone"])
                account["phone"] = digits[:4] + "***" + digits[-2:]
        return copy
    except Exception:  # noqa: BLE001
        return {"customerId": config.CUSTOMER_ID}
