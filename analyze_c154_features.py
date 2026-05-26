#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
C154 大胜特征分析 (analyze_c154_features.py)

对比大胜(>3%) vs 亏损(<0%) 交易，分析信号日当天的指标差异。
直接复用 backtest_c154 的数据管道，在回测过程中提取特征。

使用方式：
    python analyze_c154_features.py                 # 默认250天
    python analyze_c154_features.py --days 120
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
    STOCK_FACTOR_FIELDS,
    apply_strategy_marks,
    calculate_amount_rank,
    calculate_trend_indicators,
    calculate_zhixing_brick_indicator,
    fetch_and_prepare_data,
    get_nearest_trade_date,
)

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s | %(message)s")

FEATURE_COLS = [
    'kdj_qfq', 'kdj_k_qfq', 'kdj_d_qfq',
    'macd_dif_qfq', 'macd_dea_qfq', 'macd_qfq',
    'pct_chg', 'amount', 'turnover_rate',
    'close_qfq', 'ma_qfq_5', 'ma_qfq_10', 'ma_qfq_20', 'ma_qfq_60',
    'volume_ratio', 'pe_ttm', 'pb', 'total_mv', 'circ_mv',
    'amplitude', 'open_qfq', 'high_qfq', 'low_qfq',
]

RATIO_COLS_TO_COMPUTE = [
    'close_vs_ma5', 'close_vs_ma10', 'close_vs_ma20', 'close_vs_ma60',
    'body_ratio', 'upper_shadow_ratio', 'lower_shadow_ratio',
]


def parse_args():
    parser = argparse.ArgumentParser(description="C154大胜特征分析")
    parser.add_argument("--date", type=str, default=None)
    parser.add_argument("--days", type=int, default=250)
    parser.add_argument("--hold-days", type=int, default=3)
    return parser.parse_args()


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
    logging.info("预加载价格数据: %d 个交易日...", len(all_dates))
    t0 = time.time()
    full_df = data_manager.get_stock_factors(all_dates, fields)
    if full_df.empty:
        return {}
    full_df = full_df[fields].dropna(subset=["close_qfq"])
    price_lookup = {}
    for date, day_df in full_df.groupby("trade_date"):
        price_lookup[date] = day_df.set_index("ts_code")
    logging.info("预加载完成: %d 天, %.1f 秒", len(price_lookup), time.time() - t0)
    return price_lookup


def compute_derived_features(row):
    features = {}
    close = row.get('close_qfq', np.nan)
    for ma_col in ['ma_qfq_5', 'ma_qfq_10', 'ma_qfq_20', 'ma_qfq_60']:
        ma = row.get(ma_col, np.nan)
        if pd.notna(close) and pd.notna(ma) and ma > 0:
            features[f'close_vs_{ma_col}'] = (close - ma) / ma * 100
        else:
            features[f'close_vs_{ma_col}'] = np.nan

    op = row.get('open_qfq', np.nan)
    hi = row.get('high_qfq', np.nan)
    lo = row.get('low_qfq', np.nan)
    if pd.notna(hi) and pd.notna(lo) and hi > lo:
        body = abs(close - op)
        features['body_ratio'] = body / (hi - lo)
        features['upper_shadow_ratio'] = (hi - max(close, op)) / (hi - lo)
        features['lower_shadow_ratio'] = (min(close, op) - lo) / (hi - lo)
    else:
        features['body_ratio'] = np.nan
        features['upper_shadow_ratio'] = np.nan
        features['lower_shadow_ratio'] = np.nan

    return features


def run_analysis(df, trade_dates_sorted, basic, price_lookup, hold_days):
    trade_date_idx_map = {d: i for i, d in enumerate(trade_dates_sorted)}
    df_by_date = {date: day_df for date, day_df in df.groupby("trade_date")}

    all_trades = []

    for signal_date in tqdm(trade_dates_sorted, desc="逐日分析", leave=False):
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

            trade_info = {'ts_code': ts_code, 'signal_date': signal_date, 'gain_pct': gain_pct}

            for col in FEATURE_COLS:
                trade_info[col] = row.get(col, np.nan)

            derived = compute_derived_features(row)
            trade_info.update(derived)

            trade_info['has_bottom_violent_k'] = row.get('has_bottom_violent_k', False)
            trade_info['volume_surge_any'] = row.get('volume_surge_any', False)
            trade_info['ma60_upward'] = row.get('ma60_upward', False)
            trade_info['amount_top'] = row.get('amount_top', False)
            trade_info['zhixing_brick_rising'] = row.get('zhixing_brick_rising', False)
            trade_info['zhixing_brick_xg'] = row.get('zhixing_brick_xg', False)

            all_trades.append(trade_info)

    return pd.DataFrame(all_trades)


def analyze_features(trades_df):
    wins_big = trades_df[trades_df['gain_pct'] > 3].copy()
    losses = trades_df[trades_df['gain_pct'] < 0].copy()
    small_wins = trades_df[(trades_df['gain_pct'] > 0) & (trades_df['gain_pct'] <= 3)].copy()

    print('\n' + '=' * 80)
    print('C154 大胜特征分析')
    print('=' * 80)

    groups = {'大胜(>3%)': wins_big, '小胜(0~3%)': small_wins, '亏损(<0%)': losses}
    print(f'\n样本分布:')
    for name, g in groups.items():
        print(f'  {name}: {len(g)} 笔 ({len(g)/len(trades_df)*100:.1f}%)')

    numeric_cols = FEATURE_COLS + ['close_vs_ma5', 'close_vs_ma10', 'close_vs_ma20', 'close_vs_ma60',
                                    'body_ratio', 'lower_shadow_ratio', 'upper_shadow_ratio']

    print('\n' + '=' * 80)
    print('连续指标对比 (均值 ± 标准差)')
    print('=' * 80)

    rows = []
    for col in numeric_cols:
        if col not in trades_df.columns:
            continue
        big_vals = wins_big[col].dropna()
        loss_vals = losses[col].dropna()
        all_vals = trades_df[col].dropna()
        if len(big_vals) < 5 or len(loss_vals) < 5:
            continue

        big_mean = big_vals.mean()
        loss_mean = loss_vals.mean()
        diff = big_mean - loss_mean

        if len(big_vals) > 30 and len(loss_vals) > 30:
            se = np.sqrt(big_vals.var(ddof=1)/len(big_vals) + loss_vals.var(ddof=1)/len(loss_vals))
            if se > 0:
                z_val = abs(diff) / se
                p_val = 2 * (1 - 0.5 * (1 + math.erf(z_val / math.sqrt(2))))
            else:
                p_val = np.nan
        else:
            p_val = np.nan

        sig = ''
        if pd.notna(p_val):
            if p_val < 0.01:
                sig = '***'
            elif p_val < 0.05:
                sig = '**'
            elif p_val < 0.1:
                sig = '*'

        rows.append({
            '指标': col,
            '大胜(>3%)': f'{big_mean:.2f} ± {big_vals.std():.2f}',
            '亏损(<0%)': f'{loss_mean:.2f} ± {loss_vals.std():.2f}',
            '差值': f'{diff:+.2f}',
            'P值': f'{p_val:.3f}' if pd.notna(p_val) else 'N/A',
            '显著': sig,
        })

    print(tabulate(rows, headers='keys', tablefmt='github', showindex=False))

    bool_cols = ['has_bottom_violent_k', 'volume_surge_any', 'ma60_upward', 'amount_top',
                 'zhixing_brick_rising', 'zhixing_brick_xg']

    print('\n' + '=' * 80)
    print('布尔指标对比 (占比%)')
    print('=' * 80)

    bool_rows = []
    for col in bool_cols:
        if col not in trades_df.columns:
            continue
        big_pct = wins_big[col].mean() * 100 if len(wins_big) > 0 else 0
        loss_pct = losses[col].mean() * 100 if len(losses) > 0 else 0
        diff = big_pct - loss_pct

        a, b = int(wins_big[col].sum()), int(len(wins_big) - wins_big[col].sum())
        c, d = int(losses[col].sum()), int(len(losses) - losses[col].sum())
        n_total = a + b + c + d
        if n_total > 0 and min(a, b, c, d) >= 5:
            chi2 = n_total * (a*d - b*c)**2 / ((a+b)*(c+d)*(a+c)*(b+d))
            p_val = np.exp(-chi2 / 2)
        else:
            chi2, p_val = np.nan, np.nan

        sig = ''
        if pd.notna(p_val):
            if p_val < 0.01:
                sig = '***'
            elif p_val < 0.05:
                sig = '**'
            elif p_val < 0.1:
                sig = '*'

        bool_rows.append({
            '指标': col,
            '大胜(>3%)': f'{big_pct:.1f}%',
            '亏损(<0%)': f'{loss_pct:.1f}%',
            '差值': f'{diff:+.1f}pp',
            'P值': f'{p_val:.3f}' if pd.notna(p_val) else 'N/A',
            '显著': sig,
        })

    print(tabulate(bool_rows, headers='keys', tablefmt='github', showindex=False))

    print('\n' + '=' * 80)
    print('大胜交易详情')
    print('=' * 80)
    detail_cols = ['ts_code', 'signal_date', 'gain_pct', 'kdj_qfq', 'macd_dif_qfq',
                   'pct_chg', 'amount', 'close_vs_ma60', 'body_ratio', 'lower_shadow_ratio',
                   'total_mv', 'turnover_rate', 'volume_ratio']
    existing = [c for c in detail_cols if c in wins_big.columns]
    print(tabulate(wins_big[existing].sort_values('gain_pct', ascending=False).reset_index(drop=True),
                   headers=existing, tablefmt='github', showindex=False))

    print('\n' + '=' * 80)
    print('J值分位段统计')
    print('=' * 80)
    trades_df['j_bin'] = pd.cut(trades_df['kdj_qfq'], bins=[-50, -10, -5, -2, 0, 2, 5],
                                labels=['<-10', '-10~-5', '-5~-2', '-2~0', '0~2', '2~5'])
    j_stats = trades_df.groupby('j_bin', observed=True).agg(
        笔数=('gain_pct', 'count'),
        平均涨幅=('gain_pct', 'mean'),
        大胜率=('gain_pct', lambda x: (x > 3).mean() * 100),
        胜率=('gain_pct', lambda x: (x > 0).mean() * 100),
    ).round(2)
    print(tabulate(j_stats.reset_index(), headers=['J值区间', '笔数', '平均涨幅%', '大胜率%', '胜率%'],
                   tablefmt='github', showindex=False))

    print('\n' + '=' * 80)
    print('涨跌幅分位段统计')
    print('=' * 80)
    trades_df['pct_bin'] = pd.cut(trades_df['pct_chg'], bins=[-1, 0, 1, 2, 3, 5, 20],
                                  labels=['0%以下', '0~1%', '1~2%', '2~3%', '3~5%', '>5%'])
    pct_stats = trades_df.groupby('pct_bin', observed=True).agg(
        笔数=('gain_pct', 'count'),
        平均涨幅=('gain_pct', 'mean'),
        大胜率=('gain_pct', lambda x: (x > 3).mean() * 100),
        胜率=('gain_pct', lambda x: (x > 0).mean() * 100),
    ).round(2)
    print(tabulate(pct_stats.reset_index(), headers=['当日涨跌%', '笔数', '持有涨幅%', '大胜率%', '胜率%'],
                   tablefmt='github', showindex=False))

    print('\n' + '=' * 80)
    print('市值分位段统计')
    print('=' * 80)
    trades_df['mv_bin'] = pd.qcut(trades_df['total_mv'], q=4, labels=['小盘', '中小盘', '中大盘', '大盘'],
                                  duplicates='drop')
    mv_stats = trades_df.groupby('mv_bin', observed=True).agg(
        笔数=('gain_pct', 'count'),
        平均涨幅=('gain_pct', 'mean'),
        大胜率=('gain_pct', lambda x: (x > 3).mean() * 100),
        胜率=('gain_pct', lambda x: (x > 0).mean() * 100),
    ).round(2)
    print(tabulate(mv_stats.reset_index(), headers=['市值段', '笔数', '持有涨幅%', '大胜率%', '胜率%'],
                   tablefmt='github', showindex=False))

    print('\n' + '=' * 80)
    print('换手率分位段统计')
    print('=' * 80)
    trades_df['turnover_bin'] = pd.qcut(trades_df['turnover_rate'], q=4,
                                        labels=['低换手', '中低换手', '中高换手', '高换手'],
                                        duplicates='drop')
    turn_stats = trades_df.groupby('turnover_bin', observed=True).agg(
        笔数=('gain_pct', 'count'),
        平均涨幅=('gain_pct', 'mean'),
        大胜率=('gain_pct', lambda x: (x > 3).mean() * 100),
        胜率=('gain_pct', lambda x: (x > 0).mean() * 100),
    ).round(2)
    print(tabulate(turn_stats.reset_index(), headers=['换手率段', '笔数', '持有涨幅%', '大胜率%', '胜率%'],
                   tablefmt='github', showindex=False))

    print('\n' + '=' * 80)
    print('成交额分位段统计')
    print('=' * 80)
    trades_df['amt_bin'] = pd.qcut(trades_df['amount'], q=4,
                                    labels=['低成交额', '中低', '中高', '高成交额'],
                                    duplicates='drop')
    amt_stats = trades_df.groupby('amt_bin', observed=True).agg(
        笔数=('gain_pct', 'count'),
        平均涨幅=('gain_pct', 'mean'),
        大胜率=('gain_pct', lambda x: (x > 3).mean() * 100),
        胜率=('gain_pct', lambda x: (x > 0).mean() * 100),
    ).round(2)
    print(tabulate(amt_stats.reset_index(), headers=['成交额段', '笔数', '持有涨幅%', '大胜率%', '胜率%'],
                   tablefmt='github', showindex=False))


def main():
    args = parse_args()
    data_manager = DataManager()

    try:
        from datetime import datetime, timedelta

        if args.date:
            target_dt = datetime.strptime(args.date, '%Y%m%d')
        else:
            target_dt = datetime.now()

        end_date = get_nearest_trade_date(data_manager, target_dt)
        if not end_date:
            logging.error("未获取到最近交易日")
            return
        logging.info("最近交易日: %s", end_date)

        start_dt = datetime.strptime(end_date, '%Y%m%d') - timedelta(days=args.days * 2)
        start_date_str = start_dt.strftime('%Y%m%d')

        trade_dates_all = data_manager.get_trade_dates(start_date_str, end_date)
        if not trade_dates_all:
            logging.error("未获取到交易日历")
            return

        trade_dates_all = sorted(trade_dates_all)

        backtest_dates = trade_dates_all[-args.days:]
        if len(backtest_dates) < 10:
            logging.error("可用交易日不足: %d", len(backtest_dates))
            return
        logging.info("回测区间: %s ~ %s (%d 个交易日)", backtest_dates[0], backtest_dates[-1], len(backtest_dates))

        df_full = fetch_and_prepare_data(data_manager, trade_dates_all)
        if df_full.empty:
            logging.error("未获取到数据")
            return

        available_dates = sorted(df_full['trade_date'].unique())
        backtest_dates = [d for d in backtest_dates if d in set(available_dates)]
        logging.info("有效交易日: %d", len(backtest_dates))

        df = df_full[df_full['trade_date'] >= backtest_dates[0]].copy()

        basic_info = data_manager.get_stock_basic_info()
        if 'name' not in basic_info.columns:
            basic_info['name'] = basic_info['ts_code']
        basic_info['name'] = basic_info['name'].fillna(basic_info['ts_code'])
        basic = basic_info[basic_info['list_date'].notna()].copy()

        df = df.merge(basic[['ts_code', 'name']], on='ts_code', how='left')

        df = apply_strategy_marks(df)
        df = calculate_trend_indicators(df)
        df = calculate_zhixing_brick_indicator(df)
        df = calculate_amount_rank(df)

        logging.info("策略标记完成, first_j13_step=True=%d",
                     df['first_j13_step'].sum() if 'first_j13_step' in df.columns else 0)

        extended_dates = data_manager.get_trade_dates(
            backtest_dates[0],
            (datetime.strptime(backtest_dates[-1], '%Y%m%d') + timedelta(days=args.hold_days + 10)).strftime('%Y%m%d')
        )
        price_lookup = preload_price_data(data_manager, extended_dates)

        trades_df = run_analysis(df, backtest_dates, basic, price_lookup, args.hold_days)
        logging.info("共采集 %d 笔交易", len(trades_df))

        if not trades_df.empty:
            analyze_features(trades_df)
            csv_path = f"analyze_c154_{end_date}.csv"
            trades_df.to_csv(csv_path, index=False, encoding='utf-8-sig')
            logging.info("明细已保存: %s", csv_path)

    finally:
        data_manager.close()


if __name__ == "__main__":
    main()
