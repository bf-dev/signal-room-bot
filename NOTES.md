# signal-room-bot (kmong 4881110 마린투자그룹, order 7602878)

Windows desktop program that makes the customer's 해외선물 signal room look busy: one
click posts a short line from many of THEIR OWN Telegram user accounts, staggered like
people typing. Korean GUI, no console, everything configurable in the window.

- Customer: kmong partnerId **4881110** (neoworks customer id `f05d4eb9-1959-4edb-8b43-bacbc17cf77b`)
- Build repo (PUBLIC, no customer data): https://github.com/bf-dev/signal-room-bot
- Download: https://works.insu.ng/works/public/4881110/signal-room-bot-1.0.0.exe
- Update manifest: `.../4881110/version-signal-room-bot.json` (exe swap, house updater)
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
PyInstaller `--onefile --noconsole`). It also runs `--cardtest`, `--selftest` and captures
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
