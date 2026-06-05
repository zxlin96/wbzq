#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
C432 策略打分系统验证回测

验证假设：分数越高 → 胜率越高、涨幅越大、盈亏比越高
按评分等级(A/B/C/D)和分段(<50/50-65/65-80/≥80)分别统计
"""

import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from tabulate import tabulate
from tqdm import tqdm

from config import APIConfig, BACKTEST_CONFIG as BT
from data_manager import DataManager
from main_par2 import (
    apply_strategy_marks,
    calculate_amount_rank,
    calculate_trend_indicators,
    calculate_zhixing_brick_indicator,
    fetch_and_prepare_data,
    get_nearest_trade_date,
)
from main_par4 import apply_c432_filter, calculate_c432_score
from backtest_ablation import preload_price_data

import tushare as ts
ts.set_token(APIConfig.get_token())

import logging
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s | %(message)s")


def run_score_backtest(df, trade_dates_list, data_manager, hold_days):
    """逐日运行 C432 筛选 + 评分 + 次日买入回测，记录每笔交易的评分和涨幅"""
    all_trades = []
    held_positions = set()
    trade_dates_sorted = sorted(trade_dates_list)

    price_lookup, _ = preload_price_data(data_manager, trade_dates_sorted, hold_days)

    next_date_map = {}
    trade_date_idx_map = {}
    for i, d in enumerate(trade_dates_sorted):
        trade_date_idx_map[d] = i
        next_date_map[d] = trade_dates_sorted[i + 1] if i + 1 < len(trade_dates_sorted) else None

    df_by_date = {d: g for d, g in df.groupby("trade_date")}

    for signal_date in tqdm(trade_dates_sorted, desc="C432评分回测", leave=False):
        next_date = next_date_map.get(signal_date)
        if next_date is None:
            continue

        df_day = df_by_date.get(signal_date)
        if df_day is None or df_day.empty:
            continue

        # 构建临时 basic（仅用于过滤次新）
        # 次新过滤已在之前完成，这里直接用 df_day
        basic_ts_codes = set(df_day["ts_code"].unique())

        # 直接构建 C432 条件掩码
        zhixing_mid = df_day["zhixing_mid_duokong"]
        close = df_day["close_qfq"]
        near_mid_cond = (zhixing_mid > 0) & ((close - zhixing_mid).abs() / zhixing_mid <= 0.02)

        mask = (
            df_day["first_j13_step"].fillna(False)
            & (df_day["pct_chg"].fillna(-100) >= 0)
            & df_day["has_bottom_violent_k"].fillna(False)
            & (df_day["macd_dif_qfq"].fillna(0) > 0)
            & near_mid_cond
            & (df_day["kdj_qfq"].fillna(100) < -5)
            & df_day["shrink"].fillna(False)
            & df_day["has_am_in_period"].fillna(False)
        )

        selected = df_day[mask]
        selected_codes = [c for c in selected["ts_code"].unique() if c not in held_positions]

        if not selected_codes:
            continue

        # 为当天信号计算评分
        result_df = selected.copy()
        # 需要传入完整的 df 用于计算分位数
        result_df = calculate_c432_score(result_df, df)

        next_idx = trade_date_idx_map.get(next_date)
        if next_idx is None:
            continue
        end_idx = min(next_idx + hold_days, len(trade_dates_sorted) - 1)
        hold_dates = trade_dates_sorted[next_idx:end_idx + 1]

        buy_day_data = price_lookup.get(next_date)
        if buy_day_data is None:
            continue

        for _, row in result_df.iterrows():
            ts_code = row["ts_code"]
            if ts_code in held_positions:
                continue
            if ts_code not in buy_day_data.index:
                continue

            buy_price = buy_day_data.loc[ts_code, "open_qfq"]
            if pd.isna(buy_price) or buy_price <= 0:
                continue

            final_price = None
            max_price = buy_price
            valid_days = 0

            for d in hold_dates:
                day_data = price_lookup.get(d)
                if day_data is None or ts_code not in day_data.index:
                    continue
                r = day_data.loc[ts_code]
                high = r["high_qfq"]
                c = r["close_qfq"]
                if not pd.isna(high):
                    max_price = max(max_price, high)
                if not pd.isna(c):
                    final_price = c
                valid_days += 1

            if final_price is None or valid_days < 1:
                continue

            gain_pct = round((final_price - buy_price) / buy_price * 100, 2)
            max_gain_pct = round((max_price - buy_price) / buy_price * 100, 2)

            all_trades.append({
                "ts_code": ts_code,
                "signal_date": signal_date,
                "buy_date": next_date,
                "buy_price": round(buy_price, 2),
                "final_price": round(final_price, 2),
                "gain_pct": gain_pct,
                "max_gain_pct": max_gain_pct,
                "score": int(row.get("score", 0)),
                "score_level": row.get("score_level", "D"),
                # 各维度分数 (v4: 3维度)
                "score_mid_dist": int(row.get("score_mid_dist", 0)),
                "score_macd": int(row.get("score_macd", 0)),
                "score_body": int(row.get("score_body", 0)),
            })
            held_positions.add(ts_code)

        # 清理过期持仓
        expired = set()
        sig_idx = trade_date_idx_map.get(signal_date)
        if sig_idx is not None and hold_days > 0:
            expire_threshold = sig_idx - hold_days
            for code in list(held_positions):
                for t in all_trades[-len(selected_codes):]:
                    if code in t["ts_code"] and trade_date_idx_map.get(t["buy_date"], 0) < expire_threshold:
                        expired.add(code)
                        break
        held_positions -= expired

    return pd.DataFrame(all_trades)


def compute_group_stats(trades_df, group_col, group_values):
    """按分组列计算统计指标"""
    results = []
    for val in group_values:
        group = trades_df[trades_df[group_col] == val]
        if group.empty:
            continue
        gains = group["gain_pct"]
        wins = gains[gains > 0]
        losses = gains[gains < 0]
        avg_gain = wins.mean() if len(wins) > 0 else 0
        avg_loss = abs(losses.mean()) if len(losses) > 0 else 0.01
        results.append({
            group_col: val,
            "样本量": len(gains),
            "平均涨幅": round(gains.mean(), 2),
            "中位涨幅": round(gains.median(), 2),
            "胜率": round((gains > 0).mean() * 100, 1),
            "大胜率(>3%)": round((gains > 3).mean() * 100, 1),
            "盈亏比": round(avg_gain / avg_loss, 2) if avg_loss > 0 else np.nan,
            "最大涨幅": round(gains.max(), 2),
            "最大跌幅": round(gains.min(), 2),
        })
    return results


def main():
    data_manager = DataManager()
    try:
        end_date = get_nearest_trade_date(data_manager)
        logging.info("最近交易日: %s", end_date)

        end_dt = datetime.strptime(end_date, "%Y%m%d")
        lookback_start_dt = end_dt - timedelta(days=250 * 2 + 200)
        lookback_start_date = lookback_start_dt.strftime("%Y%m%d")

        trade_dates_full = data_manager.get_trade_dates(lookback_start_date, end_date)
        trade_dates_full = sorted(trade_dates_full)

        backtest_dates = trade_dates_full[-250:]
        start_date = backtest_dates[0]

        # 解析持有天数参数
        import argparse as _argparse
        _parser = _argparse.ArgumentParser()
        _parser.add_argument("--hold-days", type=int, default=0, help="持有天数")
        _args2, _ = _parser.parse_known_args()
        hold_days = _args2.hold_days

        print(f"\n{'='*70}")
        print(f"📊 C432 打分系统验证回测")
        print(f"  回测区间: {start_date} ~ {end_date} ({len(backtest_dates)} 个交易日)")
        print(f"  持有天数: {hold_days}天（次日开盘买入，{'当天收盘卖出' if hold_days == 0 else f'持有{hold_days}天收盘卖出'}）")
        print(f"{'='*70}")

        print("\n[1/4] 获取并准备数据...")
        df_full = fetch_and_prepare_data(data_manager, trade_dates_full)
        if df_full.empty:
            return

        df = df_full[df_full["trade_date"] >= start_date].copy()
        logging.info("数据: %d 条", len(df))

        print("\n[2/4] 获取基本信息...")
        basic_info = data_manager.get_stock_basic_info()
        if "name" not in basic_info.columns:
            basic_info["name"] = basic_info["ts_code"]
        if "industry_name" not in basic_info.columns:
            basic_info["industry_name"] = "未知行业"
        basic_info["industry_name"] = basic_info["industry_name"].fillna("未知行业")
        basic_info["name"] = basic_info["name"].fillna(basic_info["ts_code"])
        basic = basic_info[basic_info["list_date"].notna()].copy()
        df = df.merge(basic[["ts_code", "name", "industry_name"]], on="ts_code", how="left")

        cutoff_date = pd.to_datetime(end_date) - pd.Timedelta(days=BT.MIN_STOCK_AGE_DAYS)
        basic["list_date"] = pd.to_datetime(basic["list_date"])
        non_new_stocks = set(basic[basic["list_date"] <= cutoff_date]["ts_code"])
        df = df[df["ts_code"].isin(non_new_stocks)].copy()

        print("\n[3/4] 计算策略标记...")
        df = apply_strategy_marks(df)
        df = calculate_trend_indicators(df)
        df = calculate_zhixing_brick_indicator(df)
        df = calculate_amount_rank(df)

        print("\n[4/4] 执行评分回测...")

        trades_df = run_score_backtest(df, backtest_dates, data_manager, hold_days=hold_days)

        if trades_df.empty:
            print("\n⚠️ 回测期间无交易记录，无法验证打分系统")
            return

        print(f"\n总交易笔数: {len(trades_df)}")

        # ===== 按评分等级统计 =====
        print("\n" + "=" * 100)
        print("按评分等级(A/B/C/D)分组统计")
        print("=" * 100)
        level_results = compute_group_stats(trades_df, "score_level", ["A", "B", "C", "D"])
        if level_results:
            df_levels = pd.DataFrame(level_results)
            print(tabulate(df_levels, headers="keys", tablefmt="github", showindex=False))

        # ===== 按分数段统计 =====
        print("\n" + "=" * 100)
        print("按分数段统计")
        print("=" * 100)
        bins = [0, 30, 40, 50, 60, 70, 80, 90, 100]
        labels = ["<30", "30-40", "40-50", "50-60", "60-70", "70-80", "80-90", "≥90"]
        trades_df["score_bin"] = pd.cut(trades_df["score"], bins=bins, labels=labels, right=True, include_lowest=True)
        bin_results = compute_group_stats(trades_df, "score_bin", labels)
        if bin_results:
            df_bins = pd.DataFrame(bin_results)
            print(tabulate(df_bins, headers="keys", tablefmt="github", showindex=False))

        # ===== 按核心维度得分统计 =====
        print("\n" + "=" * 100)
        print("按「核心维度」总分(近中线+MACD, 满分90)分组")
        print("=" * 100)
        trades_df["win_score"] = (
            trades_df["score_mid_dist"]
            + trades_df["score_macd"]
        )
        win_bins = [0, 30, 45, 55, 65, 75, 90]
        win_labels = ["≤30", "31-45", "46-55", "56-65", "66-75", ">75"]
        trades_df["win_score_bin"] = pd.cut(trades_df["win_score"], bins=win_bins, labels=win_labels, right=True, include_lowest=True)
        win_results = compute_group_stats(trades_df, "win_score_bin", win_labels)
        if win_results:
            df_win = pd.DataFrame(win_results)
            print(tabulate(df_win, headers="keys", tablefmt="github", showindex=False))

        # ===== 各维度对胜率的独立贡献 =====
        print("\n" + "=" * 100)
        print("各维度高分组 vs 低分组的胜率/涨幅对比")
        print("=" * 100)
        dim_cols = {
            "score_mid_dist": ("近中线距离", 60),
            "score_macd": ("MACD强度", 30),
            "score_body": ("实体比例", 10),
        }
        dim_rows = []
        for col, (name, max_score) in dim_cols.items():
            high_threshold = max_score * 0.7  # 高分组：得分为满分的70%以上
            high = trades_df[trades_df[col] >= high_threshold]
            low = trades_df[trades_df[col] < high_threshold]
            if high.empty or low.empty:
                continue
            dim_rows.append({
                "维度": f"{name}(≥{high_threshold:.0f}分)",
                "高分组样本": len(high),
                "高分组胜率": round((high["gain_pct"] > 0).mean() * 100, 1),
                "高分组涨幅": round(high["gain_pct"].mean(), 2),
                "低分组样本": len(low),
                "低分组胜率": round((low["gain_pct"] > 0).mean() * 100, 1),
                "低分组涨幅": round(low["gain_pct"].mean(), 2),
                "胜率差": round((high["gain_pct"] > 0).mean() * 100 - (low["gain_pct"] > 0).mean() * 100, 1),
                "涨幅差": round(high["gain_pct"].mean() - low["gain_pct"].mean(), 2),
            })
        if dim_rows:
            df_dim = pd.DataFrame(dim_rows)
            print(tabulate(df_dim, headers="keys", tablefmt="github", showindex=False))

        # ===== 趋势判断 =====
        print("\n" + "=" * 100)
        print("📊 结论")
        print("=" * 100)

        if level_results:
            levels = pd.DataFrame(level_results)
            # 检查 A>B>C>D 的趋势
            wr_trend = all(
                levels.iloc[i]["胜率"] >= levels.iloc[i+1]["胜率"]
                for i in range(len(levels)-1) if pd.notna(levels.iloc[i]["胜率"]) and pd.notna(levels.iloc[i+1]["胜率"])
            )
            gain_trend = all(
                levels.iloc[i]["平均涨幅"] >= levels.iloc[i+1]["平均涨幅"]
                for i in range(len(levels)-1) if pd.notna(levels.iloc[i]["平均涨幅"]) and pd.notna(levels.iloc[i+1]["平均涨幅"])
            )
            pl_trend = all(
                levels.iloc[i]["盈亏比"] >= levels.iloc[i+1]["盈亏比"]
                for i in range(len(levels)-1) if pd.notna(levels.iloc[i]["盈亏比"]) and pd.notna(levels.iloc[i+1]["盈亏比"])
            )
            print(f"  胜率 A≥B≥C≥D: {'✅ 是' if wr_trend else '❌ 否'}")
            print(f"  涨幅 A≥B≥C≥D: {'✅ 是' if gain_trend else '❌ 否'}")
            print(f"  盈亏比 A≥B≥C≥D: {'✅ 是' if pl_trend else '❌ 否'}")

            if wr_trend and gain_trend:
                print("  → 打分系统有效：高分股确实表现更优")
            elif wr_trend:
                print("  → 胜率维度有效，涨幅维度需优化")
            elif gain_trend:
                print("  → 涨幅维度有效，胜率维度需优化")
            else:
                print("  → 打分系统需要调整权重/阈值")

    finally:
        data_manager.close()


if __name__ == "__main__":
    main()
