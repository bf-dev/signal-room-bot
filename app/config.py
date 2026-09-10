# -*- coding: utf-8 -*-
"""App-wide constants.

No secrets live here and the program ships with none. The Telegram api_id /
api_hash belong to the CUSTOMER (my.telegram.org) and are typed once into the
설정 tab; they are stored in the customer's own %APPDATA% folder, never in this
repo and never in the build.
"""

APP_NAME = "signal-room-bot"
APP_TITLE = "시그널방 도우미"
APP_VERSION = "1.0.1"
CUSTOMER_ID = "4881110"          # Kmong partnerId, order 7602878

WORKS_API = "https://works.insu.ng/works/api"
ARTIFACT_SOURCE = "signal-room-bot"
VERSION_URL = ("https://works.insu.ng/works/public/%s/version-%s.json"
               % (CUSTOMER_ID, APP_NAME))
UPDATE_CHECK_SECONDS = 1800

# ------------------------------------------------------------------ defaults
# How many accounts speak in one burst, and how they are spread out. The
# customer's screenshots show ~15 accounts over 1-3 minutes with irregular gaps,
# so the defaults reproduce that and everything is adjustable in 설정.
DEFAULTS = {
    "count_min": 5,
    "count_max": 12,
    "gap_min": 3.0,          # seconds between two consecutive posts
    "gap_max": 40.0,
    "spread_min": 60.0,      # total length of one burst, seconds
    "spread_max": 180.0,
    "lot_min": 0.50,
    "lot_max": 6.00,
    "lot_step": 0.50,
    "symbol": "XAUUSDe",
    "symbol_label": "골드",
    "contract_size": 100.0,  # XAUUSD: 1 lot = 100oz -> 2.22 price move = 222.00
    "card_time_utc": True,   # broker time on the card (KST-9), as in the samples
    "card_time_jitter": 90,  # seconds of jitter per account on the card clock
}
