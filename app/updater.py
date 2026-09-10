# -*- coding: utf-8 -*-
"""자동 업데이트 - 고객이 다시 설치하지 않아도 새 버전으로 바뀝니다.

집 표준 패턴(onefile exe 교체):
  1) 30분마다 version-signal-room-bot.json 을 확인한다(백그라운드, 방송을 절대 막지 않는다).
  2) 더 높은 버전이면 새 exe 를 임시 파일로 '완전히' 받고 크기로 검증한다.
  3) 버스트가 돌고 있지 않을 때만, PowerShell 헬퍼가 이 프로세스 종료를 기다렸다가
     exe 를 덮어쓰고 다시 실행한다.

파일명 규약: 새 빌드는 항상 버전 접미사가 붙은 파일명으로 배포하고 version 파일이 그
새 경로를 가리키게 한다. 이미 서빙 중인 파일명을 덮어쓰면 엣지 캐시가 옛 바이트를
몇 시간 동안 내보내 재시작 루프가 생긴다.

업데이트는 부가 기능이다. 어떤 실패도 실행 중인 프로그램을 건드리면 안 된다.
"""
import base64
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import threading
import urllib.request

import config
import runlog

MIN_EXE_BYTES = 5_000_000
USER_AGENT = "%s/%s (customer %s)" % (config.APP_NAME, config.APP_VERSION,
                                      config.CUSTOMER_ID)


def _version_tuple(value):
    try:
        return tuple(int(part) for part in str(value).strip().split("."))
    except Exception:  # noqa: BLE001
        return (0,)


def _ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


class UpdaterThread(threading.Thread):
    def __init__(self, can_restart=None, status_cb=None):
        super().__init__(daemon=True)
        self._stop = threading.Event()
        self._can_restart = can_restart or (lambda: True)
        self._status = status_cb or (lambda *_: None)

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.is_set():
            try:
                self._check_once()
            except Exception:  # noqa: BLE001
                pass
            self._stop.wait(config.UPDATE_CHECK_SECONDS)

    def _check_once(self):
        url = os.environ.get("SIGNALROOM_VERSION_URL") or config.VERSION_URL
        try:
            request = urllib.request.Request(
                url, headers={"Cache-Control": "no-cache", "User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=8) as response:
                if response.status != 200:
                    return
                data = json.loads(response.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            return
        latest = str(data.get("version", "")).strip()
        exe_url = data.get("exeUrl")
        if not latest or not exe_url:
            return
        if _version_tuple(latest) <= _version_tuple(config.APP_VERSION):
            return
        if not getattr(sys, "frozen", False):
            runlog.log("[업데이트] 새 버전 %s 확인(개발 실행이라 교체는 생략)." % latest)
            return
        if not self._can_restart():
            return          # 방송 중에는 건드리지 않는다. 다음 주기에 다시 본다.
        runlog.log("[업데이트] 새 버전 %s 을(를) 내려받는 중입니다." % latest)
        self._status("새 버전 %s 을(를) 내려받는 중입니다..." % latest)
        staged = self._download_verified(exe_url)
        if not staged or not self._can_restart():
            return
        self._status("새 버전 %s 으로 다시 시작합니다..." % latest)
        self._schedule_restart(staged, latest)

    def _download_verified(self, exe_url):
        path = None
        try:
            handle, path = tempfile.mkstemp(suffix=".exe")
            os.close(handle)
            request = urllib.request.Request(
                exe_url, headers={"Cache-Control": "no-cache",
                                  "User-Agent": USER_AGENT})
            total = 0
            with urllib.request.urlopen(request, timeout=180) as response:
                expected = response.headers.get("Content-Length")
                with open(path, "wb") as out:
                    while True:
                        chunk = response.read(1 << 16)
                        if not chunk:
                            break
                        out.write(chunk)
                        total += len(chunk)
            if expected and expected.isdigit() and total != int(expected):
                runlog.log("[업데이트] 다운로드가 끊겨 취소했습니다(받음=%d 기대=%s)."
                           % (total, expected))
                os.unlink(path)
                return None
            if total < MIN_EXE_BYTES:
                runlog.log("[업데이트] 받은 파일이 너무 작아 취소했습니다(%d바이트)." % total)
                os.unlink(path)
                return None
            return path
        except Exception as exc:  # noqa: BLE001
            runlog.log("[업데이트] 실패: %r" % (exc,))
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except Exception:  # noqa: BLE001
                    pass
            return None

    def _schedule_restart(self, new_exe, latest):
        current = sys.executable
        pid = os.getpid()
        steps = [
            "$ErrorActionPreference='SilentlyContinue'",
            "$deadline=(Get-Date).AddSeconds(60)",
            "while((Get-Process -Id %d -EA SilentlyContinue) -and "
            "((Get-Date) -lt $deadline)){Start-Sleep -Milliseconds 300}" % pid,
            "Copy-Item -Path %s -Destination %s -Force" % (_ps_quote(new_exe),
                                                           _ps_quote(current)),
            "Start-Process -FilePath %s" % _ps_quote(current),
            "Remove-Item -Force %s" % _ps_quote(new_exe),
        ]
        encoded = base64.b64encode("\n".join(steps).encode("utf-16-le")).decode("ascii")
        # --noconsole 빌드는 stdin/stdout 이 없어서 subprocess 가 [WinError 6] 로 죽는다.
        # 창 없는 앱이 쓰라고 있는 ShellExecuteW 가 정답이다.
        shell = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                             "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        params = ("-NoProfile -NonInteractive -ExecutionPolicy Bypass "
                  "-WindowStyle Hidden -EncodedCommand %s" % encoded)
        started = 0
        try:
            started = int(ctypes.windll.shell32.ShellExecuteW(
                None, "open", shell, params, os.path.dirname(current), 0))
        except Exception:  # noqa: BLE001
            started = 0
        if started <= 32:
            try:
                subprocess.Popen([shell, "-NoProfile", "-NonInteractive",
                                  "-ExecutionPolicy", "Bypass", "-WindowStyle",
                                  "Hidden", "-EncodedCommand", encoded],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, close_fds=True,
                                 creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
                started = 33
            except Exception:  # noqa: BLE001
                started = 0
        if started <= 32:
            runlog.log("[업데이트] 교체를 시작하지 못해 이 버전으로 계속 실행합니다.")
            self._status("업데이트를 하지 못했습니다. 계속 사용하셔도 됩니다.")
            return
        runlog.log("[업데이트] %s -> %s 재시작을 예약했습니다." % (config.APP_VERSION, latest))
        os._exit(0)


def start(can_restart=None, status_cb=None):
    thread = UpdaterThread(can_restart=can_restart, status_cb=status_cb)
    thread.start()
    return thread
