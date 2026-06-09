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
from generate_stock_html import generate_c154_html

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s | %(message)s")


SCORE_WEIGHTS = {
    'total_mv': {'weight': 25, 'desc': '小市值(<=P30)'},
    'turnover_rate': {'weight': 20, 'desc': '换手1~5%'},
    'pct_chg': {'weight': 20, 'desc': '涨幅>=1%'},
    'volume_ratio': {'weight': 10, 'desc': '量比1.0~1.5'},
    'body_ratio': {'weight': 10, 'desc': '实体>=0.4'},
    'kdj_qfq': {'weight': 10, 'desc': 'J值0~2'},
    'close_vs_ma60': {'weight': 5, 'desc': '距MA60 2~5%'},
}


def calculate_c154_score(result, df):
    """为 C154 筛选结果计算胜率评分

    基于胜率优化实验数据重新设计评分维度（目标：最大化胜率）：

    胜率最优单因子（样本>=30）：
      小市值(<=P30) 胜率62.1%    换手1~5% 胜率61.9%
      涨幅1~3% 胜率61.6%        量比1.0~1.5 胜率63.6%
      实体>=0.4 胜率60.6%       J值0~2 胜率60.5%

    胜率最优组合：
      小盘+换手1~5+涨幅>=1 → 胜率68.1%（样本859）

    评分规则（满分100）：
      1. 小市值（25分）：<= P30 得 25 分
      2. 换手率（20分）：1~5% 得 20 分，0.5~1% 或 5~8% 得 10 分
      3. 当日涨幅（20分）：>=1% 得 20 分，0.5~1% 得 12 分
      4. 量比（10分）：1.0~1.5 得 10 分，0.8~1.0 或 1.5~2.0 得 5 分
      5. 实体比例（10分）：>=0.4 得 10 分，0.2~0.4 得 5 分
      6. J值位置（10分）：0~2 得 10 分，-2~0 或 2~4 得 5 分
      7. 距MA60（5分）：2~5% 得 5 分，0~2% 得 3 分

    Args:
        result: C154 筛选结果 DataFrame
        df: 原始完整数据 DataFrame

    Returns:
        添加了 score 和各维度分列的 DataFrame，按 score 降序排列
    """
    scored = result.copy()

    mv_p30 = df['total_mv'].quantile(0.3)
    mv_p50 = df['total_mv'].median()
    scored['score_mv'] = scored['total_mv'].apply(
        lambda x: 25 if pd.notna(x) and x <= mv_p30
        else (12 if pd.notna(x) and x <= mv_p50 else (4 if pd.notna(x) else 0))
    )

    scored['score_turnover'] = scored['turnover_rate'].apply(
        lambda x: 20 if pd.notna(x) and 1 <= x < 5
        else (10 if pd.notna(x) and ((0.5 <= x < 1) or (5 <= x < 8)) else (0 if pd.notna(x) else 0))
    )

    scored['score_pct'] = scored['pct_chg'].apply(
        lambda x: 20 if pd.notna(x) and x >= 1
        else (12 if pd.notna(x) and 0.5 <= x < 1 else (5 if pd.notna(x) and 0 <= x < 0.5 else (0 if pd.notna(x) else 0)))
    )

    scored['score_vr'] = scored['volume_ratio'].apply(
        lambda x: 10 if pd.notna(x) and 1.0 <= x < 1.5
        else (5 if pd.notna(x) and ((0.8 <= x < 1.0) or (1.5 <= x < 2.0)) else (0 if pd.notna(x) else 0))
    )

    if 'open_qfq' in scored.columns and 'high_qfq' in scored.columns:
        hi = scored['high_qfq']
        lo = scored['low_qfq']
        cl = scored['close_qfq']
        op = scored['open_qfq']
        rng = hi - lo
        body = (cl - op).abs()
        scored['body_ratio'] = np.where(rng > 0, body / rng, 0)
    scored['score_body'] = scored['body_ratio'].apply(
        lambda x: 10 if x >= 0.4 else (5 if x >= 0.2 else 0)
    )

    scored['score_j'] = scored['kdj_qfq'].apply(
        lambda x: 10 if 0 <= x < 2
        else (5 if (-2 <= x < 0 or 2 <= x < 4) else (0 if pd.notna(x) else 0))
    )

    if 'close_qfq' in scored.columns and 'ma_qfq_60' in scored.columns:
        scored['close_vs_ma60'] = (scored['close_qfq'] - scored['ma_qfq_60']) / scored['ma_qfq_60'] * 100
    else:
        scored['close_vs_ma60'] = 0
    scored['score_ma60'] = scored['close_vs_ma60'].apply(
        lambda x: 5 if pd.notna(x) and 2 <= x < 5
        else (3 if pd.notna(x) and 0 <= x < 2 else (0 if pd.notna(x) else 0))
    )

    score_cols = ['score_mv', 'score_turnover', 'score_pct', 'score_vr',
                  'score_body', 'score_j', 'score_ma60']
    scored['score'] = scored[score_cols].sum(axis=1)

    scored['score_level'] = scored['score'].apply(
        lambda x: 'A' if x >= 80 else ('B' if x >= 65 else ('C' if x >= 50 else 'D'))
    )

    return scored.sort_values('score', ascending=False)


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
        'turnover_rate', 'volume_ratio', 'total_mv', 'circ_mv',
        'open_qfq', 'high_qfq', 'low_qfq', 'kdj_k_qfq', 'kdj_d_qfq',
        'macd_dea_qfq', 'macd_qfq', 'pe_ttm', 'pb',
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
    """打印 C154 策略筛选结果（含评分）"""
    print('\n========== C154 最优组合选股结果 ==========')
    print('条件: 阶梯放量+J13低吸 & 不跌 & 无出货 & 底部暴力K & J<5 & 异动')
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
            mv_wan = row['total_mv'] / 10000 if pd.notna(row.get('total_mv')) else 0
            row_items.extend([
                f'{row["score"]:.0f}',
                row['score_level'],
                f'{row.get("turnover_rate", 0):.1f}',
                f'{mv_wan:.0f}万',
                f'{row.get("volume_ratio", 0):.2f}',
                f'{row.get("body_ratio", 0):.2f}',
            ])
        table_data.append(row_items)

    if has_score:
        headers = ['代码', '名称', '行业', '收盘价', 'J值', '涨跌幅', '成交额',
                   '评分', '等级', '换手%', '市值', '量比', '实体比']
    else:
        headers = ['代码', '名称', '行业', '收盘价', 'J值', '涨跌幅', '成交额']
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


def compute_c154_funnel_stats(df, result):
    """计算 C154 策略漏斗统计数据（供外部调用）

    Args:
        df: 含所有策略标记的 DataFrame
        result: C154 筛选结果 DataFrame

    Returns:
        dict: 各阶段股票数量统计
    """
    return {
        '全市场': df['ts_code'].nunique(),
        '阶梯放量+J13': int(df.groupby('ts_code')['first_j13_step'].max().astype(bool).sum()),
        '不跌': int(df[df['first_j13_step'] & (df['pct_chg'].fillna(-100) >= 0)]['ts_code'].nunique()),
        '无出货': int(df[df['first_j13_step'] & (df['pct_chg'].fillna(-100) >= 0) & ~df['has_distribution_signal'] & ~df['has_distribution_signal_v2'] & ~df['has_distribution_signal_v3']]['ts_code'].nunique()),
        '底部暴力K': int(df[df['first_j13_step'] & (df['pct_chg'].fillna(-100) >= 0) & ~df['has_distribution_signal'] & ~df['has_distribution_signal_v2'] & ~df['has_distribution_signal_v3'] & df['has_bottom_violent_k']]['ts_code'].nunique()),
        'J<5': int(df[df['first_j13_step'] & (df['pct_chg'].fillna(-100) >= 0) & ~df['has_distribution_signal'] & ~df['has_distribution_signal_v2'] & ~df['has_distribution_signal_v3'] & df['has_bottom_violent_k'] & (df['kdj_qfq'].fillna(100) < 5)]['ts_code'].nunique()),
        '异动': int(df[df['first_j13_step'] & (df['pct_chg'].fillna(-100) >= 0) & ~df['has_distribution_signal'] & ~df['has_distribution_signal_v2'] & ~df['has_distribution_signal_v3'] & df['has_bottom_violent_k'] & (df['kdj_qfq'].fillna(100) < 5) & df['has_am_in_period']]['ts_code'].nunique()),
        '最终': int(result['ts_code'].nunique()),
    }


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

        # 计算评分并排序
        if not result.empty:
            result = calculate_c154_score(result, df)

        # 打印结果
        print_c154_results(result, df, end_date)
        save_c154_result(result, end_date)
        print_c154_stage_statistics(df, result, args)

        # 生成 HTML 报告
        industry_count = result['industry_name'].value_counts().to_dict()
        funnel_stats = {
            '全市场': df['ts_code'].nunique(),
            '阶梯放量+J13': int(df.groupby('ts_code')['first_j13_step'].max().astype(bool).sum()),
            '不跌': int(df[df['first_j13_step'] & (df['pct_chg'].fillna(-100) >= 0)]['ts_code'].nunique()),
            '无出货': int(df[df['first_j13_step'] & (df['pct_chg'].fillna(-100) >= 0) & ~df['has_distribution_signal'] & ~df['has_distribution_signal_v2'] & ~df['has_distribution_signal_v3']]['ts_code'].nunique()),
            '底部暴力K': int(df[df['first_j13_step'] & (df['pct_chg'].fillna(-100) >= 0) & ~df['has_distribution_signal'] & ~df['has_distribution_signal_v2'] & ~df['has_distribution_signal_v3'] & df['has_bottom_violent_k']]['ts_code'].nunique()),
            'J<5': int(df[df['first_j13_step'] & (df['pct_chg'].fillna(-100) >= 0) & ~df['has_distribution_signal'] & ~df['has_distribution_signal_v2'] & ~df['has_distribution_signal_v3'] & df['has_bottom_violent_k'] & (df['kdj_qfq'].fillna(100) < 5)]['ts_code'].nunique()),
            '异动': int(df[df['first_j13_step'] & (df['pct_chg'].fillna(-100) >= 0) & ~df['has_distribution_signal'] & ~df['has_distribution_signal_v2'] & ~df['has_distribution_signal_v3'] & df['has_bottom_violent_k'] & (df['kdj_qfq'].fillna(100) < 5) & df['has_am_in_period']]['ts_code'].nunique()),
            '最终': int(result['ts_code'].nunique()),
        }
        generate_c154_html(result, df, end_date, funnel_stats, industry_count)

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
