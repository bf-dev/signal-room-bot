# -*- coding: utf-8 -*-
"""핫키 = 이름 + 보낼 내용(문구 여러 개 / 사진 여러 개) + 보내는 방식.

메인 화면의 버튼 하나가 핫키 하나입니다. 버튼을 누르면 그 핫키의 문구와 사진에서
계정마다 무작위로 하나씩 뽑아 여러 계정이 시간차를 두고 올립니다.

대기 / 진입 / 청산은 '기본으로 넣어둔 핫키'일 뿐이고, 고객이 마음대로 추가/수정/삭제/
순서변경을 할 수 있습니다.
"""
import os
import shutil
import time
import uuid

import config
import paths
import phrases

MODE_TEXT = "text"       # 글만
MODE_IMAGE = "image"     # 사진 + 글(캡션)
MODE_CARD = "card"       # 자동 생성 수익카드 + 글(캡션)

MODE_LABELS = {
    MODE_TEXT: "글만 보내기",
    MODE_IMAGE: "사진 + 글",
    MODE_CARD: "수익카드 자동생성 + 글",
}


def new_id():
    return uuid.uuid4().hex[:8]


def make(name, texts, mode=MODE_TEXT, images=None, card=None, **kwargs):
    data = {
        "id": new_id(),
        "name": name,
        "mode": mode,
        "texts": list(texts),
        "images": list(images or []),
        "count_min": kwargs.get("count_min", config.DEFAULTS["count_min"]),
        "count_max": kwargs.get("count_max", config.DEFAULTS["count_max"]),
        "gap_min": kwargs.get("gap_min", config.DEFAULTS["gap_min"]),
        "gap_max": kwargs.get("gap_max", config.DEFAULTS["gap_max"]),
        "spread_min": kwargs.get("spread_min", config.DEFAULTS["spread_min"]),
        "spread_max": kwargs.get("spread_max", config.DEFAULTS["spread_max"]),
        "card": card,
    }
    return data


DEFAULT_CARD = {
    "symbol": config.DEFAULTS["symbol"],
    "direction": "buy",
    "entry": "4435.95",
    "exit": "4438.17",
    "base_lot": "1.00",
    "base_profit": "222.00",
    "lot_min": config.DEFAULTS["lot_min"],
    "lot_max": config.DEFAULTS["lot_max"],
    "lot_step": config.DEFAULTS["lot_step"],
    "time_utc": True,
    "time_jitter": config.DEFAULTS["card_time_jitter"],
}


def defaults():
    """첫 실행에 들어가 있는 핫키들 (고객이 준 문구 그대로)."""
    buy = [text.replace("{방향}", "바이") for text in phrases.DEFAULT_POOLS[phrases.ENTRY]]
    sell = [text.replace("{방향}", "셀") for text in phrases.DEFAULT_POOLS[phrases.ENTRY]]
    return [
        make("대기", phrases.DEFAULT_POOLS[phrases.WAIT]),
        make("진입 (바이)", buy),
        make("진입 (셀)", sell),
        make("청산 (수익인증)", phrases.DEFAULT_POOLS[phrases.PROFIT],
             mode=MODE_CARD, card=dict(DEFAULT_CARD)),
    ]


def images_dir(hotkey_id):
    path = os.path.join(paths.app_dir(), "images", str(hotkey_id))
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:  # noqa: BLE001
        pass
    return path


def import_image(hotkey, source_path):
    """고객이 고른 사진을 프로그램 폴더로 복사해 둡니다. 원본을 옮기거나 지워도
    핫키가 깨지지 않게 하려는 것입니다. 저장된 경로를 돌려줍니다."""
    folder = images_dir(hotkey["id"])
    base = os.path.basename(source_path)
    stem, extension = os.path.splitext(base)
    target = os.path.join(folder, base)
    if os.path.exists(target):
        target = os.path.join(folder, "%s-%d%s" % (stem, int(time.time()), extension))
    shutil.copyfile(source_path, target)
    hotkey.setdefault("images", []).append(target)
    return target


def remove_image(hotkey, path):
    try:
        hotkey.get("images", []).remove(path)
    except ValueError:
        return False
    try:
        if os.path.commonpath([os.path.abspath(path),
                               os.path.abspath(paths.app_dir())]) == os.path.abspath(paths.app_dir()):
            os.unlink(path)
    except Exception:  # noqa: BLE001
        pass
    return True


def summary(hotkey):
    parts = [MODE_LABELS.get(hotkey.get("mode"), "글만 보내기"),
             "문구 %d개" % len(hotkey.get("texts") or [])]
    if hotkey.get("images"):
        parts.append("사진 %d장" % len(hotkey["images"]))
    parts.append("%d~%d명" % (hotkey.get("count_min", 5), hotkey.get("count_max", 12)))
    parts.append("%g~%g초 간격" % (hotkey.get("gap_min", 3), hotkey.get("gap_max", 40)))
    return " · ".join(parts)
