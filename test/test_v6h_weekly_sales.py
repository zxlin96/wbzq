"""Weekly sell decisions, cumulative targets and next-session executions."""

import copy
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import weekly_dca_strategy_v2 as v2
import weekly_dca_strategy_v4 as v4
import weekly_dca_strategy_v6h as v6h
from weekly_dca_strategy import resample_to_weekly
from weekly_dca_strategy_v6 import WeeklyDCAStrategyV6
from dca_decision_ledger import sync_ledger
from email_digest import collect_events, collect_weekly_sell_actions, render_actions, render_holdings
import generate_email_report as mail


@pytest.fixture
def replay(monkeypatch, tmp_path):
    def run(end='20260123', *, js=None, quotes=None, closed=(), as_of=None, hold_half=True):
        js = js or {'20260109':10., '20260116':60., '20260123':35., '20260130':35.}
        calendar = {d.strftime('%Y%m%d'):int(d.weekday()<5 and d.strftime('%Y%m%d') not in closed)
                    for d in pd.date_range('20260105', '20260210')}
        dates = [d for d, is_open in calendar.items() if is_open and d <= end]
        daily = pd.DataFrame(dict(trade_date=dates, open_qfq=10., high_qfq=11.,
                                  low_qfq=7., close_qfq=10., mid=9., dk=8.))
        for date, values in (quotes or {}).items():
            for key, value in values.items():
                daily.loc[daily.trade_date == date, key] = value
        daily.attrs['trading_calendar'] = calendar
        weekly = resample_to_weekly(daily, as_of=as_of or f'{end} 21:00')

        def kdj(frame):
            result = frame.copy()
            result['K'], result['D'], result['J'] = 40., 50., result.trade_date.map(js).fillna(35.)
            return result

        def trend(frame):
            result = frame.copy()
            result['zhixing_mid'], result['zhixing_duokong'] = result['mid'], result['dk']
            return result

        for module in (v2, v4, v6h):
            monkeypatch.setattr(module, 'calc_kdj', kdj)
            monkeypatch.setattr(module, 'calc_zhixing_duokong', trend)
        strategy = v6h.WeeklyDCAStrategyV6H('测试ETF', state_file=str(tmp_path/'state.json'),
                                          hold_half=hold_half)
        strategy.backtest(daily, weekly)
        return strategy, daily, weekly
    return run


def test_low_j_starts_new_round_on_the_same_close_as_previous_round_sell_confirmation(replay):
    js = {'20260109':10., '20260116':60., '20260123':10., '20260130':10.}
    strategy, d, w = replay(js=js, quotes=ALL_STAGES)
    buys = [t for t in strategy.trades if t['action']=='BUY' and t['date']=='20260123']
    assert len(buys)==1 and buys[0]['round']==2 and buys[0]['amount']==1000
    old, new = strategy.positions
    assert old.sell_stage==0 and old.shares==500 and old.pending_sell['shares']==250
    assert new.total_invested==1000 and new.pending_sell is None
    source = summary(strategy, d, w)
    pending = collect_weekly_sell_actions(source, '20260123', '2026-01-23 21:00')
    events = collect_events(source, {}, {}, '20260123', '2026-01-23 21:00')
    assert len(pending)==1 and pending[0]['round']==1
    assert [(e['action'],e['round'],e['amount']) for e in events]==[('BUY',2,1000)]
    assert 'R1' in render_holdings(source) and 'R2' in render_holdings(source)

    next_week, *_ = replay('20260130', js=js, quotes=ALL_STAGES)
    assert len(next_week.positions)==2
    assert next_week.positions[0].sell_stage==3
    assert next_week.positions[1].total_invested==2000
    assert [(t['date'],t['amount']) for t in next_week.trades if t['action']=='BUY' and t['round']==2]==[
        ('20260123',1000),('20260130',1000)]


@pytest.mark.parametrize('prior_j',[20.,35.])
def test_full_budget_without_a_sell_confirmation_does_not_create_an_extra_round(replay, prior_j):
    strategy, *_ = replay(js={'20260109':10.,'20260116':prior_j,'20260123':10.})
    assert len(strategy.positions)==1
    assert strategy.positions[0].pending_sell is None
    assert not [t for t in strategy.trades if t['date']=='20260123']


ALL_STAGES = {'20260123':dict(close_qfq=8., mid=9., dk=9.5),
              '20260126':dict(open_qfq=7.)}


def summary(strategy, daily, weekly):
    item = strategy.get_next_action(daily, weekly)
    item.update(name='测试ETF', ts_code='000001.SH', recent_trades=strategy.trades)
    return dict(etf_list=[item])


def test_all_three_conditions_form_one_pending_half_sale_and_fill_next_open(replay):
    friday, d, w = replay(quotes=ALL_STAGES)
    pos = friday.positions[0]
    assert not [t for t in friday.trades if t['action']=='SELL']
    assert pos.shares == 500 and pos.sell_stage == 0
    assert pos.pending_sell['target_stage'] == 3
    assert pos.pending_sell['target_sold_ratio'] == .5
    assert pos.pending_sell['shares'] == 250
    assert pos.pending_sell['execution_date'] == '20260126'
    source = summary(friday, d, w)
    assert source['etf_list'][0]['action'] == 'sell_weekly_pending'
    assert '减仓待执行' in render_holdings(source) and 'R1' in render_holdings(source)
    assert '50.00%' in render_actions(collect_weekly_sell_actions(source, '20260123', '2026-01-23 21:00'))

    monday, *_ = replay('20260126', quotes=ALL_STAGES)
    sales = [t for t in monday.trades if t['action']=='SELL']
    assert len(sales) == 1
    assert (sales[0]['date'], sales[0]['price'], sales[0]['shares']) == ('20260126', 7., 250.)
    assert sales[0]['signal_date'] == '20260123' and sales[0]['amount'] == 1750
    assert monday.positions[0].sell_stage == 3 and monday.positions[0].shares == 250
    assert monday.positions[0].pending_sell is None


@pytest.mark.parametrize('first_stage,mid', [(1,9.), (2,11.)])
def test_existing_sale_is_deducted_from_cumulative_target(replay, first_stage, mid):
    quotes = {'20260123':dict(mid=mid), '20260126':dict(open_qfq=9.),
              '20260130':dict(close_qfq=7., mid=8., dk=9.), '20260202':dict(open_qfq=6.)}
    strategy, *_ = replay('20260206', js={'20260109':10., '20260116':60.,
                                         '20260123':95., '20260130':95.}, quotes=quotes)
    sales = [t for t in strategy.trades if t['action']=='SELL']
    assert [t['date'] for t in sales] == ['20260126', '20260202']
    assert [t['target_stage'] for t in sales] == [first_stage, 3]
    assert sales[1]['shares'] == pytest.approx(500*(3-first_stage)/6, abs=1e-4)
    assert sum(t['shares'] for t in sales) == pytest.approx(250, abs=1e-4)
    assert strategy.positions[0].shares == pytest.approx(250)


def test_midweek_stop_that_recovers_by_week_close_does_not_queue_a_sale(replay):
    strategy, *_ = replay('20260126', js={'20260109':10., '20260116':60., '20260123':60.},
                          quotes={'20260120':dict(close_qfq=8., mid=9., dk=9.5)})
    assert not [t for t in strategy.trades if t['action']=='SELL']
    assert strategy.positions[0].pending_sell is None


def test_short_week_confirms_thursday_and_fills_next_exchange_session(replay):
    quotes = {'20260122':dict(close_qfq=8., mid=9., dk=9.5), '20260126':dict(open_qfq=7.)}
    strategy, *_ = replay('20260126', js={'20260109':10., '20260116':60., '20260122':35.},
                          quotes=quotes, closed=('20260123',))
    sale = next(t for t in strategy.trades if t['action']=='SELL')
    assert sale['signal_date'] == '20260122' and sale['date'] == '20260126'
    assert sale['shares'] == 250


def test_friday_before_close_cannot_confirm_or_fill_a_sell(replay):
    strategy, *_ = replay(quotes=ALL_STAGES, as_of='20260123 14:59')
    assert not [t for t in strategy.trades if t['action']=='SELL']
    assert strategy.positions[0].pending_sell is None


def test_pending_order_survives_reload_and_cannot_execute_twice(replay):
    strategy, *_ = replay(quotes=ALL_STAGES)
    restored = v6h.WeeklyDCAStrategyV6H('测试ETF', state_file=strategy.state_file)
    pos = restored.positions[0]
    assert isinstance(pos, v6h.V6HPosition)
    assert pos.pending_sell == strategy.positions[0].pending_sell
    assert pos.base_shares == 500
    assert not restored._execute_pending_sell(pos, '20260123', 8.)
    assert restored._execute_pending_sell(pos, '20260126', 7.)
    assert not restored._execute_pending_sell(pos, '20260126', 7.)
    assert pos.shares == 250 and len([t for t in pos.trades if t['action']=='SELL']) == 1


@pytest.mark.parametrize('opening', [float('nan'), 0., float('inf')])
def test_missing_open_does_not_fall_back_to_close_or_a_later_session(replay, opening):
    quotes = copy.deepcopy(ALL_STAGES)
    quotes['20260126']['open_qfq'] = opening
    strategy, *_ = replay('20260127', quotes=quotes)
    assert not [t for t in strategy.trades if t['action']=='SELL']
    assert strategy.positions[0].pending_sell['execution_date'] == '20260126'


def test_legacy_hold_half_false_still_matches_v6_trade_for_trade(replay, tmp_path):
    strategy, d, w = replay('20260130', quotes=ALL_STAGES, hold_half=False)
    legacy = WeeklyDCAStrategyV6('baseline', state_file=str(tmp_path/'v6.json'))
    legacy.backtest(d, w)
    assert strategy.trades == legacy.trades
    assert strategy.total_sell_amount == legacy.total_sell_amount


@pytest.mark.parametrize('when,count', [('2026-01-23 14:59',0), ('2026-01-23 21:00',1),
                                       ('2026-01-25 21:00',1)])
def test_pending_order_reaches_top_of_mail_without_fabricating_a_fill(replay, monkeypatch, tmp_path, when, count):
    monkeypatch.chdir(tmp_path)
    strategy, d, w = replay(quotes=ALL_STAGES)
    source = summary(strategy, d, w)
    monkeypatch.setattr(mail, 'load_reports_json', lambda:dict(latestDate='20260123'))
    monkeypatch.setattr(mail, 'load_dca_summary', lambda:source)
    monkeypatch.setattr(mail, 'load_strategy_state', lambda:{})
    monkeypatch.setattr(mail, 'load_4pct_summary', lambda:{})
    actions = collect_weekly_sell_actions(source, '20260123', when)
    assert len(actions) == count
    if count:
        html = mail.generate_email_report(as_of=when)
        top = html.split('</section>')[0]
        for text in ['第1轮（R1）', '累计卖出目标：本轮原始份额的 50.00%',
                     '卖出 250.00 份', '20260126', '策略待执行建议']:
            assert text in top
        assert '参考金额' not in render_actions(actions)
    assert not [e for e in collect_events(source, {}, {}, '20260123', when) if e['action']=='SELL']


def test_monday_fill_replaces_pending_reminder_and_hides_completed_round(replay):
    strategy, d, w = replay('20260126', quotes=ALL_STAGES)
    source = summary(strategy, d, w)
    assert not collect_weekly_sell_actions(source, '20260126', '2026-01-26 21:00')
    events = collect_events(source, {}, {}, '20260126', '2026-01-26 21:00')
    assert len(events)==1 and events[0]['action']=='SELL' and events[0]['shares']==250
    assert 'R1' not in render_holdings(source)
    nav = strategy.calc_nav_curve(d).iloc[-1]
    assert nav['market_value'] == 250 * 10  # Monday close, not its opening fill price.
    assert nav['nav'] == (250*10 + 250*7) / 5000
    assert nav['total_value'] + strategy.total_sell_amount - nav['cum_invested'] == -750


def test_ledger_preserves_weekly_decision_but_does_not_restore_pending_after_fill(replay, tmp_path):
    strategy, d, w = replay(quotes=ALL_STAGES)
    source = summary(strategy, d, w)
    path = str(tmp_path/'ledger.json')
    old = copy.deepcopy(source)
    old['etf_list'][0].pop('sell_policy')
    old['etf_list'][0]['action'] = 'old_daily_sale'
    sync_ledger(old, path)
    sync_ledger(source, path)
    monday, d, w = replay('20260126', quotes=ALL_STAGES)
    executed = summary(monday, d, w)
    sync_ledger(executed, path)
    assert executed['etf_list'][0]['action'] == 'v6h_stage_3'
    book = json.loads(Path(path).read_text())['etfs']['测试ETF']
    assert book['superseded_decisions'][0]['action'] == 'old_daily_sale'
    assert book['weeks']['20260123']['sell_orders'][0]['shares'] == 250
