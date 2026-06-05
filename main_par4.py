#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
C432 最优组合选股策略 (main_par4.py)

基于消融实验 Part C 最优组合 C432 实现的选股策略。

核心策略条件（全部 AND）：
    1. first_j13_step = True（阶梯放量+J13低吸信号）
    2. not_falling：当日未下跌（pct_chg >= 0）
    3. has_bvk：周期内有底部暴力K信号
    4. macd_dif_qfq > 0：MACD多头（DIF>0）
    5. near_mid_2pct：收盘价接近知行中期线（偏离≤2%）
    6. j<-5：KDJ J值 < -5（极低）
    7. shrink：缩量回调
    8. has_am：周期内有异动信号

回测表现（250交易日，持有3天）：
    样本量=181, 平均涨幅=1.03%, 胜率=59.7%, 盈亏比=1.15, 综合得分=39.93

使用方式：
    python main_par4.py                              # 默认今天，250天回测
    python main_par4.py --date 20250620 --days 60    # 指定日期和天数
    python main_par4.py --backtest --hold-days 5     # 执行回测
    python main_par4.py --debug 688321.SH            # 调试单只股票
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
from generate_stock_html import generate_c432_html

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s | %(message)s")


SCORE_WEIGHTS = {
    'mid_dist':    {'weight': 60, 'desc': '近中期线≤0.3%',  'type': '胜率'},
    'macd_str':    {'weight': 30, 'desc': 'DIF>0.05',       'type': '胜率'},
    'body_ratio':  {'weight': 10, 'desc': '实体≥0.5',       'type': '涨幅'},
}


def calculate_c432_score(result, df):
    """为 C432 筛选结果计算评分 (v5)

    基于 v4 回测验证，保留3个正向有效维度，调整等级阈值。

    3 维度评分（满分100）：
      1. 近中期线距离（60分）：胜率差+10.4%，唯一持续强维度
      2. MACD强度（30分）：胜率差+5.8%
      3. 实体比例（10分）：胜率差+4.6%

    评分规则：
      1. 近中线距离：≤0.2%=60, ≤0.5%=50, ≤1.0%=38, ≤1.5%=22, ≤2%=8
      2. MACD强度：>0.15=30, >0.08=22, >0.03=15, >0=8
      3. 实体比例：≥0.6=10, ≥0.3=5, <0.3=0

    等级：A(≥80), B(≥60), C(≥30), D(<30)
    阈值依据：回测显示≥80分段胜率67.7%，60-79分段58.8%，30-59分段54.1%，<30为50%

    Args:
        result: C432 筛选结果 DataFrame
        df: 原始完整数据 DataFrame

    Returns:
        添加了 score 和各维度分列的 DataFrame，按 score 降序排列
    """
    scored = result.copy()

    # 1. 近中期线距离（60分）— 唯一持续有效维度，胜率差+8~10%
    zhixing_mid = scored['zhixing_mid_duokong']
    close = scored['close_qfq']
    scored['dist_to_mid'] = np.where(
        zhixing_mid > 0,
        (close - zhixing_mid).abs() / zhixing_mid * 100,
        99.0
    )
    scored['score_mid_dist'] = scored['dist_to_mid'].apply(
        lambda x: 60 if x <= 0.2
        else (50 if x <= 0.5
        else (38 if x <= 1.0
        else (22 if x <= 1.5
        else (8 if x <= 2.0 else 0))))
    )

    # 2. MACD强度（30分）— 第二有效维度
    scored['score_macd'] = scored['macd_dif_qfq'].apply(
        lambda x: 30 if pd.notna(x) and x > 0.15
        else (22 if pd.notna(x) and x > 0.08
        else (15 if pd.notna(x) and x > 0.03
        else (8 if pd.notna(x) and x > 0 else 0)))
    )

    # 3. 实体比例（10分）— 轻量辅助维度
    if 'body_ratio' not in scored.columns:
        if 'open_qfq' in scored.columns and 'high_qfq' in scored.columns:
            hi = scored['high_qfq']
            lo = scored['low_qfq']
            cl = scored['close_qfq']
            op = scored['open_qfq']
            rng = hi - lo
            body = (cl - op).abs()
            scored['body_ratio'] = np.where(rng > 0, body / rng, 0)
    scored['score_body'] = scored['body_ratio'].apply(
        lambda x: 10 if x >= 0.6
        else (5 if x >= 0.3 else 0)
    )

    # 计算总分
    score_cols = ['score_mid_dist', 'score_macd', 'score_body']
    scored['score'] = scored[score_cols].sum(axis=1)

    # 评分等级（阈值对齐实际数据分布的3区模式）
    scored['score_level'] = scored['score'].apply(
        lambda x: 'A' if x >= 80 else ('B' if x >= 60 else ('C' if x >= 30 else 'D'))
    )

    return scored.sort_values('score', ascending=False)


def parse_args():
    parser = argparse.ArgumentParser(description="C432最优组合选股策略")
    parser.add_argument("--date", type=str, default=None, help="回测日期，格式 YYYYMMDD，默认今天")
    parser.add_argument("--days", type=int, default=250, help="历史天数，默认250")
    parser.add_argument("--debug", type=str, default="", help="调试模式，传入股票代码（逗号分隔）")
    parser.add_argument("--backtest", action="store_true", help="是否执行回测")
    parser.add_argument("--hold-days", type=int, default=3, help="回测持有天数，默认3天")
    parser.add_argument("--detailed", action="store_true", help="是否打印逐日持仓数据")
    return parser.parse_args()


def apply_c432_filter(df, end_date, basic):
    """应用 C432 最优组合筛选条件

    条件（全部 AND）：
    1. first_j13_step = True（阶梯放量+J13低吸信号）
    2. not_falling：当日未下跌（pct_chg >= 0）
    3. has_bvk：周期内有底部暴力K信号
    4. macd_dif_qfq > 0：MACD多头
    5. near_mid_2pct：收盘价接近知行中期线（偏离≤2%）
    6. j<-5：KDJ J值 < -5（极低）
    7. shrink：缩量回调
    8. has_am：周期内有异动信号
    9. 非次新股（上市>=180天）

    Args:
        df: 含所有策略标记的 DataFrame
        end_date: 回测结束日期
        basic: 股票基本信息 DataFrame（含 list_date）

    Returns:
        筛选结果 DataFrame，按 KDJ J 值升序排列（最超卖在前）
    """
    # 剔除次新股
    cutoff_date = pd.to_datetime(end_date) - pd.Timedelta(days=BT.MIN_STOCK_AGE_DAYS)
    basic['list_date'] = pd.to_datetime(basic['list_date'])
    non_new_stocks = basic[basic['list_date'] <= cutoff_date]['ts_code']
    df_filtered = df[df['ts_code'].isin(non_new_stocks)].copy()

    # 计算 near_mid_2pct 条件
    zhixing_mid = df_filtered['zhixing_mid_duokong']
    close = df_filtered['close_qfq']
    near_mid_cond = (
        (zhixing_mid > 0)
        & ((close - zhixing_mid).abs() / zhixing_mid <= 0.02)
    )

    # C432 条件
    cond = (
        df_filtered['first_j13_step'] &                          # 1. 阶梯放量+J13低吸
        (df_filtered['pct_chg'].fillna(-100) >= 0) &             # 2. 不跌
        df_filtered['has_bottom_violent_k'] &                    # 3. 底部暴力K
        (df_filtered['macd_dif_qfq'].fillna(0) > 0) &           # 4. MACD多头
        near_mid_cond &                                          # 5. 近中期线2%
        (df_filtered['kdj_qfq'].fillna(100) < -5) &            # 6. J < -5
        df_filtered['shrink'].fillna(False) &                    # 7. 缩量
        df_filtered['has_am_in_period']                          # 8. 异动
    )

    latest = df_filtered[cond & (df_filtered['trade_date'] == end_date)]
    result = latest[[
        'ts_code', 'name', 'industry_name', 'trade_date', 'close_qfq', 'ma_qfq_60',
        'kdj_qfq', 'macd_dif_qfq', 'amount', 'pct_chg',
        'turnover_rate', 'volume_ratio', 'total_mv', 'circ_mv',
        'open_qfq', 'high_qfq', 'low_qfq', 'kdj_k_qfq', 'kdj_d_qfq',
        'macd_dea_qfq', 'macd_qfq', 'pe_ttm', 'pb',
        'zhixing_mid_duokong', 'zhixing_duokong',
        'shrink', 'amplitude',
    ]].sort_values('kdj_qfq')

    return result


def save_c432_result(result, end_date):
    """保存 C432 策略结果到 CSV"""
    if result.empty:
        return
    csv_path = f"c432_result_{end_date}.csv"
    result.to_csv(csv_path, index=False, encoding='utf-8-sig')
    logging.info("C432 结果已保存: %s (%d 只)", csv_path, len(result))


def print_c432_results(result, df, end_date):
    """打印 C432 策略筛选结果（含评分）"""
    print('\n========== C432 最优组合选股结果 ==========')
    print('条件: 阶梯放量+J13低吸 & 不跌 & 暴力K & MACD多头 & 近中期线2% & J<-5 & 缩量 & 异动')
    print()

    if result.empty:
        print('没有符合条件的股票')
        return

    has_score = 'score' in result.columns
    print(f'共找到 {len(result)} 只符合条件的股票:')

    if has_score:
        level_counts = result['score_level'].value_counts().to_dict()
        print(f'评分分布: A(≥80)={level_counts.get("A", 0)}只  B(≥65)={level_counts.get("B", 0)}只  '
              f'C(≥50)={level_counts.get("C", 0)}只  D(<50)={level_counts.get("D", 0)}只')

    # 行业分布
    industry_count = result['industry_name'].value_counts()
    print(f'\n按行业分布:')
    for industry, count in industry_count.items():
        print(f'  {industry}: {count}只')

    # 表格
    table_data = []
    for _, row in result.iterrows():
        row_items = [
            row['ts_code'],
            row['name'],
            row.get('industry_name', '未知'),
            f'{row["close_qfq"]:.2f}',
            f'{row["kdj_qfq"]:.2f}',
            f'{row["pct_chg"]:.2f}%',
            f'{row["amount"]:.0f}',
        ]
        if has_score:
            # 计算距离中期线的百分比
            mid_val = row.get('zhixing_mid_duokong', 0)
            if pd.notna(mid_val) and mid_val > 0:
                mid_dist = (row['close_qfq'] - mid_val) / mid_val * 100
            else:
                mid_dist = 0
            mv_wan = row['total_mv'] / 10000 if pd.notna(row.get('total_mv')) else 0
            row_items.extend([
                f'{row["score"]:.0f}',
                row['score_level'],
                f'{mid_dist:.2f}%',
                f'{row.get("macd_dif_qfq", 0):.4f}',
                f'{mv_wan:.0f}万',
                f'{row.get("turnover_rate", 0):.1f}',
                f'{row.get("volume_ratio", 0):.2f}',
            ])
        table_data.append(row_items)

    if has_score:
        headers = ['代码', '名称', '行业', '收盘价', 'J值', '涨跌幅', '成交额',
                   '评分', '等级', '距中线%', 'MACD-DIF', '市值', '换手%', '量比']
    else:
        headers = ['代码', '名称', '行业', '收盘价', 'J值', '涨跌幅', '成交额']
    print(tabulate(table_data, headers=headers, tablefmt='github'))


def print_c432_stage_statistics(df, result, args):
    """打印 C432 策略各阶段漏斗统计"""
    print('\n========== C432 各阶段股票计数 ==========')
    total = df['ts_code'].nunique()
    print(f'0) 全市场（{args.days} 天内）: {total:>5} 只')

    has_step = df.groupby('ts_code')['first_j13_step'].max().astype(bool).sum()
    print(f'1) 出现过阶梯放量+J13低吸: {has_step:>5} 只')

    c1 = df['first_j13_step'] & (df['pct_chg'].fillna(-100) >= 0)
    not_falling_cnt = df[c1]['ts_code'].nunique()
    print(f'2) +不跌: {not_falling_cnt:>5} 只')

    c2 = c1 & df['has_bottom_violent_k']
    bvk_cnt = df[c2]['ts_code'].nunique()
    print(f'3) +底部暴力K: {bvk_cnt:>5} 只')

    c3 = c2 & (df['macd_dif_qfq'].fillna(0) > 0)
    macd_cnt = df[c3]['ts_code'].nunique()
    print(f'4) +MACD多头: {macd_cnt:>5} 只')

    # near_mid_2pct 条件
    zhixing_mid = df['zhixing_mid_duokong']
    close = df['close_qfq']
    near_mid = (zhixing_mid > 0) & ((close - zhixing_mid).abs() / zhixing_mid <= 0.02)
    c4 = c3 & near_mid
    mid_cnt = df[c4]['ts_code'].nunique()
    print(f'5) +近中期线2%: {mid_cnt:>5} 只')

    c5 = c4 & (df['kdj_qfq'].fillna(100) < -5)
    j5_cnt = df[c5]['ts_code'].nunique()
    print(f'6) +J<-5: {j5_cnt:>5} 只')

    c6 = c5 & df['shrink'].fillna(False)
    shrink_cnt = df[c6]['ts_code'].nunique()
    print(f'7) +缩量: {shrink_cnt:>5} 只')

    c7 = c6 & df['has_am_in_period']
    am_cnt = df[c7]['ts_code'].nunique()
    print(f'8) +异动: {am_cnt:>5} 只')

    final_cnt = result['ts_code'].nunique()
    print(f'9) 最终满足条件（当日）: {final_cnt:>5} 只')


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

        # 应用 C432 筛选条件
        result = apply_c432_filter(df, end_date, basic)

        # 计算评分并排序
        if not result.empty:
            result = calculate_c432_score(result, df)

        # 打印结果
        print_c432_results(result, df, end_date)
        save_c432_result(result, end_date)
        print_c432_stage_statistics(df, result, args)

        # 生成 HTML 报告
        industry_count = result['industry_name'].value_counts().to_dict()

        # 计算漏斗统计
        c1 = df['first_j13_step'] & (df['pct_chg'].fillna(-100) >= 0)
        c2 = c1 & df['has_bottom_violent_k']
        c3 = c2 & (df['macd_dif_qfq'].fillna(0) > 0)
        zhixing_mid = df['zhixing_mid_duokong']
        close = df['close_qfq']
        near_mid = (zhixing_mid > 0) & ((close - zhixing_mid).abs() / zhixing_mid <= 0.02)
        c4 = c3 & near_mid
        c5 = c4 & (df['kdj_qfq'].fillna(100) < -5)
        c6 = c5 & df['shrink'].fillna(False)
        c7 = c6 & df['has_am_in_period']

        funnel_stats = {
            '全市场': df['ts_code'].nunique(),
            '阶梯放量+J13': int(df.groupby('ts_code')['first_j13_step'].max().astype(bool).sum()),
            '不跌': int(df[c1]['ts_code'].nunique()),
            '底部暴力K': int(df[c2]['ts_code'].nunique()),
            'MACD多头': int(df[c3]['ts_code'].nunique()),
            '近中期线2%': int(df[c4]['ts_code'].nunique()),
            'J<-5': int(df[c5]['ts_code'].nunique()),
            '缩量': int(df[c6]['ts_code'].nunique()),
            '异动': int(df[c7]['ts_code'].nunique()),
            '最终': int(result['ts_code'].nunique()),
        }
        generate_c432_html(result, df, end_date, funnel_stats, industry_count)

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
