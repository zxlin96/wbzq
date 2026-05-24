#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
选股条件消融实验回测脚本 (backtest_ablation.py)

目标：探究除了 J<13 (first_j13_step) 以外，其他各选股条件对成功率的影响。
成功率以买入后3天内涨幅为标准。

实验设计：
  Part A - 基准递增：在 J<13 基准上逐个加入条件，观察每个条件的边际贡献
  Part B - 完整消融：从完整16条件中逐个移除，观察每个条件的必要性

使用方式：
    python backtest_ablation.py                    # 默认最近250交易日，持有3天
    python backtest_ablation.py --days 120         # 最近120交易日
    python backtest_ablation.py --hold-days 5      # 持有5天
    python backtest_ablation.py --skip-b           # 跳过Part B（节省时间）
"""

import argparse
import inspect
import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from tabulate import tabulate
from tqdm import tqdm

from config import APIConfig, BACKTEST_CONFIG as BT, STRATEGY_CONFIG as ST
from data_manager import DataManager
from main_par2 import (
    STOCK_FACTOR_FIELDS,
    apply_strategy_marks,
    calculate_amount_rank,
    calculate_trend_indicators,
    calculate_zhixing_brick_indicator,
    fetch_and_prepare_data,
    get_nearest_trade_date,
    get_simple_industry_info,
    mark_abnormal_movement,
    mark_bottom_violent_k,
    mark_distribution_signal,
    mark_distribution_signal_v2,
    mark_distribution_signal_v3,
    mark_step_vol_price,
    mark_volume_surge,
    _threaded_apply_grouped,
    _is_main_board,
    identify_candle_pattern,
)

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s | %(message)s")

import tushare as ts
ts.set_token(APIConfig.get_token())
pro = ts.pro_api()


def parse_args():
    parser = argparse.ArgumentParser(description="选股条件消融实验回测")
    parser.add_argument("--days", type=int, default=250, help="回测交易日数，默认250")
    parser.add_argument("--hold-days", type=int, default=3, help="持有天数，默认3")
    parser.add_argument("--skip-a", action="store_true", help="跳过Part A基准递增实验")
    parser.add_argument("--skip-b", action="store_true", help="跳过Part B完整消融实验")
    parser.add_argument("--skip-c", action="store_true", help="跳过Part C最优组合实验")
    parser.add_argument("--skip-d", action="store_true", help="跳过Part D多头/空头择时回测")
    return parser.parse_args()


from web_dashboard import MARKET_PHASES, generate_html_report

CONDITIONS = {
    "first_j13_step": "阶梯放量+J<13",
    "macd_dif>0": "MACD多头(DIF>0)",
    "shrink": "缩量回调",
    "~gap_up": "无跳空高开",
    "close>MA60": "收盘价>60日线",
    "ma60_upward": "MA60向上",
    "candle_ok": "K线形态可接受",
    "amplitude_ok": "振幅达标",
    "has_am": "周期内异动",
    "amount_top": "成交额前60%",
    "has_bvk": "底部暴力K",
    "no_dist": "无出货信号",
    "zhixing_ok": "知行多空线",
}

CONDITION_KEYS = list(CONDITIONS.keys())


def build_condition_mask(df, condition_key: str, basic_ts_codes) -> pd.Series:
    """根据条件 key 构建布尔掩码"""
    if condition_key == "first_j13_step":
        return df["first_j13_step"].fillna(False)
    elif condition_key == "macd_dif>0":
        return df["macd_dif_qfq"] > 0
    elif condition_key == "shrink":
        return df["shrink"].fillna(False)
    elif condition_key == "~gap_up":
        return ~df["gap_up"].fillna(False)
    elif condition_key == "close>MA60":
        return df["close_qfq"] > df["ma_qfq_60"]
    elif condition_key == "ma60_upward":
        return df["ma60_upward"].fillna(False)
    elif condition_key == "candle_ok":
        return df["is_acceptable_candle"].fillna(False)
    elif condition_key == "amplitude_ok":
        return df["is_amplitude_ok"].fillna(False)
    elif condition_key == "has_am":
        return df["has_am_in_period"].fillna(False)
    elif condition_key == "amount_top":
        return df["is_amount_top30"].fillna(False)
    elif condition_key == "has_bvk":
        return df["has_bottom_violent_k"].fillna(False)
    elif condition_key == "no_dist":
        return (
            ~df["has_distribution_signal"].fillna(False)
            & ~df["has_distribution_signal_v2"].fillna(False)
            & ~df["has_distribution_signal_v3"].fillna(False)
        )
    elif condition_key == "zhixing_ok":
        return (
            (df["zhixing_mid_duokong"] > df["zhixing_duokong"])
            & (df["close_qfq"] >= df["zhixing_duokong"])
        )
    elif condition_key == "j_ultra_low":
        return df["kdj_qfq"].fillna(100) < 5
    elif condition_key == "vol_ratio>1":
        return df["volume_ratio"].fillna(0) > 1
    elif condition_key == "close>MA5":
        if "ma_qfq_5" not in df.columns:
            return pd.Series(False, index=df.index)
        return df["close_qfq"] > df["ma_qfq_5"]
    elif condition_key == "close>MA20":
        if "ma_qfq_20" not in df.columns:
            return pd.Series(False, index=df.index)
        return df["close_qfq"] > df["ma_qfq_20"]
    elif condition_key == "not_falling":
        return df["pct_chg"].fillna(-100) >= 0
    elif condition_key == "turnover>2":
        return df["turnover_rate"].fillna(0) > 2
    elif condition_key == "zhixing_mid_up":
        return df["zhixing_mid_duokong"] > df["zhixing_duokong"]
    elif condition_key.startswith("j<"):
        try:
            threshold = float(condition_key[2:])
        except ValueError:
            raise ValueError(f"Invalid J threshold in condition key: {condition_key}")
        return df["kdj_qfq"].fillna(100) < threshold
    else:
        raise ValueError(f"Unknown condition key: {condition_key}")


def apply_conditions(df, condition_keys: List[str], basic_ts_codes,
                     mask_cache: Optional[Dict[str, pd.Series]] = None) -> pd.Series:
    mask = pd.Series(True, index=df.index)
    for key in condition_keys:
        if mask_cache is not None and key in mask_cache:
            full_mask = mask_cache[key]
            mask = mask & full_mask.reindex(df.index, fill_value=False)
        else:
            mask = mask & build_condition_mask(df, key, basic_ts_codes)
    return mask


def compute_stats(trades_df: pd.DataFrame) -> Dict:
    """从交易记录计算统计指标"""
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


def preload_price_data(data_manager, trade_dates_list, hold_days):
    """预加载所有交易日的前复权价格数据到内存

    一次性加载全量价格数据，避免回测时逐日重复读取 parquet。
    返回按 trade_date 索引的 dict，每个值是只含选定字段的 DataFrame。

    Args:
        data_manager: DataManager 实例
        trade_dates_list: 所有交易日列表
        hold_days: 持有天数（用于扩展加载范围）

    Returns:
        price_lookup: {trade_date: DataFrame} 每日价格数据
        trade_date_set: 所有可用交易日的 set（用于快速查找 next_date）
    """
    fields = ["ts_code", "trade_date", "open_qfq", "high_qfq", "low_qfq", "close_qfq"]
    all_dates = sorted(set(trade_dates_list))

    logging.info("预加载价格数据: %d 个交易日...", len(all_dates))
    t0 = time.time()

    full_df = data_manager.get_stock_factors(all_dates, fields)

    if full_df.empty:
        logging.error("预加载价格数据失败")
        return {}, set()

    full_df = full_df[fields].dropna(subset=["close_qfq"])

    price_lookup = {}
    for date, day_df in full_df.groupby("trade_date"):
        price_lookup[date] = day_df[fields].set_index("ts_code")

    trade_date_set = set(all_dates)

    elapsed = time.time() - t0
    mem_mb = full_df.memory_usage(deep=True).sum() / 1024 / 1024
    logging.info("预加载完成: %d 天, %.1f MB, %.1f 秒", len(price_lookup), mem_mb, elapsed)

    return price_lookup, trade_date_set


def run_single_day_backtest_fast(
    df_signal_day: pd.DataFrame,
    signal_mask: pd.Series,
    next_date: str,
    price_lookup: dict,
    trade_date_sorted_list: list,
    trade_date_idx_map: dict,
    hold_days: int,
    held_positions: set,
) -> pd.DataFrame:
    """从预加载的内存数据执行单日回测（不调用 data_manager）

    Args:
        df_signal_day: 信号日当天的全市场数据
        signal_mask: 信号掩码
        next_date: 下一个交易日（买入日）
        price_lookup: {trade_date: DataFrame(index=ts_code)} 预加载价格
        trade_date_sorted_list: 有序交易日列表
        trade_date_idx_map: {date: index} 日期到索引的映射
        hold_days: 持有天数
        held_positions: 当前持仓中的股票集合

    Returns:
        交易记录 DataFrame
    """
    selected = df_signal_day[signal_mask]
    selected_codes = [c for c in selected["ts_code"].unique() if c not in held_positions]

    if not selected_codes:
        return pd.DataFrame()

    next_idx = trade_date_idx_map.get(next_date)
    if next_idx is None:
        return pd.DataFrame()

    end_idx = min(next_idx + hold_days, len(trade_date_sorted_list) - 1)
    hold_dates = trade_date_sorted_list[next_idx:end_idx + 1]

    if len(hold_dates) < 2:
        return pd.DataFrame()

    buy_day_data = price_lookup.get(next_date)
    if buy_day_data is None:
        return pd.DataFrame()

    buy_prices = buy_day_data.loc[buy_day_data.index.isin(selected_codes), "open_qfq"]

    results = []
    for ts_code in selected_codes:
        if ts_code not in buy_prices.index:
            continue
        buy_price = buy_prices[ts_code]
        if pd.isna(buy_price) or buy_price <= 0:
            continue

        final_price = None
        max_price = buy_price
        valid_days = 0

        for d in hold_dates:
            day_data = price_lookup.get(d)
            if day_data is None or ts_code not in day_data.index:
                continue
            row = day_data.loc[ts_code]
            high = row["high_qfq"]
            close = row["close_qfq"]
            if not pd.isna(high):
                max_price = max(max_price, high)
            if not pd.isna(close):
                final_price = close
            valid_days += 1

        if final_price is None or valid_days < 2:
            continue

        gain_pct = round((final_price - buy_price) / buy_price * 100, 2)
        max_gain_pct = round((max_price - buy_price) / buy_price * 100, 2)

        results.append(
            {
                "ts_code": ts_code,
                "signal_date": df_signal_day["trade_date"].iloc[0] if "trade_date" in df_signal_day.columns else "",
                "buy_date": next_date,
                "buy_price": round(buy_price, 2),
                "final_price": round(final_price, 2),
                "gain_pct": gain_pct,
                "max_gain_pct": max_gain_pct,
            }
        )

    return pd.DataFrame(results)


def run_single_day_backtest(
    df_signal_day: pd.DataFrame,
    signal_mask: pd.Series,
    next_date: str,
    data_manager: DataManager,
    hold_days: int,
    held_positions: set,
) -> pd.DataFrame:
    """对单个信号日执行回测

    Args:
        df_signal_day: 信号日当天的全市场数据
        signal_mask: 信号掩码（哪些行满足条件）
        next_date: 下一个交易日（买入日）
        data_manager: 数据管理器
        hold_days: 持有天数
        held_positions: 当前持仓中的股票集合（用于去重）

    Returns:
        交易记录 DataFrame
    """
    selected = df_signal_day[signal_mask]
    selected_codes = selected["ts_code"].unique().tolist()

    selected_codes = [c for c in selected_codes if c not in held_positions]

    if not selected_codes:
        return pd.DataFrame()

    hold_end_date = pd.to_datetime(next_date, format="%Y%m%d") + pd.Timedelta(days=hold_days + 5)
    trade_dates_hold = data_manager.get_trade_dates(next_date, hold_end_date.strftime("%Y%m%d"))

    if len(trade_dates_hold) < 2:
        return pd.DataFrame()

    fields = ["ts_code", "trade_date", "open_qfq", "high_qfq", "low_qfq", "close_qfq"]
    bt_df = data_manager.get_stock_factors(trade_dates_hold, fields)

    if bt_df.empty:
        return pd.DataFrame()

    bt_df = bt_df[bt_df["ts_code"].isin(selected_codes)]

    results = []
    for ts_code in selected_codes:
        stock_data = bt_df[bt_df["ts_code"] == ts_code].sort_values("trade_date")
        if stock_data.empty:
            continue

        buy_row = stock_data[stock_data["trade_date"] == next_date]
        if buy_row.empty:
            continue

        buy_price = buy_row["open_qfq"].iloc[0]
        if pd.isna(buy_price) or buy_price <= 0:
            continue

        hold_data = stock_data[stock_data["trade_date"] >= next_date].head(hold_days + 1)
        if len(hold_data) < 2:
            continue

        final_price = hold_data["close_qfq"].iloc[-1]
        gain_pct = round((final_price - buy_price) / buy_price * 100, 2)
        max_price = hold_data["high_qfq"].max()
        max_gain_pct = round((max_price - buy_price) / buy_price * 100, 2)

        results.append(
            {
                "ts_code": ts_code,
                "signal_date": df_signal_day["trade_date"].iloc[0] if "trade_date" in df_signal_day.columns else "",
                "buy_date": next_date,
                "buy_price": round(buy_price, 2),
                "final_price": round(final_price, 2),
                "gain_pct": gain_pct,
                "max_gain_pct": max_gain_pct,
            }
        )

    return pd.DataFrame(results)


def compute_phase_stats(trades_df: pd.DataFrame) -> List[Dict]:
    """将交易记录按多头/空头区间拆分，分别计算统计指标

    Returns:
        List of Dict，每个元素含 实验组、市场阶段、阶段类型 及各项统计
    """
    phase_results = []
    for phase in MARKET_PHASES:
        phase_type = "多头" if "多头" in phase["label"] else "空头"
        phase_trades = trades_df[
            (trades_df["signal_date"] >= phase["start"])
            & (trades_df["signal_date"] <= phase["end"])
        ]
        stats = compute_stats(phase_trades)
        stats["市场阶段"] = phase["label"]
        stats["阶段类型"] = phase_type
        phase_results.append(stats)
    return phase_results


def print_phase_split_table(trades_map: Dict, label_prefix: str):
    """为某个 Part 的 trades_map 打印多头/空头拆分表

    Args:
        trades_map: {实验组label: trades_df} 字典
        label_prefix: "A" / "B" / "C"
    """
    all_phase_rows = []
    for group_label, trades_df in trades_map.items():
        if trades_df.empty:
            continue
        phase_stats = compute_phase_stats(trades_df)
        for ps in phase_stats:
            ps["实验组"] = group_label
            all_phase_rows.append(ps)

    if not all_phase_rows:
        return

    df_phases = pd.DataFrame(all_phase_rows)
    for phase_type in ["多头", "空头"]:
        phase_rows = df_phases[df_phases["阶段类型"] == phase_type]
        if phase_rows.empty:
            continue
        print(f"\n  --- Part {label_prefix} {phase_type}阶段 ---")
        short_cols = ["实验组", "市场阶段", "样本量", "平均涨幅", "中位涨幅", "胜率", "大胜率(>3%)", "盈亏比"]
        existing = [c for c in short_cols if c in phase_rows.columns]
        print(tabulate(phase_rows[existing].reset_index(drop=True), headers="keys", tablefmt="github", showindex=False))


def print_phase_vs_baseline_table(trades_map: Dict, label_prefix: str):
    """打印各区间的 vs 基准对比摘要表

    以第一个实验组作为基准，计算其他组在各个市场阶段的胜率差和涨幅差。
    这样能一眼看出每个条件在多头/空头区间的边际贡献。
    """
    all_phase_rows = []
    for group_label, trades_df in trades_map.items():
        if trades_df.empty:
            continue
        phase_stats = compute_phase_stats(trades_df)
        for ps in phase_stats:
            ps["实验组"] = group_label
            all_phase_rows.append(ps)

    if not all_phase_rows:
        return

    df_phases = pd.DataFrame(all_phase_rows)
    baseline_label = list(trades_map.keys())[0]

    for phase_type in ["多头", "空头"]:
        phase_rows = df_phases[df_phases["阶段类型"] == phase_type]
        if phase_rows.empty:
            continue

        compare_rows = []
        for market_stage in phase_rows["市场阶段"].unique():
            stage_data = phase_rows[phase_rows["市场阶段"] == market_stage]
            baseline_row = stage_data[stage_data["实验组"] == baseline_label]
            if baseline_row.empty:
                continue
            b_wr = baseline_row["胜率"].values[0]
            b_gain = baseline_row["平均涨幅"].values[0]
            b_sample = baseline_row["样本量"].values[0]

            for _, row in stage_data.iterrows():
                compare_rows.append({
                    "市场阶段": market_stage,
                    "实验组": row["实验组"],
                    "样本量": row["样本量"],
                    "胜率": row["胜率"],
                    "胜率差": round(row["胜率"] - b_wr, 1) if not pd.isna(row["胜率"]) and not pd.isna(b_wr) else np.nan,
                    "平均涨幅": row["平均涨幅"],
                    "涨幅差": round(row["平均涨幅"] - b_gain, 2) if not pd.isna(row["平均涨幅"]) and not pd.isna(b_gain) else np.nan,
                    "盈亏比": row["盈亏比"],
                })

        if not compare_rows:
            continue

        df_compare = pd.DataFrame(compare_rows)
        print(f"\n  --- Part {label_prefix} {phase_type}阶段 vs 基准({baseline_label}) ---")
        cols = ["市场阶段", "实验组", "样本量", "胜率", "胜率差", "平均涨幅", "涨幅差", "盈亏比"]
        print(tabulate(df_compare[cols].reset_index(drop=True), headers="keys", tablefmt="github", showindex=False))


def run_ablation_backtest(
    df: pd.DataFrame,
    trade_dates_list: List[str],
    data_manager: DataManager,
    condition_keys: List[str],
    hold_days: int,
    label: str,
    price_lookup: Optional[dict] = None,
    df_by_date: Optional[Dict[str, pd.DataFrame]] = None,
    mask_cache: Optional[Dict[str, pd.Series]] = None,
) -> Tuple[pd.DataFrame, Dict]:
    all_trades = []
    held_positions = set()
    trade_dates_sorted = sorted(trade_dates_list)
    use_fast = price_lookup is not None
    use_grouped = df_by_date is not None

    next_date_map = {}
    trade_date_idx_map = {}
    for i, d in enumerate(trade_dates_sorted):
        trade_date_idx_map[d] = i
        if i + 1 < len(trade_dates_sorted):
            next_date_map[d] = trade_dates_sorted[i + 1]
        else:
            next_date_map[d] = None

    for signal_date in tqdm(trade_dates_sorted, desc=label, leave=False):
        next_date = next_date_map.get(signal_date)
        if next_date is None:
            continue

        if use_grouped:
            df_day = df_by_date.get(signal_date)
            if df_day is None or df_day.empty:
                continue
        else:
            df_day = df[df["trade_date"] == signal_date]
            if df_day.empty:
                continue

        basic_ts_codes = set(df_day["ts_code"].unique())
        mask = apply_conditions(df_day, condition_keys, basic_ts_codes,
                                mask_cache=mask_cache)

        if use_fast:
            trades = run_single_day_backtest_fast(
                df_day, mask, next_date, price_lookup,
                trade_dates_sorted, trade_date_idx_map,
                hold_days, held_positions,
            )
        else:
            trades = run_single_day_backtest(
                df_day, mask, next_date, data_manager, hold_days, held_positions,
            )

        if not trades.empty:
            all_trades.append(trades)
            for _, row in trades.iterrows():
                held_positions.add(row["ts_code"])

        expired = set()
        for code in list(held_positions):
            buy_idx = trade_date_idx_map.get(None)
            sig_idx = trade_date_idx_map.get(signal_date)
            if sig_idx is None:
                continue
            expire_threshold = sig_idx - hold_days
            found = False
            for t in all_trades[-hold_days:]:
                if code in t["ts_code"].values:
                    code_rows = t[t["ts_code"] == code]
                    if not code_rows.empty:
                        bd = code_rows["buy_date"].iloc[0]
                        bi = trade_date_idx_map.get(bd)
                        if bi is not None and bi < expire_threshold:
                            found = True
                            break
            if found:
                expired.add(code)
        held_positions -= expired

    if all_trades:
        all_trades_df = pd.concat(all_trades, ignore_index=True)
    else:
        all_trades_df = pd.DataFrame()

    stats = compute_stats(all_trades_df)
    stats["实验组"] = label

    return all_trades_df, stats


def run_part_a(df, trade_dates_list, data_manager, hold_days, price_lookup=None, df_by_date=None, mask_cache=None):
    results = []
    all_trades_map = {}

    baseline_key = ["first_j13_step"]
    trades, stats = run_ablation_backtest(
        df, trade_dates_list, data_manager, baseline_key, hold_days, "A0-基准(J<13)",
        price_lookup=price_lookup, df_by_date=df_by_date, mask_cache=mask_cache,
    )
    results.append(stats)
    all_trades_map["A0-基准(J<13)"] = trades

    # 原有条件：逐个加入 CONDITION_KEYS 中的条件
    other_keys = [k for k in CONDITION_KEYS if k != "first_j13_step"]
    for i, key in enumerate(other_keys, 1):
        label = f"A{i}-+{CONDITIONS[key]}"
        keys = baseline_key + [key]
        trades, stats = run_ablation_backtest(
            df, trade_dates_list, data_manager, keys, hold_days, label,
            price_lookup=price_lookup, df_by_date=df_by_date, mask_cache=mask_cache,
        )
        results.append(stats)
        all_trades_map[label] = trades

    # 新增条件：candidate_keys 中不在 CONDITION_KEYS 的条件（避免重复计算）
    candidate_keys = [
        "close>MA20",
        "not_falling",
        "j_ultra_low",
        "close>MA5",
        "zhixing_mid_up",
        "j<-10",
        "j<-5",
        "j<0",
        "j<3",
        "j<8",
        "j<10",
        "j<15",
        "j<20",
    ]

    key_short = {
        "close>MA20": ">MA20",
        "not_falling": "不跌",
        "j_ultra_low": "J<-5",
        "close>MA5": ">MA5",
        "zhixing_mid_up": "知行中>多空",
        "j<-10": "J<-10",
        "j<-5": "J<-5",
        "j<0": "J<0",
        "j<3": "J<3",
        "j<8": "J<8",
        "j<10": "J<10",
        "j<15": "J<15",
        "j<20": "J<20",
    }

    next_idx = len(other_keys) + 1
    for i, key in enumerate(candidate_keys, next_idx):
        short = key_short.get(key, CONDITIONS.get(key, key))
        label = f"A{i}-+{short}(新)"
        keys = baseline_key + [key]
        trades, stats = run_ablation_backtest(
            df, trade_dates_list, data_manager, keys, hold_days, label,
            price_lookup=price_lookup, df_by_date=df_by_date, mask_cache=mask_cache,
        )
        results.append(stats)
        all_trades_map[label] = trades

    return results, all_trades_map


def run_part_b(df, trade_dates_list, data_manager, hold_days, price_lookup=None, df_by_date=None, mask_cache=None):
    results = []
    all_trades_map = {}

    full_keys = CONDITION_KEYS[:]
    trades, stats = run_ablation_backtest(
        df, trade_dates_list, data_manager, full_keys, hold_days, "B0-完整策略",
        price_lookup=price_lookup, df_by_date=df_by_date, mask_cache=mask_cache,
    )
    results.append(stats)
    all_trades_map["B0-完整策略"] = trades

    for i, key in enumerate(full_keys, 1):
        removed_keys = [k for k in full_keys if k != key]
        label = f"B{i}-去掉{CONDITIONS[key]}"
        trades, stats = run_ablation_backtest(
            df, trade_dates_list, data_manager, removed_keys, hold_days, label,
            price_lookup=price_lookup, df_by_date=df_by_date, mask_cache=mask_cache,
        )
        results.append(stats)
        all_trades_map[label] = trades

    return results, all_trades_map


def run_part_c(df, trade_dates_list, data_manager, hold_days, price_lookup=None, df_by_date=None, mask_cache=None):
    from itertools import combinations

    results = []
    all_trades_map = {}
    min_samples = 50

    base_key = "first_j13_step"
    # Top 10 最佳贡献条件（基于 Part A 基准递增实验结果）
    candidate_keys = [
        "not_falling",      # Top1: 不跌，涨幅+0.25%
        "amount_top",       # Top2: 成交额前60%，涨幅+0.11%
        "no_dist",          # Top3: 无出货信号，涨幅+0.10%
        "has_bvk",          # Top4: 底部暴力K，涨幅+0.08%
        "macd_dif>0",       # Top5: MACD多头，涨幅+0.07%
        "ma60_upward",      # Top6: MA60向上，涨幅+0.06%
        "j<-5",             # Top7: J<-5（原J<5改为更严格）
        "shrink",           # Top8: 缩量回调，涨幅+0.03%
        "has_am",           # Top9: 周期内异动，涨幅+0.03%
        "close>MA5",        # Top10: >MA5，涨幅+0.02%
    ]

    key_short = {
        "not_falling": "不跌",
        "amount_top": "成交额",
        "no_dist": "无出货",
        "has_bvk": "暴力K",
        "macd_dif>0": "MACD",
        "ma60_upward": "MA60↑",
        "j<-5": "J<-5",
        "shrink": "缩量",
        "has_am": "异动",
        "close>MA5": ">MA5",
    }

    combo_groups = [{"label": "C0-基准(J<13 only)", "keys": [base_key]}]

    must_have = {"j<-5"}
    other_keys = [k for k in candidate_keys if k not in must_have]

    for r in range(2, len(candidate_keys) + 1):
        for combo in combinations(candidate_keys, r):
            if not must_have.issubset(set(combo)):
                continue
            short_names = "+".join(key_short[k] for k in combo)
            label = f"C{len(combo_groups)}-{short_names}"
            combo_groups.append({"label": label, "keys": [base_key] + list(combo)})

    logging.info("Part C 排列组合: %d 个实验组（含基准）, min_samples=%d", len(combo_groups), min_samples)

    for group in combo_groups:
        trades, stats = run_ablation_backtest(
            df, trade_dates_list, data_manager, group["keys"], hold_days, group["label"],
            price_lookup=price_lookup, df_by_date=df_by_date, mask_cache=mask_cache,
        )
        if stats["样本量"] >= min_samples or group["label"].startswith("C0-"):
            results.append(stats)
            all_trades_map[group["label"]] = trades
        else:
            logging.info("跳过 %s: 样本量 %d < %d", group["label"], stats["样本量"], min_samples)

    logging.info("Part C 有效结果: %d 组（样本量 >= %d）", len(results), min_samples)

    return results, all_trades_map


PART_C_COMBOS = [
    {"label": "C0-基准(J<13 only)", "keys": ["first_j13_step"]},
    {"label": "C3-收益双件(无出货+成交额)", "keys": ["first_j13_step", "no_dist", "amount_top"]},
    {"label": "C2-胜率三件套(MA60向上+K线+MACD)", "keys": ["first_j13_step", "ma60_upward", "candle_ok", "macd_dif>0"]},
    {"label": "C1-5条件全选", "keys": ["first_j13_step", "ma60_upward", "candle_ok", "macd_dif>0", "no_dist", "amount_top"]},
]


def run_part_d(df, trade_dates_list, data_manager, hold_days, price_lookup=None, df_by_date=None, mask_cache=None):
    results = []
    all_trades_map = {}

    for phase in MARKET_PHASES:
        phase_dates = [d for d in trade_dates_list if phase["start"] <= d <= phase["end"]]
        if not phase_dates:
            continue

        phase_type = "多头" if "多头" in phase["label"] else "空头"

        phase_df_by_date = None
        if df_by_date is not None:
            phase_df_by_date = {d: df_by_date[d] for d in phase_dates if d in df_by_date}

        for combo in PART_C_COMBOS:
            exp_label = f"D-{phase['label']}|{combo['label']}"
            trades, stats = run_ablation_backtest(
                df, phase_dates, data_manager, combo["keys"], hold_days, exp_label,
                price_lookup=price_lookup, df_by_date=phase_df_by_date, mask_cache=mask_cache,
            )
            stats["市场阶段"] = phase["label"]
            stats["阶段类型"] = phase_type
            results.append(stats)
            all_trades_map[exp_label] = trades

    return results, all_trades_map


def main():
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    args = parse_args()
    data_manager = DataManager()

    try:
        end_date = get_nearest_trade_date(data_manager)
        logging.info("最近交易日: %s", end_date)

        end_dt = datetime.strptime(end_date, "%Y%m%d")
        ma_max_period = 114
        lookback_buffer = args.days + ma_max_period + 60
        lookback_start_dt = end_dt - timedelta(days=lookback_buffer)
        lookback_start_date = lookback_start_dt.strftime("%Y%m%d")

        trade_dates_full = data_manager.get_trade_dates(lookback_start_date, end_date)
        trade_dates_full = sorted(trade_dates_full)

        if len(trade_dates_full) < args.days:
            logging.warning("交易日历不足 %d 天，实际 %d 天", args.days, len(trade_dates_full))

        backtest_dates = trade_dates_full[-args.days:]
        start_date = backtest_dates[0]

        print(f"\n{'='*70}")
        print(f"📊 消融实验回测配置")
        print(f"  回测区间: {start_date} ~ {end_date} ({len(backtest_dates)} 个交易日)")
        print(f"  持有天数: {args.hold_days}")
        print(f"  买入方式: 信号日次日开盘价")
        print(f"{'='*70}")

        print("\n[1/5] 获取并准备数据...")
        df_full = fetch_and_prepare_data(data_manager, trade_dates_full)
        if df_full.empty:
            logging.error("数据获取失败")
            return

        df = df_full[df_full["trade_date"] >= start_date].copy()
        logging.info("回测区间数据: %d 条", len(df))

        print("\n[2/5] 获取股票基本信息...")
        basic_info = data_manager.get_stock_basic_info()
        industry_info = get_simple_industry_info()

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
        logging.info("剔除次新后: %d 条", len(df))

        print("\n[3/5] 计算策略标记...")
        df = apply_strategy_marks(df)
        df = calculate_trend_indicators(df)
        df = calculate_zhixing_brick_indicator(df)
        df = calculate_amount_rank(df)

        print("\n[4/5] 执行消融实验...")

        print("\n预加载回测价格数据...")
        price_lookup, _ = preload_price_data(data_manager, trade_dates_full, args.hold_days)

        t0 = time.time()
        logging.info("构建 df_by_date (按日期预分组)...")
        df_by_date = {d: g for d, g in df.groupby("trade_date")}
        logging.info("df_by_date: %d 天, %.1f 秒", len(df_by_date), time.time() - t0)

        all_condition_keys = set()
        for part_func in [run_part_a, run_part_b, run_part_c, run_part_d]:
            src = inspect.getsource(part_func)
            for ck in re.findall(r'"([a-z_~><\-\d]+)"', src):
                if ck in CONDITIONS or ck in {
                    "j_ultra_low", "vol_ratio>1", "close>MA5", "close>MA20",
                    "not_falling", "turnover>2",
                } or ck.startswith("j<"):
                    all_condition_keys.add(ck)

        t0 = time.time()
        logging.info("构建 mask_cache: %d 个条件...", len(all_condition_keys))
        mask_cache = {}
        for ck in all_condition_keys:
            try:
                mask_cache[ck] = build_condition_mask(df, ck, set(df["ts_code"].unique()))
            except Exception:
                pass
        logging.info("mask_cache: %d 个, %.1f 秒", len(mask_cache), time.time() - t0)

        part_a_stats = []
        part_a_trades = {}
        part_b_stats = []
        part_b_trades = {}
        part_c_stats = []
        part_c_trades = {}
        part_d_stats = []
        part_d_trades = {}

        if not args.skip_a:
            print("\n===== Part A: 基准递增实验 =====")
            part_a_stats, part_a_trades = run_part_a(df, backtest_dates, data_manager, args.hold_days, price_lookup=price_lookup, df_by_date=df_by_date, mask_cache=mask_cache)

        if not args.skip_b:
            print("\n===== Part B: 完整消融实验 =====")
            part_b_stats, part_b_trades = run_part_b(df, backtest_dates, data_manager, args.hold_days, price_lookup=price_lookup, df_by_date=df_by_date, mask_cache=mask_cache)

        if not args.skip_c:
            print("\n===== Part C: 最优条件组合实验 =====")
            part_c_stats, part_c_trades = run_part_c(df, backtest_dates, data_manager, args.hold_days, price_lookup=price_lookup, df_by_date=df_by_date, mask_cache=mask_cache)

        if not args.skip_d:
            print("\n===== Part D: 多头/空头择时回测 =====")
            part_d_stats, part_d_trades = run_part_d(df, backtest_dates, data_manager, args.hold_days, price_lookup=price_lookup, df_by_date=df_by_date, mask_cache=mask_cache)

        print("\n[5/5] 生成报告...")

        cols = ["实验组", "样本量", "平均涨幅", "中位涨幅", "胜率", "大胜率(>3%)", "盈亏比", "最大涨幅", "最大跌幅"]

        if part_a_stats:
            print("\n" + "=" * 100)
            print("Part A: 基准递增实验（在 J<13 基准上逐个加入条件）")
            print("=" * 100)
            df_a_display = pd.DataFrame(part_a_stats)
            existing_cols = [c for c in cols if c in df_a_display.columns]
            print(tabulate(df_a_display[existing_cols], headers="keys", tablefmt="github", showindex=False))
            print_phase_split_table(part_a_trades, "A")
            print_phase_vs_baseline_table(part_a_trades, "A")

        if part_b_stats:
            print("\n" + "=" * 100)
            print("Part B: 完整消融实验（从完整策略中逐个移除条件）")
            print("=" * 100)
            df_b_display = pd.DataFrame(part_b_stats)
            existing_cols = [c for c in cols if c in df_b_display.columns]
            print(tabulate(df_b_display[existing_cols], headers="keys", tablefmt="github", showindex=False))
            print_phase_split_table(part_b_trades, "B")
            print_phase_vs_baseline_table(part_b_trades, "B")

        if part_c_stats:
            print("\n" + "=" * 100)
            print("Part C: 最优条件排列组合实验（10个有效条件的全排列，样本量>=50）")
            print("=" * 100)
            df_c_display = pd.DataFrame(part_c_stats)
            existing_cols = [c for c in cols if c in df_c_display.columns]
            print(tabulate(df_c_display[existing_cols], headers="keys", tablefmt="github", showindex=False))

            df_c_ranked = df_c_display[df_c_display["样本量"] >= 50].copy()
            if len(df_c_ranked) > 1:
                df_c_ranked["综合得分"] = (
                    df_c_ranked["胜率"].fillna(0) * 0.4
                    + df_c_ranked["平均涨幅"].fillna(0) * 10
                    + df_c_ranked["盈亏比"].fillna(0) * 5
                )
                df_c_ranked = df_c_ranked.sort_values("综合得分", ascending=False)
                print("\n  --- Part C 综合排名（得分 = 0.4×胜率 + 10×平均涨幅 + 5×盈亏比）---")
                rank_cols = ["实验组", "样本量", "平均涨幅", "胜率", "盈亏比", "综合得分"]
                rank_existing = [c for c in rank_cols if c in df_c_ranked.columns]
                print(tabulate(df_c_ranked[rank_existing].reset_index(drop=True), headers="keys", tablefmt="github", showindex=False))

                best = df_c_ranked.iloc[0]
                print(f"\n  🏆 最优组合: {best['实验组']}  样本={int(best['样本量'])}  "
                      f"胜率={best['胜率']}%  平均涨幅={best['平均涨幅']}%  盈亏比={best['盈亏比']}")

            print_phase_split_table(part_c_trades, "C")
            print_phase_vs_baseline_table(part_c_trades, "C")

        if part_d_stats:
            print("\n" + "=" * 100)
            print("Part D: 多头/空头择时回测")
            print("=" * 100)
            df_d_display = pd.DataFrame(part_d_stats)
            cols_d = ["实验组", "市场阶段", "阶段类型", "样本量", "平均涨幅", "中位涨幅", "胜率", "大胜率(>3%)", "盈亏比"]
            existing_cols_d = [c for c in cols_d if c in df_d_display.columns]
            print(tabulate(df_d_display[existing_cols_d], headers="keys", tablefmt="github", showindex=False))

            print("\n" + "=" * 100)
            print("Part D 汇总：按市场阶段分组")
            print("=" * 100)
            df_d = pd.DataFrame(part_d_stats)
            for phase_type in ["多头", "空头"]:
                phase_rows = df_d[df_d["阶段类型"] == phase_type]
                if phase_rows.empty:
                    continue
                print(f"\n--- {phase_type}阶段 ---")
                short_cols = ["实验组", "样本量", "平均涨幅", "中位涨幅", "胜率", "大胜率(>3%)", "盈亏比"]
                existing_short = [c for c in short_cols if c in phase_rows.columns]
                print(tabulate(phase_rows[existing_short], headers="keys", tablefmt="github", showindex=False))

        html_dir = os.path.join("html", end_date)
        html_path = os.path.join(html_dir, "ablation_report.html")
        generate_html_report(
            part_a_stats, part_b_stats, part_a_trades, part_b_trades,
            args.hold_days, len(backtest_dates), html_path,
            part_c_stats=part_c_stats, part_c_trades=part_c_trades,
            part_d_stats=part_d_stats, part_d_trades=part_d_trades,
        )

        print("\n✅ 消融实验完成！")

    finally:
        data_manager.close()


if __name__ == "__main__":
    main()
