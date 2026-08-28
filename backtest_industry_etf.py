#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
行业超阈值 ETF 隔日开盘买入回测

当出现 multi_indicator_hints 中的超阈值行业时，于下一交易日开盘买入对应行业 ETF，
统计买入后 T+1 / T+2 / T+3 交易日的胜率和涨跌幅，并输出可视化报告。

使用方式:
    # 首次使用：根据 etf_config.json 生成默认回测配置文件
    python backtest_industry_etf.py --init-config

    # 使用已有 hints 文件回测
    python backtest_industry_etf.py --start-date 20260601 --end-date 20260827

    # 自动生成缺失的 hints 后回测
    python backtest_industry_etf.py --start-date 20240601 --end-date 20240827 --generate-hints
"""

import argparse
import glob
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s | %(message)s")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
DEFAULT_CONFIG_PATH = "backtest_industry_etf_config.json"
EXAMPLE_CONFIG_PATH = "backtest_industry_etf_config.example.json"
ETF_CONFIG_PATH = "etf_config.json"
OUTPUT_TRADES_CSV = "backtest_industry_etf_trades.csv"
OUTPUT_REPORT_HTML = "backtest_industry_etf_report.html"
ETF_CACHE_DIR = "data_cache"


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def load_json(path: str) -> dict:
    """加载 JSON 文件，缺失时返回空字典。"""
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data: dict, path: str):
    """保存 JSON 文件。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def ensure_dir(path: str):
    """确保目录存在。"""
    os.makedirs(path, exist_ok=True)


# ---------------------------------------------------------------------------
# 配置管理
# ---------------------------------------------------------------------------
def init_config_file(output_path: str = DEFAULT_CONFIG_PATH):
    """根据 etf_config.json 生成默认回测配置文件。"""
    etf_config = load_json(ETF_CONFIG_PATH)
    base_map = etf_config.get("industry_etf_map", {})

    config = {"industry_etf_map": {}}
    for industry, info in base_map.items():
        config["industry_etf_map"][industry] = {
            "ts_code": info.get("ts_code", ""),
            "name": info.get("name", ""),
            "enabled": True,
        }

    save_json(config, output_path)
    logging.info("默认回测配置文件已生成: %s", output_path)

    # 同时生成示例文件
    example = {
        "说明": "本配置用于覆盖或扩展 etf_config.json 中的 industry_etf_map",
        "industry_etf_map": {
            "通信": {"ts_code": "515880.SH", "name": "通信", "enabled": True},
            "电力": {"ts_code": "159611.SZ", "name": "电力", "enabled": True},
            "有色金属": {"ts_code": "512400.SH", "name": "有色金属", "enabled": False},
        },
    }
    save_json(example, EXAMPLE_CONFIG_PATH)
    logging.info("示例配置文件已生成: %s", EXAMPLE_CONFIG_PATH)


def load_backtest_config(config_path: Optional[str] = None) -> Dict[str, dict]:
    """
    加载回测配置中的行业→ETF映射。
    合并优先级：用户指定配置文件 > 默认配置文件 > etf_config.json。
    仅返回 enabled != false 的映射。
    """
    etf_config = load_json(ETF_CONFIG_PATH)
    base_map = etf_config.get("industry_etf_map", {})

    user_map = {}
    for path in [DEFAULT_CONFIG_PATH, config_path]:
        if path and os.path.exists(path):
            cfg = load_json(path)
            user_map.update(cfg.get("industry_etf_map", {}))

    merged = {}
    for industry, info in base_map.items():
        merged[industry] = dict(info)
    for industry, info in user_map.items():
        if industry in merged:
            merged[industry].update(info)
        else:
            merged[industry] = dict(info)

    return {
        industry: info
        for industry, info in merged.items()
        if info.get("enabled", True)
    }


# ---------------------------------------------------------------------------
# 信号加载与生成
# ---------------------------------------------------------------------------
def load_hints(start_date: str, end_date: str) -> pd.DataFrame:
    """加载已有 hints 文件，限定在日期区间内。"""
    records = []
    for fp in sorted(glob.glob("multi_indicator_hints_*.json")):
        fname = os.path.basename(fp)
        date_str = fname.replace("multi_indicator_hints_", "").replace(".json", "")
        if not (start_date <= date_str <= end_date):
            continue
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data:
                records.append({
                    "signal_date": date_str,
                    "industry": item.get("industry", ""),
                    "count": item.get("count", 0),
                })
        except Exception as e:
            logging.warning("读取 hints 文件失败 %s: %s", fp, e)

    return pd.DataFrame(records)


def generate_missing_hints(start_date: str, end_date: str):
    """
    对日期范围内缺失的 hints 文件，调用多指标选股逻辑生成。
    需要 TUSHARE_TOKEN 环境变量以及本地 data_cache。
    """
    from data_manager import DataManager
    from multi_indicator_pick import apply_multi_indicator_filter, generate_industry_count_hints
    from config import STRATEGY_CONFIG as ST

    data_manager = DataManager()
    try:
        # 向前扩展 180 天保证技术指标收敛
        lookback_dt = pd.to_datetime(start_date, format="%Y%m%d") - timedelta(days=180)
        lookback_start = lookback_dt.strftime("%Y%m%d")

        trade_dates_range = data_manager.get_trade_dates(lookback_start, end_date)
        target_dates = [d for d in trade_dates_range if start_date <= d <= end_date]
        if not target_dates:
            logging.warning("指定区间内无交易日")
            return

        missing_dates = [
            d for d in target_dates
            if not os.path.exists(f"multi_indicator_hints_{d}.json")
        ]
        if not missing_dates:
            logging.info("无需生成 hints，所有日期已存在")
            return

        logging.info("正在为 %d 个日期生成 hints...", len(missing_dates))

        fields = list(DataManager.ALL_FACTOR_COLS)
        df_full = data_manager.get_stock_factors(trade_dates_range, fields)
        if df_full.empty:
            logging.error("未获取到股票因子数据，无法生成 hints")
            return

        # 股票基本信息
        basic_info = data_manager.get_stock_basic_info()
        if basic_info.empty:
            logging.error("未获取到股票基本信息，无法生成 hints")
            return
        if "name" not in basic_info.columns:
            basic_info["name"] = basic_info["ts_code"]
        if "industry_name" not in basic_info.columns:
            basic_info["industry_name"] = "未知行业"
        basic_info["industry_name"] = basic_info["industry_name"].fillna("未知行业")
        basic_info["name"] = basic_info["name"].fillna(basic_info["ts_code"])
        basic = basic_info[basic_info["list_date"].notna()].copy()

        # 仅保留目标日期区间数据并合并名称/行业
        df = df_full[df_full["trade_date"] >= start_date].copy()
        df = df.merge(basic[["ts_code", "name", "industry_name"]], on="ts_code", how="left")

        for date in missing_dates:
            result, _ = apply_multi_indicator_filter(df, date, basic, ST)
            hints = generate_industry_count_hints(result, ST.MULTI_INDUSTRY_COUNT_THRESHOLD, basic)
            out_path = f"multi_indicator_hints_{date}.json"
            save_json(hints, out_path)
            logging.info("已生成 hints: %s (%d 条)", out_path, len(hints))
    finally:
        data_manager.close()


# ---------------------------------------------------------------------------
# ETF 数据获取（带本地缓存）
# ---------------------------------------------------------------------------
def _etf_cache_file(ts_code: str) -> str:
    safe_code = ts_code.replace(".", "_")
    return os.path.join(ETF_CACHE_DIR, f"etf_{safe_code}.csv")


def get_etf_data_cached(ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """
    获取 ETF 日线数据，优先读取本地 CSV 缓存，缺失时调用 Tushare。
    """
    from weekly_dca_strategy import _get_etf_data

    ensure_dir(ETF_CACHE_DIR)
    cache_file = _etf_cache_file(ts_code)

    start_dt = pd.to_datetime(start_date, format="%Y%m%d")
    end_dt = pd.to_datetime(end_date, format="%Y%m%d")

    existing = None
    if os.path.exists(cache_file):
        try:
            existing = pd.read_csv(cache_file, dtype={"trade_date": str})
            existing["trade_date"] = existing["trade_date"].astype(str)
            for col in ["open", "high", "low", "close", "open_qfq", "high_qfq", "low_qfq", "close_qfq"]:
                if col in existing.columns:
                    existing[col] = pd.to_numeric(existing[col], errors="coerce")
        except Exception as e:
            logging.warning("ETF 缓存读取失败 %s: %s", cache_file, e)
            existing = None

    if existing is not None and not existing.empty:
        min_date = pd.to_datetime(existing["trade_date"].min(), format="%Y%m%d")
        max_date = pd.to_datetime(existing["trade_date"].max(), format="%Y%m%d")
        if min_date <= start_dt and max_date >= end_dt:
            return existing[
                (existing["trade_date"] >= start_date) & (existing["trade_date"] <= end_date)
            ].copy().reset_index(drop=True)

    # 需要向 Tushare 请求数据
    fetch_start = start_dt
    fetch_end = end_dt
    if existing is not None and not existing.empty:
        min_date = pd.to_datetime(existing["trade_date"].min(), format="%Y%m%d")
        max_date = pd.to_datetime(existing["trade_date"].max(), format="%Y%m%d")
        fetch_start = min(fetch_start, min_date)
        fetch_end = max(fetch_end, max_date)

    logging.info("从 Tushare 获取 ETF 数据: %s %s ~ %s", ts_code,
                 fetch_start.strftime("%Y%m%d"), fetch_end.strftime("%Y%m%d"))
    df_new = _get_etf_data(ts_code, fetch_start, fetch_end)

    # 合并缓存
    if existing is not None and not existing.empty:
        df_new = pd.concat([existing, df_new], ignore_index=True)
        df_new = df_new.drop_duplicates(subset=["trade_date"], keep="last")

    df_new = df_new.sort_values("trade_date").reset_index(drop=True)
    df_new.to_csv(cache_file, index=False, encoding="utf-8-sig")

    return df_new[
        (df_new["trade_date"] >= start_date) & (df_new["trade_date"] <= end_date)
    ].copy().reset_index(drop=True)


# ---------------------------------------------------------------------------
# 回测引擎
# ---------------------------------------------------------------------------
def run_backtest(signals: pd.DataFrame, industry_etf_map: Dict[str, dict],
                 start_date: str, end_date: str, no_repeat: bool = False,
                 filter_zhixing_ok: bool = False) -> pd.DataFrame:
    """
    对信号列表执行回测，返回每笔交易的明细。

    no_repeat: 同一 ETF 在持仓期间（信号日 ~ T+10 结束）只首次建仓，
               期间再次出现信号则跳过，避免连续信号重复买入。
    filter_zhixing_ok: 买入日需满足知行多空过滤
                       （中期线 > 多空线 且 收盘价 >= 多空线），
                       否则跳过该笔交易，仅参与多头趋势下的信号。
    """
    from data_manager import DataManager
    from weekly_dca_strategy import calc_zhixing_duokong

    if signals.empty:
        logging.warning("无信号数据")
        return pd.DataFrame()

    data_manager = DataManager()
    try:
        # 获取区间交易日历，用于推导 T+1 / T+2 / T+3 / T+5 / T+10
        # 向后延长 20 个自然日，确保最后一笔信号的 T+10 仍在日历范围内
        calendar_end_dt = pd.to_datetime(end_date, format="%Y%m%d") + timedelta(days=20)
        calendar_end = calendar_end_dt.strftime("%Y%m%d")
        all_trade_dates = sorted(data_manager.get_trade_dates(start_date, calendar_end))
        if len(all_trade_dates) < 4:
            logging.warning("交易日数据不足，无法进行回测")
            return pd.DataFrame()

        date_index = {d: i for i, d in enumerate(all_trade_dates)}

        # 计算 ETF 数据预热起点：知行多空线用到 MA114，向前扩展约 250 个交易日
        pre_start_dt = pd.to_datetime(start_date, format="%Y%m%d") - timedelta(days=400)
        pre_start = pre_start_dt.strftime("%Y%m%d")

        # 预先按 ETF 分组加载并计算知行多空指标，避免循环内重复拉取
        etf_data_cache: Dict[str, pd.DataFrame] = {}
        if filter_zhixing_ok:
            needed_codes = set()
            for _, row in signals.iterrows():
                if row["industry"] in industry_etf_map:
                    code = industry_etf_map[row["industry"]].get("ts_code", "")
                    if code:
                        needed_codes.add(code)
            for code in needed_codes:
                try:
                    df_pre = get_etf_data_cached(code, pre_start, calendar_end)
                    if not df_pre.empty:
                        df_pre = calc_zhixing_duokong(df_pre)
                        etf_data_cache[code] = df_pre
                except Exception as e:
                    logging.warning("预热获取 ETF 数据失败 %s: %s", code, e)

        # 同一信号日、同一 ETF 仅交易一次（避免化学制药/生物制药等同买创新药 ETF）
        signals = signals.sort_values(
            ["signal_date", "count"], ascending=[True, False]
        ).reset_index(drop=True)
        seen_pairs = set()
        # 持仓中的 ETF -> 持仓结束日（信号日 ~ T+10 结束），用于 no_repeat 模式
        holding_until = {}

        trades = []
        for _, row in signals.iterrows():
            signal_date = row["signal_date"]
            industry = row["industry"]

            if industry not in industry_etf_map:
                logging.debug("行业 '%s' 无 ETF 映射，跳过", industry)
                continue

            etf_info = industry_etf_map[industry]
            ts_code = etf_info.get("ts_code", "")
            etf_name = etf_info.get("name", "")
            if not ts_code:
                continue

            pair = (signal_date, ts_code)
            if pair in seen_pairs:
                logging.debug(
                    "信号日 %s 的 ETF %s 已交易，跳过行业 '%s'",
                    signal_date, ts_code, industry
                )
                continue
            seen_pairs.add(pair)

            if signal_date not in date_index:
                continue
            buy_idx = date_index[signal_date] + 1
            if buy_idx >= len(all_trade_dates):
                continue
            buy_date = all_trade_dates[buy_idx]

            # 需要持有到 T+10
            hold_end_idx = buy_idx + 10
            if hold_end_idx >= len(all_trade_dates):
                continue
            hold_end_date = all_trade_dates[hold_end_idx]

            # no_repeat 模式：若该 ETF 仍处于上次持仓期间，则跳过本次信号
            if no_repeat and ts_code in holding_until and signal_date <= holding_until[ts_code]:
                logging.debug(
                    "ETF %s 在持仓期间（至 %s）内再次出现信号 %s，跳过建仓",
                    ts_code, holding_until[ts_code], signal_date
                )
                continue

            # 获取 ETF 数据
            if filter_zhixing_ok and ts_code in etf_data_cache:
                df_etf = etf_data_cache[ts_code]
                df_etf = df_etf[(df_etf["trade_date"] >= buy_date) &
                                (df_etf["trade_date"] <= hold_end_date)].copy()
            else:
                try:
                    df_etf = get_etf_data_cached(ts_code, buy_date, hold_end_date)
                except Exception as e:
                    logging.warning("获取 ETF 数据失败 %s: %s", ts_code, e)
                    continue

            if df_etf.empty or len(df_etf) < 2:
                continue

            # 知行多空过滤：买入日需中期 > 多空 且 收盘 >= 多空线
            if filter_zhixing_ok:
                buy_rows_full = etf_data_cache.get(ts_code)
                if buy_rows_full is not None:
                    chk = buy_rows_full[buy_rows_full["trade_date"] == buy_date]
                    if not chk.empty:
                        mid_v = chk["zhixing_mid"].iloc[0]
                        dk_v = chk["zhixing_duokong"].iloc[0]
                        close_v = chk["close_qfq"].iloc[0]
                        if not (pd.notna(mid_v) and pd.notna(dk_v) and pd.notna(close_v)
                                and mid_v > dk_v and close_v >= dk_v):
                            logging.debug(
                                "ETF %s 买入日 %s 不满足知行多空过滤(中期=%s 多空=%s 收盘=%s)，跳过",
                                ts_code, buy_date, mid_v, dk_v, close_v
                            )
                            continue

            buy_rows = df_etf[df_etf["trade_date"] == buy_date]
            if buy_rows.empty:
                continue
            buy_price = float(buy_rows["open_qfq"].iloc[0])
            if buy_price <= 0 or np.isnan(buy_price):
                continue

            record = {
                "signal_date": signal_date,
                "buy_date": buy_date,
                "industry": industry,
                "count": row.get("count", 0),
                "etf_ts_code": ts_code,
                "etf_name": etf_name,
                "buy_price": round(buy_price, 4),
            }

            valid_days = 0
            for day_offset in [1, 2, 3, 5, 10]:
                target_idx = buy_idx + day_offset
                if target_idx >= len(all_trade_dates):
                    break
                target_date = all_trade_dates[target_idx]
                target_rows = df_etf[df_etf["trade_date"] == target_date]
                if target_rows.empty:
                    continue
                close_price = float(target_rows["close_qfq"].iloc[0])
                if close_price <= 0 or np.isnan(close_price):
                    continue
                ret_pct = (close_price - buy_price) / buy_price * 100
                record[f"close_t{day_offset}"] = round(close_price, 4)
                record[f"return_t{day_offset}"] = round(ret_pct, 4)
                valid_days += 1

            if valid_days == 0:
                continue

            # no_repeat 模式：记录该 ETF 的持仓结束日（用于屏蔽期间内的连续信号）
            if no_repeat:
                holding_until[ts_code] = hold_end_date

            trades.append(record)

        return pd.DataFrame(trades)
    finally:
        data_manager.close()


# ---------------------------------------------------------------------------
# 统计汇总
# ---------------------------------------------------------------------------
def compute_stats(trades: pd.DataFrame) -> Dict[int, dict]:
    """计算 T+1/T+2/T+3/T+5/T+10 的胜率与收益统计。"""
    stats = {}
    for day in [1, 2, 3, 5, 10]:
        col = f"return_t{day}"
        if col not in trades.columns:
            continue
        returns = trades[col].dropna()
        if returns.empty:
            stats[day] = {
                "count": 0, "win_rate": 0, "avg_return": 0,
                "avg_win": 0, "avg_loss": 0,
                "max_return": 0, "min_return": 0,
            }
            continue

        wins = returns[returns > 0]
        losses = returns[returns <= 0]
        stats[day] = {
            "count": int(len(returns)),
            "win_rate": round(len(wins) / len(returns) * 100, 2),
            "avg_return": round(returns.mean(), 4),
            "avg_win": round(wins.mean(), 4) if not wins.empty else 0,
            "avg_loss": round(losses.mean(), 4) if not losses.empty else 0,
            "max_return": round(returns.max(), 4),
            "min_return": round(returns.min(), 4),
        }
    return stats


def print_stats(stats: Dict[int, dict]):
    """控制台打印统计表格。"""
    try:
        from tabulate import tabulate
    except ImportError:
        tabulate = None

    rows = []
    for day in sorted(stats.keys()):
        s = stats[day]
        rows.append([
            f"T+{day}", s["count"], f"{s['win_rate']:.2f}%",
            f"{s['avg_return']:.4f}%", f"{s['avg_win']:.4f}%",
            f"{s['avg_loss']:.4f}%", f"{s['max_return']:.4f}%",
            f"{s['min_return']:.4f}%",
        ])

    headers = ["持有期", "交易笔数", "胜率", "平均收益", "平均盈利", "平均亏损", "最大收益", "最小收益"]
    if tabulate:
        print("\n" + tabulate(rows, headers=headers, tablefmt="grid"))
    else:
        print("\n" + " | ".join(headers))
        for row in rows:
            print(" | ".join(str(x) for x in row))


# ---------------------------------------------------------------------------
# 可视化
# ---------------------------------------------------------------------------
def build_summary_table_html(stats: Dict[int, dict]) -> str:
    """生成统计汇总 HTML 表格。"""
    rows = ""
    for day in sorted(stats.keys()):
        s = stats[day]
        rows += f"""
        <tr>
            <td>T+{day}</td>
            <td>{s['count']}</td>
            <td>{s['win_rate']:.2f}%</td>
            <td>{s['avg_return']:.4f}%</td>
            <td>{s['avg_win']:.4f}%</td>
            <td>{s['avg_loss']:.4f}%</td>
            <td>{s['max_return']:.4f}%</td>
            <td>{s['min_return']:.4f}%</td>
        </tr>
        """

    return f"""
    <table style="border-collapse:collapse;width:100%;max-width:900px;margin:16px 0;font-size:14px">
        <tr style="background:#f5f5f5">
            <th style="padding:8px;border:1px solid #ddd">持有期</th>
            <th style="padding:8px;border:1px solid #ddd">交易笔数</th>
            <th style="padding:8px;border:1px solid #ddd">胜率</th>
            <th style="padding:8px;border:1px solid #ddd">平均收益</th>
            <th style="padding:8px;border:1px solid #ddd">平均盈利</th>
            <th style="padding:8px;border:1px solid #ddd">平均亏损</th>
            <th style="padding:8px;border:1px solid #ddd">最大收益</th>
            <th style="padding:8px;border:1px solid #ddd">最小收益</th>
        </tr>
        {rows}
    </table>
    """


def build_top_bottom_table_html(trades: pd.DataFrame, top_n: int = 10) -> str:
    """生成各持有期收益率正负前 N 的明细表格。"""
    sections = ""
    for day in [1, 2, 3, 5, 10]:
        col = f"return_t{day}"
        if col not in trades.columns:
            continue
        d = trades.dropna(subset=[col]).copy()
        if d.empty:
            continue

        pos = d.nlargest(top_n, col)
        neg = d.nsmallest(top_n, col)

        def rows_html(sub: pd.DataFrame, positive: bool) -> str:
            color = "#27ae60" if positive else "#e74c3c"
            out = ""
            for _, r in sub.iterrows():
                ret = r[col]
                sign = "+" if ret >= 0 else ""
                out += f"""
                <tr>
                    <td style="padding:6px 8px;border:1px solid #ddd;text-align:right;color:{color};font-weight:600">{sign}{ret:.4f}%</td>
                    <td style="padding:6px 8px;border:1px solid #ddd">{r['signal_date']}</td>
                    <td style="padding:6px 8px;border:1px solid #ddd">{r['industry']}</td>
                    <td style="padding:6px 8px;border:1px solid #ddd">{r['etf_name']}</td>
                </tr>
                """
            return out

        sections += f"""
        <h3 style="color:#34495e;margin:20px 0 8px">T+{day} 收益率 正前{top_n}（共 {len(d)} 笔）</h3>
        <table style="border-collapse:collapse;width:100%;max-width:760px;margin-bottom:8px;font-size:13px">
            <tr style="background:#f5f5f5">
                <th style="padding:6px 8px;border:1px solid #ddd">收益率</th>
                <th style="padding:6px 8px;border:1px solid #ddd">信号日</th>
                <th style="padding:6px 8px;border:1px solid #ddd">行业</th>
                <th style="padding:6px 8px;border:1px solid #ddd">ETF</th>
            </tr>
            {rows_html(pos, True)}
        </table>
        <h3 style="color:#34495e;margin:8px 0">T+{day} 收益率 负前{top_n}</h3>
        <table style="border-collapse:collapse;width:100%;max-width:760px;margin-bottom:16px;font-size:13px">
            <tr style="background:#f5f5f5">
                <th style="padding:6px 8px;border:1px solid #ddd">收益率</th>
                <th style="padding:6px 8px;border:1px solid #ddd">信号日</th>
                <th style="padding:6px 8px;border:1px solid #ddd">行业</th>
                <th style="padding:6px 8px;border:1px solid #ddd">ETF</th>
            </tr>
            {rows_html(neg, False)}
        </table>
        """

    return f"""
    <h2>🏆 收益率正负前 {top_n}</h2>
    {sections}
    """


def generate_visual_report(trades: pd.DataFrame, stats: Dict[int, dict],
                           start_date: str, end_date: str, output_path: str):
    """生成 Plotly 可视化 HTML 报告。"""
    days = sorted(stats.keys())
    win_rates = [stats[d]["win_rate"] for d in days]
    avg_returns = [stats[d]["avg_return"] for d in days]
    avg_wins = [stats[d]["avg_win"] for d in days]
    avg_losses = [stats[d]["avg_loss"] for d in days]
    day_labels = [f"T+{d}" for d in days]

    figures = []

    # 1. 胜率柱状图
    fig1 = go.Figure(data=[
        go.Bar(x=day_labels, y=win_rates, marker_color="#3498db", text=[f"{v:.2f}%" for v in win_rates],
               textposition="auto")
    ])
    fig1.update_layout(title="各持有期胜率", yaxis_title="胜率 (%)",
                       xaxis_title="持有期", template="plotly_white")
    figures.append(("fig_win_rate", fig1))

    # 2. 平均收益率柱状图
    fig2 = go.Figure(data=[
        go.Bar(name="平均收益", x=day_labels, y=avg_returns, marker_color="#2ecc71",
               text=[f"{v:.4f}%" for v in avg_returns], textposition="auto"),
        go.Bar(name="平均盈利", x=day_labels, y=avg_wins, marker_color="#27ae60"),
        go.Bar(name="平均亏损", x=day_labels, y=avg_losses, marker_color="#e74c3c"),
    ])
    fig2.update_layout(barmode="group", title="平均收益率对比", yaxis_title="收益率 (%)",
                       xaxis_title="持有期", template="plotly_white")
    figures.append(("fig_returns", fig2))

    # 3. 收益分布直方图
    fig3 = make_subplots(rows=1, cols=len(days), subplot_titles=day_labels)
    colors = ["#3498db", "#9b59b6", "#e67e22"]
    for i, day in enumerate(days):
        col = f"return_t{day}"
        if col in trades.columns:
            fig3.add_trace(
                go.Histogram(x=trades[col].dropna(), nbinsx=15, marker_color=colors[i % len(colors)],
                             name=f"T+{day}"),
                row=1, col=i + 1
            )
    fig3.update_layout(title="收益率分布", template="plotly_white")
    fig3.update_xaxes(title_text="收益率 (%)")
    fig3.update_yaxes(title_text="频次")
    figures.append(("fig_distribution", fig3))

    # 4. 累计净值曲线（基于 T+1 收益，等权每日复利）
    if "return_t1" in trades.columns and not trades.empty:
        trades_sorted = trades.sort_values("buy_date").reset_index(drop=True)
        # 同一 buy_date 多行业时，取等权平均收益
        daily_avg = trades_sorted.groupby("buy_date")["return_t1"].mean().reset_index()
        daily_avg["nav"] = (1 + daily_avg["return_t1"] / 100).cumprod()
        fig4 = go.Figure(data=[
            go.Scatter(x=daily_avg["buy_date"], y=daily_avg["nav"], mode="lines+markers",
                       line=dict(color="#2980b9"), name="累计净值")
        ])
        fig4.update_layout(title="T+1 等权累计净值曲线", yaxis_title="净值",
                           xaxis_title="买入日期", template="plotly_white")
        figures.append(("fig_nav", fig4))

    # 5. 信号次数时间序列
    if not trades.empty:
        signal_counts = trades.groupby("signal_date").size().reset_index(name="count")
        fig5 = go.Figure(data=[
            go.Bar(x=signal_counts["signal_date"], y=signal_counts["count"],
                   marker_color="#f39c12", name="信号次数")
        ])
        fig5.update_layout(title="每日触发信号次数", yaxis_title="信号数",
                           xaxis_title="信号日期", template="plotly_white")
        figures.append(("fig_signals", fig5))

    # 组装 HTML
    divs = "\n".join(
        f'<div style="margin-bottom:40px">{fig.to_html(full_html=False, include_plotlyjs=("cdn" if i == 0 else False))}</div>'
        for i, (_, fig) in enumerate(figures)
    )

    summary_table = build_summary_table_html(stats)
    top_bottom_table = build_top_bottom_table_html(trades, top_n=10)

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>行业超阈值 ETF 回测报告</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
               color: #333; max-width: 1000px; margin: 0 auto; padding: 24px; }}
        h1 {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 8px; }}
        h2 {{ color: #34495e; margin-top: 32px; }}
    </style>
</head>
<body>
    <h1>📊 行业超阈值 ETF 回测报告</h1>
    <p><strong>回测区间:</strong> {start_date} ~ {end_date} &nbsp;|&nbsp;
       <strong>总交易笔数:</strong> {len(trades)}</p>

    <h2>📈 统计汇总</h2>
    {summary_table}

    <h2>📉 可视化图表</h2>
    {divs}

    {top_bottom_table}

    <hr style="border:none;border-top:1px solid #ddd;margin:32px 0">
    <p style="color:#999;font-size:12px">由 backtest_industry_etf.py 自动生成</p>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    logging.info("可视化报告已生成: %s", output_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="行业超阈值 ETF 隔日开盘买入回测",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python backtest_industry_etf.py --init-config
  python backtest_industry_etf.py --start-date 20260601 --end-date 20260827
  python backtest_industry_etf.py --start-date 20240601 --end-date 20240827 --generate-hints
        """,
    )
    parser.add_argument("--start-date", type=str,
                        help="回测开始日期，格式 YYYYMMDD")
    parser.add_argument("--end-date", type=str,
                        help="回测结束日期，格式 YYYYMMDD")
    parser.add_argument("--generate-hints", action="store_true",
                        help="自动生成日期范围内缺失的 multi_indicator_hints 文件")
    parser.add_argument("--no-repeat", action="store_true",
                        help="同一 ETF 持仓期间（信号日~T+10）只首次建仓，连续信号不重复买入")
    parser.add_argument("--filter-zhixing", action="store_true",
                        help="买入日需满足知行多空过滤(中期线>多空线 且 收盘>=多空线)，仅参与多头趋势")
    parser.add_argument("--config", type=str, default=None,
                        help="行业→ETF 映射配置文件路径，默认读取 backtest_industry_etf_config.json")
    parser.add_argument("--init-config", action="store_true",
                        help="根据 etf_config.json 生成默认回测配置文件并退出")
    parser.add_argument("--output-dir", type=str, default=".",
                        help="输出目录，默认当前目录")
    return parser.parse_args()


def main():
    args = parse_args()

    if args.init_config:
        init_config_file(DEFAULT_CONFIG_PATH)
        return

    if not args.start_date or not args.end_date:
        print("错误: 必须指定 --start-date 和 --end-date（或使用 --init-config）")
        sys.exit(1)

    # 验证日期格式
    try:
        datetime.strptime(args.start_date, "%Y%m%d")
        datetime.strptime(args.end_date, "%Y%m%d")
    except ValueError:
        print("错误: 日期格式必须为 YYYYMMDD")
        sys.exit(1)

    if args.start_date > args.end_date:
        print("错误: start-date 不能晚于 end-date")
        sys.exit(1)

    # 生成缺失 hints
    if args.generate_hints:
        generate_missing_hints(args.start_date, args.end_date)

    # 加载配置与信号
    industry_etf_map = load_backtest_config(args.config)
    if not industry_etf_map:
        print("错误: 未找到有效的行业→ETF 映射，请先运行 --init-config")
        sys.exit(1)

    logging.info("有效行业→ETF 映射: %s", list(industry_etf_map.keys()))

    signals = load_hints(args.start_date, args.end_date)
    if signals.empty:
        print(f"警告: 区间 {args.start_date} ~ {args.end_date} 内未找到任何 hints 信号")
        print("可尝试添加 --generate-hints 参数自动生成缺失的 hints")
        sys.exit(0)

    logging.info("加载到 %d 条行业信号", len(signals))

    # 执行回测
    trades = run_backtest(signals, industry_etf_map, args.start_date, args.end_date,
                          no_repeat=args.no_repeat, filter_zhixing_ok=args.filter_zhixing)
    if trades.empty:
        print("未生成任何有效交易，请检查 ETF 数据是否可获取或映射是否正确")
        sys.exit(0)

    # 保存逐笔明细
    output_dir = args.output_dir
    ensure_dir(output_dir)
    trades_csv = os.path.join(output_dir, OUTPUT_TRADES_CSV)
    trades.to_csv(trades_csv, index=False, encoding="utf-8-sig")
    logging.info("交易明细已保存: %s", trades_csv)

    # 统计与可视化
    stats = compute_stats(trades)
    print_stats(stats)

    report_html = os.path.join(output_dir, OUTPUT_REPORT_HTML)
    generate_visual_report(trades, stats, args.start_date, args.end_date, report_html)


if __name__ == "__main__":
    main()
