#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MACD零轴金叉选股策略 (main_par5.py)

基于MACD金叉条件的选股策略。

核心策略条件（全部 AND）：
    1. 前一天 MACD DIF < 0（零轴下方）
    2. 当天 MACD DIF > 0（上穿零轴）
    3. 黄白线（知行多空线与知行中期多空线）很接近，偏差 <= 0.3%（相对收盘价）
    4. 回测周期内出现过中期多空线(白) > 知行多空线(黄)（黄线需从下方上穿）
    5. 非次新股（上市>=180天）

使用方式：
    python main_par5.py                              # 默认今天，250天回测
    python main_par5.py --date 20250620 --days 60    # 指定日期和天数
    python main_par5.py --backtest --hold-days 5     # 执行回测
    python main_par5.py --debug 688321.SH            # 调试单只股票
"""

import argparse
import logging
import os
import time

import pandas as pd
import numpy as np
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
    parser = argparse.ArgumentParser(description="MACD零轴金叉选股策略")
    parser.add_argument("--date", type=str, default=None, help="回测日期，格式 YYYYMMDD，默认今天")
    parser.add_argument("--days", type=int, default=250, help="历史天数，默认250")
    parser.add_argument("--debug", type=str, default="", help="调试模式，传入股票代码（逗号分隔）")
    parser.add_argument("--backtest", action="store_true", help="是否执行回测")
    parser.add_argument("--hold-days", type=int, default=3, help="回测持有天数，默认3天")
    parser.add_argument("--detailed", action="store_true", help="是否打印逐日持仓数据")
    return parser.parse_args()


def apply_macd_filter(df, end_date, basic):
    """应用 MACD 零轴金叉 + 黄白线接近 + 白线曾上穿黄线 筛选条件

    条件（全部 AND）：
    1. 前一天 MACD DIF < 0
    2. 当天 MACD DIF > 0
    3. 黄白线接近：|知行多空线(黄) - 知行中期多空线(白)| / 收盘价 <= 0.3%
    4. 回测周期内出现过中期多空线(白) > 知行多空线(黄)
    5. 非次新股（上市>=180天）

    Args:
        df: 含所有策略标记的 DataFrame
        end_date: 回测结束日期
        basic: 股票基本信息 DataFrame（含 list_date）

    Returns:
        筛选结果 DataFrame，按 MACD DIF 值降序排列
    """
    # 计算前一天 MACD DIF
    df['macd_dif_prev'] = df.groupby('ts_code')['macd_dif_qfq'].shift(1)

    # 剔除次新股
    cutoff_date = pd.to_datetime(end_date) - pd.Timedelta(days=BT.MIN_STOCK_AGE_DAYS)
    basic['list_date'] = pd.to_datetime(basic['list_date'])
    non_new_stocks = basic[basic['list_date'] <= cutoff_date]['ts_code']
    df_filtered = df[df['ts_code'].isin(non_new_stocks)].copy()

    # MACD 金叉 + 黄白线接近 条件
    cond = (
        (df_filtered['macd_dif_prev'].fillna(0) < 0) &                                  # 1. 前一天DIF < 0
        (df_filtered['macd_dif_qfq'].fillna(0) > 0) &                                   # 2. 当天DIF > 0
        ((df_filtered['zhixing_duokong'] - df_filtered['zhixing_mid_duokong']).abs()
         / df_filtered['close_qfq'].replace(0, np.nan) * 100 <= 0.3)                    # 3. 黄白线偏差 <= 0.3%
    )

    latest = df_filtered[cond & (df_filtered['trade_date'] == end_date)]
    result = latest[[
        'ts_code', 'name', 'industry_name', 'trade_date', 'close_qfq',
        'zhixing_duokong', 'zhixing_mid_duokong',
        'macd_dif_qfq', 'macd_dea_qfq', 'macd_qfq',
        'pct_chg', 'amount', 'turnover_rate', 'volume_ratio',
        'total_mv', 'circ_mv', 'pe_ttm', 'pb',
        'kdj_qfq', 'ma_qfq_60', 'open_qfq', 'high_qfq', 'low_qfq',
    ]].sort_values('macd_dif_qfq', ascending=False)

    # 条件4: 回测周期内出现过中期多空线(白) > 知行多空线(黄)
    if not result.empty:
        df_filtered['mid_gt_duokong'] = df_filtered['zhixing_mid_duokong'] > df_filtered['zhixing_duokong']
        stocks_with_cross = df_filtered.groupby('ts_code')['mid_gt_duokong'].max()
        valid_stocks = stocks_with_cross[stocks_with_cross].index
        result = result[result['ts_code'].isin(valid_stocks)]

    return result


def save_macd_result(result, end_date):
    """保存 MACD 策略结果到 CSV"""
    if result.empty:
        return
    csv_path = f"macd_result_{end_date}.csv"
    result.to_csv(csv_path, index=False, encoding='utf-8-sig')
    logging.info("MACD结果已保存: %s (%d 只)", csv_path, len(result))


def print_macd_results(result, df, end_date):
    """打印 MACD 策略筛选结果"""
    print('\n========== MACD 零轴金叉选股结果 ==========')
    print('条件: 前一天DIF<0 & 当天DIF>0 & |黄-白|/收盘价<=0.3% & 白线曾>黄线(历史)')
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
        zx_duokong = row['zhixing_duokong']
        zx_mid = row['zhixing_mid_duokong']
        close = row['close_qfq']
        line_diff = abs(zx_duokong - zx_mid)
        line_diff_pct = line_diff / close * 100 if close > 0 else 0
        macd_dif = row['macd_dif_qfq']
        row_items = [
            row['ts_code'],
            row['name'],
            row.get('industry_name', '未知'),
            f'{close:.2f}',
            f'{zx_duokong:.4f}',
            f'{zx_mid:.4f}',
            f'{line_diff:.4f}',
            f'{line_diff_pct:.3f}%',
            f'{macd_dif:.4f}',
            f'{row["pct_chg"]:.2f}%',
        ]
        table_data.append(row_items)

    headers = ['代码', '名称', '行业', '收盘价', '黄线(多空)', '白线(中期)', '|黄-白|', '偏差%', 'DIF', '涨跌幅']
    print(tabulate(table_data, headers=headers, tablefmt='github'))


def print_macd_stage_statistics(df, result, args, end_date):
    """打印 MACD 策略各阶段漏斗统计（仅统计当天数据）"""
    print('\n========== MACD 各阶段股票计数 ==========')
    total = df['ts_code'].nunique()
    print(f'0) 全市场（{args.days} 天内）: {total:>5} 只')

    # 先在完整数据上计算前一天DIF
    df = df.copy()
    df['macd_dif_prev'] = df.groupby('ts_code')['macd_dif_qfq'].shift(1)

    # 只取当天数据
    today = df[df['trade_date'] == end_date]
    today_total = today['ts_code'].nunique()
    print(f'   当天交易股票: {today_total:>5} 只')

    # 统计当天各条件
    c1 = (today['macd_dif_prev'].fillna(0) < 0)
    pre_neg_cnt = c1.sum()
    print(f'1) 前一天DIF<0: {pre_neg_cnt:>5} 只')

    c2 = c1 & (today['macd_dif_qfq'].fillna(0) > 0)
    cur_pos_cnt = c2.sum()
    print(f'2) +当天DIF>0: {cur_pos_cnt:>5} 只')

    c3 = c2 & ((today['zhixing_duokong'] - today['zhixing_mid_duokong']).abs()
                   / today['close_qfq'].replace(0, np.nan) * 100 <= 0.3)
    near_cnt = c3.sum()
    print(f'3) +|黄-白|/收盘价<=0.3%: {near_cnt:>5} 只')

    # 条件4: 回测周期内出现过中期多空线(白) > 知行多空线(黄)
    # 对当天满足条件的股票，检查其历史数据
    c4_stocks = today[c3]['ts_code'].unique()
    if len(c4_stocks) > 0:
        df['mid_gt_duokong'] = df['zhixing_mid_duokong'] > df['zhixing_duokong']
        cross_check = df[df['ts_code'].isin(c4_stocks)].groupby('ts_code')['mid_gt_duokong'].max()
        c4_cnt = cross_check.sum()
    else:
        c4_cnt = 0
    print(f'4) +白线曾>黄线(历史): {c4_cnt:>5} 只')

    final_cnt = result['ts_code'].nunique()
    print(f'5) 最终满足条件: {final_cnt:>5} 只')


def compute_macd_funnel_stats(df, result, end_date):
    """计算 MACD 策略漏斗统计数据（仅统计当天，供外部调用）

    Args:
        df: 含所有策略标记的 DataFrame
        result: MACD 筛选结果 DataFrame
        end_date: 目标日期

    Returns:
        dict: 各阶段股票数量统计
    """
    df = df.copy()
    df['macd_dif_prev'] = df.groupby('ts_code')['macd_dif_qfq'].shift(1)
    today = df[df['trade_date'] == end_date]
    c1 = (today['macd_dif_prev'].fillna(0) < 0)
    c2 = c1 & (today['macd_dif_qfq'].fillna(0) > 0)
    c3 = c2 & ((today['zhixing_duokong'] - today['zhixing_mid_duokong']).abs()
                   / today['close_qfq'].replace(0, np.nan) * 100 <= 0.3)

    # 条件4: 回测周期内出现过中期多空线(白) > 知行多空线(黄)
    c4_stocks = today[c3]['ts_code'].unique()
    if len(c4_stocks) > 0:
        df['mid_gt_duokong'] = df['zhixing_mid_duokong'] > df['zhixing_duokong']
        cross_check = df[df['ts_code'].isin(c4_stocks)].groupby('ts_code')['mid_gt_duokong'].max()
        c4_cnt = int(cross_check.sum())
    else:
        c4_cnt = 0

    return {
        '全市场': df['ts_code'].nunique(),
        '前一天DIF<0': int(c1.sum()),
        '+当天DIF>0': int(c2.sum()),
        '+|黄-白|/收盘价<=0.3%': int(c3.sum()),
        '+白线曾>黄线(历史)': c4_cnt,
        '最终': int(result['ts_code'].nunique()),
    }


def main():
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

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
        lookback_days = args.days * 2 + 200
        start_dt = datetime.strptime(end_date, '%Y%m%d') - timedelta(days=lookback_days)
        start_date = start_dt.strftime('%Y%m%d')

        trade_dates_range = data_manager.get_trade_dates(start_date, end_date)
        if not trade_dates_range:
            logging.error("未获取到交易日历")
            return

        backtest_dates = trade_dates_range[-args.days:]
        actual_start = backtest_dates[0]
        logging.info("回测区间: %s ~ %s (%d 个交易日，回看窗口 %d 天)",
                     actual_start, end_date, len(backtest_dates), lookback_days)

        # 获取并准备数据
        df_full = fetch_and_prepare_data(data_manager, trade_dates_range)
        if df_full.empty:
            logging.error("未获取到数据")
            return

        # 先截断到回测区间，再计算策略标记
        df = df_full[df_full['trade_date'] >= actual_start].copy()
        logging.info("数据截断后: %d 条", len(df))

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

        # 应用 MACD 筛选条件
        result = apply_macd_filter(df, end_date, basic)

        # 打印结果
        print_macd_results(result, df, end_date)
        save_macd_result(result, end_date)
        print_macd_stage_statistics(df, result, args, end_date)

        # 生成 HTML 报告
        if not result.empty:
            from generate_stock_html import generate_macd_html
            industry_count = result['industry_name'].value_counts().to_dict()
            funnel_stats = compute_macd_funnel_stats(df, result, end_date)
            generate_macd_html(result, df, end_date, funnel_stats, industry_count)

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


if __name__ == '__main__':
    main()