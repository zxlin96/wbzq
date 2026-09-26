#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一致性回归测试：验证"决策只用已收盘周线"修复生效。

测试方法：把同一批历史数据分别截断到同一周内的多个交易日（0914~0918）
以及下周初（0921），各跑一遍完整回测+get_next_action，断言：

  A. 同一周内任意截断日，已收盘周线集合相同 → 交易信号与操作建议必须完全一致；
  B. 下周初(0921)的run与本周(0918)的run相比，fill日期<=0917的交易必须一致
     （新一周只允许引入基于0918收盘周线的新决策，不得改写历史）；
  C. signal_week_date 必须指向最后一根已收盘周线，而非本周半成品bar。

用法：
    python test_weekly_decision_consistency.py            # 全量4只ETF
    python test_weekly_decision_consistency.py --quick    # 只跑2只
"""
import argparse
import logging
import sys
from datetime import timedelta

import pandas as pd

logging.basicConfig(level=logging.ERROR)

from weekly_dca_strategy import (
    WeeklyDCAStrategy, _get_etf_data, resample_to_weekly,
)
from weekly_dca_strategy_v6 import WeeklyDCAStrategyV6

TRUNCATION_DATES = ['20260914', '20260915', '20260916', '20260917', '20260918', '20260921']
YEARS = 5
WARMUP_WEEKS = 30


def run_one(etf, daily_full, end_date):
    """截断到end_date，按config同等参数跑回测，返回(trades, next_action)。"""
    daily = daily_full[daily_full['trade_date'] <= end_date].copy().reset_index(drop=True)
    if daily.empty:
        raise ValueError(f"{etf['name']} 无 {end_date} 之前的数据")
    weekly = resample_to_weekly(daily)
    now = pd.to_datetime(end_date)
    backtest_start = (now - timedelta(days=YEARS * 365)).strftime('%Y%m%d')
    effective_start = backtest_start
    if len(weekly) > WARMUP_WEEKS:
        warmup_end = str(weekly.iloc[WARMUP_WEEKS]['trade_date'])
        if warmup_end > effective_start:
            effective_start = warmup_end

    if etf.get('strategy', 'v6') == 'v1':
        s = WeeklyDCAStrategy(name=etf['name'], base_amount=1000, sell_version='v1')
    else:
        s = WeeklyDCAStrategyV6(name=etf['name'], base_amount=1000)
    s.backtest(daily, weekly, backtest_start=effective_start)
    action_info = s.get_next_action(daily, weekly)
    return s, action_info


def norm_trades(s, upto=None):
    """交易规范化：去掉fill价噪声，只保留(日期,动作,金额)；只保留 <= upto 的部分。"""
    rows = []
    for t in s.trades:
        d = str(t['date'])
        if upto and d > upto:
            continue
        rows.append((d, t['action'], round(float(t.get('amount', 0)), 0)))
    return rows


def check_etf(etf, daily_full):
    name = etf['name']
    results = {}
    ok = True
    for d in TRUNCATION_DATES:
        s, ai = run_one(etf, daily_full, d)
        results[d] = (s, ai)

    intra = TRUNCATION_DATES[:5]          # 同一周 0914~0918
    first = intra[0]

    # 断言A1：同周内交易信号一致（fill日期<=0916，避开末日fill回退差异）
    ref = norm_trades(results[first][0], upto='20260916')
    for d in intra[1:]:
        cur = norm_trades(results[d][0], upto='20260916')
        if cur != ref:
            ok = False
            print(f"  ❌ A1失败: {d}截断的交易与{first}不一致\n     {first}: {ref}\n     {d}: {cur}")

    # 断言A2：同周内操作建议完全一致
    ref_ai = results[first][1]
    ref_key = (ref_ai['action'], ref_ai['action_label'], ref_ai['weekly_j'],
               ref_ai.get('signal_week_date'))
    for d in intra[1:]:
        ai = results[d][1]
        key = (ai['action'], ai['action_label'], ai['weekly_j'], ai.get('signal_week_date'))
        if key != ref_key:
            ok = False
            print(f"  ❌ A2失败: {d}截断的建议与{first}不一致: {key} vs {ref_key}")

    # 断言B：0918 vs 0921，fill<=0917的交易一致（新周不得改写历史）
    t0918 = norm_trades(results['20260918'][0], upto='20260917')
    t0921 = norm_trades(results['20260921'][0], upto='20260917')
    if t0918 != t0921:
        ok = False
        print(f"  ❌ B失败: 0921截断改写了<=0917的历史交易\n     0918: {t0918}\n     0921: {t0921}")

    # 断言C：signal_week_date 是最后一根已收盘周线
    for d in intra:
        sw = results[d][1].get('signal_week_date')
        if sw != '20260911':   # 0914~0918本周的上一根已收盘周线是0911
            ok = False
            print(f"  ❌ C失败: {d}截断的signal_week_date={sw}, 期望20260911")
    sw = results['20260921'][1].get('signal_week_date')
    if sw != '20260918':
        ok = False
        print(f"  ❌ C失败: 0921截断的signal_week_date={sw}, 期望20260918")

    # 输出观察表
    print(f"\n[{name}] {'✅ 通过' if ok else '❌ 未通过'}")
    print(f"  {'截断日':<10}{'建议':<28}{'决策J':>8}{'信号周':<10}{'预览J(未确认)':>12}")
    for d in TRUNCATION_DATES:
        ai = results[d][1]
        print(f"  {d:<10}{ai['action_label']:<28}{ai['weekly_j']:>8}"
              f"{str(ai.get('signal_week_date')):<10}{str(ai.get('preview_weekly_j')):>12}")
    return ok


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--quick', action='store_true', help='只跑2只ETF')
    args = parser.parse_args()

    etfs = [
        {'ts_code': '563300.SH', 'name': '中证2000', 'strategy': 'v6'},
        {'ts_code': '510300.SH', 'name': '沪深300', 'strategy': 'v6'},
        {'ts_code': '512890.SH', 'name': '红利低波', 'strategy': 'v6'},
        {'ts_code': '159941.SZ', 'name': 'NASDAQ100', 'strategy': 'v1'},
    ]
    if args.quick:
        etfs = etfs[:2]

    all_ok = True
    for etf in etfs:
        try:
            daily_full = _get_etf_data(
                etf['ts_code'],
                pd.to_datetime('20260921') - timedelta(days=(YEARS + 1) * 365),
                pd.to_datetime('20260921'))
            all_ok &= check_etf(etf, daily_full)
        except Exception as e:
            all_ok = False
            print(f"[{etf['name']}] ❌ 异常: {e}")

    print('\n' + '=' * 60)
    print('总体结果:', '✅ 全部通过 — 同周内建议不随截断日漂移' if all_ok else '❌ 存在失败项')
    sys.exit(0 if all_ok else 1)


if __name__ == '__main__':
    main()
