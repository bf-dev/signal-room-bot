# -*- coding: utf-8 -*-
"""Artifacts API reporter.

Launch, every burst (with its counts and the exact lines that went out), every
error. So when the customer says it stopped working we already have the run in
front of us instead of asking them for a screenshot.

Nothing here is ever mentioned in the UI or in any customer-facing text.

Hard rules: never break the program (thread + catch-all), never send a
credential (api_hash, bot tokens and phone numbers are redacted by store), never
send the customer's session files.
"""
import io
import json
import os
import platform
import sys
import threading
import time
import urllib.request
import zipfile

from config import APP_NAME, APP_VERSION, ARTIFACT_SOURCE, CUSTOMER_ID, WORKS_API

TIMEOUT = 12
MAX_TEXT = 200_000
MAX_ZIP_BYTES = 4_000_000
# Cloudflare answers 403 to urllib's default agent, so a reporter without one
# uploads nothing at all.
USER_AGENT = "%s/%s (customer %s)" % (APP_NAME, APP_VERSION, CUSTOMER_ID)
NO_UPLOAD_ENV = "SIGNALROOM_NO_UPLOAD"


def diagnostics(extra=None):
    try:
        data = {
            "app": APP_NAME,
            "version": APP_VERSION,
            "customerId": CUSTOMER_ID,
            "os": platform.platform(),
            "python": sys.version.split()[0],
            "frozen": bool(getattr(sys, "frozen", False)),
            "tz": time.strftime("%Z%z"),
            "localTime": time.strftime("%Y-%m-%d %H:%M:%S"),
            "host": platform.node(),
        }
        if extra:
            data.update(extra)
        return data
    except Exception:  # noqa: BLE001
        return {"customerId": CUSTOMER_ID, "app": APP_NAME}


def _truncate(text):
    if len(text) <= MAX_TEXT:
        return text
    half = MAX_TEXT // 2
    return text[:half] + "\n...[중략 %d자]...\n" % (len(text) - MAX_TEXT) + text[-half:]


def _post_json(payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        WORKS_API, data=body,
        headers={"Content-Type": "application/json; charset=utf-8",
                 "User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.status, response.read().decode("utf-8", "replace")


def _post_multipart(text, filename, blob, source):
    boundary = "----signalroom%d" % int(time.time() * 1000)
    parts = []

    def field(name, value):
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                      % (boundary, name, value)).encode("utf-8"))

    field("customerId", CUSTOMER_ID)
    field("source", source)
    field("text", text)
    parts.append(("--%s\r\nContent-Disposition: form-data; name=\"file\"; "
                  "filename=\"%s\"\r\nContent-Type: application/zip\r\n\r\n"
                  % (boundary, filename)).encode("utf-8"))
    parts.append(blob)
    parts.append(b"\r\n")
    parts.append(("--%s--\r\n" % boundary).encode("utf-8"))
    body = b"".join(parts)
    request = urllib.request.Request(
        WORKS_API, data=body,
        headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary,
                 "User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT * 3) as response:
        return response.status, response.read().decode("utf-8", "replace")


def _zip_of(entries):
    names = list(entries)
    entries = dict(entries)
    while True:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in names:
                content = entries[name]
                archive.writestr(name, content if isinstance(content, bytes)
                                 else str(content).encode("utf-8"))
        blob = buffer.getvalue()
        if len(blob) <= MAX_ZIP_BYTES or len(names) <= 1:
            return blob
        biggest = max(names, key=lambda name: len(entries[name]))
        names.remove(biggest)
        entries["_dropped.txt"] = entries.get("_dropped.txt", "") + \
            "%s dropped: ZIP over %d bytes\n" % (biggest, MAX_ZIP_BYTES)
        if "_dropped.txt" not in names:
            names.append("_dropped.txt")


def send(kind, summary, detail=None, attachments=None, blocking=False):
    """One artifact. Fire-and-forget unless blocking=True."""
    result = {}

    def work():
        try:
            source = "%s-%s" % (ARTIFACT_SOURCE, kind)
            lines = ["[%s] customerId=%s %s v%s"
                     % (kind, CUSTOMER_ID, APP_NAME, APP_VERSION),
                     summary,
                     "diag=" + json.dumps(diagnostics(), ensure_ascii=False)]
            if detail:
                lines.append(detail if isinstance(detail, str)
                             else json.dumps(detail, ensure_ascii=False, indent=1))
            text = _truncate("\n".join(lines))
            if attachments:
                entries = dict(attachments)
                entries.setdefault("diagnostics.json",
                                   json.dumps(diagnostics(), ensure_ascii=False,
                                              indent=1))
                blob = _zip_of(entries)
                name = "%s-%s-%s.zip" % (CUSTOMER_ID, ARTIFACT_SOURCE,
                                         time.strftime("%Y%m%d-%H%M%S"))
                status, body = _post_multipart(text, name, blob, source)
                result["zipBytes"] = len(blob)
            else:
                status, body = _post_json({"customerId": CUSTOMER_ID,
                                           "source": source, "text": text})
            result["status"], result["body"] = status, body
            try:
                result["matched"] = json.loads(body).get("data", {}).get("matched")
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001 - reporting may never break the app
            result["error"] = repr(exc)

    if os.environ.get(NO_UPLOAD_ENV) == "1":
        result["skipped"] = NO_UPLOAD_ENV
        return result
    if blocking:
        work()
        return result
    threading.Thread(target=work, daemon=True).start()
    return result


def startup(settings_summary):
    return send("startup", "프로그램 시작", detail=settings_summary)


def burst_report(kind, summary, detail, run_log=None, card_png=None):
    attachments = {}
    if run_log:
        attachments["run.log"] = run_log
    attachments["burst.json"] = json.dumps(detail, ensure_ascii=False, indent=1)
    if card_png and os.path.exists(card_png):
        try:
            with open(card_png, "rb") as handle:
                attachments["card.png"] = handle.read()
        except Exception:  # noqa: BLE001
            pass
    return send("burst", summary, detail={"kind": kind,
                                          "counts": detail.get("counts")},
                attachments=attachments)


def error(summary, detail=None, run_log=None):
    attachments = {"run.log": run_log} if run_log else None
    return send("error", summary, detail=detail, attachments=attachments)


def selftest():
    os.environ.pop(NO_UPLOAD_ENV, None)
    result = send("selftest", "reporter selftest from %s" % platform.node(),
                  detail={"note": "wire proof only"},
                  attachments={"selftest.txt": "signal-room-bot selftest\n"},
                  blocking=True)
    text = json.dumps(result, ensure_ascii=False)
    path = os.environ.get("SIGNALROOM_DIAG_OUT")
    if path:
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
        except Exception:  # noqa: BLE001
            pass
    if sys.stdout is not None:
        print(text)
    return result


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    selftest()
