"""逐日计数、重启恢复和实时/回测一致性的离线回归验证。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with patch('config.APIConfig.get_token', return_value='offline-test'), \
        patch('tushare.set_token'), patch('tushare.pro_api'):
    from backtest_sentiment_rebound import BacktestEngine
from sentiment_rebound_strategy import SentimentReboundStrategy


class DailyCountingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.json'
        self.strategy = SentimentReboundStrategy(state_file=str(self.path))
        self.rows = []

    def day(self, date, rising, buy=False, strategy=None):
        self.rows.append(dict(trade_date=date, zhixing_brick_rising=rising, close=1.))
        return (strategy or self.strategy).generate_current_signal(
            dict(current=200 if buy else 0, percentile_90=100, is_above_threshold=buy),
            date, pd.DataFrame(self.rows), 1.)

    def test_october_22_buy_green_breaks_the_run(self):
        actions = []
        for date, rising, buy in [
            ('20261015', False, True), ('20261016', False, True),
            ('20261019', True, True), ('20261020', True, False),
            ('20261021', True, False), ('20261022', False, True),
            ('20261023', True, False), ('20261026', True, False),
        ]:
            actions.extend(self.day(date, rising, buy))
            if date == '20261022':
                self.assertEqual(self.strategy.red_bar_count, 0)
        self.assertEqual(self.strategy.red_bar_count, 2)
        self.assertEqual([a['action'] for a in actions], ['BUY'] * 4)

    def test_fourth_red_on_buy_day_defers_sale_without_losing_count(self):
        self.day('20261015', True, True)
        self.assertEqual(self.strategy.red_bar_count, 0)  # 建仓前的柱不计入持仓期
        for date in ['20261016', '20261019', '20261020']:
            self.day(date, True)
        self.assertEqual([s['action'] for s in self.day('20261021', True, True)], ['BUY'])
        self.assertEqual(self.strategy.red_bar_count, 4)
        sale = self.day('20261022', True)
        self.assertEqual(sale[0]['ratio'], .5)
        self.assertEqual(sale[0]['red_bars'], 5)
        self.assertEqual(self.strategy.red_bar_count, 0)
        for date in ['20261023', '20261026', '20261027']:
            self.assertEqual(self.day(date, True), [])
        self.assertEqual(self.day('20261028', True)[0]['ratio'], .5)

    def test_buy_red_interrupts_green_run(self):
        self.day('20261015', False, True)
        self.day('20261016', False)
        self.day('20261019', False)
        self.day('20261020', True, True)
        self.assertEqual(self.strategy.green_bar_count, 0)
        self.assertEqual(self.day('20261021', False), [])
        self.assertEqual(self.strategy.green_bar_count, 1)

    def test_third_green_on_buy_day_is_counted_and_buy_has_priority(self):
        self.day('20261015', False, True)
        self.day('20261016', False)
        self.day('20261019', False)
        self.assertEqual([s['action'] for s in self.day('20261020', False, True)], ['BUY'])
        self.assertEqual(self.strategy.green_bar_count, 3)
        sale = self.day('20261021', False)
        self.assertEqual(sale[0]['ratio'], 1.)
        self.assertEqual(sale[0]['green_bars'], 4)
        self.assertEqual(self.strategy.position, 0)

    def test_quiet_day_count_survives_restart_and_same_day_is_idempotent(self):
        self.day('20261015', True, True)
        self.day('20261016', True)
        self.day('20261019', True)
        restored = SentimentReboundStrategy(state_file=str(self.path))
        self.assertEqual(restored.red_bar_count, 2)
        self.assertEqual(restored.last_bar_date, '20261019')
        before = self.path.read_bytes()
        signals = restored.generate_current_signal(
            dict(current=0, percentile_90=100, is_above_threshold=False),
            '20261019', pd.DataFrame(self.rows), 1.)
        self.assertEqual(signals, [])
        self.assertEqual(restored.red_bar_count, 2)
        self.assertEqual(self.path.read_bytes(), before)
        self.day('20261020', True, strategy=restored)
        self.assertEqual(self.day('20261021', True, strategy=restored)[0]['ratio'], .5)
        reopened = SentimentReboundStrategy(state_file=str(self.path))
        self.assertEqual(reopened.position, .5)
        self.assertEqual(reopened.trades[-1]['action'], 'SELL')
        self.assertEqual(reopened.red_bar_count, 0)

    def test_old_state_counter_is_rebuilt_without_rewriting_trades_or_position(self):
        self.path.write_text(json.dumps(dict(
            position=8, red_bar_count=3, green_bar_count=0, last_trade_date='20261023',
            trades=[dict(date='20261015', action='BUY', amount=2000)],
        )))
        restored = SentimentReboundStrategy(state_file=str(self.path))
        self.rows = [dict(trade_date=d, zhixing_brick_rising=r) for d, r in [
            ('20261015', False), ('20261016', False), ('20261019', True),
            ('20261020', True), ('20261021', True), ('20261022', False), ('20261023', True),
        ]]
        self.assertEqual(self.day('20261026', True, strategy=restored), [])
        self.assertEqual(restored.red_bar_count, 2)
        self.assertEqual(restored.position, 8)
        self.assertEqual(len(restored.trades), 1)

    def test_missed_run_replays_the_intervening_green(self):
        self.day('20261015', True, True)
        self.day('20261016', True)
        self.day('20261019', True)
        self.rows.append(dict(trade_date='20261020', zhixing_brick_rising=False))
        self.assertEqual(self.day('20261021', True), [])
        self.assertEqual(self.strategy.red_bar_count, 1)

    def test_missing_direction_breaks_the_run(self):
        self.day('20261015', True, True)
        self.day('20261016', True)
        self.day('20261019', True)
        self.assertEqual(self.day('20261020', None), [])
        self.assertEqual(self.strategy.red_bar_count, 0)
        self.assertEqual(self.day('20261021', True), [])
        self.assertEqual(self.strategy.red_bar_count, 1)

    def test_old_state_keeps_past_half_sale_as_counter_reset(self):
        trades = [dict(date='20261015', action='BUY', amount=2000),
                  dict(date='20261026', action='SELL', ratio=.5)]
        self.path.write_text(json.dumps(dict(position=.5, red_bar_count=0,
            green_bar_count=0, last_trade_date='20261026', trades=trades)))
        restored = SentimentReboundStrategy(state_file=str(self.path))
        self.rows = [dict(trade_date=d, zhixing_brick_rising=True)
                     for d in ['20261019', '20261020', '20261021', '20261023', '20261026']]
        self.assertEqual(self.day('20261027', True, strategy=restored), [])
        self.assertEqual(restored.red_bar_count, 1)
        self.assertEqual(restored.position, .5)
        self.assertEqual(restored.trades, trades)

    def test_flat_period_does_not_carry_into_new_position(self):
        self.day('20261015', False, True)
        for date in ['20261016', '20261019', '20261020']:
            self.day(date, False)
        self.day('20261021', True)
        self.day('20261022', True)
        self.day('20261023', True, True)
        self.assertEqual(self.day('20261026', True), [])
        self.assertEqual(self.strategy.red_bar_count, 1)

    def test_future_rows_are_ignored_and_stale_price_is_not_counted_twice(self):
        self.day('20261015', True, True)
        bars = pd.DataFrame([
            dict(trade_date='20261016', zhixing_brick_rising=False),
            dict(trade_date='20261019', zhixing_brick_rising=True),
        ])
        self.strategy.update_bar_counts(bars, '20261016')
        self.assertEqual(self.strategy.green_bar_count, 1)
        self.strategy.update_bar_counts(bars, '20261016')
        self.assertEqual(self.strategy.green_bar_count, 1)
        stale = bars.iloc[:1]
        self.assertIsNone(self.strategy.update_bar_counts(stale, '20261019'))
        self.assertEqual(self.strategy.green_bar_count, 1)

    def test_single_precomputed_bar_can_be_counted(self):
        self.day('20261015', True, True)
        one = pd.DataFrame([dict(trade_date='20261016', zhixing_brick_rising=True)])
        self.assertIsNone(self.strategy.generate_sell_signal(one, '20261016', 1.))
        self.assertEqual(self.strategy.red_bar_count, 1)
        self.strategy.generate_sell_signal(one, '20261016', 1.)
        self.assertEqual(self.strategy.red_bar_count, 1)

    def test_reset_clears_processed_dates(self):
        self.day('20261015', True, True)
        self.day('20261016', True)
        self.strategy.reset()
        self.assertIsNone(self.strategy.last_trade_date)
        self.assertIsNone(self.strategy.last_bar_date)


class BacktestConsistencyTests(unittest.TestCase):
    def test_realtime_execute_and_standalone_backtest_have_identical_signals(self):
        # Includes a red buy day, a green buy day, repeated half exits, and a
        # fourth-red buy day followed by a fifth-red sale.
        cases = [
            ([False, True, True, False, True, True], {0, 3}),
            ([False, True, True, True, True, True, True, True, True, True], {0, 4}),
            ([False, False, False, True, False, False, False], {0, 3}),
            ([False, False, False, False, False], {0, 3}),
        ]
        for colors, buys in cases:
            with self.subTest(colors=colors, buys=buys), tempfile.TemporaryDirectory() as temp:
                dates = pd.bdate_range('2026-05-01', periods=60 + len(colors)).strftime('%Y%m%d')
                j13 = pd.DataFrame(dict(trade_date=dates, count=[100] * 60 +
                                       [1000 if i in buys else 0 for i in range(len(colors))]))
                etf = pd.DataFrame(dict(trade_date=dates[60:], close=1.))
                brick = etf.assign(zhixing_brick_rising=colors)
                live = SentimentReboundStrategy(state_file=str(Path(temp) / 'live.json'))
                for i, date in enumerate(dates[60:]):
                    threshold = j13.iloc[i:60 + i]['count'].quantile(.9)
                    live.generate_current_signal(dict(current=int(j13.iloc[60 + i]['count']),
                        percentile_90=threshold, is_above_threshold=i in buys),
                        date, brick.iloc[:i + 1], 1.)
                historical = SentimentReboundStrategy(state_file=str(Path(temp) / 'historical.json'))
                # Integer YYYYMMDD used to be interpreted as nanoseconds.
                historical.execute_strategy(j13.astype({'trade_date': int}),
                    etf.astype({'trade_date': int}), brick.astype({'trade_date': int}))
                engine = BacktestEngine()
                engine.run(j13, etf, brick)

                def signature(trades):
                    return [(t['date'], t['action'], t.get('ratio', 1. if t.get('type') == '全卖'
                             else .5 if t.get('type') == '卖一半' else None),
                             t.get('amount') if t['action'] == 'BUY' else None) for t in trades]

                self.assertEqual(signature(live.trades), signature(historical.trades))
                self.assertEqual(signature(live.trades), signature(engine.trades))
                self.assertEqual(live.red_bar_count, engine.red_bar_count)
                self.assertEqual(live.green_bar_count, engine.green_bar_count)
                self.assertEqual(live.position, engine.position_shares)


if __name__ == '__main__':
    unittest.main()
