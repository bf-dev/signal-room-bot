# -*- coding: utf-8 -*-
"""settings.json in the customer's own %APPDATA% folder.

Holds: their Telegram api_id/api_hash, the account list (label + session name +
enabled flag, never a password), the target room, the burst timing and the
핫키(버튼)들. 비밀번호는 저장하지 않습니다: Telethon 세션 파일이 로그인 상태를 들고
있고, 그 파일은 sessions/ 안에만 있습니다.
"""
import json
import os
import threading

import config
import hotkeys
import paths

_LOCK = threading.RLock()

DEFAULT = {
    "api_id": "",
    "api_hash": "",
    "accounts": [],          # {key, kind: "user", label, phone, enabled}
    "target": None,          # {"id": int, "title": str}
    "burst": dict(config.DEFAULTS),
    "hotkeys": [],           # 메인 화면 버튼들. 비어 있으면 기본 핫키를 넣어준다.
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
            else:
                data[key] = value
        if not data.get("hotkeys"):
            data["hotkeys"] = hotkeys.defaults()
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
    """A copy safe to put in an artifact: no api_hash, phone numbers masked."""
    try:
        copy = json.loads(json.dumps(data))
        if copy.get("api_hash"):
            copy["api_hash"] = "***"
        for account in copy.get("accounts", []):
            if account.get("phone"):
                digits = str(account["phone"])
                account["phone"] = digits[:4] + "***" + digits[-2:]
        return copy
    except Exception:  # noqa: BLE001
        return {"customerId": config.CUSTOMER_ID}
