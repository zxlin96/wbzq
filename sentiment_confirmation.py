"""Validate live sentiment inputs before any strategy state is advanced."""

from datetime import datetime
import math
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Shanghai')


def date_key(value):
    return str(value or '').replace('-', '')[:8]


def confirmation_error(signal_date, market_date, price_date, price, *, as_of=None):
    day = date_key(signal_date)
    try:
        session = datetime.strptime(day, '%Y%m%d').date()
        stamp = datetime.fromisoformat(as_of.replace('Z', '+00:00')) if isinstance(as_of, str) else as_of
        stamp = stamp or datetime.now(TZ)
        stamp = stamp.replace(tzinfo=TZ) if stamp.tzinfo is None else stamp.astimezone(TZ)
    except (TypeError, ValueError):
        return '信号日期或运行时间无效'
    if session > stamp.date():
        return '信号日期尚未到达'
    if session == stamp.date() and stamp.hour < 15:
        return '等待北京时间15:00收盘'
    if date_key(market_date) != day:
        return f'J13行情日期{date_key(market_date) or "缺失"}与信号日期{day}不一致'
    if date_key(price_date) != day:
        return f'ETF行情日期{date_key(price_date) or "缺失"}与信号日期{day}不一致'
    try:
        if not math.isfinite(float(price)) or float(price) <= 0:
            return 'ETF收盘价无效'
    except (TypeError, ValueError):
        return 'ETF收盘价无效'
    return None
