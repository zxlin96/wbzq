#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V6H 等价性断言：hold_half=False 时，V6H 必须与 V6 逐笔一致。

这是 V6H 上生产前的必要门槛（沿用 V7 TODO 的纪律：
"等价性断言是变体类开发的必要门槛"）。任何一笔不一致都说明 fork 时改动越界。

用法：.conda/wbzq/python.exe test_v6h_equivalence.py
"""
import logging
import os
import sys

import pandas as pd

from weekly_dca_strategy import resample_to_weekly
from weekly_dca_strategy_v6 import WeeklyDCAStrategyV6
from weekly_dca_strategy_v6h import WeeklyDCAStrategyV6H


def load_daily(path: str) -> pd.DataFrame:
    df = pd.read_csv(path).sort_values('trade_date').reset_index(drop=True)
    df['date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
    return df


def build_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    return resample_to_weekly(daily)

CASES = [
    ('data/cache/512890_SH_daily.csv', '红利低波'),
    ('data/cache/510300_SH_daily.csv', '沪深300'),
    ('data/cache/515880_SH_daily.csv', '通信'),
    ('data/cache/516080_SH_daily.csv', '创新药'),
    ('data/cache/159819_SZ_daily.csv', '人工智能'),
    ('data/cache/512400_SH_daily.csv', '有色'),
]


def norm(trades):
    rows = []
    for t in trades:
        rows.append((str(t['date']), t['action'], round(float(t['price']), 6),
                     round(float(t['amount']), 4), round(float(t['shares']), 4),
                     t.get('round'), t.get('reason', '')))
    return sorted(rows)


def run(cls, daily, weekly, tag, **kw):
    f = f'dca_state_test_{tag}.json'
    if os.path.exists(f):
        os.remove(f)
    s = cls(name=tag, base_amount=1000, round_budget=5000, round_periods=5,
            state_file=f, **kw)
    s.backtest(daily, weekly)
    if os.path.exists(f):
        os.remove(f)
    return s


def main():
    logging.disable(logging.CRITICAL)
    ok = True
    print(f"{'标的':<10}{'V6笔数':>8}{'V6H笔数':>9}{'投入一致':>10}{'卖出一致':>10}  结果")
    for path, name in CASES:
        if not os.path.exists(path):
            print(f'{name}: 数据缺失，跳过')
            continue
        daily = load_daily(path)
        weekly = build_weekly(daily)
        a = run(WeeklyDCAStrategyV6, daily, weekly, f'v6_{name}')
        b = run(WeeklyDCAStrategyV6H, daily, weekly, f'v6h_{name}', hold_half=False)

        ta, tb = norm(a.trades), norm(b.trades)
        same_trades = ta == tb
        same_inv = abs(a.total_invested - b.total_invested) < 1e-6
        same_sell = abs(a.total_sell_amount - b.total_sell_amount) < 1e-6
        passed = same_trades and same_inv and same_sell
        ok &= passed
        print(f'{name:<10}{len(ta):>8}{len(tb):>9}'
              f'{"✅" if same_inv else "❌":>9}{"✅" if same_sell else "❌":>10}'
              f'  {"PASS" if passed else "FAIL"}')
        if not passed:
            sa, sb = set(ta), set(tb)
            for r in list(sa - sb)[:5]:
                print('   仅V6有:', r)
            for r in list(sb - sa)[:5]:
                print('   仅V6H有:', r)

    print('\n' + '-' * 60)
    print('不变量单测')
    print('-' * 60)

    # 1) 状态恢复：base_shares 丢失时应能从 trades 反推，卖出仍是 1/6
    s = WeeklyDCAStrategyV6H(name='ut', state_file='dca_state_ut.json')
    p = s._new_position()
    p.trades = [{'action': 'BUY', 'shares': 1000.0, 'amount': 1000},
                {'action': 'BUY', 'shares': 2000.0, 'amount': 2000}]
    p.shares, p.total_cost, p.base_shares = 3000.0, 3000.0, 0.0
    s._sell_tranche(p, '20260101', 1.0, 'ut')
    ok1 = abs((3000.0 - p.shares) - 500.0) < 1e-9

    # 2) 50% 硬上限：连续调用 5 次，累计卖出应停在 50%
    p2 = s._new_position()
    p2.trades = [{'action': 'BUY', 'shares': 3000.0, 'amount': 3000}]
    p2.shares, p2.total_cost, p2.base_shares = 3000.0, 3000.0, 0.0
    for i in range(5):
        s._sell_tranche(p2, f'2026010{i + 1}', 1.0, f'ut{i}')
    ok2 = abs((3000.0 - p2.shares) - 1500.0) < 1e-9

    for f in ('dca_state_ut.json',):
        if os.path.exists(f):
            os.remove(f)
    print(f'  状态恢复后卖出 1/6: {"PASS" if ok1 else "FAIL"}')
    print(f'  累计卖出停在上限 50%: {"PASS" if ok2 else "FAIL"}')
    ok &= ok1 and ok2

    print('\n结果:', 'ALL PASS' if ok else 'FAILED')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
