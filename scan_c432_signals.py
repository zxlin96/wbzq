#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成最近 250 天内所有触发过 C432 信号的交易日列表。
"""

import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from config import BACKTEST_CONFIG as BT
from data_manager import DataManager
from main_par2 import (
    apply_strategy_marks,
    calculate_trend_indicators,
    calculate_zhixing_brick_indicator,
    calculate_amount_rank,
    fetch_and_prepare_data,
    get_nearest_trade_date,
)


def main():
    data_manager = DataManager()
    try:
        # 默认到今天
        target_dt = datetime.now()
        end_date = get_nearest_trade_date(data_manager, target_dt)
        if not end_date:
            print("未获取到最近交易日")
            return
        print(f"最近交易日: {end_date}")

        # 取最近 250 个交易日，回看窗口使用 250*2+200 天（参考 backtest_c432_score.py）
        lookback_days = 250 * 2 + 200
        start_dt = datetime.strptime(end_date, '%Y%m%d') - timedelta(days=lookback_days)
        start_date = start_dt.strftime('%Y%m%d')
        trade_dates = data_manager.get_trade_dates(start_date, end_date)
        if not trade_dates:
            print("未获取到交易日历")
            return
        trade_dates_range = trade_dates
        backtest_dates = trade_dates_range[-250:]
        actual_start = backtest_dates[0]
        print(f"区间: {actual_start} ~ {end_date} ({len(backtest_dates)} 个交易日，回看窗口 {lookback_days} 天)")

        # 获取数据
        df_full = fetch_and_prepare_data(data_manager, trade_dates_range)
        if df_full.empty:
            print("未获取到数据")
            return
        df = df_full[df_full['trade_date'] >= actual_start].copy()
        print(f"数据: {len(df)} 条")

        # 基本信息
        basic_info = data_manager.get_stock_basic_info()
        if 'name' not in basic_info.columns:
            basic_info['name'] = basic_info['ts_code']
        if 'industry_name' not in basic_info.columns:
            basic_info['industry_name'] = '未知行业'
        basic_info['industry_name'] = basic_info['industry_name'].fillna('未知行业')
        basic_info['name'] = basic_info['name'].fillna(basic_info['ts_code'])
        basic = basic_info[basic_info['list_date'].notna()].copy()

        # 合并名称
        df = df.merge(basic[['ts_code', 'name', 'industry_name']], on='ts_code', how='left')

        # 策略标记
        df = apply_strategy_marks(df)
        df = calculate_trend_indicators(df)
        df = calculate_zhixing_brick_indicator(df)
        df = calculate_amount_rank(df)

        # 剔除次新股
        cutoff_date = pd.to_datetime(end_date) - pd.Timedelta(days=BT.MIN_STOCK_AGE_DAYS)
        basic['list_date'] = pd.to_datetime(basic['list_date'])
        non_new_stocks = basic[basic['list_date'] <= cutoff_date]['ts_code']
        df = df[df['ts_code'].isin(non_new_stocks)].copy()

        # 计算 near_mid_2pct
        zhixing_mid = df['zhixing_mid_duokong']
        close = df['close_qfq']
        near_mid_cond = (
            (zhixing_mid > 0)
            & ((close - zhixing_mid).abs() / zhixing_mid <= 0.02)
        )

        # C432 全部条件（不限当天）
        cond = (
            df['first_j13_step'].fillna(False)
            & (df['pct_chg'].fillna(-100) >= 0)
            & df['has_bottom_violent_k'].fillna(False)
            & (df['macd_dif_qfq'].fillna(0) > 0)
            & near_mid_cond
            & (df['kdj_qfq'].fillna(100) < -5)
            & df['shrink'].fillna(False)
            & df['has_am_in_period'].fillna(False)
        )

        hits = df[cond].copy()
        print(f"\n共找到 {len(hits)} 条 C432 信号")

        if hits.empty:
            print("250 天内无 C432 信号")
            return

        # 按日期汇总
        cols = ['ts_code', 'name', 'industry_name', 'trade_date', 'close_qfq',
                'pct_chg', 'kdj_qfq', 'macd_dif_qfq', 'amount', 'total_mv']
        available_cols = [c for c in cols if c in hits.columns]
        result = hits[available_cols].sort_values(['trade_date', 'kdj_qfq'])

        # 保存
        output_file = f"c432_signal_list_{end_date}.csv"
        result.to_csv(output_file, index=False, encoding='utf-8-sig')
        print(f"已保存: {output_file}")

        # 打印按日期统计
        daily = result.groupby('trade_date').agg(
            数量=('ts_code', 'count'),
            股票=('ts_code', lambda x: ', '.join(x))
        ).reset_index()

        print(f"\n{'='*60}")
        print(f"{'日期':<12} {'数量':>4}  {'股票代码'}")
        print(f"{'='*60}")
        for _, row in daily.iterrows():
            print(f"{row['trade_date']:<12} {row['数量']:>4}  {row['股票']}")
        print(f"{'='*60}")
        print(f"共 {len(daily)} 个交易日触发，合计 {len(result)} 条信号")

    finally:
        data_manager.close()


if __name__ == '__main__':
    main()
