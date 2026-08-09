#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断：V6 本周为什么各 ETF 都是"暂停定投"。
复用 compare_dca_strategies 的数据加载，跑 V6 回测，打印最后交易日各活跃仓位状态。
"""
import os, json, logging, sys
from datetime import datetime, timedelta

logging.basicConfig(level=logging.ERROR, format='%(message)s')

from weekly_dca_strategy_v2 import resample_to_weekly, calc_kdj
from weekly_dca_strategy_v6 import WeeklyDCAStrategyV6
from compare_dca_strategies import fetch_data

def main():
    with open('etf_config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
    etf_list = [e for e in config.get('etf_list', []) if e.get('enabled', True)]

    print(f"{'ETF':<12}{'strategy':<6}{'最后日期':<12}{'周J':>8}{'本周操作'}")
    print("-" * 80)
    for etf in etf_list:
        try:
            daily = fetch_data(etf, 5)
            weekly = resample_to_weekly(daily)
            strat = etf.get('strategy', 'v6')
            s = WeeklyDCAStrategyV6(name=etf['name'], base_amount=1000,
                                    state_file=f"diag_v6_{etf['name']}.json")
            # 预热
            warmup_weeks_needed = 30
            effective_start = weekly.iloc[0]['trade_date']
            if len(weekly) > warmup_weeks_needed:
                warmup_end = weekly.iloc[warmup_weeks_needed]['trade_date']
                if warmup_end > effective_start:
                    effective_start = warmup_end
            s.backtest(daily, weekly, backtest_start=effective_start)

            last_date = daily['trade_date'].iloc[-1]
            # 最近一周 J
            weekly_kdj = calc_kdj(weekly)
            last_week = weekly_kdj[weekly_kdj['trade_date'] <= last_date].iloc[-1]
            j_last = last_week.get('J', None)
            last_week_date = last_week['trade_date']

            # 本周操作：收集最后一周(last_week_date)及前后几个交易日的买卖
            week_start = daily['trade_date'].iloc[-8] if len(daily) >= 8 else daily['trade_date'].iloc[0]
            thisweek_ops = []
            for pos in s.positions:
                for t in pos.trades:
                    if t['date'] >= week_start:
                        if t.get('action') == 'BUY':
                            thisweek_ops.append(f"{t['date']}买{t.get('amount',0):.0f}")
                        else:
                            thisweek_ops.append(f"{t['date']}卖{t.get('amount',0):.0f}")
            op_str = "; ".join(thisweek_ops) if thisweek_ops else "无操作"
            j_str = f"{j_last:.2f}" if j_last is not None and not pd_isna(j_last) else "N/A"
            print(f"{etf['name']:<14}{strat:<6}{last_date:<12}{j_str:>8}{op_str}")
        except Exception as e:
            print(f"{etf['name']}: 错误 {e}")

def pd_isna(v):
    import pandas as pd
    return pd.isna(v)

if __name__ == '__main__':
    main()