#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
定投策略对比脚本：V1 vs V2 vs V4 vs V5 vs V6

对 etf_config.json 中启用的每只 ETF，分别用契约策略回测并输出对比报告。
- V1: weekly_dca_strategy.py (sell_version='v1')
- V2: weekly_dca_strategy_v2.py
- V4: weekly_dca_strategy_v4.py (V2 + 跌破本轮最低点止损)
- V5: weekly_dca_strategy_v5.py (V2 去倍投/限购买次数)
- V6: weekly_dca_strategy_v6.py (V5 买入 + V4 止损)

输出：
- 控制台汇总表格
- html/dca_compare/v{ver}_report_{name}.html（各策略报告）
- html/dca_compare/comparison.html（多策略收益对比）
"""

import os
import json
import logging
from datetime import datetime, timedelta

import pandas as pd

from weekly_dca_strategy import (
    WeeklyDCAStrategy as StrategyV1,
    generate_backtest_report as gen_report_v1,
)
from weekly_dca_strategy_v2 import (
    WeeklyDCAStrategy as StrategyV2,
    _get_etf_data,
    get_nasdaq_data,
    resample_to_weekly,
    generate_backtest_report as gen_report_v2,
)
from weekly_dca_strategy_v4 import (
    WeeklyDCAStrategyV4,
)
from weekly_dca_strategy_v5 import (
    WeeklyDCAStrategyV5,
)
from weekly_dca_strategy_v6 import (
    WeeklyDCAStrategyV6,
)

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s | %(message)s')


def fetch_data(etf: dict, years: int = 5):
    """获取某ETF日线数据（含复权），返回 daily_warmup"""
    tgt = etf['target']
    ts_code = etf.get('ts_code', '')
    if tgt == 'nasdaq':
        return get_nasdaq_data(years=years + 1)
    return _get_etf_data(
        ts_code,
        datetime.now() - timedelta(days=(years + 1) * 365),
        datetime.now())


def compute_metrics(strategy, last_price: float) -> dict:
    """根据回测后策略状态计算对比指标"""
    market_value = strategy.shares * last_price if strategy.shares > 0 else 0
    total_return = market_value + strategy.total_sell_amount - strategy.total_invested
    return_pct = total_return / strategy.total_invested * 100 if strategy.total_invested > 0 else 0
    return {
        'total_invested': round(strategy.total_invested, 2),
        'total_sell': round(strategy.total_sell_amount, 2),
        'shares': round(strategy.shares, 4),
        'market_value': round(market_value, 2),
        'total_return': round(total_return, 2),
        'return_pct': round(return_pct, 2),
        'trade_count': len(strategy.trades),
    }


def run_one(etf: dict, years: int, out_dir: str, daily_warmup, weekly,
            j_exit_threshold_v4: float = 20,
            start_date: str = None):
    """对单只ETF跑多种策略，返回 {version: metrics}"""
    cfg = {
        'name': etf['name'],
        'ts_code': etf.get('ts_code', ''),
        'target': etf['target'],
    }
    base_amount = 1000
    if start_date:
        backtest_start = start_date
    else:
        backtest_start = (datetime.now() - timedelta(days=years * 365)).strftime('%Y%m%d')
    # 预热逻辑（与V2一致）：前30周为预热期
    effective_start = backtest_start
    warmup_weeks_needed = 30
    if len(weekly) > warmup_weeks_needed:
        warmup_end_date = weekly.iloc[warmup_weeks_needed]['trade_date']
        if warmup_end_date > backtest_start:
            effective_start = warmup_end_date

    last_price = daily_warmup['close_qfq'].iloc[-1]
    last_date = daily_warmup['trade_date'].iloc[-1]
    results = {}

    # V1
    s1 = StrategyV1(name=f"{cfg['name']}V1", base_amount=base_amount,
                    sell_version='v1', state_file=f"cmp_v1_{cfg['name']}.json")
    s1.backtest(daily_warmup, weekly, backtest_start=effective_start)
    s1._nav_curve = s1.calc_nav_curve(daily_warmup)
    s1._daily_df = daily_warmup
    s1._weekly_df = weekly
    results['V1'] = compute_metrics(s1, last_price)
    gen_report_v1(s1, daily_warmup, weekly, os.path.join(out_dir, f"v1_report_{cfg['name']}.html"))

    # V2
    s2 = StrategyV2(name=f"{cfg['name']}V2", base_amount=base_amount,
                    state_file=f"cmp_v2_{cfg['name']}.json")
    s2.backtest(daily_warmup, weekly, backtest_start=effective_start)
    s2._nav_curve = s2.calc_nav_curve(daily_warmup)
    s2._daily_df = daily_warmup
    s2._weekly_df = weekly
    results['V2'] = compute_metrics(s2, last_price)
    gen_report_v2(s2, daily_warmup, weekly, os.path.join(out_dir, f"v2_report_{cfg['name']}.html"))

    # V4
    s4 = WeeklyDCAStrategyV4(name=f"{cfg['name']}V4", base_amount=base_amount,
                             j_exit_threshold=j_exit_threshold_v4,
                             state_file=f"cmp_v4_{cfg['name']}.json")
    s4.backtest(daily_warmup, weekly, backtest_start=effective_start)
    s4._nav_curve = s4.calc_nav_curve(daily_warmup)
    s4._daily_df = daily_warmup
    s4._weekly_df = weekly
    results['V4'] = compute_metrics(s4, last_price)
    gen_report_v2(s4, daily_warmup, weekly, os.path.join(out_dir, f"v4_report_{cfg['name']}.html"))

    # V5
    s5 = WeeklyDCAStrategyV5(name=f"{cfg['name']}V5", base_amount=base_amount,
                             state_file=f"cmp_v5_{cfg['name']}.json")
    s5.backtest(daily_warmup, weekly, backtest_start=effective_start)
    s5._nav_curve = s5.calc_nav_curve(daily_warmup)
    s5._daily_df = daily_warmup
    s5._weekly_df = weekly
    results['V5'] = compute_metrics(s5, last_price)
    gen_report_v2(s5, daily_warmup, weekly, os.path.join(out_dir, f"v5_report_{cfg['name']}.html"))

    # V6
    s6 = WeeklyDCAStrategyV6(name=f"{cfg['name']}V6", base_amount=base_amount,
                             j_exit_threshold=j_exit_threshold_v4,
                             state_file=f"cmp_v6_{cfg['name']}.json")
    s6.backtest(daily_warmup, weekly, backtest_start=effective_start)
    s6._nav_curve = s6.calc_nav_curve(daily_warmup)
    s6._daily_df = daily_warmup
    s6._weekly_df = weekly
    results['V6'] = compute_metrics(s6, last_price)
    gen_report_v2(s6, daily_warmup, weekly, os.path.join(out_dir, f"v6_report_{cfg['name']}.html"))

    return {
        'name': cfg['name'],
        'last_date': last_date,
        'last_price': round(float(last_price), 4),
        'results': results,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description='定投策略 V1/V2/V4/V5/V6 对比')
    parser.add_argument('--config', type=str, default='etf_config.json')
    parser.add_argument('--years', type=int, default=5)
    parser.add_argument('--etf', type=str, default=None,
                        help='只对比指定ETF名称，如 红利低波')
    parser.add_argument('--j-exit-v4', type=float, default=20,
                        help='V4的日线跌破多空线全清的周线J阈值（默认20）')
    parser.add_argument('--start-date', type=str, default=None,
                        help='回测起始日期(YYYYMMDD)，如20240924；默认按years回溯')
    args = parser.parse_args()

    with open(args.config, 'r', encoding='utf-8') as f:
        config = json.load(f)
    etf_list = [e for e in config.get('etf_list', []) if e.get('enabled', True)]
    if args.etf:
        etf_list = [e for e in etf_list if e['name'] == args.etf]
    if not etf_list:
        logging.error("没有匹配的ETF")
        return

    out_dir = 'html/dca_compare'
    os.makedirs(out_dir, exist_ok=True)

    all_rows = []
    nav_series = {}
    for etf in etf_list:
        logging.info(f"\n=== 回测 {etf['name']} ({etf.get('ts_code','')}) ===")
        try:
            daily_warmup = fetch_data(etf, args.years)
            weekly = resample_to_weekly(daily_warmup)
            r = run_one(etf, args.years, out_dir, daily_warmup, weekly,
                        j_exit_threshold_v4=args.j_exit_v4,
                        start_date=args.start_date)
            all_rows.append(r)
            # 收集V2/V4 nav曲线用于对比图
            for ver in ['V2', 'V4']:
                fname = os.path.join(out_dir, f"{ver.lower()}_report_{etf['name']}.html")
                if os.path.exists(fname):
                    nav_series.setdefault(ver, {})[etf['name']] = fname
        except Exception as e:
            import traceback
            logging.error(f"{etf['name']} 回测失败: {e}")
            traceback.print_exc()

    # 控制台汇总
    print("\n" + "=" * 100)
    print(f"{'ETF':<12}{'版本':<6}{'总投入':>12}{'总卖出':>12}{'持仓市值':>12}{'总收益':>12}{'收益率%':>10}{'交易数':>8}")
    print("-" * 100)
    for r in all_rows:
        for ver in ['V1', 'V2', 'V4', 'V5', 'V6']:
            m = r['results'][ver]
            print(f"{r['name']:<12}{ver:<6}{m['total_invested']:>12,.0f}{m['total_sell']:>12,.0f}"
                  f"{m['market_value']:>12,.0f}{m['total_return']:>12,.0f}{m['return_pct']:>10,.2f}"
                  f"{m['trade_count']:>8}")
    print("=" * 100)

    # 平均收益对比
    print("\n各版本平均收益率：")
    for ver in ['V1', 'V2', 'V4', 'V5', 'V6']:
        vals = [r['results'][ver]['return_pct'] for r in all_rows if r['results'][ver]['total_invested'] > 0]
        if vals:
            print(f"  {ver}: {sum(vals)/len(vals):.2f}%  (n={len(vals)})")

    # 生成对比汇总JSON
    summary = {
        'generated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'rows': all_rows,
    }
    with open(os.path.join(out_dir, 'comparison.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
