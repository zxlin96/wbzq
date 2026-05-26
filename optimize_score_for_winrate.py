#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
胜率优化评分权重搜索 (optimize_score_for_winrate.py)

目标：找到使胜率最高的评分阈值组合。
方法：对每个维度搜索不同阈值，计算各阈值下的胜率，取最优切分点。
"""

import argparse
import logging
import math
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from tabulate import tabulate
from tqdm import tqdm

from config import BACKTEST_CONFIG as BT
from data_manager import DataManager
from main_par2 import (
    apply_strategy_marks,
    calculate_amount_rank,
    calculate_trend_indicators,
    calculate_zhixing_brick_indicator,
    fetch_and_prepare_data,
    get_nearest_trade_date,
)

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s | %(message)s")


def apply_c154_filter_per_day(df_day, basic, signal_date):
    cutoff_date = pd.to_datetime(signal_date) - pd.Timedelta(days=BT.MIN_STOCK_AGE_DAYS)
    basic_ld = basic.copy()
    basic_ld['list_date'] = pd.to_datetime(basic_ld['list_date'])
    non_new = basic_ld[basic_ld['list_date'] <= cutoff_date]['ts_code']
    df_f = df_day[df_day['ts_code'].isin(non_new)].copy()
    cond = (
        df_f['first_j13_step']
        & (df_f['pct_chg'].fillna(-100) >= 0)
        & ~df_f['has_distribution_signal']
        & ~df_f['has_distribution_signal_v2']
        & ~df_f['has_distribution_signal_v3']
        & df_f['has_bottom_violent_k']
        & (df_f['kdj_qfq'].fillna(100) < 5)
        & df_f['has_am_in_period']
    )
    return df_f[cond]


def preload_price_data(data_manager, trade_dates_list):
    fields = ["ts_code", "trade_date", "open_qfq", "high_qfq", "low_qfq", "close_qfq"]
    all_dates = sorted(set(trade_dates_list))
    full_df = data_manager.get_stock_factors(all_dates, fields)
    if full_df.empty:
        return {}
    full_df = full_df[fields].dropna(subset=["close_qfq"])
    price_lookup = {}
    for date, day_df in full_df.groupby("trade_date"):
        price_lookup[date] = day_df.set_index("ts_code")
    return price_lookup


def collect_trades(df, trade_dates_sorted, basic, price_lookup, hold_days):
    trade_date_idx_map = {d: i for i, d in enumerate(trade_dates_sorted)}
    df_by_date = {date: day_df for date, day_df in df.groupby("trade_date")}

    all_trades = []
    for signal_date in tqdm(trade_dates_sorted, desc="采集交易", leave=False):
        sig_idx = trade_date_idx_map[signal_date]
        buy_idx = sig_idx + 1
        if buy_idx >= len(trade_dates_sorted):
            continue
        buy_date = trade_dates_sorted[buy_idx]
        end_idx = min(buy_idx + hold_days, len(trade_dates_sorted) - 1)
        hold_dates = trade_dates_sorted[buy_idx:end_idx + 1]
        if len(hold_dates) < 2:
            continue
        df_day = df_by_date.get(signal_date)
        if df_day is None or df_day.empty:
            continue
        filtered = apply_c154_filter_per_day(df_day, basic, signal_date)
        if filtered.empty:
            continue

        buy_day_data = price_lookup.get(buy_date)
        if buy_day_data is None:
            continue

        for _, row in filtered.iterrows():
            ts_code = row['ts_code']
            if ts_code not in buy_day_data.index:
                continue
            buy_price = buy_day_data.loc[ts_code, 'open_qfq']
            if pd.isna(buy_price) or buy_price <= 0:
                continue
            final_price = None
            for d in hold_dates:
                day_data = price_lookup.get(d)
                if day_data is None or ts_code not in day_data.index:
                    continue
                close = day_data.loc[ts_code, 'close_qfq']
                if not pd.isna(close):
                    final_price = close
            if final_price is None:
                continue
            gain_pct = round((final_price - buy_price) / buy_price * 100, 2)

            hi = row.get('high_qfq', np.nan)
            lo = row.get('low_qfq', np.nan)
            cl = row.get('close_qfq', np.nan)
            op = row.get('open_qfq', np.nan)
            rng = hi - lo if pd.notna(hi) and pd.notna(lo) else 0
            body_ratio = abs(cl - op) / rng if rng > 0 and pd.notna(cl) and pd.notna(op) else 0

            ma60 = row.get('ma_qfq_60', np.nan)
            close_vs_ma60 = (cl - ma60) / ma60 * 100 if pd.notna(cl) and pd.notna(ma60) and ma60 > 0 else np.nan

            all_trades.append({
                'ts_code': ts_code,
                'signal_date': signal_date,
                'gain_pct': gain_pct,
                'win': 1 if gain_pct > 0 else 0,
                'big_win': 1 if gain_pct > 3 else 0,
                'total_mv': row.get('total_mv', np.nan),
                'turnover_rate': row.get('turnover_rate', np.nan),
                'pct_chg': row.get('pct_chg', np.nan),
                'volume_ratio': row.get('volume_ratio', np.nan),
                'kdj_qfq': row.get('kdj_qfq', np.nan),
                'body_ratio': body_ratio,
                'close_vs_ma60': close_vs_ma60,
                'amount': row.get('amount', np.nan),
            })

    return pd.DataFrame(all_trades)


def search_optimal_thresholds(trades_df):
    print('\n' + '=' * 80)
    print('各维度不同阈值的胜率搜索')
    print('=' * 80)

    dims = {
        'total_mv': {
            'label': '总市值',
            'type': 'lower_better',
            'quantiles': [0.1, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.7],
            'unit': '万',
            'scale': 1/10000,
        },
        'turnover_rate': {
            'label': '换手率',
            'type': 'range',
            'ranges': [(0, 1), (1, 2), (2, 3), (3, 5), (5, 8), (8, 100)],
            'unit': '%',
        },
        'pct_chg': {
            'label': '当日涨跌幅',
            'type': 'range',
            'ranges': [(-1, 0), (0, 0.5), (0.5, 1), (1, 2), (2, 3), (3, 5), (5, 20)],
            'unit': '%',
        },
        'volume_ratio': {
            'label': '量比',
            'type': 'range',
            'ranges': [(0, 0.5), (0.5, 0.8), (0.8, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 10)],
            'unit': '',
        },
        'kdj_qfq': {
            'label': 'J值',
            'type': 'range',
            'ranges': [(-50, -10), (-10, -5), (-5, -2), (-2, 0), (0, 2), (2, 5)],
            'unit': '',
        },
        'body_ratio': {
            'label': '实体比例',
            'type': 'range',
            'ranges': [(0, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.4), (0.4, 0.5), (0.5, 1.0)],
            'unit': '',
        },
        'close_vs_ma60': {
            'label': '距MA60',
            'type': 'range',
            'ranges': [(-20, -5), (-5, -2), (-2, 0), (0, 2), (2, 5), (5, 10), (10, 50)],
            'unit': '%',
        },
        'amount': {
            'label': '成交额',
            'type': 'quantile_range',
            'quantiles': [0.1, 0.25, 0.5, 0.75, 0.9],
            'unit': '万',
            'scale': 1/10000,
        },
    }

    best_conditions = {}

    for dim, cfg in dims.items():
        print(f'\n--- {cfg["label"]} ---')
        rows = []
        if cfg['type'] == 'lower_better':
            for q in cfg['quantiles']:
                threshold = trades_df[dim].quantile(q)
                mask = trades_df[dim] <= threshold
                subset = trades_df[mask]
                if len(subset) < 20:
                    continue
                wr = subset['win'].mean() * 100
                bwr = subset['big_win'].mean() * 100
                avg = subset['gain_pct'].mean()
                scale = cfg.get('scale', 1)
                rows.append({
                    '条件': f'<= {threshold*scale:.0f}{cfg["unit"]} (P{int(q*100)})',
                    '样本量': len(subset),
                    '胜率': f'{wr:.1f}%',
                    '大胜率': f'{bwr:.1f}%',
                    '平均涨幅': f'{avg:.2f}%',
                })
        elif cfg['type'] == 'range':
            for lo, hi in cfg['ranges']:
                mask = (trades_df[dim] >= lo) & (trades_df[dim] < hi)
                subset = trades_df[mask]
                if len(subset) < 20:
                    continue
                wr = subset['win'].mean() * 100
                bwr = subset['big_win'].mean() * 100
                avg = subset['gain_pct'].mean()
                rows.append({
                    '条件': f'{lo}~{hi}{cfg["unit"]}',
                    '样本量': len(subset),
                    '胜率': f'{wr:.1f}%',
                    '大胜率': f'{bwr:.1f}%',
                    '平均涨幅': f'{avg:.2f}%',
                })
        elif cfg['type'] == 'quantile_range':
            qs = cfg['quantiles']
            boundaries = [trades_df[dim].quantile(q) for q in qs]
            for i in range(len(boundaries) + 1):
                if i == 0:
                    lo_val = 0
                    hi_val = boundaries[0]
                    label = f'< P{int(qs[0]*100)}'
                elif i == len(boundaries):
                    lo_val = boundaries[-1]
                    hi_val = float('inf')
                    label = f'> P{int(qs[-1]*100)}'
                else:
                    lo_val = boundaries[i-1]
                    hi_val = boundaries[i]
                    label = f'P{int(qs[i-1]*100)}~P{int(qs[i]*100)}'
                mask = (trades_df[dim] >= lo_val) & (trades_df[dim] < hi_val)
                subset = trades_df[mask]
                if len(subset) < 20:
                    continue
                wr = subset['win'].mean() * 100
                bwr = subset['big_win'].mean() * 100
                avg = subset['gain_pct'].mean()
                rows.append({
                    '条件': label,
                    '样本量': len(subset),
                    '胜率': f'{wr:.1f}%',
                    '大胜率': f'{bwr:.1f}%',
                    '平均涨幅': f'{avg:.2f}%',
                })

        print(tabulate(rows, headers='keys', tablefmt='github', showindex=False))

        best_wr = 0
        best_row = None
        for r in rows:
            wr_val = float(r['胜率'].replace('%', ''))
            n = r['样本量']
            if n >= 30 and wr_val > best_wr:
                best_wr = wr_val
                best_row = r
        if best_row:
            best_conditions[dim] = {'condition': best_row['条件'], 'winrate': best_row['胜率']}

    print('\n' + '=' * 80)
    print('各维度胜率最优条件汇总')
    print('=' * 80)
    summary_rows = []
    for dim, info in best_conditions.items():
        summary_rows.append({'维度': dim, '最优条件': info['condition'], '胜率': info['winrate']})
    print(tabulate(summary_rows, headers='keys', tablefmt='github', showindex=False))

    return best_conditions


def search_condition_combos(trades_df):
    print('\n' + '=' * 80)
    print('条件组合胜率搜索')
    print('=' * 80)

    combos = [
        {'name': '基准(全选)', 'mask': pd.Series(True, index=trades_df.index)},
        {'name': '小市值(<=P30)', 'mask': trades_df['total_mv'] <= trades_df['total_mv'].quantile(0.3)},
        {'name': '换手1~5%', 'mask': (trades_df['turnover_rate'] >= 1) & (trades_df['turnover_rate'] < 5)},
        {'name': '涨幅0.5~3%', 'mask': (trades_df['pct_chg'] >= 0.5) & (trades_df['pct_chg'] < 3)},
        {'name': '量比0.5~1.0', 'mask': (trades_df['volume_ratio'] >= 0.5) & (trades_df['volume_ratio'] < 1.0)},
        {'name': 'J值-5~2', 'mask': (trades_df['kdj_qfq'] >= -5) & (trades_df['kdj_qfq'] < 2)},
        {'name': '实体>=0.3', 'mask': trades_df['body_ratio'] >= 0.3},
        {'name': '距MA60 -2~5%', 'mask': (trades_df['close_vs_ma60'] >= -2) & (trades_df['close_vs_ma60'] < 5)},
    ]

    progressive_masks = [
        ('+小市值', trades_df['total_mv'] <= trades_df['total_mv'].quantile(0.3)),
        ('+换手1~5%', (trades_df['turnover_rate'] >= 1) & (trades_df['turnover_rate'] < 5)),
        ('+涨幅>=0.5', trades_df['pct_chg'] >= 0.5),
        ('+量比<1.0', trades_df['volume_ratio'] < 1.0),
        ('+J>=-5', trades_df['kdj_qfq'] >= -5),
        ('+实体>=0.2', trades_df['body_ratio'] >= 0.2),
        ('+距MA60>-2', trades_df['close_vs_ma60'] > -2),
    ]

    print('\n--- 单条件胜率 ---')
    rows = []
    for c in combos:
        subset = trades_df[c['mask']]
        if len(subset) < 10:
            continue
        wr = subset['win'].mean() * 100
        bwr = subset['big_win'].mean() * 100
        avg = subset['gain_pct'].mean()
        rows.append({
            '条件': c['name'],
            '样本量': len(subset),
            '胜率': f'{wr:.1f}%',
            '大胜率': f'{bwr:.1f}%',
            '平均涨幅': f'{avg:.2f}%',
        })
    print(tabulate(rows, headers='keys', tablefmt='github', showindex=False))

    print('\n--- 递增条件组合（AND叠加）---')
    cum_mask = pd.Series(True, index=trades_df.index)
    combo_rows = []
    for name, mask in progressive_masks:
        cum_mask = cum_mask & mask
        subset = trades_df[cum_mask]
        if len(subset) < 10:
            combo_rows.append({'条件': name, '样本量': len(subset), '胜率': 'N/A', '大胜率': 'N/A', '平均涨幅': 'N/A'})
            continue
        wr = subset['win'].mean() * 100
        bwr = subset['big_win'].mean() * 100
        avg = subset['gain_pct'].mean()
        combo_rows.append({
            '条件': name,
            '样本量': len(subset),
            '胜率': f'{wr:.1f}%',
            '大胜率': f'{bwr:.1f}%',
            '平均涨幅': f'{avg:.2f}%',
        })
    print(tabulate(combo_rows, headers='keys', tablefmt='github', showindex=False))

    print('\n--- 全量穷举Top组合（按胜率排序，样本>=30）---')
    top_rows = []
    all_combos = [
        ('小盘+换手1~5', (trades_df['total_mv'] <= trades_df['total_mv'].quantile(0.3)) & ((trades_df['turnover_rate'] >= 1) & (trades_df['turnover_rate'] < 5))),
        ('小盘+涨幅>=1', (trades_df['total_mv'] <= trades_df['total_mv'].quantile(0.3)) & (trades_df['pct_chg'] >= 1)),
        ('小盘+量比<1', (trades_df['total_mv'] <= trades_df['total_mv'].quantile(0.3)) & (trades_df['volume_ratio'] < 1.0)),
        ('小盘+J>=-5', (trades_df['total_mv'] <= trades_df['total_mv'].quantile(0.3)) & (trades_df['kdj_qfq'] >= -5)),
        ('小盘+实体>=0.3', (trades_df['total_mv'] <= trades_df['total_mv'].quantile(0.3)) & (trades_df['body_ratio'] >= 0.3)),
        ('换手1~5+涨幅>=1', ((trades_df['turnover_rate'] >= 1) & (trades_df['turnover_rate'] < 5)) & (trades_df['pct_chg'] >= 1)),
        ('换手1~5+量比<1', ((trades_df['turnover_rate'] >= 1) & (trades_df['turnover_rate'] < 5)) & (trades_df['volume_ratio'] < 1.0)),
        ('小盘+换手1~5+涨幅>=1', (trades_df['total_mv'] <= trades_df['total_mv'].quantile(0.3)) & ((trades_df['turnover_rate'] >= 1) & (trades_df['turnover_rate'] < 5)) & (trades_df['pct_chg'] >= 1)),
        ('小盘+换手1~5+量比<1', (trades_df['total_mv'] <= trades_df['total_mv'].quantile(0.3)) & ((trades_df['turnover_rate'] >= 1) & (trades_df['turnover_rate'] < 5)) & (trades_df['volume_ratio'] < 1.0)),
        ('小盘+涨幅>=1+实体>=0.3', (trades_df['total_mv'] <= trades_df['total_mv'].quantile(0.3)) & (trades_df['pct_chg'] >= 1) & (trades_df['body_ratio'] >= 0.3)),
        ('小盘+换手1~5+涨幅0.5~3+实体>=0.2', (trades_df['total_mv'] <= trades_df['total_mv'].quantile(0.3)) & ((trades_df['turnover_rate'] >= 1) & (trades_df['turnover_rate'] < 5)) & ((trades_df['pct_chg'] >= 0.5) & (trades_df['pct_chg'] < 3)) & (trades_df['body_ratio'] >= 0.2)),
        ('小盘+换手1~5+涨幅>=0.5+量比<1', (trades_df['total_mv'] <= trades_df['total_mv'].quantile(0.3)) & ((trades_df['turnover_rate'] >= 1) & (trades_df['turnover_rate'] < 5)) & (trades_df['pct_chg'] >= 0.5) & (trades_df['volume_ratio'] < 1.0)),
        ('小盘+换手1~5+涨幅>=0.5+J>=-5+实体>=0.2', (trades_df['total_mv'] <= trades_df['total_mv'].quantile(0.3)) & ((trades_df['turnover_rate'] >= 1) & (trades_df['turnover_rate'] < 5)) & (trades_df['pct_chg'] >= 0.5) & (trades_df['kdj_qfq'] >= -5) & (trades_df['body_ratio'] >= 0.2)),
        ('小盘+换手1~5+涨幅0.5~3+量比<1+实体>=0.2', (trades_df['total_mv'] <= trades_df['total_mv'].quantile(0.3)) & ((trades_df['turnover_rate'] >= 1) & (trades_df['turnover_rate'] < 5)) & ((trades_df['pct_chg'] >= 0.5) & (trades_df['pct_chg'] < 3)) & (trades_df['volume_ratio'] < 1.0) & (trades_df['body_ratio'] >= 0.2)),
    ]
    for name, mask in all_combos:
        subset = trades_df[mask]
        if len(subset) < 10:
            continue
        wr = subset['win'].mean() * 100
        bwr = subset['big_win'].mean() * 100
        avg = subset['gain_pct'].mean()
        top_rows.append({'组合': name, '样本量': len(subset), '胜率': wr, '大胜率': f'{bwr:.1f}%', '平均涨幅': f'{avg:.2f}%'})

    top_rows.sort(key=lambda x: x['胜率'], reverse=True)
    for r in top_rows:
        r['胜率'] = f'{r["胜率"]:.1f}%'
    print(tabulate(top_rows, headers='keys', tablefmt='github', showindex=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=250)
    parser.add_argument("--hold-days", type=int, default=3)
    args = parser.parse_args()
    data_manager = DataManager()

    try:
        end_date = get_nearest_trade_date(data_manager, datetime.now())
        if not end_date:
            return

        start_dt = datetime.strptime(end_date, '%Y%m%d') - timedelta(days=args.days * 2)
        trade_dates_all = sorted(data_manager.get_trade_dates(start_dt.strftime('%Y%m%d'), end_date))
        backtest_dates = trade_dates_all[-args.days:]
        df_full = fetch_and_prepare_data(data_manager, trade_dates_all)
        available_dates = sorted(df_full['trade_date'].unique())
        backtest_dates = [d for d in backtest_dates if d in set(available_dates)]

        df = df_full[df_full['trade_date'] >= backtest_dates[0]].copy()
        basic_info = data_manager.get_stock_basic_info()
        basic_info['name'] = basic_info.get('name', basic_info['ts_code']).fillna(basic_info['ts_code'])
        basic = basic_info[basic_info['list_date'].notna()].copy()
        df = df.merge(basic[['ts_code', 'name']], on='ts_code', how='left')
        df = apply_strategy_marks(df)
        df = calculate_trend_indicators(df)
        df = calculate_zhixing_brick_indicator(df)
        df = calculate_amount_rank(df)

        extended_dates = data_manager.get_trade_dates(
            backtest_dates[0],
            (datetime.strptime(backtest_dates[-1], '%Y%m%d') + timedelta(days=args.hold_days + 10)).strftime('%Y%m%d')
        )
        price_lookup = preload_price_data(data_manager, extended_dates)

        trades_df = collect_trades(df, backtest_dates, basic, price_lookup, args.hold_days)
        logging.info("共采集 %d 笔交易", len(trades_df))

        if trades_df.empty:
            return

        print(f'\n总体: {len(trades_df)} 笔, 胜率={trades_df["win"].mean()*100:.1f}%, 大胜率={trades_df["big_win"].mean()*100:.1f}%, 平均涨幅={trades_df["gain_pct"].mean():.2f}%')

        best = search_optimal_thresholds(trades_df)
        search_condition_combos(trades_df)

    finally:
        data_manager.close()


if __name__ == "__main__":
    main()
