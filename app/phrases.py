# -*- coding: utf-8 -*-
"""문구 풀 (대기 / 진입 / 수익인증).

Seeded with the customer's own lists, plus the natural variants visible in the
three room screenshots they sent, so one burst never reads like a template.
Everything here is only the STARTING pool: the customer edits, adds and deletes
in the 문구 tab and the edited pools are what get posted.

Placeholders, substituted per post:
    {방향}   바이 / 셀   (a 셀 signal can never post "바이 진입")
    {종목}   골드 / the label typed in the 진입 form
    {금액}   that account's own profit figure, e.g. 222
"""

WAIT = "wait"
ENTRY = "entry"
PROFIT = "profit"

CATEGORY_LABELS = {
    WAIT: "대기",
    ENTRY: "진입",
    PROFIT: "수익인증",
}

# 고객이 직접 적어준 문구 + 스크린샷에서 확인된 실제 방 문구
DEFAULT_POOLS = {
    WAIT: [
        "대기대기~",
        "대기입니다!",
        "대기중!!!",
        "대기 하고있습니다",
        "진입대기",
        "대기",
        "진입대기 입니다",
        "{종목} 진입대기",
        "진입대기~~",
        "대기요",
        "진입 대기 나왔네요",
        "대기대기 - 3연승 갑시다?",
        "대기~",
        "대기중입니다",
        "대기 들어갑니다",
        "대기 하겠습니다~",
        "진입대기 걸어둡니다",
        "대기 확인했습니다",
    ],
    ENTRY: [
        "{방향} 진입",
        "진입 했어요~",
        "연승 가자!!!!",
        "진입완~",
        "{방향} 진입~",
        "{방향} 진입입니다",
        "진입갑니다",
        "{방향} 진입 나왔네요",
        "진입~",
        "{방향} 진입 완료",
        "{방향} 진입했습니다",
        "{종목} {방향} 진입",
        "저도 {방향} 진입~",
        "{방향} 들어갑니다",
        "진입 완료했습니다",
        "{방향} 진입 가즈아~",
    ],
    PROFIT: [
        "수익 감사합니다",
        "전문가님 최고👍",
        "{금액} 불 수익 나이스~",
        "수익청산~^^",
        "수익청산 나이스",
        "수익 감사드립니다",
        "행복합니다^^",
        "수익청산 나이스^^",
        "3연승 나이스~👍👍",
        "수익청산 감사합니다 오늘도 가즈아~~",
        "{금액} 불 먹고 갑니다~",
        "깔끔하게 수익청산했습니다",
        "감사합니다 오늘도 수익~",
        "수익 확인했습니다 감사합니다",
        "{금액} 불 수익입니다 감사합니다",
        "청산 완료~ 감사합니다",
    ],
}

DIRECTION_WORDS = {"buy": "바이", "sell": "셀"}


def fill(template, direction=None, symbol_label=None, profit=None):
    """Substitute the placeholders for one post."""
    text = template
    if "{방향}" in text:
        text = text.replace("{방향}", DIRECTION_WORDS.get(direction, "바이"))
    if "{종목}" in text:
        text = text.replace("{종목}", symbol_label or "골드")
    if "{금액}" in text:
        text = text.replace("{금액}", format_amount(profit))
    return text


def format_amount(profit):
    """222.00 -> 222, 1332.50 -> 1332.5. The room writes round numbers."""
    try:
        value = float(profit)
    except (TypeError, ValueError):
        return ""
    if abs(value - round(value)) < 0.005:
        return str(int(round(value)))
    return ("%.2f" % value).rstrip("0").rstrip(".")


def needs_profit(template):
    return "{금액}" in template
