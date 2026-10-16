#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
"低估值 + 4%定投法"回测：给原版4%法加估值门是否有效？

动机：原版4%定投法是纯价格策略——台阶只看"较上次买入再跌4%"，不看"当前贵不贵"。
价格位置≠估值（深跌可能仍贵，未跌可能已便宜）。社区改良版（雪球）为
"当指数进入低估值后，资金分10份，每下跌4%补仓一次"，即把估值作为开轮门。

本脚本验证（A股宽基，有指数PE数据的6只）：
  - 无门4%法（基准）
  - 估值门版：PE_TTM 滚动分位 < 50% / < 30% 时才允许开启新一轮
    （轮内台阶补仓不受门限制——补仓天然发生在更低位置）
  - 对照：每周定投
  PE分位 = 当日PE_TTM在滚动1250个交易日（约5年）中的百分位，不足250天不设限。

资金模型与 backtest_4pct_dca.py 完全一致（2万池、XIRR口径）。

用法：
  python backtest_4pct_valuation.py              # 5年窗口
  python backtest_4pct_valuation.py --start 20180101   # 更长窗口（沪深300等PE数据自2014起）
输出：控制台表格 + html/dca_4pct/valuation_results.json
"""

import os
import json
import time
import logging
import argparse
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import tushare as ts

from config import APIConfig
from backtest_4pct_dca import (
    load_etf_daily, run_fourpct, PoolWeeklyDCA,
    DATA_CACHE_DIR, REPORT_DIR,
)

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s | %(message)s')
logger = logging.getLogger(__name__)

# ETF -> 对应宽基指数（index_dailybasic 可查询的）
ETF_INDEX = [
    ('510300.SH', '沪深300', '000300.SH'),
    ('510050.SH', '上证50', '000016.SH'),
    ('510500.SH', '中证500', '000905.SH'),
    ('512100.SH', '中证1000', '000852.SH'),
    ('159915.SZ', '创业板指', '399006.SZ'),
    ('588000.SH', '科创50', '000688.SH'),
]

PE_PCT_WINDOW = 1250   # 滚动5年
PE_PCT_MIN = 250       # 历史不足250日时不设限


def load_index_pe(index_code: str, refresh: bool = False) -> pd.DataFrame:
    """获取指数PE_TTM日序列（本地缓存）"""
    os.makedirs(DATA_CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(DATA_CACHE_DIR, f'pe_{index_code.replace(".", "_")}.csv')
    if not refresh and os.path.exists(cache_file):
        df = pd.read_csv(cache_file, dtype={'trade_date': str})
        if (datetime.now() - datetime.strptime(df['trade_date'].iloc[-1], '%Y%m%d')).days <= 7:
            return df
    ts.set_token(APIConfig.get_token())
    pro = ts.pro_api()
    dfs, end = [], datetime.now().strftime('%Y%m%d')
    while True:
        d = pro.index_dailybasic(ts_code=index_code, start_date='20100101', end_date=end,
                                 fields='trade_date,pe_ttm')
        if d is None or d.empty:
            break
        dfs.append(d)
        if len(d) < 3000:
            break
        end = (pd.to_datetime(d['trade_date'].min()) - timedelta(days=1)).strftime('%Y%m%d')
    df = pd.concat(dfs, ignore_index=True).drop_duplicates('trade_date')
    df = df.sort_values('trade_date').reset_index(drop=True)
    df.to_csv(cache_file, index=False)
    time.sleep(0.2)
    return df


def pe_percentile(pe_df: pd.DataFrame) -> pd.DataFrame:
    """滚动PE分位：当日PE在过去1250个交易日中的百分位"""
    pe_df = pe_df.sort_values('trade_date').reset_index(drop=True)
    pe_df['pe_pct'] = pe_df['pe_ttm'].rolling(PE_PCT_WINDOW, min_periods=PE_PCT_MIN).apply(
        lambda a: (a < a[-1]).mean(), raw=True)
    return pe_df[['trade_date', 'pe_ttm', 'pe_pct']]


def build_gate(df: pd.DataFrame, pe_df: pd.DataFrame, threshold: float):
    """按阈值生成逐日开轮门（True=允许开轮）；PE分位缺失日不设限"""
    m = df[['trade_date']].merge(pe_df, on='trade_date', how='left')
    pct = m['pe_pct']
    gate = (pct < threshold).fillna(True).values
    return gate


def main():
    ap = argparse.ArgumentParser(description='低估值+4%定投法回测')
    ap.add_argument('--start', default=(datetime.now() - pd.Timedelta(days=5 * 365)).strftime('%Y%m%d'))
    ap.add_argument('--budget', type=float, default=20000.0)
    ap.add_argument('--refresh', action='store_true')
    args = ap.parse_args()

    rows = []
    detail = {}
    for etf_code, name, index_code in ETF_INDEX:
        try:
            df = load_etf_daily(etf_code, args.start, datetime.now().strftime('%Y%m%d'))
            pe_raw = load_index_pe(index_code, refresh=args.refresh)
        except Exception as e:
            logger.error(f'{name} 数据失败: {e}')
            continue
        pe_df = pe_percentile(pe_raw)
        sim = df[df['trade_date'] >= args.start].reset_index(drop=True)
        if len(sim) < 250:
            logger.warning(f'{name} 窗口不足，跳过')
            continue

        base_kw = dict(initial_capital=args.budget, round_budget=args.budget)
        variants = {
            '4%法无门': run_fourpct(sim, **base_kw),
            '4%法+PE<50%': run_fourpct(sim, gate=build_gate(sim, pe_df, 0.50), **base_kw),
            '4%法+PE<30%': run_fourpct(sim, gate=build_gate(sim, pe_df, 0.30), **base_kw),
            '每周定投': PoolWeeklyDCA(args.budget, 104).run(sim),
        }
        cur_pe = pe_df[pe_df['trade_date'] <= sim['trade_date'].iloc[-1]]['pe_ttm'].iloc[-1]
        cur_pct = pe_df[pe_df['trade_date'] <= sim['trade_date'].iloc[-1]]['pe_pct'].iloc[-1]

        row = {'ETF': name}
        drow = {'ts_code': etf_code, 'index': index_code,
                'cur_pe': round(float(cur_pe), 1), 'cur_pe_pct': round(float(cur_pct) * 100, 0)}
        for label, r in variants.items():
            row[f'{label}|XIRR%'] = r['xirr_pct']
            row[f'{label}|空仓日'] = r.get('flat_days', 0)
            drow[label] = {'xirr': r['xirr_pct'], 'final': r['final_assets'],
                           'trades': r['n_trades'], 'rounds': r['n_rounds'],
                           'flat_days': r.get('flat_days', 0)}
        buys = [t for t in variants['4%法+PE<50%']['trades'] if t['action'] == 'BUY']
        first_buy = buys[0]['date'] if buys else '从未开轮'
        row['当前PE'] = drow['cur_pe']
        row['当前PE分位%'] = drow['cur_pe_pct']
        row['PE<50%首次开轮'] = first_buy
        drow['first_buy_50'] = first_buy
        rows.append(row)
        detail[name] = drow

    out = pd.DataFrame(rows)
    pd.set_option('display.width', 250)
    print('\n' + '=' * 130)
    print(f'低估值+4%定投法回测  区间 {args.start}~今  初始资金 {args.budget:.0f} 元')
    print(f'估值门：PE_TTM 滚动{PE_PCT_WINDOW}日分位低于阈值才允许开启新一轮')
    print('=' * 130)
    cols = ['ETF', '当前PE', '当前PE分位%',
            '4%法无门|XIRR%', '4%法+PE<50%|XIRR%', '4%法+PE<30%|XIRR%', '每周定投|XIRR%',
            '4%法+PE<50%|空仓日', 'PE<50%首次开轮']
    print(out[cols].round(2).to_string(index=False))

    avg = {
        '4%法无门': out['4%法无门|XIRR%'].mean(),
        '4%法+PE<50%': out['4%法+PE<50%|XIRR%'].mean(),
        '4%法+PE<30%': out['4%法+PE<30%|XIRR%'].mean(),
        '每周定投': out['每周定投|XIRR%'].mean(),
    }
    print('\n平均 XIRR%: ' + '  '.join(f'{k}={v:.2f}' for k, v in avg.items()))

    os.makedirs(REPORT_DIR, exist_ok=True)
    out_path = os.path.join(REPORT_DIR, 'valuation_results.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({'params': vars(args), 'avg_xirr': avg, 'detail': detail},
                  f, ensure_ascii=False, indent=1, default=str)
    logger.info(f'已保存 {out_path}')


if __name__ == '__main__':
    main()
