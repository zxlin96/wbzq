"""Weekly close confirmation, historical stability and Friday email regression."""

import copy
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import weekly_dca_strategy as v1
import weekly_dca_strategy_v2 as v2
import weekly_dca_strategy_v4 as v4
import weekly_dca_strategy_v6 as v6
import weekly_dca_strategy_v6h as v6h
import weekly_dca_strategy_v7 as v7
from dca_decision_ledger import sync_ledger
from generate_email_report import generate_dca_section
from weekly_completion import (CONFIRMATION_RULE, _CALENDARS, attach_exchange_calendar,
                               mark_week_completion)

CLASSES = [v1.WeeklyDCAStrategy, v2.WeeklyDCAStrategy, v4.WeeklyDCAStrategyV4,
           v6.WeeklyDCAStrategyV6, v6h.WeeklyDCAStrategyV6H, v7.WeeklyDCAStrategyV7]


def daily(end="20261023"):
    dates = pd.bdate_range("20260701", end).strftime("%Y%m%d")
    return pd.DataFrame(dict(trade_date=dates, open_qfq=10., close_qfq=10.,
                             high_qfq=11., low_qfq=9.))


@pytest.mark.parametrize("resample", [v1.resample_to_weekly, v2.resample_to_weekly])
@pytest.mark.parametrize("as_of,complete", [
    ("2026-10-23 14:59:59", False), ("2026-10-23 15:00:00", True),
    ("2026-10-23 20:00:00", True), ("2026-10-25 18:00:00", True),
    ("2026-10-23T07:00:00Z", True),
])
def test_friday_close_needs_no_monday_quote(resample, as_of, complete):
    w = resample(daily(), as_of=as_of)
    assert bool(w.iloc[-1].week_completed) is complete
    assert w.iloc[-1].trade_date == "20261023"
    assert w.iloc[-1].week_end_date == "20261023"


@pytest.mark.parametrize("end", ["20261019", "20261020", "20261021", "20261022"])
def test_midweek_or_missing_friday_quote_is_not_complete(end):
    w = v1.resample_to_weekly(daily(end), as_of="2026-10-25 18:00")
    assert not w.iloc[-1].week_completed


def test_short_holiday_week_confirms_on_last_exchange_session():
    cal = {d: int(d <= "20260930") for d in pd.date_range("20260928", "20261002").strftime("%Y%m%d")}
    d = daily("20260930")
    before = v1.resample_to_weekly(d, as_of="2026-09-30 14:59", trade_calendar=cal)
    after = v1.resample_to_weekly(d, as_of="2026-09-30 15:00", trade_calendar=cal)
    assert not before.iloc[-1].week_completed
    assert after.iloc[-1].week_completed
    assert after.iloc[-1].week_end_date == "20260930"


def test_old_week_completed_flags_are_recomputed():
    for end, wrong, expected in [("20261023", False, True), ("20261022", True, False)]:
        d = daily(end)
        w = v1.resample_to_weekly(d, as_of="2026-10-25 18:00")
        w["week_completed"] = wrong
        assert bool(mark_week_completion(w, d).iloc[-1].week_completed) is expected


@pytest.fixture
def fixed_weekly_j(monkeypatch):
    def calculate(frame):
        result = frame.copy()
        result["K"] = 40.
        result["D"] = 55.
        result["J"] = result.trade_date.map(lambda d: 11.02 if str(d) == "20261023" else 50.)
        return result
    for module in [v1, v2, v4, v6h]:
        monkeypatch.setattr(module, "calc_kdj", calculate)


def run(cls, tmp_path, end, observed):
    d = daily(end)
    w = v1.resample_to_weekly(d, as_of=observed)
    strategy = cls(name="标普500", state_file=str(tmp_path / f"{cls.__module__}_{end}.json"))
    strategy.backtest(d, w, backtest_start="20261019")
    return strategy, strategy.get_next_action(d, w), d, w


@pytest.mark.parametrize("cls", CLASSES)
def test_all_variants_confirm_friday_and_keep_same_history_on_monday(cls, tmp_path, fixed_weekly_j):
    friday, signal, *_ = run(cls, tmp_path, "20261023", "2026-10-23 20:00")
    monday, next_signal, *_ = run(cls, tmp_path, "20261026", "2026-10-26 20:00")
    assert signal["signal_week_date"] == "20261023"
    assert signal["weekly_j"] == 11.02
    assert signal["action"] == "buy"
    assert len(friday.trades) == 1
    assert friday.trades[0]["date"] == "20261023"
    assert friday.trades[0]["amount"] == 1000
    # V1 uses next-session-open fills; V2/V4/V6/V6H/V7 use close fills.
    # Signal-close histories must already be complete on Friday evening.
    if cls is not v1.WeeklyDCAStrategy:
        assert monday.trades == friday.trades
    else:
        assert monday.trades[0]['date'] == '20261026'
        assert monday.trades[0]['amount'] == friday.trades[0]['amount']
    assert next_signal["signal_week_date"] == "20261023"
    assert signal["confirmation_rule"] == CONFIRMATION_RULE


@pytest.mark.parametrize("cls", CLASSES)
def test_friday_intraday_does_not_use_unclosed_signal(cls, tmp_path, fixed_weekly_j):
    strategy, signal, *_ = run(cls, tmp_path, "20261023", "2026-10-23 14:59")
    assert not strategy.trades
    assert signal["signal_week_date"] == "20261016"
    assert signal["action"] != "buy"
    assert signal["preview_weekly_j"] == 11.02


@pytest.mark.parametrize("cls", CLASSES)
def test_weekend_email_includes_friday_buy_even_with_old_permanent_holdings(cls, tmp_path, fixed_weekly_j):
    strategy, _, d, w = run(cls, tmp_path, "20261023", "2026-10-25 18:00")
    signal = strategy.get_next_action(d, w)
    signal.update(name="标普500", ts_code="513500.SH")
    # Model the existing R1-R8 long-term holdings before the new round.
    old = dict(round_id=0, shares=100., total_invested=1000., return_pct=10.,
               action="v6h_stage_3", action_label="永久持有", action_color="#333")
    signal["positions"].insert(0, old)
    from weekly_completion import refresh_action_summary
    refresh_action_summary(signal)
    assert signal["action"] == "buy"
    html = generate_dca_section({"etf_list": [signal]})
    assert "建议买入1000元" in html
    assert "依据20261023收盘周线" in html
    assert "已执行请勿重复买入" in html


@pytest.mark.parametrize("cls", CLASSES)
def test_no_confirmed_week_does_not_fall_back_to_forming_j(cls, tmp_path, fixed_weekly_j):
    d = daily("20261022").tail(4).copy().reset_index(drop=True)
    w = v1.resample_to_weekly(d, as_of="2026-10-22 20:00")
    strategy = cls(name="waiting", state_file=str(tmp_path / "waiting.json"))
    strategy.backtest(d, w)
    signal = strategy.get_next_action(d, w)
    assert signal["action"] == "wait"
    assert signal["signal_week_date"] is None
    assert signal["weekly_j"] is None
    assert not strategy.trades


def test_calendar_includes_closed_days_and_is_shared_across_etfs():
    _CALENDARS.clear()
    pro = Mock()
    pro.trade_cal.return_value = pd.DataFrame(dict(cal_date=["20260928", "20260929", "20260930", "20261001", "20261002"], is_open=[1, 1, 1, 0, 0]))
    one = attach_exchange_calendar(daily("20260930"), pro)
    two = attach_exchange_calendar(daily("20260930"), pro)
    assert pro.trade_cal.call_count == 1
    assert pro.trade_cal.call_args.kwargs["exchange"] == "SSE"
    assert "is_open" not in pro.trade_cal.call_args.kwargs  # Need closed days too.
    assert one.attrs["trading_calendar"] == two.attrs["trading_calendar"]
    assert v1.resample_to_weekly(one, as_of="2026-09-30 20:00").iloc[-1].week_completed
    _CALENDARS.clear()


@pytest.mark.parametrize("cls", CLASSES)
def test_integer_date_inputs_keep_the_same_confirmation(cls, tmp_path, fixed_weekly_j):
    d = daily().astype({'trade_date': int})
    w = v1.resample_to_weekly(d, as_of='2026-10-23 20:00')
    s = cls(name='integer', state_file=str(tmp_path / 'int.json'))
    s.backtest(d, w, backtest_start=20261019)
    assert s.get_next_action(d, w)['signal_week_date'] == '20261023'
    assert len(s.trades) == 1


def test_waiting_for_confirmation_keeps_existing_holdings_visible(tmp_path, fixed_weekly_j):
    d = daily('20261022').tail(4).reset_index(drop=True)
    w = v1.resample_to_weekly(d, as_of='2026-10-22 20:00')
    s = v6h.WeeklyDCAStrategyV6H(name='held', state_file=str(tmp_path / 'held.json'))
    pos = s._new_position()
    pos.buy('20261016', 10., 1000., 'existing holding')
    action = s.get_next_action(d, w)
    assert action['action'] == 'wait'
    assert action['shares'] == 100
    assert action['avg_cost'] == 10
    assert len(action['positions']) == 1


def test_old_decision_is_retained_when_confirmation_policy_is_corrected(tmp_path):
    path = tmp_path / "ledger.json"
    old = dict(action="v6h_stage_3", action_label="R1 永久持有", signal_week="20261023")
    path.write_text(json.dumps(dict(version=1, etfs={"标普500": dict(current_week="20261023", weeks={"20261023": old})})))
    item = dict(name="标普500", signal_week_date="20261023", confirmation_rule=CONFIRMATION_RULE,
                action="buy", action_label="R10 建议定投", weekly_j=11.02)
    first = sync_ledger({"etf_list": [copy.deepcopy(item)]}, str(path))
    assert first["etf_list"][0]["action"] == "buy"
    book = json.loads(path.read_text())["etfs"]["标普500"]
    assert book["superseded_decisions"][0]["action"] == old["action"]
    assert book["weeks"]["20261023"]["source"] == "rule_migration"
    item.update(action="wait", action_label="等待")
    second = sync_ledger({"etf_list": [item]}, str(path))
    assert second["etf_list"][0]["action"] == "buy"
    assert len(json.loads(path.read_text())["etfs"]["标普500"]["superseded_decisions"]) == 1
