"""Shared end-of-week confirmation for mainland-listed ETF reports.

Quotes dated on the final exchange session become a confirmed weekly bar at
15:00 Asia/Shanghai. Confirmation never requires another day's market quote.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

CONFIRMATION_RULE = "exchange_week_close_v2"
_CALENDARS = {}


def attach_exchange_calendar(daily, pro):
    """Fetch the full SSE calendar, including closed days and upcoming Friday.

    Calendar data are shared across ETFs in a process. On API failure regular
    Friday confirmation remains available; shortened weeks fail conservatively.
    """
    if daily.empty:
        return daily
    dates = pd.to_datetime(daily.trade_date.astype(str))
    start, end = f"{dates.min().year}0101", f"{dates.max().year}1231"
    key = (start, end)
    if key not in _CALENDARS:
        try:
            cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=end,
                                fields="cal_date,is_open")
            if cal is None or cal.empty:
                raise ValueError("Empty exchange calendar")
            _CALENDARS[key] = {str(r.cal_date): int(r.is_open) for r in cal.itertuples()}
        except Exception as exc:
            logging.warning("交易日历获取失败，暂按周五收盘确认周线（节假日短周不提前确认）: %s",
                            type(exc).__name__)
            return daily
    daily.attrs["trading_calendar"] = _CALENDARS[key]
    return daily


def mark_week_completion(weekly, daily=None, *, as_of=None, trade_calendar=None):
    """Recompute flags, including stale flags produced by older report code.

    ``as_of`` is an optional Shanghai-local cutoff for reproducible intraday
    tests/reports. A complete calendar maps YYYYMMDD to 0/1 (closed/open).
    """
    result = weekly.copy()
    attrs = dict(getattr(daily, "attrs", {}))
    attrs.update(weekly.attrs)
    observed = as_of if as_of is not None else attrs.get("weekly_as_of")
    stamp = pd.Timestamp(observed if observed is not None else datetime.now(ZoneInfo("Asia/Shanghai")))
    stamp = stamp.tz_localize("Asia/Shanghai") if stamp.tzinfo is None else stamp.tz_convert("Asia/Shanghai")
    calendar = trade_calendar if trade_calendar is not None else attrs.get("trading_calendar", {})
    if isinstance(calendar, pd.DataFrame):
        calendar = {str(r.cal_date): int(r.is_open) for r in calendar.itertuples()}
    calendar = {str(k): int(v) for k, v in calendar.items()}
    flags, ends = [], []
    for raw in result.trade_date:
        day = pd.Timestamp(str(raw)).normalize()
        monday = day - pd.Timedelta(days=day.weekday())
        weekdays = pd.date_range(monday, periods=5).strftime("%Y%m%d").tolist()
        if all(d in calendar for d in weekdays):
            sessions = [d for d in weekdays if calendar[d] == 1]
            last = sessions[-1] if sessions else None
        else:
            last = weekdays[-1]
        close_at = (pd.Timestamp(last).tz_localize("Asia/Shanghai") + pd.Timedelta(hours=15)
                    if last else None)
        flags.append(bool(last and day.strftime("%Y%m%d") == last and stamp >= close_at))
        ends.append(last)
    result["week_completed"] = flags
    result["week_end_date"] = ends
    result.attrs.update(attrs)
    result.attrs["weekly_as_of"] = stamp.isoformat()
    result.attrs["trading_calendar"] = calendar
    return result


def confirmed_weeks(weekly, daily=None):
    marked = mark_week_completion(weekly, daily)
    return marked[marked.week_completed], marked[~marked.week_completed]


def confirmed_week_dates(weekly, daily):
    done, _ = confirmed_weeks(weekly, daily)
    if "J" in done:
        done = done[done.J.notna()]
    return sorted(done.trade_date.tolist())


def refresh_action_summary(result):
    """An old permanent holding must not hide a new round's buy instruction."""
    positions = result.get("positions", [])
    if not positions:
        return result
    def priority(pos):
        action = pos.get("action", "")
        if action in ("buy", "invest_remaining", "trim", "refill") or action.startswith("sell_"):
            return 0
        return 2 if action in ("hold_long_term", "v6h_stage_3") else 1
    first = min(positions, key=priority)
    for field in ("action", "action_label", "action_color"):
        if field in first:
            result[field] = first[field]
    result["action_detail"] = " | ".join(p.get("action_detail", "") for p in positions)
    return result


def waiting_for_week_close(strategy, daily, forming):
    """No incomplete weekly J may stand in for a missing confirmed signal."""
    latest = daily.iloc[-1]
    preview = forming.iloc[-1].get("J") if not forming.empty else None
    positions = [dict(round_id=p.round_id, shares=float(p.shares),
                      avg_cost=p.get_avg_cost(), total_invested=p.total_invested,
                      action="wait", action_label="等待周线收盘", action_color="#95a5a6")
                 for p in strategy._active_positions()]
    avg_cost = strategy.get_avg_cost()
    return dict(last_date=str(latest.trade_date), last_price=float(latest.close_qfq),
                weekly_j=None, weekly_k=None, weekly_d=None, signal_week_date=None,
                confirmation_rule=CONFIRMATION_RULE,
                preview_weekly_j=float(preview) if pd.notna(preview) else None,
                shares=float(strategy.shares), avg_cost=avg_cost or None, loss_pct=0,
                mid_value=None, dk_value=None, mid_above_dk=False, positions=positions,
                action="wait", action_label="等待周线收盘", action_color="#95a5a6",
                action_detail="尚无已确认的收盘周线，等待本周最后一个交易日收盘。")


def display_weekly_value(value):
    return f"{value:.2f}" if value is not None and pd.notna(value) else "—"
