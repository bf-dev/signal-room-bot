# -*- coding: utf-8 -*-
"""자동 업데이트 - 고객이 다시 설치하지 않아도 새 버전으로 바뀝니다.

이 프로그램은 **onedir** 빌드(폴더 하나)로 나갑니다. onefile 은 실행할 때마다 임시
폴더에 1300개가 넘는 파일을 풀어놓기 때문에, 실사용 PC에서 백신 검사에 걸려 첫 실행이
3분 44초까지 걸리고 결국 윈도우가 막아버린 사고가 있었습니다(2026-08-25). 그래서
교체 대상도 exe 한 개가 아니라 폴더입니다:

  1) 30분마다 version-signal-room-bot.json 을 확인한다(백그라운드, 방송을 절대 막지 않는다).
  2) 더 높은 버전이면 ZIP 을 임시 폴더에 '완전히' 받고 크기와 내용으로 검증한다.
  3) 버스트가 돌고 있지 않을 때만, PowerShell 헬퍼가 이 프로세스 종료를 기다렸다가
     폴더를 덮어쓰고 다시 실행한다.

파일명 규약: 새 빌드는 항상 버전 접미사가 붙은 파일명으로 배포하고 version 파일이 그
새 경로를 가리키게 한다. 이미 서빙 중인 파일명을 덮어쓰면 엣지 캐시가 옛 바이트를
몇 시간 동안 내보내 재시작 루프가 생긴다.

업데이트는 부가 기능이다. 어떤 실패도 실행 중인 프로그램을 건드리면 안 된다.
"""
import base64
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile

import config
import paths
import runlog

MIN_ZIP_BYTES = 3_000_000
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
    """백그라운드 업데이트 감시 스레드.

    can_restart: 지금 교체+재시작해도 되는지 물어보는 콜백. 버스트가 도는 중에는
                 False 를 돌려주므로, 글 올리는 도중에 프로그램이 사라지지 않습니다.
    """

    def __init__(self, can_restart=None, status_cb=None):
        super().__init__(daemon=True)
        self._stop = threading.Event()
        self._can_restart = can_restart or (lambda: True)
        self._status = status_cb or (lambda *_: None)
        self._loop_logged = False

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.is_set():
            try:
                self._check_once()
            except Exception:  # noqa: BLE001
                pass          # 업데이트는 부가 기능이라 절대 앱을 죽이면 안 된다.
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
        zip_url = data.get("zipUrl") or data.get("exeUrl")
        if not latest or not zip_url:
            return
        if _version_tuple(latest) <= _version_tuple(config.APP_VERSION):
            return
        if self._already_installed(latest):
            # 재시작 루프 방지: 교체를 했는데도 버전이 그대로면(엣지 캐시가 옛 ZIP 을
            # 계속 주는 경우 등) 다시 받지 않는다. 한 버전당 한 시간에 한 번.
            return
        if not getattr(sys, "frozen", False):
            runlog.log("[업데이트] 새 버전 %s 확인(개발 실행이라 교체는 생략)." % latest)
            return
        if not self._can_restart():
            return          # 방송 중에는 건드리지 않는다. 다음 주기에 다시 본다.
        runlog.log("[업데이트] 새 버전 %s 을(를) 내려받는 중입니다." % latest)
        self._status("새 버전 %s 을(를) 내려받는 중입니다..." % latest)
        staged = self._download_verified(zip_url)
        if not staged:
            return
        if not self._can_restart():
            shutil.rmtree(os.path.dirname(staged), ignore_errors=True)
            return
        self._status("새 버전 %s 으로 다시 시작합니다..." % latest)
        self._schedule_restart(staged, latest)

    def _download_verified(self, zip_url):
        """ZIP 을 받아서 임시 폴더에 풀고, exe 가 들어 있는 폴더를 돌려줍니다."""
        temp_root = None
        try:
            temp_root = tempfile.mkdtemp(prefix="%s-update-" % config.APP_NAME)
            archive_path = os.path.join(temp_root, "update.zip")
            request = urllib.request.Request(
                zip_url, headers={"Cache-Control": "no-cache", "User-Agent": USER_AGENT})
            total = 0
            with urllib.request.urlopen(request, timeout=300) as response:
                expected = response.headers.get("Content-Length")
                with open(archive_path, "wb") as handle:
                    while True:
                        chunk = response.read(1 << 16)
                        if not chunk:
                            break
                        handle.write(chunk)
                        total += len(chunk)
            if expected and expected.isdigit() and total != int(expected):
                runlog.log("[업데이트] 다운로드가 끊겨 취소했습니다(받음=%d 기대=%s)."
                           % (total, expected))
                shutil.rmtree(temp_root, ignore_errors=True)
                return None
            if total < MIN_ZIP_BYTES:
                runlog.log("[업데이트] 받은 파일이 너무 작아 취소했습니다(%d바이트)." % total)
                shutil.rmtree(temp_root, ignore_errors=True)
                return None

            extract_dir = os.path.join(temp_root, "new")
            with zipfile.ZipFile(archive_path) as archive:
                archive.extractall(extract_dir)
            os.unlink(archive_path)

            program_dir = _find_program_dir(extract_dir)
            if not program_dir:
                runlog.log("[업데이트] 받은 압축 파일에 실행 파일이 없어 취소했습니다.")
                shutil.rmtree(temp_root, ignore_errors=True)
                return None
            return program_dir
        except Exception as exc:  # noqa: BLE001
            runlog.log("[업데이트] 실패: %r" % (exc,))
            if temp_root:
                shutil.rmtree(temp_root, ignore_errors=True)
            return None

    def _already_installed(self, latest):
        try:
            with open(paths.in_app_dir("update-state.json"), encoding="utf-8") as handle:
                state = json.load(handle)
            if str(state.get("version")) != str(latest):
                return False
            age = time.time() - float(state.get("at") or 0)
            if age < 0 or age > 3600:
                return False
        except Exception:  # noqa: BLE001
            return False
        if not self._loop_logged:
            self._loop_logged = True
            runlog.log("[업데이트] 새 버전 %s 을(를) 이미 받았는데 버전이 그대로라 다시 "
                       "받지 않습니다. 이 버전으로 계속 쓰셔도 됩니다." % latest)
        return True

    def _remember_installed(self, latest):
        try:
            with open(paths.in_app_dir("update-state.json"), "w",
                      encoding="utf-8") as handle:
                json.dump({"version": str(latest), "at": time.time(),
                           "from": config.APP_VERSION}, handle)
        except Exception:  # noqa: BLE001
            pass

    def _schedule_restart(self, new_dir, latest):
        current_exe = sys.executable
        app_dir = os.path.dirname(os.path.abspath(current_exe))
        old_exe_name = os.path.basename(current_exe)
        pid = os.getpid()

        # 새로 깐 폴더에 있는 exe 이름으로 다시 실행한다. Copy-Item 은 병합이라
        # 옛 exe 가 남는데, 이름이 바뀐 릴리스에서 옛 exe 를 다시 실행하면 옛 버전이
        # 또 업데이트를 시도하는 무한 재시작이 된다.
        new_exe_name = old_exe_name
        try:
            names = [name for name in os.listdir(new_dir) if name.lower().endswith(".exe")]
            if names:
                new_exe_name = old_exe_name if old_exe_name in names else names[0]
        except Exception:  # noqa: BLE001
            pass

        # 배치 파일이 아니라 PowerShell -EncodedCommand 를 쓴다. 배치는 콘솔 코드페이지로
        # 읽혀서 한글 경로(설치 폴더 이름)가 깨진다. EncodedCommand 는 UTF-16LE 라
        # 코드페이지가 개입할 수 없다.
        steps = [
            "$ErrorActionPreference='SilentlyContinue'",
            "$deadline=(Get-Date).AddSeconds(60)",
            "while((Get-Process -Id %d -EA SilentlyContinue) -and "
            "((Get-Date) -lt $deadline)){Start-Sleep -Milliseconds 300}" % pid,
            "Copy-Item -Path %s -Destination %s -Recurse -Force"
            % (_ps_quote(os.path.join(new_dir, "*")), _ps_quote(app_dir)),
            "$target=Join-Path %s %s" % (_ps_quote(app_dir), _ps_quote(new_exe_name)),
            "if((Test-Path $target) -and (%s -ne %s)){Remove-Item (Join-Path %s %s) -Force}"
            % (_ps_quote(new_exe_name), _ps_quote(old_exe_name),
               _ps_quote(app_dir), _ps_quote(old_exe_name)),
            "if(Test-Path $target){Start-Process -FilePath $target -WorkingDirectory %s}"
            % _ps_quote(app_dir),
            "Remove-Item -Recurse -Force %s" % _ps_quote(os.path.dirname(new_dir)),
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
                None, "open", shell, params, app_dir, 0))
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
            # 교체를 시작하지 못했다고 프로그램을 끄면 안 된다. 한 버전 뒤처지는 편이
            # 창이 사라지는 것보다 낫다.
            runlog.log("[업데이트] 교체를 시작하지 못해 이 버전으로 계속 실행합니다.")
            self._status("업데이트를 하지 못했습니다. 계속 사용하셔도 됩니다.")
            return
        self._remember_installed(latest)
        runlog.log("[업데이트] %s -> %s 재시작을 예약했습니다." % (config.APP_VERSION, latest))
        os._exit(0)


def _find_program_dir(root):
    """압축을 푼 폴더 안에서 exe 가 들어 있는 폴더를 찾습니다."""
    for base, _dirs, files in os.walk(root):
        for name in files:
            if name.lower().endswith(".exe"):
                return base
    return None


def start(can_restart=None, status_cb=None):
    thread = UpdaterThread(can_restart=can_restart, status_cb=status_cb)
    thread.start()
    return thread
