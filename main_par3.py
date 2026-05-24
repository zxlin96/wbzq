#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
C154 最优组合选股策略 (main_par3.py)

基于消融实验 Part C 最优组合 C154 实现的选股策略。

核心策略条件（全部 AND）：
    1. first_j13_step = True（阶梯放量+J13低吸信号）
    2. not_falling：当日未下跌（pct_chg >= 0）
    3. no_dist：无出货信号（V1/V2/V3均无）
    4. has_bvk：周期内有底部暴力K信号
    5. j_ultra_low：KDJ J值 < 5
    6. has_am：周期内有异动信号
    7. 非次新股（上市>=180天）

回测表现（250交易日，持有3天）：
    样本量=717, 平均涨幅=1.66%, 胜率=60.0%, 盈亏比=1.72, 综合得分=49.20

使用方式：
    python main_par3.py                              # 默认今天，250天回测
    python main_par3.py --date 20250620 --days 60    # 指定日期和天数
    python main_par3.py --backtest --hold-days 5     # 执行回测
    python main_par3.py --debug 688321.SH            # 调试单只股票
"""

import argparse
import logging
import os
import time

import pandas as pd
from tabulate import tabulate

from config import (
    APIConfig,
    BACKTEST_CONFIG as BT,
    STRATEGY_CONFIG as ST,
)
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


def parse_args():
    parser = argparse.ArgumentParser(description="C154最优组合选股策略")
    parser.add_argument("--date", type=str, default=None, help="回测日期，格式 YYYYMMDD，默认今天")
    parser.add_argument("--days", type=int, default=250, help="历史天数，默认250")
    parser.add_argument("--debug", type=str, default="", help="调试模式，传入股票代码（逗号分隔）")
    parser.add_argument("--backtest", action="store_true", help="是否执行回测")
    parser.add_argument("--hold-days", type=int, default=3, help="回测持有天数，默认3天")
    parser.add_argument("--detailed", action="store_true", help="是否打印逐日持仓数据")
    return parser.parse_args()


def apply_c154_filter(df, end_date, basic):
    """应用 C154 最优组合筛选条件

    条件（全部 AND）：
    1. first_j13_step = True（阶梯放量+J13低吸信号）
    2. not_falling：当日未下跌（pct_chg >= 0）
    3. no_dist：无出货信号（V1/V2/V3均无）
    4. has_bvk：周期内有底部暴力K信号
    5. j_ultra_low：KDJ J值 < 5
    6. has_am：周期内有异动信号
    7. 非次新股（上市>=180天）

    Args:
        df: 含所有策略标记的 DataFrame
        end_date: 回测结束日期
        basic: 股票基本信息 DataFrame（含 list_date）

    Returns:
        筛选结果 DataFrame，按 KDJ J 值升序排列
    """
    # 剔除次新股
    cutoff_date = pd.to_datetime(end_date) - pd.Timedelta(days=BT.MIN_STOCK_AGE_DAYS)
    basic['list_date'] = pd.to_datetime(basic['list_date'])
    non_new_stocks = basic[basic['list_date'] <= cutoff_date]['ts_code']
    df_filtered = df[df['ts_code'].isin(non_new_stocks)].copy()

    # C154 条件
    cond = (
        df_filtered['first_j13_step'] &                          # 阶梯放量+J13低吸
        (df_filtered['pct_chg'].fillna(-100) >= 0) &             # 不跌
        ~df_filtered['has_distribution_signal'] &                # 无出货V1
        ~df_filtered['has_distribution_signal_v2'] &             # 无出货V2
        ~df_filtered['has_distribution_signal_v3'] &             # 无出货V3
        df_filtered['has_bottom_violent_k'] &                    # 底部暴力K
        (df_filtered['kdj_qfq'].fillna(100) < 5) &              # J < 5
        df_filtered['has_am_in_period']                          # 周期内异动
    )

    latest = df_filtered[cond & (df_filtered['trade_date'] == end_date)]
    result = latest[[
        'ts_code', 'name', 'industry_name', 'trade_date', 'close_qfq', 'ma_qfq_60',
        'kdj_qfq', 'macd_dif_qfq', 'amount', 'pct_chg',
    ]].sort_values('kdj_qfq')

    return result


def save_c154_result(result, end_date):
    """保存 C154 策略结果到 CSV"""
    if result.empty:
        return
    csv_path = f"c154_result_{end_date}.csv"
    result.to_csv(csv_path, index=False, encoding='utf-8-sig')
    logging.info("C154 结果已保存: %s (%d 只)", csv_path, len(result))


def print_c154_results(result, df, end_date):
    """打印 C154 策略筛选结果"""
    print('\n========== C154 最优组合选股结果 ==========')
    print('条件: 阶梯放量+J13低吸 & 不跌 & 无出货 & 底部暴力K & J<5 & 异动')
    print()

    if result.empty:
        print('没有符合条件的股票')
        return

    print(f'共找到 {len(result)} 只符合条件的股票:')

    # 行业分布
    industry_count = result['industry_name'].value_counts()
    print(f'\n按行业分布:')
    for industry, count in industry_count.items():
        print(f'  {industry}: {count}只')

    # 表格
    table_data = []
    for _, row in result.iterrows():
        table_data.append([
            row['ts_code'],
            row['name'],
            row.get('industry_name', '未知'),
            row['trade_date'],
            f'{row["close_qfq"]:.2f}',
            f'{row["kdj_qfq"]:.2f}',
            f'{row["pct_chg"]:.2f}%',
            f'{row["macd_dif_qfq"]:.4f}',
            f'{row["amount"]:.2f}',
        ])

    headers = ['代码', '名称', '行业', '日期', '收盘价', 'J值', '涨跌幅', 'MACD-DIF', '成交额']
    print(tabulate(table_data, headers=headers, tablefmt='github'))


def print_c154_stage_statistics(df, result, args):
    """打印 C154 策略各阶段漏斗统计"""
    print('\n========== C154 各阶段股票计数 ==========')
    total = df['ts_code'].nunique()
    print(f'0) 全市场（{args.days} 天内）: {total:>5} 只')

    has_step = df.groupby('ts_code')['first_j13_step'].max().astype(bool).sum()
    print(f'1) 出现过阶梯放量+J13低吸: {has_step:>5} 只')

    not_falling_cnt = df[(df['first_j13_step']) & (df['pct_chg'].fillna(-100) >= 0)]['ts_code'].nunique()
    print(f'2) +不跌: {not_falling_cnt:>5} 只')

    no_dist_cnt = df[
        df['first_j13_step']
        & (df['pct_chg'].fillna(-100) >= 0)
        & ~df['has_distribution_signal']
        & ~df['has_distribution_signal_v2']
        & ~df['has_distribution_signal_v3']
    ]['ts_code'].nunique()
    print(f'3) +无出货: {no_dist_cnt:>5} 只')

    bvk_cnt = df[
        df['first_j13_step']
        & (df['pct_chg'].fillna(-100) >= 0)
        & ~df['has_distribution_signal']
        & ~df['has_distribution_signal_v2']
        & ~df['has_distribution_signal_v3']
        & df['has_bottom_violent_k']
    ]['ts_code'].nunique()
    print(f'4) +底部暴力K: {bvk_cnt:>5} 只')

    j5_cnt = df[
        df['first_j13_step']
        & (df['pct_chg'].fillna(-100) >= 0)
        & ~df['has_distribution_signal']
        & ~df['has_distribution_signal_v2']
        & ~df['has_distribution_signal_v3']
        & df['has_bottom_violent_k']
        & (df['kdj_qfq'].fillna(100) < 5)
    ]['ts_code'].nunique()
    print(f'5) +J<5: {j5_cnt:>5} 只')

    am_cnt = df[
        df['first_j13_step']
        & (df['pct_chg'].fillna(-100) >= 0)
        & ~df['has_distribution_signal']
        & ~df['has_distribution_signal_v2']
        & ~df['has_distribution_signal_v3']
        & df['has_bottom_violent_k']
        & (df['kdj_qfq'].fillna(100) < 5)
        & df['has_am_in_period']
    ]['ts_code'].nunique()
    print(f'6) +异动: {am_cnt:>5} 只')

    final_cnt = result['ts_code'].nunique()
    print(f'7) 最终满足条件（当日）: {final_cnt:>5} 只')


def main():
    args = parse_args()
    data_manager = DataManager()

    try:
        from datetime import datetime, timedelta

        # 准备交易日期
        if args.date:
            target_dt = datetime.strptime(args.date, '%Y%m%d')
        else:
            target_dt = datetime.now()

        end_date = get_nearest_trade_date(data_manager, target_dt)
        if not end_date:
            logging.error("未获取到最近交易日")
            return
        logging.info("最近交易日: %s", end_date)

        # 获取数据
        start_dt = datetime.strptime(end_date, '%Y%m%d') - timedelta(days=args.days * 2)
        start_date = start_dt.strftime('%Y%m%d')

        trade_dates_range = data_manager.get_trade_dates(start_date, end_date)
        if not trade_dates_range:
            logging.error("未获取到交易日历")
            return

        backtest_dates = trade_dates_range[-args.days:]
        actual_start = backtest_dates[0]
        logging.info("回测区间: %s ~ %s (%d 个交易日)", actual_start, end_date, len(backtest_dates))

        # 获取并准备数据
        df_full = fetch_and_prepare_data(data_manager, trade_dates_range)
        if df_full.empty:
            logging.error("未获取到数据")
            return

        df = df_full[df_full['trade_date'] >= actual_start].copy()
        logging.info("数据筛选后: %d 条", len(df))

        # 获取股票基本信息
        basic_info = data_manager.get_stock_basic_info()
        if 'name' not in basic_info.columns:
            basic_info['name'] = basic_info['ts_code']
        if 'industry_name' not in basic_info.columns:
            basic_info['industry_name'] = '未知行业'
        basic_info['industry_name'] = basic_info['industry_name'].fillna('未知行业')
        basic_info['name'] = basic_info['name'].fillna(basic_info['ts_code'])
        basic = basic_info[basic_info['list_date'].notna()].copy()

        # 合并名称和行业
        df = df.merge(basic[['ts_code', 'name', 'industry_name']], on='ts_code', how='left')

        # 应用策略标记
        df = apply_strategy_marks(df)

        # 计算趋势指标
        df = calculate_trend_indicators(df)

        # 计算知行砖形图指标
        df = calculate_zhixing_brick_indicator(df)

        # 计算成交额排名
        df = calculate_amount_rank(df)

        # 应用 C154 筛选条件
        result = apply_c154_filter(df, end_date, basic)

        # 打印结果
        print_c154_results(result, df, end_date)
        save_c154_result(result, end_date)
        print_c154_stage_statistics(df, result, args)

        # 回测
        if args.backtest and not result.empty:
            from main_par2 import backtest_selected_stocks, print_backtest_stats
            buy_date = get_nearest_trade_date(
                data_manager,
                pd.to_datetime(end_date) + pd.Timedelta(days=1)
            )
            if buy_date:
                backtest_results = backtest_selected_stocks(
                    result['ts_code'].tolist(),
                    buy_date,
                    data_manager,
                    hold_days=args.hold_days,
                    detailed=args.detailed
                )
                print_backtest_stats(backtest_results)

        # 调试模式
        if args.debug:
            from main_par2 import debug_stock_strategy_detailed
            for ts_code in [c.strip() for c in args.debug.split(',')]:
                debug_stock_strategy_detailed(df, ts_code, end_date, basic)

    finally:
        data_manager.close()


if __name__ == "__main__":
    main()
