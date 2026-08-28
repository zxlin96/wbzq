#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多指标联合选股 + 超阈值行业提示 (multi_indicator_pick.py)

作为统一策略管道中的独立并行策略（策略3）接入，不修改现有主策略与 MACD 策略行为。

核心筛选条件（全部 AND，spec 5.1.1 规则 1-9）：
    1. KDJ-J < 13            (超卖低吸)
    2. MACD-DIF > 0          (多头趋势)
    3. 排除 ST 股票
    4. 收盘价 > 60 日均线     (站上中期趋势支撑)
    5. 涨跌幅 < 3.00%
    6. 涨跌幅 > -3.00%
    7. 成交额排名前 60%
    8. 总市值 > 50 亿
    9. 振幅 < 7%

当某行业入选股票数量 > 10 时，提示该行业（spec 5.2）。

使用方式（由 run_all_strategies.py 统一管道调用）：
    from multi_indicator_pick import run_multi_indicator_strategy
    result, funnel, hints = run_multi_indicator_strategy(df, end_date, basic, ST)
"""

import json
import logging
import os

import numpy as np
import pandas as pd

from config import STRATEGY_CONFIG as ST

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s | %(message)s")

# 入选结果输出列（spec 4.5.2 兼容列 + 业务所需字段）
RESULT_COLUMNS = [
    'ts_code', 'name', 'industry_name', 'trade_date',
    'close_qfq', 'ma_qfq_60', 'kdj_qfq', 'macd_dif_qfq',
    'amount', 'total_mv', 'pct_chg',
]

# 漏斗统计各阶段键名（顺序即筛选顺序）
FUNNEL_KEYS = [
    '全市场',
    '+KDJ-J<13',
    '+MACD-DIF>0',
    '+非ST',
    '+收盘>MA60',
    '+涨跌幅<上限',
    '+涨跌幅>下限',
    '+成交额前60%',
    '+市值>下限',
    '+振幅<上限',
    '最终',
]


# ============================================================================
# T2.1 阈值校验
# ============================================================================

def validate_multi_indicator_thresholds(thresholds):
    """校验多指标联合选股阈值参数合法性，非法时抛 ValueError。

    Args:
        thresholds: StrategyThresholds 实例（含 MULTI_* 字段）

    Raises:
        ValueError: 任一阈值非法时抛出，消息指明哪个阈值非法及其取值。
    """
    if thresholds.MULTI_PCT_CHG_MIN >= thresholds.MULTI_PCT_CHG_MAX:
        raise ValueError(
            f"MULTI_PCT_CHG_MIN({thresholds.MULTI_PCT_CHG_MIN}) "
            f">= MULTI_PCT_CHG_MAX({thresholds.MULTI_PCT_CHG_MAX})，下限必须小于上限"
        )
    if thresholds.MULTI_MV_MIN_BILLION <= 0:
        raise ValueError(
            f"MULTI_MV_MIN_BILLION({thresholds.MULTI_MV_MIN_BILLION}) 必须为正数"
        )
    if not (0 < thresholds.MULTI_AMOUNT_TOP_PERCENT <= 1):
        raise ValueError(
            f"MULTI_AMOUNT_TOP_PERCENT({thresholds.MULTI_AMOUNT_TOP_PERCENT}) "
            f"必须处于 (0, 1] 区间"
        )
    if thresholds.MULTI_INDUSTRY_COUNT_THRESHOLD < 0:
        raise ValueError(
            f"MULTI_INDUSTRY_COUNT_THRESHOLD({thresholds.MULTI_INDUSTRY_COUNT_THRESHOLD}) "
            f"必须为非负整数"
        )
    if thresholds.MULTI_MA_PERIOD <= 0:
        raise ValueError(
            f"MULTI_MA_PERIOD({thresholds.MULTI_MA_PERIOD}) 必须为正整数"
        )


# ============================================================================
# T2.2 / T2.3 核心筛选 + 漏斗统计（共享条件判定逻辑）
# ============================================================================

def _compute_conditions(today, thresholds):
    """计算 9 项 AND 条件向量（NaN 安全，对 NaN 一律判 False）。

    Args:
        today: 目标交易日当日数据 DataFrame（已 copy，不修改入参）
        thresholds: StrategyThresholds 实例

    Returns:
        tuple: (c1..c9, amount_threshold)
        c1: KDJ-J < 上限
        c2: MACD-DIF > 下限
        c3: 非 ST
        c4: 收盘价 > MA60
        c5: 涨跌幅 < 上限
        c6: 涨跌幅 > 下限
        c7: 成交额 >= 当日分位阈值
        c8: 总市值 > 下限（万元）
        c9: 振幅 < 上限
        amount_threshold: 当日成交额分位阈值（用于漏斗展示）
    """
    # 成交额分位阈值（前 60% => quantile(0.60)，即成交额 >= 该阈值的股票入选）
    amount_threshold = today['amount'].quantile(thresholds.MULTI_AMOUNT_TOP_PERCENT)

    c1 = today['kdj_qfq'] < thresholds.MULTI_KDJ_J_MAX
    c2 = today['macd_dif_qfq'] > thresholds.MULTI_MACD_DIF_MIN
    c3 = ~today['name'].astype(str).str.contains('ST', case=False, na=False)
    c4 = today['close_qfq'] > today['ma_qfq_60']
    c5 = today['pct_chg'] < thresholds.MULTI_PCT_CHG_MAX
    c6 = today['pct_chg'] > thresholds.MULTI_PCT_CHG_MIN
    c7 = today['amount'] >= amount_threshold
    c8 = today['total_mv'] > thresholds.MULTI_MV_MIN_BILLION * 10000
    # 振幅 = (high - low) / pre_close * 100，pre_close 为 0 或 NaN 时振幅置 NaN（判 False）
    pre_close_safe = today['pre_close'].replace(0, np.nan)
    amplitude = (today['high_qfq'] - today['low_qfq']) / pre_close_safe * 100
    c9 = amplitude < thresholds.MULTI_AMPLITUDE_MAX

    return c1, c2, c3, c4, c5, c6, c7, c8, c9, amount_threshold


def _empty_result():
    """返回空结果 DataFrame（含结果列定义）。"""
    return pd.DataFrame(columns=RESULT_COLUMNS)


def _empty_funnel():
    """返回全 0 的漏斗 dict。"""
    return {k: 0 for k in FUNNEL_KEYS}


def apply_multi_indicator_filter(df, end_date, basic, thresholds):
    """应用 9 项多指标联合筛选条件（spec 5.1.1）。

    Args:
        df: 含全部策略标记的共享数据 DataFrame（不修改）
        end_date: 目标交易日（YYYYMMDD 字符串）
        basic: 股票基本信息 DataFrame（含 list_date）
        thresholds: StrategyThresholds 实例

    Returns:
        tuple: (入选股票 DataFrame, 漏斗统计 dict)
        入选结果按 kdj_qfq 升序排列，含 RESULT_COLUMNS 列。
    """
    validate_multi_indicator_thresholds(thresholds)

    # 取目标交易日当日数据（copy，不修改入参 df）
    today = df[df['trade_date'] == end_date].copy()

    if today.empty:
        logging.warning("多指标联合选股: 目标交易日 %s 无行情数据", end_date)
        return _empty_result(), _empty_funnel()

    # 确保所需列存在
    required_cols = ['kdj_qfq', 'macd_dif_qfq', 'name', 'close_qfq', 'ma_qfq_60',
                     'pct_chg', 'amount', 'total_mv', 'high_qfq', 'low_qfq', 'pre_close']
    for col in required_cols:
        if col not in today.columns:
            today[col] = np.nan

    # 行业字段缺失填 "未知行业"
    if 'industry_name' not in today.columns:
        today['industry_name'] = '未知行业'
    else:
        today['industry_name'] = today['industry_name'].fillna('未知行业')

    # 计算 9 项条件
    c1, c2, c3, c4, c5, c6, c7, c8, c9, _ = _compute_conditions(today, thresholds)

    # 联合判定（AND）
    cond = c1 & c2 & c3 & c4 & c5 & c6 & c7 & c8 & c9

    # 取结果（仅 RESULT_COLUMNS），按 KDJ-J 升序
    result = today[cond][RESULT_COLUMNS].sort_values('kdj_qfq').reset_index(drop=True)

    # 漏斗统计
    funnel = compute_multi_indicator_funnel_stats(df, end_date, thresholds)

    logging.info("多指标联合选股: %s 入选 %d 只 (全市场 %d 只)",
                 end_date, len(result), len(today))
    return result, funnel


def compute_multi_indicator_funnel_stats(df, end_date, thresholds):
    """计算多指标联合选股各阶段漏斗统计（逐级 AND 累积）。

    Args:
        df: 含全部策略标记的共享数据 DataFrame
        end_date: 目标交易日
        thresholds: StrategyThresholds 实例

    Returns:
        dict: 各阶段剩余股票数，键为 FUNNEL_KEYS，值单调不增。
    """
    today = df[df['trade_date'] == end_date]
    if today.empty:
        return _empty_funnel()

    # 确保所需列存在
    required_cols = ['kdj_qfq', 'macd_dif_qfq', 'name', 'close_qfq', 'ma_qfq_60',
                     'pct_chg', 'amount', 'total_mv', 'high_qfq', 'low_qfq', 'pre_close']
    today = today.copy()
    for col in required_cols:
        if col not in today.columns:
            today[col] = np.nan

    c1, c2, c3, c4, c5, c6, c7, c8, c9, _ = _compute_conditions(today, thresholds)

    # 逐级 AND 累积
    s0 = len(today)
    s1 = int((c1).sum())
    s2 = int((c1 & c2).sum())
    s3 = int((c1 & c2 & c3).sum())
    s4 = int((c1 & c2 & c3 & c4).sum())
    s5 = int((c1 & c2 & c3 & c4 & c5).sum())
    s6 = int((c1 & c2 & c3 & c4 & c5 & c6).sum())
    s7 = int((c1 & c2 & c3 & c4 & c5 & c6 & c7).sum())
    s8 = int((c1 & c2 & c3 & c4 & c5 & c6 & c7 & c8).sum())
    s9 = int((c1 & c2 & c3 & c4 & c5 & c6 & c7 & c8 & c9).sum())

    return {
        '全市场': s0,
        '+KDJ-J<13': s1,
        '+MACD-DIF>0': s2,
        '+非ST': s3,
        '+收盘>MA60': s4,
        '+涨跌幅<上限': s5,
        '+涨跌幅>下限': s6,
        '+成交额前60%': s7,
        '+市值>下限': s8,
        '+振幅<上限': s9,
        '最终': s9,
    }


# ============================================================================
# T3.1 超阈值行业提示
# ============================================================================

def generate_industry_count_hints(result, threshold, basic=None):
    """生成超阈值行业提示清单。

    规则：
    - 保险：入选股票数量 >= 2 即触发。
    - 其他行业（且 basic 提供行业总个股数）：行业总个股数 > 50 且
      入选股票数量 > max(行业总个股数 * 10%, 10) 时触发。
    - 未提供 basic 时退化为旧逻辑：入选股票数量 > threshold 触发。
    - 未知行业不生成提示。

    Args:
        result: 入选股票 DataFrame（含 industry_name 列）
        threshold: 行业入选数量阈值（basic 缺失时的退化阈值）
        basic: 股票基本信息 DataFrame（含 industry_name 列），可选

    Returns:
        list: 提示条目，每条含 {industry: str, count: int}，
              按 count 降序，count 相同时按 industry 字典序升序。
    """
    if result.empty:
        return []

    # 处理 industry_name 列缺失
    if 'industry_name' not in result.columns:
        industry_series = pd.Series(['未知行业'] * len(result))
    else:
        industry_series = result['industry_name'].fillna('未知行业')

    industry_count = industry_series.value_counts()

    # 计算各行业总个股数（用于百分比规则）
    industry_total_counts = {}
    if basic is not None and not basic.empty and 'industry_name' in basic.columns:
        industry_total_counts = (
            basic['industry_name'].fillna('未知行业').value_counts().to_dict()
        )

    hints = []
    for industry, count in industry_count.items():
        if str(industry) == '未知行业':
            continue

        # 保险特殊规则：2 只即触发
        if str(industry) == '保险':
            if count >= 2:
                hints.append({'industry': str(industry), 'count': int(count)})
            continue

        # 提供 basic 时：总个股 > 50 且 入选数 > max(总个股*10%, 10)
        if industry_total_counts:
            total = industry_total_counts.get(industry, 0)
            threshold_count = max(total * 0.1, 10)
            if total > 50 and count > threshold_count:
                hints.append({'industry': str(industry), 'count': int(count)})
            continue

        # 退化逻辑
        if count > threshold:
            hints.append({'industry': str(industry), 'count': int(count)})

    # 按 count 降序，count 相同时按 industry 字典序升序
    hints.sort(key=lambda x: (-x['count'], x['industry']))
    return hints


# ============================================================================
# T4.1 CSV 落盘
# ============================================================================

def save_multi_indicator_result(result, end_date):
    """保存多指标联合选股结果到 CSV。

    Args:
        result: 入选股票 DataFrame
        end_date: 目标交易日

    Returns:
        str: CSV 文件路径；result 为空时返回 ""。
    """
    if result.empty:
        logging.info("多指标联合选股: 入选结果为空，跳过 CSV 落盘")
        return ""
    csv_path = f"multi_indicator_result_{end_date}.csv"
    result.to_csv(csv_path, index=False, encoding='utf-8-sig')
    logging.info("多指标联合选股: 结果已保存 %s (%d 只)", csv_path, len(result))
    return csv_path


# ============================================================================
# T4.2 提示落盘
# ============================================================================

def save_multi_indicator_hints(hints, end_date):
    """保存超阈值行业提示到 JSON 文件（供邮件报告独立进程读取）。

    Args:
        hints: 提示清单 list[dict]
        end_date: 目标交易日

    Returns:
        str: JSON 文件路径；写入失败时返回 ""。空清单仍落盘 []。
    """
    json_path = f"hints/multi_indicator_hints_{end_date}.json"
    try:
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(hints, f, ensure_ascii=False, indent=2)
        logging.info("多指标联合选股: 超阈值行业提示已保存 %s (%d 条)", json_path, len(hints))
        return json_path
    except OSError as e:
        logging.error("多指标联合选股: 提示落盘失败 (%s)", e)
        return ""


# ============================================================================
# T5.1 管道入口
# ============================================================================

def run_multi_indicator_strategy(df, end_date, basic, thresholds=None):
    """多指标联合选股 + 超阈值行业提示 管道入口（串联全流程）。

    Args:
        df: 含全部策略标记的共享数据 DataFrame（不修改）
        end_date: 目标交易日
        basic: 股票基本信息 DataFrame
        thresholds: StrategyThresholds 实例，默认使用全局 STRATEGY_CONFIG

    Returns:
        tuple: (入选股票 DataFrame, 漏斗统计 dict, 超阈值行业提示 list)
    """
    if thresholds is None:
        thresholds = ST

    # 1. 阈值校验
    validate_multi_indicator_thresholds(thresholds)

    # 2. 多指标联合筛选
    result, funnel = apply_multi_indicator_filter(df, end_date, basic, thresholds)

    # 3. 超阈值行业提示
    hints = generate_industry_count_hints(result, thresholds.MULTI_INDUSTRY_COUNT_THRESHOLD, basic)

    # 4. CSV 落盘
    save_multi_indicator_result(result, end_date)

    # 5. 提示落盘（供邮件报告独立进程读取）
    save_multi_indicator_hints(hints, end_date)

    # 6. HTML 报告（延迟导入，避免循环依赖）
    try:
        from generate_stock_html import generate_multi_indicator_html
        industry_count = result['industry_name'].value_counts().to_dict() if not result.empty else {}
        generate_multi_indicator_html(result, df, end_date, funnel, industry_count, industry_hints=hints)
    except Exception as e:
        logging.error("多指标联合选股: HTML 报告生成失败 (%s)", e)

    # 7. 打印漏斗统计
    print_multi_indicator_stage_statistics(funnel, hints)

    return result, funnel, hints


def print_multi_indicator_stage_statistics(funnel, hints):
    """打印多指标联合选股各阶段漏斗统计与超阈值行业提示。"""
    print('\n========== 多指标联合选股 各阶段股票计数 ==========')
    for key, value in funnel.items():
        print(f'  {key}: {value:>5} 只')

    print('\n========== 超阈值行业提示 ==========')
    if hints:
        for h in hints:
            print(f'  {h["industry"]}: 入选 {h["count"]} 只')
    else:
        print('  暂无超阈值行业')