# -*- coding: utf-8 -*-
"""수익인증 카드 이미지 생성 (MT4/MT5 거래 공유 카드 모양).

Copied from the customer's own screenshot, one deal row:

    ┌ grey ────────────────────────────────────────────────┐
    │ white                                                │
    │  XAUUSDe, buy 1.00              2026.09.08 04:58:20  │
    │  4 435.95 → 4 438.17                         222.00  │
    │ white                                                │
    └ grey ────────────────────────────────────────────────┘

symbol bold black, "buy" blue / "sell" red, prices grey, the date grey and
small, the profit bold blue and right-aligned under it. Thousands are grouped
with a thin space (4 435.95), exactly like the terminal does.
"""
import os
import random
import time

from PIL import Image, ImageDraw, ImageFont

import paths

WIDTH = 1080
HEIGHT = 232
GREY_TOP = 60
WHITE_H = 118

COL_GREY_BG = (200, 200, 200)
COL_WHITE = (255, 255, 255)
COL_SYMBOL = (26, 26, 26)
COL_BUY = (56, 126, 199)
COL_SELL = (204, 62, 62)
COL_PRICE = (128, 128, 128)
COL_DATE = (150, 150, 150)
COL_PROFIT = (35, 116, 204)
COL_LOSS = (204, 62, 62)

_FONT_CACHE = {}

_BOLD_CANDIDATES = [
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
]
_REGULAR_CANDIDATES = [
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
]


def _font(bold, size):
    key = (bold, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    for candidate in (_BOLD_CANDIDATES if bold else _REGULAR_CANDIDATES):
        try:
            if os.path.exists(candidate):
                font = ImageFont.truetype(candidate, size)
                _FONT_CACHE[key] = font
                return font
        except Exception:  # noqa: BLE001
            continue
    font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


def group(value, decimals=2):
    """4435.95 -> '4 435.95' (thin space, like the terminal)."""
    text = "%.*f" % (decimals, float(value))
    negative = text.startswith("-")
    if negative:
        text = text[1:]
    whole, _, frac = text.partition(".")
    parts = []
    while len(whole) > 3:
        parts.insert(0, whole[-3:])
        whole = whole[:-3]
    parts.insert(0, whole)
    out = " ".join(parts)
    if frac:
        out += "." + frac
    return ("-" if negative else "") + out


def profit_for(lot, entry, exit_price, direction, contract_size):
    delta = (float(exit_price) - float(entry))
    if direction == "sell":
        delta = -delta
    return round(delta * float(lot) * float(contract_size), 2)


def render(path, symbol, direction, lot, entry, exit_price, profit,
           when=None, currency_decimals=2, price_decimals=2):
    """Write one card PNG. Returns the path."""
    when = when or time.gmtime()
    image = Image.new("RGB", (WIDTH, HEIGHT), COL_GREY_BG)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, GREY_TOP, WIDTH, GREY_TOP + WHITE_H], fill=COL_WHITE)

    left = 44
    right = WIDTH - 44
    line1_y = GREY_TOP + 20
    line2_y = GREY_TOP + 64

    f_symbol = _font(True, 36)
    f_side = _font(False, 36)
    f_price = _font(False, 36)
    f_date = _font(False, 27)
    f_profit = _font(True, 36)

    head = "%s, " % symbol
    draw.text((left, line1_y), head, font=f_symbol, fill=COL_SYMBOL)
    offset = left + draw.textlength(head, font=f_symbol)
    side_color = COL_BUY if direction == "buy" else COL_SELL
    side = "%s %s" % (direction, "%.2f" % float(lot))
    draw.text((offset, line1_y), side, font=f_side, fill=side_color)

    prices = "%s → %s" % (group(entry, price_decimals),
                               group(exit_price, price_decimals))
    draw.text((left, line2_y), prices, font=f_price, fill=COL_PRICE)

    stamp = time.strftime("%Y.%m.%d %H:%M:%S", when)
    draw.text((right - draw.textlength(stamp, font=f_date), line1_y + 6),
              stamp, font=f_date, fill=COL_DATE)

    amount = group(profit, currency_decimals)
    color = COL_PROFIT if float(profit) >= 0 else COL_LOSS
    draw.text((right - draw.textlength(amount, font=f_profit), line2_y),
              amount, font=f_profit, fill=color)

    image.save(path, "PNG")
    return path


def build_batch(accounts, symbol, direction, entry, exit_price, lots,
                contract_size, use_utc=True, jitter=90, base_time=None):
    """One card per account. Returns [(account_key, path, profit, lot)]."""
    out = []
    base = base_time or (time.time() - (0 if use_utc else 0))
    for index, (key, lot) in enumerate(zip(accounts, lots)):
        profit = profit_for(lot, entry, exit_price, direction, contract_size)
        stamp = base - random.uniform(0, max(0, jitter))
        when = time.gmtime(stamp) if use_utc else time.localtime(stamp)
        path = os.path.join(paths.cards_dir(), "card-%02d.png" % index)
        render(path, symbol, direction, lot, entry, exit_price, profit, when=when)
        out.append((key, path, profit, lot))
    return out


def random_lots(count, low, high, step):
    """A lot size per account, spread across the range, no two identical when
    the range allows it (the sample room shows 1.00 / 6.00 / 4.00 / 2.00)."""
    low, high, step = float(low), float(high), float(step or 0.01)
    if high < low:
        low, high = high, low
    steps = max(1, int(round((high - low) / step)) + 1)
    choices = [round(low + index * step, 2) for index in range(steps)]
    choices = [value for value in choices if value > 0] or [1.0]
    if count <= len(choices):
        return random.sample(choices, count)
    out = []
    while len(out) < count:
        pool = choices[:]
        random.shuffle(pool)
        out.extend(pool)
    return out[:count]
