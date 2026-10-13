# signal-room-bot (kmong 4881110 마린투자그룹, order 7602878)

Windows desktop program that makes the customer's 해외선물 signal room look busy: one
click posts a short line from many of THEIR OWN Telegram user accounts, staggered like
people typing. Korean GUI, no console, everything configurable in the window.

- Customer: kmong partnerId **4881110** (neoworks customer id `f05d4eb9-1959-4edb-8b43-bacbc17cf77b`)
- Build repo (PUBLIC, no customer data): https://github.com/bf-dev/signal-room-bot
- Download (current): https://static.neoworks.us/4881110/signal-room-bot-1.0.2.zip
- Update manifest: `https://static.neoworks.us/4881110/version-signal-room-bot.json` -> `zipUrl`
  (folder swap). 1.0.0/1.0.1 poll the same file through the old `works.insu.ng/works/public/`
  path (same directory on disk), so `FORCE=1 works-publish` of the manifest reaches all of them.
- Artifacts source: `signal-room-bot-*` (startup / burst / error / selftest)

## Build / run

```
python app/main.py                 # dev run (needs telethon + pillow)
python app/main.py --cardtest      # renders one trade card, no network
python app/main.py --selftest      # builds the window and closes it (exit 0)
python app/main.py --guidemo       # fills the window with fake data for screenshots
python app/reporter.py             # Artifacts wire proof, prints matched:true
```

Windows build is **GitHub Actions** (`.github/workflows/build.yml`, windows-latest,
PyInstaller `--onedir --noconsole`, zipped with the Korean guide inside).

**ONEDIR, NEVER ONEFILE.** A 29MB onefile deliverable on this account unpacked ~1300 files
into `%TEMP%` on every launch, took **3m44s** to start on a real customer PC and was then
blocked by Windows (2026-08-25). This build is 1222 files / 25.3MB zipped and starts in
**0.54-0.58s** on the runner from the extracted ZIP. The updater is a **folder swap** to
match: the manifest carries `zipUrl`, `updater.py` downloads the ZIP, verifies size, and a
PowerShell `-EncodedCommand` helper waits for the PID, `Copy-Item -Recurse` merges the new
folder in, and relaunches by the exe name found in the unpacked folder.

Two CI gotchas that cost a run: Windows PowerShell 5.1 reads a YAML-embedded script as ANSI
and mangles the `_읽어주세요.txt` literal (use `shell: pwsh` and match `_*.txt` by pattern),
and `Compress-Archive` stores that name without the UTF-8 flag, so Linux `unzip` shows
mojibake while Windows shows it correctly. It also runs `--cardtest`, `--selftest` and captures
all three screens with `ci/gui_screenshot.ps1` (PrintWindow on the real desktop session).
`gh run download <id>` gives `dist/signal-room-bot.exe` + `screenshots/*.png`.
`winbuild` also works but its screenshot step hung on 2026-09-10 ("Task may not run
because /ST is earlier than current time"); CI was faster and is the path used for 1.0.0.

## Shape of the app (this is the second spec, the first one is gone)

Three screens, from the owner's correction mid-build:

1. **메인** - a grid of big 핫키 buttons. One click = one burst. Live progress + 중지.
   Plus 선택 계정으로 한 마디 for an ad-hoc line.
2. **핫키 설정** - hotkeys are user-defined: name, mode, MANY candidate texts, MANY
   images, how many accounts, gap range, total spread. Add / copy / delete / reorder.
3. **계정 설정** - api_id/api_hash + the Telethon login flow, one row per account.

`대기 / 진입(바이) / 진입(셀) / 청산(수익인증)` are only the SEEDED hotkeys
(`app/hotkeys.py: defaults()`), built from the customer's own phrase lists in
`app/phrases.py`. Do not re-hardcode them as categories.

**USER ACCOUNTS ONLY.** Bot-token support existed for about an hour and was removed on
the owner's instruction: a bot sender does not look like a room member. `app/tg.py` has
no bot path any more. If you add one back, you are undoing a deliberate decision.

## Things that cost time (do not rediscover)

- **Telegram test DCs do not work for lab accounts.** `+99966<dc><4 digits>` with the code
  `<dc>x5` is rejected with `PhoneCodeInvalidError` on DC 1, 2 and 3 (measured 2026-09-10).
  Do not plan a multi-account rehearsal on them.
- **BotFather is at the 20-bot cap** on our account: `/newbot` answers "Sorry, you can't
  add more than 20 bots". Never delete an existing bot to free a slot, they are customers'.
  Tokens of OUR OWN internal bots can be read back with `/token` + the inline button
  (`tools/`-style driver lived in `tmp/4881110-lab/`, tmp is pruned after 14 days).
- BotFather's token line is wrapped in backticks; strip them before parsing.
- `store.load()` originally returned early when `settings.json` did not exist, so a fresh
  install had ZERO hotkeys and the main screen was empty. Fixed; keep the default-seed
  after the merge loop, not inside the try.
- The reporter must send a `User-Agent`; Cloudflare 403s `Python-urllib/3.12`.

## How 1.0.0 was verified (2026-09-10)

Test room: our own supergroup **시그널방 테스트랩** `-1004385880988`. The customer's real
room was never touched.

- **Shipped path, real user account**: `app/tg.py` Manager driven directly with a real
  Telegram USER session (the mautrix bridge auth key materialised into
  `sessions/user_<digits>.session`, exactly where the GUI login writes it):
  `list_dialogs -> 29 rooms`, `check_members -> ok`, `send_text -> msg 19`,
  `send_card -> msg 20`.
- **Fan-out**: the shipped `burst.build_items` / `plan_gaps` / `run` driving 5 senders
  (1 real user account + 4 of our own internal bots over the Bot HTTP API, harness only).
  4 senders landed, 1 bot answered 403 (not a member). Three bursts (대기 / 진입 / 청산)
  with staggered timestamps 9s-11s apart and per-account profit cards. Transcript in
  `evidence/lab-transcript.txt`.
- **Only 1 distinct real USER account exists on this host**, so the many-user fan-out was
  proven with mixed senders. If you ever get more user sessions, redo it with users only.
- **Stop mid-burst**: a 4-post plan stopped after post 2 during a gap wait ->
  `sent=2 skipped=2`, `{"event":"stopped"}` then `{"event":"done"}`.
- Artifacts: `POST /works/api` returned `200 {"matched": true}` twice, for the reporter
  selftest and for a real burst report (ZIP with `card.png` + `run.log` + `burst.json`).

## Where things live on the customer PC

`%APPDATA%\signal-room-bot\`: `settings.json` (accounts, hotkeys, target room),
`sessions/*.session` (Telegram logins, never leave the PC), `images/<hotkeyId>/` (their
own uploaded photos, copied in so moving the original does not break a hotkey),
`cards/` (generated trade cards), `run.log`.

The customer's api_id/api_hash are THEIRS (my.telegram.org) and typed into 계정 설정. We
do not ship any Telegram credential.

## 1.0.2 (2026-10-13): "database is locked" on two accounts

Symptom (customer run.log, DESKTOP-89N9I0J): from 10-12, accounts 붕 어 빵 and 이장욱
failed EVERY burst with `OperationalError('database is locked')`, the other ~14 sent fine.
Those two were exactly the accounts logged in mid-run after `PhonePasswordFloodError` on
인증코드 받기 (08:09/08:10 before 붕 어 빵 08:17, 08:24/08:43 before 이장욱 08:54).

Cause (from code, high confidence): 1.0.1 `request_code` only disconnected on
`PhoneNumberInvalidError`/`FloodWaitError`. `PhonePasswordFloodError` is an `AuthKeyError`,
so the login client was LEAKED, still connected, still holding an sqlite connection on
`<key>.session`. The next successful login opened a second client on the same file with
the same auth key, so Telegram pushed updates to both. Telethon writes entities in an
implicit transaction and commits only from its 60 s keepalive; once a keepalive died on
a lock error, that connection held the write lock forever and every send of the account
failed. Ruled out: a second app instance (all 23 uploads of 10-12/13 carry the same
`07:32:18 프로그램을 시작했습니다 (v1.0.1)` line). Not reproduced end to end offline (the
flood replay in `tests/repro_session_lock.py` shows multiple clients per file and held
locks, but not the permanent send failure), so the fix does not depend on the exact race.

Fix in `app/tg.py`:
- No client ever holds a sqlite file. `FileSession` (a `StringSession`) keeps the login
  in memory and writes `sessions/<key>.str` with an atomic replace, only when the auth
  key/DC string changes. A 1.0.1 `<key>.session` is read once (read-only; if locked, a
  copy of the file is read) and migrated, entities included. The `.session` file stays
  on disk untouched as a fallback; logout deletes both.
- One client per account: per-key asyncio lock in `_client_for`, `_close_all_for(key)`
  before any new login/connect, every failure path in `request_code` disconnects, and
  the login client is adopted as THE account client (`_adopt`).
- `_with_recovery`: an sqlite / "database is locked" error during a send closes every
  client of the account, reopens, retries once. If it fired after Telegram accepted the
  message (where 1.0.1 raised it) it does NOT resend (no double post). Both log a
  `세션 잠김 ...` line in run.log, which already rides the Artifacts burst ZIP.

Tests (offline, fake network, Telethon 1.44 real above the sender):
```
python tests/test_held_lock.py app            # PASS on 1.0.2, FAIL on 1.0.1 (0/4 sends)
python tests/repro_session_lock.py app 40     # flood-login replay, both accounts send
```
Needs `telethon==1.44.0` (a venv lived in tmp/4881110-1013/venv, tmp is pruned; recreate
with `python3 -m venv v && v/bin/pip install telethon==1.44.0`). Also passes on the
Windows builder: `ssh bfdev@windows-builder`, `cd C:\builds\260910-kmong-4881110-telegram-room-bot;
python tests\test_held_lock.py app` (PowerShell, no `&&`).

Build: `winbuild` on windows-builder worked this time (screenshot ok). The ZIP was
packaged on Linux with Python `zipfile` in the same layout as 1.0.1
(`signal-room-bot/` + `_읽어주세요.txt` + exe + `_internal/`), extracted on the builder and
`--selftest`/`--cardtest` exit 0 from the extracted copy.

Restart: closing and reopening 1.0.1 clears it (the leaked clients and their locks die
with the process; the new process opens one client per account), until the next flood-y
login. The 1.0.1 updater does that restart itself when it pulls 1.0.2 (polls every 30 min,
only when no burst is running).
