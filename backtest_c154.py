#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
C154 策略逐日回测脚本 (backtest_c154.py)

回测逻辑：
  - 遍历 250 个交易日，在每个交易日应用 C154 筛选条件
  - 如果当天有符合条件的股票，选 KDJ J 值最小的一只
  - 以次日开盘价买入，持有 3 天后以收盘价卖出
  - 统计胜率、平均涨幅、盈亏比

使用方式：
    python backtest_c154.py                  # 默认最近250交易日，持有3天
    python backtest_c154.py --days 120       # 最近120交易日
    python backtest_c154.py --hold-days 5    # 持有5天
    python backtest_c154.py --top-n 3        # 每天选J最小的前3只
"""

import argparse
import logging
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from tabulate import tabulate
from tqdm import tqdm

from config import BACKTEST_CONFIG as BT, STRATEGY_CONFIG as ST
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
from main_par3 import apply_c154_filter, calculate_c154_score

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s | %(message)s")

PICK_LABELS = {"min": "J值最小", "median": "J值中位数", "max": "J值最大", "score": "评分最高B级"}


def parse_args():
    parser = argparse.ArgumentParser(description="C154策略逐日回测")
    parser.add_argument("--date", type=str, default=None, help="回测结束日期，格式 YYYYMMDD，默认今天")
    parser.add_argument("--days", type=int, default=250, help="回测交易日数，默认250")
    parser.add_argument("--hold-days", type=int, default=3, help="持有天数，默认3天")
    parser.add_argument("--pick", type=str, default="min", choices=["min", "median", "max", "score", "all"],
                        help="选股方式: min=J最小, median=J中位数, max=J最大, score=评分最高B级, all=全部对比")
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
        logging.error("预加载价格数据失败")
        return {}

    full_df = full_df[fields].dropna(subset=["close_qfq"])

    price_lookup = {}
    for date, day_df in full_df.groupby("trade_date"):
        price_lookup[date] = day_df.set_index("ts_code")

    elapsed = time.time() - t0
    mem_mb = full_df.memory_usage(deep=True).sum() / 1024 / 1024
    logging.info("预加载完成: %d 天, %.1f MB, %.1f 秒", len(price_lookup), mem_mb, elapsed)

    return price_lookup


def pick_stock(filtered, mode="min", df=None):
    if filtered.empty:
        return None

    if mode == "score":
        scored = calculate_c154_score(filtered, df)
        above_b = scored[scored['score_level'].isin(['A', 'B'])]
        if above_b.empty:
            return None
        return above_b.iloc[0]

    sorted_df = filtered.sort_values('kdj_qfq').reset_index(drop=True)
    n = len(sorted_df)

    if mode == "min":
        return sorted_df.iloc[0]
    elif mode == "max":
        return sorted_df.iloc[-1]
    elif mode == "median":
        mid = n // 2
        return sorted_df.iloc[mid]
    return sorted_df.iloc[0]


def run_backtest(df, trade_dates_sorted, basic, price_lookup, hold_days, pick_mode="min"):
    trade_date_idx_map = {d: i for i, d in enumerate(trade_dates_sorted)}

    df_by_date = {}
    for date, day_df in df.groupby("trade_date"):
        df_by_date[date] = day_df

    all_trades = []
    signal_days = 0

    for signal_date in tqdm(trade_dates_sorted, desc=f"逐日回测({pick_mode})", leave=False):
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

        signal_days += 1
        selected = pick_stock(filtered, pick_mode, df)
        if selected is None:
            continue

        buy_day_data = price_lookup.get(buy_date)
        if buy_day_data is None:
            continue

        ts_code = selected['ts_code']
        if ts_code not in buy_day_data.index:
            continue

        buy_price = buy_day_data.loc[ts_code, 'open_qfq']
        if pd.isna(buy_price) or buy_price <= 0:
            continue

        final_price = None
        max_price = buy_price
        for d in hold_dates:
            day_data = price_lookup.get(d)
            if day_data is None or ts_code not in day_data.index:
                continue
            high = day_data.loc[ts_code, 'high_qfq']
            close = day_data.loc[ts_code, 'close_qfq']
            if not pd.isna(high):
                max_price = max(max_price, high)
            if not pd.isna(close):
                final_price = close

        if final_price is None:
            continue

        gain_pct = round((final_price - buy_price) / buy_price * 100, 2)
        max_gain_pct = round((max_price - buy_price) / buy_price * 100, 2)

        all_trades.append({
            'ts_code': ts_code,
            'name': selected.get('name', ''),
            'signal_date': signal_date,
            'buy_date': buy_date,
            'buy_price': round(buy_price, 2),
            'final_price': round(final_price, 2),
            'max_price': round(max_price, 2),
            'gain_pct': gain_pct,
            'max_gain_pct': max_gain_pct,
            'kdj_j': round(selected['kdj_qfq'], 2),
            'score': selected.get('score', np.nan),
            'score_level': selected.get('score_level', ''),
        })

    logging.info("有信号的交易日: %d / %d (%s)", signal_days, len(trade_dates_sorted), pick_mode)
    return pd.DataFrame(all_trades)


def compute_stats(trades_df):
    if trades_df.empty:
        return {
            "样本量": 0,
            "平均涨幅": np.nan,
            "中位涨幅": np.nan,
            "胜率": np.nan,
            "大胜率(>3%)": np.nan,
            "最大涨幅": np.nan,
            "最大跌幅": np.nan,
            "盈亏比": np.nan,
        }

    gains = trades_df["gain_pct"]
    wins = gains[gains > 0]
    losses = gains[gains < 0]

    avg_gain = wins.mean() if len(wins) > 0 else 0
    avg_loss = abs(losses.mean()) if len(losses) > 0 else 0.01

    return {
        "样本量": len(gains),
        "平均涨幅": round(gains.mean(), 2),
        "中位涨幅": round(gains.median(), 2),
        "胜率": round((gains > 0).mean() * 100, 1),
        "大胜率(>3%)": round((gains > 3).mean() * 100, 1),
        "最大涨幅": round(gains.max(), 2),
        "最大跌幅": round(gains.min(), 2),
        "盈亏比": round(avg_gain / avg_loss, 2) if avg_loss > 0 else np.nan,
    }


def print_results(trades_df, stats, hold_days, pick_mode):
    print('\n' + '=' * 70)
    print(f'C154 策略逐日回测结果 (选股: J值{PICK_LABELS.get(pick_mode, pick_mode)})')
    print('=' * 70)
    print(f'回测参数: 持有{hold_days}天')
    print()

    stat_rows = [[k, v] for k, v in stats.items()]
    print(tabulate(stat_rows, headers=['指标', '值'], tablefmt='github'))

    if trades_df.empty:
        print('\n无交易记录')
        return

    print(f'\n共 {len(trades_df)} 笔交易')

    gains = trades_df['gain_pct']
    print(f'\n涨幅分布:')
    print(f'  > 5%:  {(gains > 5).sum():>4} 笔 ({(gains > 5).mean()*100:.1f}%)')
    print(f'  > 3%:  {(gains > 3).sum():>4} 笔 ({(gains > 3).mean()*100:.1f}%)')
    print(f'  > 0%:  {(gains > 0).sum():>4} 笔 ({(gains > 0).mean()*100:.1f}%)')
    print(f'  < 0%:  {(gains < 0).sum():>4} 笔 ({(gains < 0).mean()*100:.1f}%)')
    print(f'  < -3%: {(gains < -3).sum():>4} 笔 ({(gains < -3).mean()*100:.1f}%)')
    print(f'  < -5%: {(gains < -5).sum():>4} 笔 ({(gains < -5).mean()*100:.1f}%)')

    print('\n最近20笔交易:')
    recent = trades_df.tail(20)
    display_cols = ['ts_code', 'name', 'signal_date', 'buy_price', 'final_price', 'gain_pct', 'kdj_j']
    existing_cols = [c for c in display_cols if c in recent.columns]
    print(tabulate(recent[existing_cols].reset_index(drop=True), headers=existing_cols, tablefmt='github', showindex=False))

    print('\n涨幅最大的10笔:')
    top10 = trades_df.nlargest(10, 'gain_pct')
    print(tabulate(top10[existing_cols].reset_index(drop=True), headers=existing_cols, tablefmt='github', showindex=False))

    print('\n跌幅最大的10笔:')
    bottom10 = trades_df.nsmallest(10, 'gain_pct')
    print(tabulate(bottom10[existing_cols].reset_index(drop=True), headers=existing_cols, tablefmt='github', showindex=False))

    monthly = trades_df.copy()
    monthly['month'] = monthly['signal_date'].str[:6]
    monthly_stats = monthly.groupby('month').agg(
        笔数=('gain_pct', 'count'),
        平均涨幅=('gain_pct', 'mean'),
        胜率=('gain_pct', lambda x: (x > 0).mean() * 100),
    ).round(2)

    print('\n月度统计:')
    print(tabulate(monthly_stats.reset_index(), headers=['月份', '笔数', '平均涨幅%', '胜率%'], tablefmt='github', showindex=False))


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
        actual_start = backtest_dates[0]
        logging.info("回测区间: %s ~ %s (%d 个交易日)", actual_start, backtest_dates[-1], len(backtest_dates))

        df_full = fetch_and_prepare_data(data_manager, trade_dates_all)
        if df_full.empty:
            logging.error("未获取到数据")
            return

        available_dates = sorted(df_full['trade_date'].unique())
        backtest_dates = [d for d in backtest_dates if d in set(available_dates)]
        if len(backtest_dates) < 10:
            logging.error("有效交易日不足: %d", len(backtest_dates))
            return
        logging.info("有效交易日: %d", len(backtest_dates))

        df = df_full[df_full['trade_date'] >= backtest_dates[0]].copy()
        logging.info("数据筛选后: %d 条", len(df))

        basic_info = data_manager.get_stock_basic_info()
        if 'name' not in basic_info.columns:
            basic_info['name'] = basic_info['ts_code']
        if 'industry_name' not in basic_info.columns:
            basic_info['industry_name'] = '未知行业'
        basic_info['industry_name'] = basic_info['industry_name'].fillna('未知行业')
        basic_info['name'] = basic_info['name'].fillna(basic_info['ts_code'])
        basic = basic_info[basic_info['list_date'].notna()].copy()

        df = df.merge(basic[['ts_code', 'name', 'industry_name']], on='ts_code', how='left')

        df = apply_strategy_marks(df)
        df = calculate_trend_indicators(df)
        df = calculate_zhixing_brick_indicator(df)
        df = calculate_amount_rank(df)

        logging.info("策略标记完成, first_j13_step=True=%d, J<5=%d",
                     df['first_j13_step'].sum() if 'first_j13_step' in df.columns else 0,
                     (df['kdj_qfq'] < 5).sum() if 'kdj_qfq' in df.columns else 0)

        extended_dates = data_manager.get_trade_dates(
            backtest_dates[0],
            (datetime.strptime(backtest_dates[-1], '%Y%m%d') + timedelta(days=args.hold_days + 10)).strftime('%Y%m%d')
        )
        price_lookup = preload_price_data(data_manager, extended_dates)

        if args.pick == "all":
            modes = ["min", "median", "max", "score"]
        else:
            modes = [args.pick]

        all_results = {}
        for mode in modes:
            trades_df = run_backtest(
                df, backtest_dates, basic, price_lookup,
                hold_days=args.hold_days, pick_mode=mode,
            )
            stats = compute_stats(trades_df)
            all_results[mode] = (trades_df, stats)
            print_results(trades_df, stats, args.hold_days, mode)

            if not trades_df.empty:
                csv_path = f"backtest_c154_{mode}_{end_date}.csv"
                trades_df.to_csv(csv_path, index=False, encoding='utf-8-sig')
                logging.info("交易记录已保存: %s", csv_path)

        if len(modes) > 1:
            print('\n' + '=' * 70)
            print('C154 策略对比汇总')
            print('=' * 70)
            compare_rows = []
            for mode in modes:
                _, stats = all_results[mode]
                compare_rows.append({
                    '选股方式': f'J值{PICK_LABELS[mode]}',
                    '样本量': stats['样本量'],
                    '平均涨幅': stats['平均涨幅'],
                    '中位涨幅': stats['中位涨幅'],
                    '胜率': stats['胜率'],
                    '大胜率(>3%)': stats['大胜率(>3%)'],
                    '盈亏比': stats['盈亏比'],
                    '最大涨幅': stats['最大涨幅'],
                    '最大跌幅': stats['最大跌幅'],
                })
            print(tabulate(pd.DataFrame(compare_rows), headers='keys', tablefmt='github', showindex=False))

    finally:
        data_manager.close()


if __name__ == "__main__":
    main()
