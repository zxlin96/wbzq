#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PortfolioTrimStrategy 不变量测试。

组合策略没有"标准答案"可做等价性断言（不像 V6H 能逐笔对 V6），
因此改为验证规则约束：
  1. 仓位只会落在 {100%, keep_ratio} 两档（建仓后）
  2. 每次调仓都由"已收盘周线"的 J 触发，且方向正确
  3. 现金/份额非负，成本计入了成交金额
  4. 净值曲线首值≈1，末值 = 组合总值/初始本金
  5. 未完成周线不产生信号（week_completed=False 的行被跳过）

用法：.conda/wbzq/python.exe test_portfolio_trim.py
"""
import logging
import os
import sys

import pandas as pd

from portfolio_trim_strategy import PortfolioTrimStrategy
from weekly_dca_strategy import calc_kdj, resample_to_weekly

CASES = [
    ('data/cache/512890_SH_daily.csv', '红利低波'),
    ('data/cache/510300_SH_daily.csv', '沪深300'),
    ('data/cache/513100_SH_daily.csv', '纳指100'),
]


def load(path):
    d = pd.read_csv(path).sort_values('trade_date').reset_index(drop=True)
    d['trade_date'] = d['trade_date'].astype(str)
    d['date'] = pd.to_datetime(d['trade_date'], format='%Y%m%d')
    return d


def main():
    logging.disable(logging.CRITICAL)
    ok = True
    print(f"{'标的':<10}{'期末总值':>12}{'仓位':>7}{'交易':>6}{'仓位档':>8}{'方向':>6}{'现金':>6}{'净值':>6}  结果")

    for path, name in CASES:
        if not os.path.exists(path):
            continue
        daily = load(path)
        weekly = resample_to_weekly(daily)
        w = calc_kdj(weekly)
        s = PortfolioTrimStrategy(name=name, initial_capital=100000)
        s.backtest(daily, weekly)

        # 2) 每次交易的日期都必须是某个已完成周线的执行日，且方向与 J 一致
        sig = w[w['week_completed'] & w['J'].notna()]
        first_day = daily.groupby(
            daily['date'].dt.isocalendar().year.astype(str) + '_' +
            daily['date'].dt.isocalendar().week.astype(str).str.zfill(2)
        )['date'].min()
        keys = list(first_day.index)
        nxt = {k: (first_day[keys[i + 1]] if i + 1 < len(keys) else pd.NaT)
               for i, k in enumerate(keys)}
        wk = sig.assign(week_key=(sig['date'].dt.isocalendar().year.astype(str) + '_'
                                  + sig['date'].dt.isocalendar().week.astype(str).str.zfill(2)))
        wk['exec'] = wk['week_key'].map(nxt)
        plan = {pd.Timestamp(r['exec']).strftime('%Y%m%d'): float(r['J'])
                for _, r in wk.iterrows() if pd.notna(r['exec'])}

        # 建仓那笔单独排除
        trades = s.trades[1:]
        dates_ok = all(str(t['date']) in plan for t in trades)
        dir_ok = True
        for t in trades:
            j = plan[str(t['date'])]
            if t['action'] == 'SELL' and not j > s.trim_j:
                dir_ok = False
            if t['action'] == 'BUY' and not j < s.refill_j:
                dir_ok = False

        # 1) 仓位档：卖出后应≈keep_ratio，补满后应≈100%（价格漂移只影响期末，不影响成交瞬间）
        ratio_ok = True
        for t in trades:
            r = t.get('ratio_after')
            if r is None:
                ratio_ok = False
                break
            if t['action'] == 'SELL' and abs(r - s.keep_ratio) > 0.02:
                ratio_ok = False
            if t['action'] == 'BUY' and abs(r - 1.0) > 0.02:
                ratio_ok = False

        # 3) 非负
        pos_ok = s.cash >= -1e-6 and s.shares >= -1e-6

        # 4) 净值
        nav = s.calc_nav_curve(daily)
        nav_ok = (not nav.empty
                  and abs(nav['nav'].iloc[0] - 1.0) < 0.02
                  and abs(nav['nav'].iloc[-1] - s.portfolio_value / s.initial_capital) < 0.01)

        passed = dates_ok and dir_ok and ratio_ok and pos_ok and nav_ok
        ok &= passed
        print(f"{name:<10}{s.portfolio_value:>12,.0f}{s.position_ratio*100:>6.0f}%"
              f"{len(s.trades):>6}{'✅' if ratio_ok else '❌':>7}{'✅' if dir_ok else '❌':>6}"
              f"{'✅' if pos_ok else '❌':>6}{'✅' if nav_ok else '❌':>6}"
              f"  {'PASS' if passed else 'FAIL'}")
        if not (dates_ok and dir_ok):
            print(f"   信号匹配={dates_ok} 方向正确={dir_ok}")

    print('\n结果:', 'ALL PASS' if ok else 'FAILED')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
