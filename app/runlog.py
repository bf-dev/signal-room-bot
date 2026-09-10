# -*- coding: utf-8 -*-
"""The run log: what the customer sees in the window, and what we read out of
an uploaded artifact when they say it stopped working.

Kept in memory (bounded) and appended to run.log next to the settings.
"""
import threading
import time

import paths

_LOCK = threading.Lock()
_LINES = []
_MAX = 4000
_SINKS = []


def add_sink(callback):
    """GUI hook: called with each new line, from whatever thread logged it."""
    _SINKS.append(callback)


def log(message):
    line = "%s  %s" % (time.strftime("%H:%M:%S"), message)
    with _LOCK:
        _LINES.append(line)
        if len(_LINES) > _MAX:
            del _LINES[: len(_LINES) - _MAX]
    try:
        with open(paths.in_app_dir("run.log"), "a", encoding="utf-8") as handle:
            handle.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message))
    except Exception:  # noqa: BLE001
        pass
    for sink in list(_SINKS):
        try:
            sink(line)
        except Exception:  # noqa: BLE001
            pass
    return line


def text():
    with _LOCK:
        return "\n".join(_LINES)


def lines():
    with _LOCK:
        return list(_LINES)


def flush():
    return True
