# -*- coding: utf-8 -*-
"""Where the program keeps its own files.

%APPDATA%\\signal-room-bot on Windows, ~/.signal-room-bot elsewhere. Telethon
session files (one per account) live in sessions/ and never leave that folder:
they are login credentials for the customer's own Telegram accounts.
"""
import os
import sys

import config

_APP_DIR = None


def app_dir():
    global _APP_DIR
    if _APP_DIR:
        return _APP_DIR
    override = os.environ.get("SIGNALROOM_HOME")
    if override:
        path = override
    else:
        base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        if base and os.path.isdir(base):
            path = os.path.join(base, config.APP_NAME)
        else:
            path = os.path.join(os.path.expanduser("~"), "." + config.APP_NAME)
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:  # noqa: BLE001
        path = os.path.abspath(".")
    _APP_DIR = path
    return path


def in_app_dir(name):
    return os.path.join(app_dir(), name)


def sessions_dir():
    path = os.path.join(app_dir(), "sessions")
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:  # noqa: BLE001
        pass
    return path


def cards_dir():
    """Generated trade cards. Rewritten every 청산 burst, kept so the customer
    (and we, from an artifact) can see exactly what was posted."""
    path = os.path.join(app_dir(), "cards")
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:  # noqa: BLE001
        pass
    return path


def resource(name):
    """A file bundled into the build (PyInstaller unpacks beside the exe)."""
    for base in (getattr(sys, "_MEIPASS", None),
                 os.path.dirname(os.path.abspath(sys.argv[0])),
                 os.path.dirname(os.path.abspath(__file__))):
        if not base:
            continue
        candidate = os.path.join(base, name)
        if os.path.exists(candidate):
            return candidate
    return None
