# -*- coding: utf-8 -*-
"""버스트 엔진: 여러 계정이 사람처럼 한 마디씩 올립니다.

한 번의 버스트 규칙 (고객 방 스크린샷과 같은 모양):
  - 사용중인 계정 중 N개를 무작위로 뽑고 순서도 섞는다
  - 같은 계정이 한 버스트에 두 번 말하지 않는다
  - 같은 문구가 한 버스트에 두 번 나오지 않는다 (문구가 부족할 때만 재사용)
  - 간격은 3~40초 사이로 들쭉날쭉하게, 전체는 1~3분에 걸쳐서
  - FloodWait 이 나면 그 계정만 기다렸다 재시도하고, 안 되면 건너뛰고 계속한다
  - 고객이 중지를 누르면 남은 예약은 즉시 취소된다
"""
import asyncio
import random
import time

import card
import phrases
import runlog
import tg


def pick_accounts(enabled, count):
    count = max(1, min(int(count), len(enabled)))
    chosen = random.sample(list(enabled), count)
    random.shuffle(chosen)
    return chosen


def pick_phrases(pool, count):
    """No repeat inside one burst; only reuse when the pool is smaller."""
    pool = [item for item in pool if str(item).strip()]
    if not pool:
        return ["대기"] * count
    if count <= len(pool):
        return random.sample(pool, count)
    out = []
    while len(out) < count:
        block = pool[:]
        random.shuffle(block)
        out.extend(block)
    return out[:count]


def plan_gaps(count, gap_min, gap_max, spread_min, spread_max):
    """Irregular gaps that still add up to a believable total spread."""
    if count <= 1:
        return []
    gaps = [random.uniform(float(gap_min), float(gap_max)) for _ in range(count - 1)]
    total = sum(gaps)
    target = random.uniform(float(spread_min), float(spread_max))
    if total > 0:
        scale = target / total
        gaps = [max(1.0, gap * scale) for gap in gaps]
    return gaps


def build_wait_items(accounts, pool, symbol_label):
    texts = pick_phrases(pool, len(accounts))
    return [{"account": account,
             "text": phrases.fill(text, symbol_label=symbol_label)}
            for account, text in zip(accounts, texts)]


def build_entry_items(accounts, pool, direction, symbol_label):
    texts = pick_phrases(pool, len(accounts))
    return [{"account": account,
             "text": phrases.fill(text, direction=direction,
                                  symbol_label=symbol_label)}
            for account, text in zip(accounts, texts)]


def build_close_items(accounts, pool, form, burst_settings):
    """청산: 계정마다 랏과 수익금이 다른 카드 + 수익 문구 한 줄."""
    lots = card.random_lots(len(accounts), form["lot_min"], form["lot_max"],
                            form.get("lot_step") or 0.5)
    base_lot = float(form.get("base_lot") or 1.0)
    base_profit = form.get("base_profit")
    delta = float(form["exit"]) - float(form["entry"])
    if form["direction"] == "sell":
        delta = -delta
    if base_profit not in (None, "", 0) and delta != 0 and base_lot:
        contract = abs(float(base_profit)) / (abs(delta) * base_lot)
    else:
        contract = float(form.get("contract_size") or 100.0)

    base_time = form.get("base_time") or time.time()
    use_utc = bool(form.get("card_time_utc", True))
    jitter = int(form.get("card_time_jitter") or 90)
    texts = pick_phrases(pool, len(accounts))

    items = []
    for index, (account, lot, text) in enumerate(zip(accounts, lots, texts)):
        profit = card.profit_for(lot, form["entry"], form["exit"],
                                 form["direction"], contract)
        stamp = base_time - random.uniform(0, max(0, jitter))
        when = time.gmtime(stamp) if use_utc else time.localtime(stamp)
        import paths
        import os
        path = os.path.join(paths.cards_dir(), "card-%02d.png" % index)
        card.render(path, form["symbol"], form["direction"], lot,
                    form["entry"], form["exit"], profit, when=when)
        items.append({"account": account,
                      "text": phrases.fill(text, direction=form["direction"],
                                           symbol_label=form.get("symbol_label"),
                                           profit=profit),
                      "image": path, "profit": profit, "lot": lot})
    return items


class Controller(object):
    """One running burst. stop() cancels everything still queued."""

    def __init__(self):
        self._stopped = False
        self.sent = 0
        self.failed = 0
        self.skipped = 0
        self.started_at = time.time()
        self.detail = []

    def stop(self):
        self._stopped = True

    @property
    def stopped(self):
        return self._stopped


async def run(manager, controller, kind, items, chat_id, gaps, on_progress):
    """Post the planned items. Never raises: every failure is logged and the
    burst keeps going, because a half-empty room is better than a dead one."""
    total = len(items)
    for index, item in enumerate(items):
        if controller.stopped:
            controller.skipped = total - index
            on_progress({"event": "stopped", "index": index, "total": total})
            break
        if index:
            gap = gaps[index - 1] if index - 1 < len(gaps) else 5.0
            waited = 0.0
            while waited < gap:
                if controller.stopped:
                    break
                step = min(0.25, gap - waited)
                await asyncio.sleep(step)
                waited += step
            if controller.stopped:
                controller.skipped = total - index
                on_progress({"event": "stopped", "index": index, "total": total})
                break

        account = item["account"]
        label = account.get("label", account["key"])
        try:
            if item.get("image"):
                await manager.send_card(account, chat_id, item["image"], item["text"])
            else:
                await manager.send_text(account, chat_id, item["text"])
            controller.sent += 1
            controller.detail.append({"account": label, "text": item["text"],
                                      "ok": True})
            on_progress({"event": "sent", "index": index + 1, "total": total,
                         "label": label, "text": item["text"]})
        except Exception as exc:  # noqa: BLE001
            seconds = 0
            try:
                seconds = tg.flood_seconds(exc) if tg.is_flood(exc) else 0
            except Exception:  # noqa: BLE001
                seconds = 0
            if seconds and seconds <= 90 and not controller.stopped:
                on_progress({"event": "flood", "label": label, "seconds": seconds,
                             "index": index + 1, "total": total})
                runlog.log("[%s] 텔레그램 제한으로 %d초 대기 후 재시도합니다." % (label, seconds))
                waited = 0.0
                while waited < seconds and not controller.stopped:
                    await asyncio.sleep(0.25)
                    waited += 0.25
                try:
                    if item.get("image"):
                        await manager.send_card(account, chat_id, item["image"],
                                                item["text"])
                    else:
                        await manager.send_text(account, chat_id, item["text"])
                    controller.sent += 1
                    controller.detail.append({"account": label, "text": item["text"],
                                              "ok": True})
                    on_progress({"event": "sent", "index": index + 1, "total": total,
                                 "label": label, "text": item["text"]})
                    continue
                except Exception as exc2:  # noqa: BLE001
                    exc = exc2
            controller.failed += 1
            controller.detail.append({"account": label, "text": item["text"],
                                      "ok": False, "error": repr(exc)})
            runlog.log("[%s] 전송 실패: %r" % (label, exc))
            on_progress({"event": "failed", "index": index + 1, "total": total,
                         "label": label, "error": str(exc)[:120]})
    on_progress({"event": "done", "sent": controller.sent,
                 "failed": controller.failed, "skipped": controller.skipped,
                 "kind": kind,
                 "elapsed": round(time.time() - controller.started_at, 1)})
    return controller
