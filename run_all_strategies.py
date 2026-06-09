#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一策略管道 (run_all_strategies.py)

将 main_par2 / main_par3 / main_par4 合并为一次执行，
共享数据获取和策略标记计算，避免重复调用 API 和重复计算。

核心优化：
  - fetch_and_prepare_data() 只执行 1 次（原先执行 3 次）
  - apply_strategy_marks() 只执行 1 次（原先执行 3 次）
  - 预计运行时间从 ~30min 降至 ~10-12min

使用方式：
    python run_all_strategies.py --date 20260609 --backtest --hold-days 3
    python run_all_strategies.py --date 20260609 --days 250 --backtest

各脚本仍可独立运行：
    python main_par2.py --date 20260609 --backtest --hold-days 3
    python main_par3.py --date 20260609
    python main_par4.py --date 20260609
"""

import argparse
import logging
from datetime import datetime, timedelta

import pandas as pd

from config import (
    APIConfig,
    BACKTEST_CONFIG as BT,
    DBConfig,
    ParallelConfig,
    STRATEGY_CONFIG as ST,
)
from data_manager import DataManager

# main_par2 的共享函数
from main_par2 import (
    STOCK_FACTOR_FIELDS,
    apply_strategy_marks,
    backtest_selected_stocks,
    calculate_amount_rank,
    calculate_daily_stats,
    calculate_trend_indicators,
    calculate_zhixing_brick_indicator,
    debug_stock_strategy_detailed,
    fetch_and_prepare_data,
    generate_industry_visualization,
    generate_j13_trend,
    get_nearest_trade_date,
    get_next_trade_date,
    prepare_trade_dates,
    print_backtest_stats,
    print_daily_stats,
    print_results,
    print_stage_statistics,
    run_sentiment_rebound_strategy,
    apply_final_filter,
)

# main_par3 的 C154 策略函数
from main_par3 import (
    apply_c154_filter,
    calculate_c154_score,
    compute_c154_funnel_stats,
    print_c154_results,
    print_c154_stage_statistics,
    save_c154_result,
)

# main_par4 的 C432 策略函数
from main_par4 import (
    apply_c432_filter,
    calculate_c432_score,
    compute_c432_funnel_stats,
    print_c432_results,
    print_c432_stage_statistics,
    save_c432_result,
)

# HTML 报告生成
from generate_stock_html import generate_c154_html, generate_c432_html

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s | %(message)s")


def parse_args():
    parser = argparse.ArgumentParser(
        description="统一策略管道 — 一次计算，三套筛选",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python run_all_strategies.py --date 20260609 --backtest --hold-days 3
  python run_all_strategies.py --date 20260609 --days 250
        """,
    )
    parser.add_argument("--date", type=str, default=None,
                        help="目标日期，格式 YYYYMMDD，默认今天")
    parser.add_argument("--days", type=int, default=250,
                        help="回测天数，默认250（兼顾 C154/C432 回测需求）")
    parser.add_argument("--debug", type=str, default="",
                        help="调试模式，传入股票代码（逗号分隔）")
    parser.add_argument("--backtest", action="store_true",
                        help="是否执行回测")
    parser.add_argument("--hold-days", type=int, default=3,
                        help="回测持有天数，默认3天")
    parser.add_argument("--detailed", action="store_true",
                        help="是否打印逐日持仓数据")
    return parser.parse_args()


def prepare_unified_trade_dates(args, data_manager):
    """准备统一交易日期范围

    取各策略的最大回看窗口，确保数据覆盖所有策略需求：
    - main_par2: days + 114(MA) + 60(buffer) = ~424 天回看
    - main_par3: days * 2 回看
    - main_par4: days * 2 + 200 回看（最大）

    使用 main_par4 的回看窗口（最大），保证所有策略都能正确计算。
    """
    if args.date:
        end_date = args.date
        today = datetime.strptime(end_date, "%Y%m%d")
    else:
        end_date = get_nearest_trade_date(data_manager)
        today = datetime.strptime(end_date, "%Y%m%d")

    # main_par4 的回看窗口最大：days*2 + 200
    # 再加 MA 最大周期 114 + 60 缓冲
    lookback_days = args.days * 2 + 200 + 114 + 60
    lookback_start_dt = today - timedelta(days=lookback_days)
    lookback_start_date = lookback_start_dt.strftime('%Y%m%d')

    trade_dates_range = data_manager.get_trade_dates(lookback_start_date, end_date)
    trade_dates_range = sorted(trade_dates_range)

    if len(trade_dates_range) >= args.days:
        recent_trade_dates = trade_dates_range[-args.days:]
        start_date = recent_trade_dates[0]
        end_date = recent_trade_dates[-1]
        actual_days = args.days
    else:
        logging.warning("交易日历数据不足 %d 天，实际只有 %d 天", args.days, len(trade_dates_range))
        start_date = trade_dates_range[0] if trade_dates_range else end_date
        actual_days = len(trade_dates_range)

    print(f"回测区间：{start_date} ~ {end_date}，共 {actual_days} 个交易日")
    return start_date, end_date, actual_days, trade_dates_range


def main():
    args = parse_args()
    data_manager = DataManager()

    try:
        # 第一阶段：共享数据准备（只执行一次）
        print("=" * 70)
        print("统一策略管道 -- 开始执行")
        print("=" * 70)

        # 1. 准备日期（取最大回看窗口）
        start_date, end_date, actual_days, trade_dates_range = prepare_unified_trade_dates(args, data_manager)

        # 2. 获取数据（API 调用 -- 最耗时之一，只做一次）
        print("\n获取股票数据...")
        df_full = fetch_and_prepare_data(data_manager, trade_dates_range)
        if df_full.empty:
            logging.error("未获取到数据，退出程序")
            return

        df_chart = df_full.copy()
        df = df_full[df_full['trade_date'] >= start_date].copy()
        logging.info("数据筛选后: %d 条记录 (回测区间 %s ~ %s)", len(df), start_date, end_date)

        # 3. 获取股票基本信息
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

        # 4. 应用策略标记（并行计算 -- 最耗时，只做一次）
        print("\n计算策略标记...")
        df = apply_strategy_marks(df)
        df = calculate_trend_indicators(df)
        df = calculate_zhixing_brick_indicator(df)
        df = calculate_amount_rank(df)

        print("共享数据准备完成，共 %d 只股票，%d 条记录" % (df['ts_code'].nunique(), len(df)))

        # 第二阶段：分别应用三套筛选策略

        # 策略 1：主策略 (原 main_par2)
        print("\n" + "=" * 70)
        print("策略1：主策略筛选 (stock_selection)")
        print("=" * 70)
        result_par2 = apply_final_filter(df, end_date, basic)
        print_results(result_par2, df, end_date, df_chart)
        print_stage_statistics(df, result_par2, args)

        # 策略 2：C154 最优组合
        print("\n" + "=" * 70)
        print("策略2：C154 最优组合筛选")
        print("=" * 70)
        result_c154 = apply_c154_filter(df, end_date, basic)
        if not result_c154.empty:
            result_c154 = calculate_c154_score(result_c154, df)
        print_c154_results(result_c154, df, end_date)
        save_c154_result(result_c154, end_date)
        print_c154_stage_statistics(df, result_c154, args)

        # 生成 C154 HTML 报告
        if not result_c154.empty:
            industry_count_c154 = result_c154['industry_name'].value_counts().to_dict()
            funnel_c154 = compute_c154_funnel_stats(df, result_c154)
            generate_c154_html(result_c154, df, end_date, funnel_c154, industry_count_c154)

        # 策略 3：C432 最优组合
        print("\n" + "=" * 70)
        print("策略3：C432 最优组合筛选")
        print("=" * 70)
        result_c432 = apply_c432_filter(df, end_date, basic)
        if not result_c432.empty:
            result_c432 = calculate_c432_score(result_c432, df)
        print_c432_results(result_c432, df, end_date)
        save_c432_result(result_c432, end_date)
        print_c432_stage_statistics(df, result_c432, args)

        # 生成 C432 HTML 报告
        if not result_c432.empty:
            industry_count_c432 = result_c432['industry_name'].value_counts().to_dict()
            funnel_c432 = compute_c432_funnel_stats(df, result_c432)
            generate_c432_html(result_c432, df, end_date, funnel_c432, industry_count_c432)

        # 第三阶段：回测 + 可视化

        # 回测
        if args.backtest:
            buy_date = get_next_trade_date(end_date, data_manager)
            if buy_date:
                strategies = [
                    ("主策略 Stock Selection", result_par2),
                    ("C154 最优组合", result_c154),
                    ("C432 最优组合", result_c432),
                ]
                for name, result in strategies:
                    if not result.empty:
                        print(f"\n{'=' * 70}")
                        print(f"回测: {name}")
                        print(f"{'=' * 70}")
                        bt_results = backtest_selected_stocks(
                            result['ts_code'].tolist(),
                            buy_date,
                            data_manager,
                            hold_days=args.hold_days,
                            detailed=args.detailed,
                        )
                        print_backtest_stats(bt_results)

        # 每日统计和可视化
        print(f"\n{'=' * 70}")
        print("生成可视化报告")
        print(f"{'=' * 70}")
        daily_stats = calculate_daily_stats(df, basic_info)
        print_daily_stats(daily_stats)
        generate_industry_visualization(df, daily_stats, end_date)
        generate_j13_trend(df, end_date)

        # 情绪反弹策略
        run_sentiment_rebound_strategy(df, end_date, data_manager)

        # 调试模式
        if args.debug:
            for ts_code in [c.strip() for c in args.debug.split(',')]:
                debug_stock_strategy_detailed(df, ts_code, end_date, basic)

        print(f"\n{'=' * 70}")
        print("全部策略执行完成！")
        print(f"{'=' * 70}")

    finally:
        data_manager.close()


if __name__ == "__main__":
    main()
