"""Exercise per-ETF V6H budgets through the production config entry point."""

import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import weekly_dca_strategy as runner
import weekly_dca_strategy_v2 as v2
import weekly_dca_strategy_v6h as v6h


@pytest.mark.parametrize('rebound, expected_buys', [
    (False, [3000] * 5),
    (True, [3000, 3000, 9000]),
])
def test_nasdaq_config_replays_history_with_its_own_budget(
        monkeypatch, tmp_path, rebound, expected_buys):
    config = json.loads((ROOT / 'etf_config.json').read_text())
    config['etf_list'] = [e for e in config['etf_list']
                          if e['name'] in ('纳指100', '标普500')]
    (tmp_path / 'etf_config.json').write_text(json.dumps(config), encoding='utf-8')
    monkeypatch.chdir(tmp_path)

    class Clock(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 11, 10, 20)

    monkeypatch.setattr(runner, 'datetime', Clock)
    dates = pd.bdate_range('20260105', periods=40 * 5).strftime('%Y%m%d')
    daily = pd.DataFrame(dict(trade_date=dates, open_qfq=10., high_qfq=11.,
                              low_qfq=9., close_qfq=10.))
    weeks = runner.resample_to_weekly(daily, as_of='2026-11-10 20:00')
    # Production starts after 30 warm-up weeks; two low-J weeks then a rebound
    # must top up the budget once, rather than produce a sixth installment.
    js = dict(zip(weeks.trade_date, [20.] * 30 +
                  ([10., 10.] + [20.] * 8 if rebound else [10.] * 10)))

    def kdj(frame):
        frame = frame.copy()
        frame['K'], frame['D'], frame['J'] = 40., 50., frame.trade_date.map(js)
        return frame

    def trend(frame):
        frame = frame.copy()
        frame['zhixing_mid'], frame['zhixing_duokong'] = 9., 8.
        return frame

    monkeypatch.setattr(runner, '_get_etf_data', lambda *args: daily.copy())
    for module in (v6h, v2):
        monkeypatch.setattr(module, 'calc_kdj', kdj)
        monkeypatch.setattr(module, 'calc_zhixing_duokong', trend)
    # Keep this regression focused on config, trades and the published summary.
    monkeypatch.setattr(runner, 'generate_backtest_report_v6',
                        lambda *args: Path(args[-1]).write_text('report'))
    monkeypatch.setattr(runner, '_generate_comparison_chart', lambda *args: None)

    results = runner.run_backtest_from_config(output_dir=str(tmp_path / 'reports'))
    nasdaq = results['纳指100']
    assert nasdaq.base_amount == 3000
    assert [t['amount'] for t in nasdaq.trades if t['action'] == 'BUY'] == expected_buys
    assert nasdaq.positions[0].round_budget == 15000
    assert nasdaq.positions[0].round_periods == 5
    assert nasdaq.positions[0].buy_count <= 5
    assert nasdaq.total_invested == 15000
    assert nasdaq.positions[0].get_invest_amount(10.) == 0

    other = results['标普500']
    assert other.base_amount == 1000
    assert other.round_budget == 5000
    assert other.total_invested == 5000
    assert [t['amount'] for t in other.trades if t['action'] == 'BUY'] == [v / 3 for v in expected_buys]
    summary = json.loads((tmp_path / 'reports' / 'dca_summary.json').read_text())
    position = next(e for e in summary['etf_list'] if e['name'] == '纳指100')['positions'][0]
    assert position['round_budget'] == 15000
    assert position['total_invested'] == 15000
