#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票多因子选股策略回测系统 (main_par2.py)

本模块实现了基于多因子策略的股票筛选与回测系统，核心策略为"阶梯放量+J13低吸"，
即在上穿60日线后出现阶梯放量，并在KDJ的J值低于13时进行低吸。

主要策略逻辑：
    1. 基础趋势筛选：MACD>0、收盘价>MA60、MA60向上
    2. 阶梯放量策略：上穿60日线后出现连续价随量升
    3. 放量确认：周期内存在成交额显著放大的交易日
    4. 异动检测：收集区异动 / 堆量建仓 / 突破放量
    5. K线形态过滤：仅保留阳线、十字星、带下影阴线
    6. 振幅过滤：主板<4%、创业板/科创板<7%
    7. 底部暴力K确认：周期内存在底部放量长阳信号
    8. 出货信号排除：排除存在主力出货信号的股票
    9. 知行多空线：中期多空线>多空线，收盘价>=多空线
    10. 次新股排除：上市不足180天
    11. 成交额排名：当日成交额处于全市场前60%

所有策略阈值参数均通过 config.py 中的 STRATEGY_CONFIG (ST) 和 BACKTEST_CONFIG (BT) 集中管理，
便于策略调优和回测验证。

使用方式：
    python main_par2.py                              # 默认今天，60天回测
    python main_par2.py --date 20250620 --days 60    # 指定日期和天数
    python main_par2.py --debug 688321.SH            # 调试单只股票
    python main_par2.py --backtest --hold-days 5     # 执行回测
"""

# ========== 标准库导入 ==========
import argparse
import concurrent.futures
import glob
import logging
import os
import pickle
import time
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Optional, Tuple

# ========== 第三方库导入 ==========
import numpy as np
import pandas as pd
import plotly.express as px
import tushare as ts
from tabulate import tabulate
from tqdm import tqdm

# ========== 项目模块导入 ==========
from config import (
    APIConfig,
    BACKTEST_CONFIG as BT,
    DBConfig,
    ParallelConfig,
    STRATEGY_CONFIG as ST,
)
from data_manager import DataManager
from generate_stock_html import generate_stock_selection_html
from generate_trend_html import generate_industry_trend_html, generate_j13_trend_html
from sentiment_rebound_strategy import SentimentReboundStrategy, generate_strategy_report
# from dtw_similarity import DTWSimilarityAnalyzer  # 模块不存在，已注释

# ========== 全局配置 ==========
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s | %(message)s")

# Tushare API 初始化
ts.set_token(APIConfig.get_token())
pro = ts.pro_api()

# 行业缓存配置
INDUSTRY_CACHE_FILE = 'industry_cache.pkl'
INDUSTRY_CACHE_EXPIRE = 7 * 24 * 3600  # 7天

# 全局线程池配置
CPU_COUNT = multiprocessing.cpu_count()
# 线程池大小设置为CPU核心数的2倍，平衡并发性能和系统资源
GLOBAL_THREAD_POOL_SIZE = min(CPU_COUNT * 2, 24)  # 最多24个线程
# 创建全局线程池
global_thread_pool = ThreadPoolExecutor(max_workers=GLOBAL_THREAD_POOL_SIZE)
logging.info(f"全局线程池初始化完成，大小: {GLOBAL_THREAD_POOL_SIZE} (CPU核心数: {CPU_COUNT})")

# ========== 数据字段定义 ==========
# 将长字段列表提取到顶部，便于维护
STOCK_FACTOR_FIELDS = [
    # 1. 基础价格 & 成交
    'ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'pre_close', 'pct_chg', 'amount',
    # 2. 换手 & 量比 & 估值 & 股本（来自 daily_basic）
    'turnover_rate', 'turnover_rate_f', 'volume_ratio',
    'pe', 'pe_ttm', 'pb', 'ps', 'ps_ttm', 'dv_ratio', 'dv_ttm',
    'total_share', 'float_share', 'free_share', 'total_mv', 'circ_mv', 'adj_factor',
    # 3. 前复权价格
    'open_qfq', 'high_qfq', 'low_qfq', 'close_qfq',
    # 4. 均线（前复权）
    'ma_qfq_5', 'ma_qfq_10', 'ma_qfq_20', 'ma_qfq_30', 'ma_qfq_60', 'ma_qfq_90', 'ma_qfq_250',
    # 5. EMA（前复权）
    'ema_qfq_10', 'ema_qfq_13', 'ema_qfq_30', 'ema_qfq_60', 'ema_qfq_90', 'ema_qfq_250',
    # 6. MACD（前复权）
    'macd_dif_qfq', 'macd_dea_qfq', 'macd_qfq',
    # 7. KDJ（前复权）
    'kdj_k_qfq', 'kdj_d_qfq', 'kdj_qfq',
]


# ========== 工具函数 ==========

def _is_main_board(ts_code: str) -> bool:
    """判断是否为主板股票
    
    主板股票代码以 00（深市主板）或 60（沪市主板）开头，
    创业板以 30 开头，科创板以 68 开头，北交所以 8/4 开头。
    
    Args:
        ts_code: 股票代码，如 '000547.SZ'、'688321.SH'
    
    Returns:
        True 为主板，False 为创业板/科创板/北交所
    """
    return ts_code.startswith(('00', '60'))


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="股票策略回测脚本")
    parser.add_argument("--date", type=str, default=None, help="回测日期，格式为 YYYYMMDD，默认使用今天")
    parser.add_argument("--days", type=int, default=60, help="回测所用历史天数，默认 60 天")
    parser.add_argument("--debug", type=str, default="", help="调试模式，传入股票代码（逗号分隔）")
    parser.add_argument("--backtest", action="store_true", help="是否执行回测")
    parser.add_argument("--hold-days", type=int, default=3, help="回测持有天数，默认3天")
    parser.add_argument("--detailed", action="store_true", help="是否打印每只股票逐日持仓数据")
    return parser.parse_args()


def get_simple_industry_info() -> pd.DataFrame:
    """获取简单的行业信息（带7天缓存）"""
    if os.path.exists(INDUSTRY_CACHE_FILE):
        file_age = time.time() - os.path.getmtime(INDUSTRY_CACHE_FILE)
        if file_age < INDUSTRY_CACHE_EXPIRE:
            try:
                with open(INDUSTRY_CACHE_FILE, 'rb') as f:
                    return pickle.load(f)
            except:
                pass
    
    try:
        stock_basic = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name,industry')
        stock_basic = stock_basic.rename(columns={'industry': 'industry_name'})
        stock_basic['industry_name'] = stock_basic['industry_name'].fillna('未知行业')
        result = stock_basic[['ts_code', 'industry_name']]
        
        with open(INDUSTRY_CACHE_FILE, 'wb') as f:
            pickle.dump(result, f)
        
        return result
    except Exception as e:
        print(f"获取行业信息出错: {e}")
        if os.path.exists(INDUSTRY_CACHE_FILE):
            print("⚠️ 使用缓存数据（可能过期）")
            with open(INDUSTRY_CACHE_FILE, 'rb') as f:
                return pickle.load(f)
        return pd.DataFrame(columns=['ts_code', 'industry_name'])


def get_nearest_trade_date(data_manager, target_date: Optional[datetime] = None, max_lookback_days: int = 10) -> str:
    """获取最近的交易日"""
    if target_date is None:
        target_date = datetime.now()
    
    start_dt = target_date - timedelta(days=max_lookback_days)
    end_date_str = target_date.strftime('%Y%m%d')
    start_date_str = start_dt.strftime('%Y%m%d')
    
    logging.info("正在获取 %s 到 %s 之间的交易日历...", start_date_str, end_date_str)
    
    trade_dates = data_manager.get_trade_dates(start_date_str, end_date_str)
    
    if not trade_dates:
        logging.warning("未获取到任何交易日历，使用目标日期 %s", end_date_str)
        return end_date_str
    
    nearest_date = max(trade_dates)
    
    if nearest_date > end_date_str:
        trade_dates = [d for d in trade_dates if d <= end_date_str]
        if trade_dates:
            nearest_date = max(trade_dates)
        else:
            nearest_date = end_date_str
    
    logging.info("目标日期 %s 的最近交易日为 %s", end_date_str, nearest_date)
    return nearest_date


def _threaded_apply_grouped(func, grouped_data, desc: str = "Processing"):
    """使用全局线程池对分组数据并行执行函数
    
    将 DataFrame 的 groupby 分组结果提交到全局线程池并行处理，
    适用于按股票代码分组后独立计算策略标记的场景。
    
    Args:
        func: 处理单个分组的函数，接收 group DataFrame，返回 pd.Series
        grouped_data: pd.core.groupby.DataFrameGroupBy 对象
        desc: tqdm 进度条描述文本
    
    Returns:
        合并后的 pd.Series，索引与原始 DataFrame 对齐
    """
    results = []
    
    # 使用全局线程池
    global global_thread_pool
    future_to_key = {global_thread_pool.submit(func, group): name for name, group in grouped_data}
    
    # 等待所有任务完成，收集结果
    for future in tqdm(concurrent.futures.as_completed(future_to_key), 
                      total=len(future_to_key), desc=desc):
        ts_code = future_to_key[future]
        try:
            result = future.result()
            results.append(result)
        except Exception as e:
            # 单只股票处理失败不影响整体，返回全 False 的 Series
            print(f'{ts_code} 处理失败: {e}')
            group = grouped_data.get_group(ts_code)
            results.append(pd.Series(False, index=group.index))
    
    # 合并所有结果并按原始索引排序
    if results:
        return pd.concat(results).sort_index()
    return pd.Series([], dtype=bool)



# ========== 分析函数 ==========

def is_ignorable_gap(gap_row, after_gap, debug: bool = False) -> bool:
    """判断跳空缺口是否可忽略（即后续走势已回补缺口）
    
    跳空缺口分为两种情况：
    - 小跳空（< MIN_GAP_SIZE_RATIO）：如果后续量能和价格都显著放大，则可忽略
    - 大跳空（>= MIN_GAP_SIZE_RATIO）：需要同时满足以下条件才可忽略：
      1. 跳空幅度 < GAP_SIZE_RATIO（2.5%）
      2. 缺口位于60日线下方
      3. 后续量能放大 > GAP_LARGE_VOL_RATE 倍
      4. 后续价格上涨 > GAP_LARGE_PRICE_RATE 倍
    
    Args:
        gap_row: 跳空日的那一行数据（需含 low_qfq, prev_high, close_qfq, ma_qfq_60, amount）
        after_gap: 跳空日之后的所有数据（DataFrame）
        debug: 是否输出调试信息
    
    Returns:
        True 表示缺口可忽略（已回补），False 表示缺口未回补
    """
    # 计算跳空幅度：当日最低价 - 前一日最高价
    gap_size = gap_row['low_qfq'] - gap_row['prev_high']
    gap_percent = gap_size / max(gap_row['prev_high'], 0.01) * 100
    
    if debug:
        print(f"跳空幅度: {gap_size:.4f} ({gap_percent:.2f}%)")
    
    if gap_percent < ST.MIN_GAP_SIZE_RATIO * 100:
        # 小跳空：后续量能和价格都显著放大则可忽略
        max_vol = after_gap['amount'].max()
        vol_rate = max_vol / max(gap_row['amount'], 1)
        max_prc = after_gap['close_qfq'].max()
        prc_rate = max_prc / max(gap_row['close_qfq'], 0.01)
        return (vol_rate > ST.GAP_IGNORE_VOL_RATE) and (prc_rate > ST.GAP_IGNORE_PRICE_RATE)
    
    # 大跳空：需同时满足多个条件才可忽略
    below_ma60 = gap_row['close_qfq'] < gap_row['ma_qfq_60']
    max_volume_after = after_gap['amount'].max()
    volume_ratio = max_volume_after / max(gap_row['amount'], 1)
    max_price_after = after_gap['close_qfq'].max()
    price_ratio = max_price_after / max(gap_row['close_qfq'], 0.01)
    
    conditions = {
        '跳空幅度<2.5%': gap_percent < ST.GAP_SIZE_RATIO * 100,
        '位于60日线下': below_ma60,
        '量能放大>3x': volume_ratio > ST.GAP_LARGE_VOL_RATE,
        '价格上涨>15%': price_ratio > ST.GAP_LARGE_PRICE_RATE
    }
    
    return all(conditions.values())


def identify_candle_pattern(df) -> Tuple[pd.Series, pd.Series]:
    """识别K线形态（向量化计算）
    
    将K线分为四种形态，按优先级排序：
    1. yang（阳线）：收盘>开盘，且涨跌幅在 KLINE_PCT_RANGE 范围内
    2. doji（十字星）：实体占比 < BODY_RATIO_THRESHOLD，且涨跌幅在范围内
    3. yin_with_shadow（带下影阴线）：阴线且下影线占比 >= MIN_SHADOW_RATIO
    4. other（其他）：不符合以上条件的K线
    
    Args:
        df: 含 open_qfq, close_qfq, high_qfq, low_qfq, pct_chg 的 DataFrame
    
    Returns:
        (candle_label, candle_rank): 形态名称 Series 和优先级 Series（1=最优，4=最差）
    """
    open_ = df['open_qfq']
    close = df['close_qfq']
    high = df['high_qfq']
    low = df['low_qfq']
    
    # 计算实体和振幅
    body = (close - open_).abs()
    range_ = high - low
    range_ = np.where(range_ == 0, 1e-6, range_)  # 避免除零
    body_ratio = body / range_  # 实体占振幅的比例
    
    # 涨跌幅范围过滤（排除涨停/跌停等极端K线）
    in_range = df['pct_chg'].between(ST.KLINE_PCT_RANGE[0], ST.KLINE_PCT_RANGE[1])
    
    # 条件1：阳线
    is_yang = (close > open_) & in_range
    
    # 条件2：十字星（实体极小）
    is_doji = (body_ratio < ST.BODY_RATIO_THRESHOLD) & in_range
    
    # 条件3：带下影阴线（下影线较长，说明下方有支撑）
    lower_shadow = np.minimum(open_, close) - low
    lower_shadow_ratio = lower_shadow / range_
    is_yin_shadow = (close < open_) & in_range & (lower_shadow_ratio >= ST.MIN_SHADOW_RATIO)
    
    # 使用 np.select 按优先级选择形态（条件列表按优先级从高到低排列）
    condition_list = [is_yang, (~is_yang) & is_doji, (~is_yang) & (~is_doji) & is_yin_shadow]
    choice_label = ['yang', 'doji', 'yin_with_shadow']
    choice_rank = [1, 2, 3]
    
    candle_label = np.select(condition_list, choice_label, default='other')
    candle_rank = np.select(condition_list, choice_rank, default=4)
    
    return pd.Series(candle_label, index=df.index), pd.Series(candle_rank, index=df.index)


def mark_step_vol_price(group, debug: bool = False) -> pd.Series:
    """标记阶梯放量策略信号（first_j13_step）
    
    策略逻辑：
    1. 找到上穿60日线且不跳空的交易日
    2. 检查上穿后的走势中是否存在不可忽略的跳空缺口
    3. 检查是否出现连续价随量升（至少 PRICE_VOLUME_CONSECUTIVE 天）
    4. 检查最大成交额日是否为阴线下跌（排除出货嫌疑）
    5. 在满足以上条件且 J<13 的日期标记信号
    
    Args:
        group: 单只股票的 DataFrame（按 ts_code 分组）
        debug: 是否输出调试信息
    
    Returns:
        布尔 Series，True 表示该日为阶梯放量+J13低吸信号
    """
    out = pd.Series(False, index=group.index)
    
    # 排除ST股和北交所股票
    basic_mask = (~group['name'].str.contains('ST|st', na=False)) & (~group['ts_code'].str.endswith('.BJ'))
    group = group.loc[basic_mask]
    
    if group.empty:
        return out
    
    # 找到上穿60日线且不跳空的交易日
    cross_rows = group[group['cross'] & ~group['gap_up']]
    if cross_rows.empty:
        return out
    
    for _, cross_row in cross_rows.iterrows():
        cross_idx = cross_row.name
        # 取上穿日及之后的所有数据
        after = group.loc[group.index >= cross_idx].copy()
        
        # 检查跳空缺口是否可忽略
        gap_days = after[after['gap_up']]
        if not gap_days.empty:
            all_gaps_ok = True
            for _, gap_row in gap_days.iterrows():
                gap_high = gap_row['prev_high']
                after_gap = after.loc[after.index > gap_row.name]
                
                if is_ignorable_gap(gap_row, after_gap, debug=False):
                    continue
                
                if not (after_gap['close_qfq'] < gap_high).any():
                    all_gaps_ok = False
                    break
            
            if not all_gaps_ok:
                continue
        
        # 检查连续价随量升天数
        consecutive_rise = 0
        max_consecutive = 0
        
        for i in range(1, len(after)):
            if (after['amount'].iloc[i] > after['amount'].iloc[i-1]) and \
               (after['close_qfq'].iloc[i] > after['close_qfq'].iloc[i-1]):
                consecutive_rise += 1
                max_consecutive = max(max_consecutive, consecutive_rise)
            else:
                consecutive_rise = 0
        
        # 连续价随量升天数不足，跳过
        if max_consecutive < ST.PRICE_VOLUME_CONSECUTIVE - 1:
            continue
        
        # 检查最大成交额日是否为阴线下跌（出货嫌疑检查）
        max_volume = after['amount'].max()
        valid_volume = True
        
        for i in range(1, len(after)):
            is_yin_line = after['close_qfq'].iloc[i] < after['open_qfq'].iloc[i]
            if (after['close_qfq'].iloc[i] < after['close_qfq'].iloc[i-1]) and \
               (abs(after['amount'].iloc[i] - max_volume) < 1e-6) and is_yin_line:
                valid_volume = False
                break
        
        if not valid_volume:
            continue
        
        # 标记J值<13的日期为信号
        j_below_13_mask = after['kdj_qfq'] < 13
        if j_below_13_mask.any():
            for idx in after[j_below_13_mask].index:
                out.loc[idx] = True
    
    return out


def mark_volume_surge(group, debug: bool = False) -> pd.Series:
    """标记放量信号（向量化计算）
    
    判断当日成交额是否 >= 前5日均值 × VOLUME_SURGE_RATIO。
    使用 rolling 窗口计算前5日均值，避免逐行循环。
    
    Args:
        group: 单只股票的 DataFrame（按 ts_code 分组）
        debug: 是否输出调试信息
    
    Returns:
        布尔 Series，True 表示该日为放量日
    """
    if len(group) < 6:  # 至少需要6天数据（5天窗口+1天当前）
        return pd.Series(False, index=group.index)
    group = group.sort_values('trade_date')
    # 计算前5日成交额均值（shift(1)确保不包含当日）
    rolling_avg = group['amount'].rolling(window=5, min_periods=5).mean().shift(1)
    out = group['amount'] >= (rolling_avg * ST.VOLUME_SURGE_RATIO)
    return out.fillna(False)


def mark_abnormal_movement(group, debug: bool = False, max_life: int = 60) -> pd.Series:
    """标记异动信号（三种类型）
    
    基于上穿60日线判断收集区，识别三种异动类型：
    
    类型1 - 收集区异动：上穿60日线后，在收集区（收盘价 <= MA60 × COLLECT_MA60_TOLERANCE）内
        出现放量（成交额 > MA5 × COLLECT_VOL_MULTIPLIER）且涨幅达标（主板>=3.8%，其他>=7%）
    
    类型2 - 堆量建仓：在收集区内出现 >= 2 次放量上涨
        （成交额 > MA5 × VOLUME_MULTIPLIER，涨幅阈值主板>=2.5%，其他>=5%）
    
    类型3 - 突破放量：上穿60日线当日放量突破
        （成交额 > MA5 × BREAKTHROUGH_VOL_MULTIPLIER，涨幅>=3%）
    
    Args:
        group: 单只股票的 DataFrame（按 ts_code 分组）
        debug: 是否输出调试信息
        max_life: 收集区最长存活天数（默认60天）
    
    Returns:
        布尔 Series，True 表示该日为异动日
    """
    out = pd.Series(False, index=group.index)
    
    if len(group) < 20:
        return out
    
    group = group.sort_values('trade_date')
    group['trade_date'] = pd.to_datetime(group['trade_date'], format='%Y%m%d')
    group['amount_ma5'] = group['amount'].rolling(5).mean()  # 5日成交额均值
    
    ts_code = group['ts_code'].iloc[0]
    # 根据板块确定收集区涨幅阈值
    pct_threshold = ST.COLLECT_PCT_00_60 if _is_main_board(ts_code) else ST.COLLECT_PCT_OTHER
    
    # 找到上穿60日线的交易日
    up_crosses = group[
        (group['close_qfq'] >= group['ma_qfq_60']) &
        (group['close_qfq'].shift(1) < group['ma_qfq_60'].shift(1))
    ]
    
    if not up_crosses.empty:
        latest_up = up_crosses.index.max()
        up_date = group.loc[latest_up, 'trade_date']
        
        # 确定收集区结束日期：下次跌破60日线，或超过max_life天
        after_up = group.loc[latest_up:]
        pullback_mask = after_up[after_up['close_qfq'] < after_up['ma_qfq_60']].index
        
        if pullback_mask.empty:
            life_end = up_date + pd.Timedelta(days=max_life)
        else:
            life_end = group.loc[pullback_mask[0], 'trade_date']
        
        # 类型1：收集区异动 - 在收集区内放量且涨幅达标
        collect_mask = (
            (group['trade_date'] >= up_date) &
            (group['trade_date'] <= life_end) &
            (group['close_qfq'] <= group['ma_qfq_60'] * ST.COLLECT_MA60_TOLERANCE)
        )
        
        cond1 = (
            collect_mask &
            (group['amount'] > group['amount_ma5'] * ST.COLLECT_VOL_MULTIPLIER) &
            (group['pct_chg'] >= pct_threshold)
        )
        out |= cond1
    
    # 类型2：堆量建仓 - 收集区内出现 >= 2 次放量上涨
    if not up_crosses.empty:
        latest2_up = up_crosses.index.max()
        up2_date = group.loc[latest2_up, 'trade_date']
        life2_end = life_end if 'life_end' in locals() else up2_date + pd.Timedelta(days=max_life)
        
        sub2_df = group[
            (group['trade_date'] >= up2_date) &
            (group['trade_date'] <= life2_end) &
            (group['close_qfq'] <= group['ma_qfq_60'] * ST.COLLECT_MA60_TOLERANCE)
        ].copy()
        
        # 堆量建仓涨幅阈值（主板/其他不同）
        pct_threshold2 = ST.PILE_PCT_00_60 if _is_main_board(ts_code) else ST.PILE_PCT_OTHER
        
        day_hit2 = (
            (sub2_df['amount'] > sub2_df['amount_ma5'] * ST.VOLUME_MULTIPLIER) &
            (sub2_df['pct_chg'] >= pct_threshold2)
        )
        
        if day_hit2.sum() >= 2:
            out.loc[sub2_df.index] = True
    
    # 类型3：突破放量 - 上穿60日线当日放量突破
    break_through = (
        (group['close_qfq'] >= group['ma_qfq_60']) &
        (group['close_qfq'].shift(1) < group['ma_qfq_60'].shift(1)) &
        (group['amount'] > group['amount_ma5'] * ST.BREAKTHROUGH_VOL_MULTIPLIER) &
        (group['pct_chg'] >= ST.BREAKTHROUGH_PCT)
    )
    out |= break_through
    
    return out


def mark_bottom_violent_k(group, debug: bool = False) -> pd.Series:
    """标记底部暴力K线信号
    
    底部暴力K线表示在相对底部位置出现的放量长阳线，暗示主力资金介入。
    需同时满足三个条件：
    1. 放量：当日成交额 >= 前一日 × BOTTOM_VK_VOL_MULTIPLIER
    2. 长阳：实体涨幅 = (收盘-开盘)/开盘 >= 阈值
       - 主板（10%涨停板）：>= BOTTOM_VK_BODY_PCT_00_60 (3%)
       - 创业板/科创板（20%涨停板）：>= BOTTOM_VK_BODY_PCT_OTHER (6%)
    3. 接近60日线：|收盘价/MA60 - 1| <= 阈值
       - 主板：<= BOTTOM_VK_MA60_TOL_00_60 (10%)
       - 创业板/科创板：<= BOTTOM_VK_MA60_TOL_OTHER (20%)
    
    Args:
        group: 单只股票的 DataFrame（按 ts_code 分组）
        debug: 是否输出调试信息
    
    Returns:
        布尔 Series，True 表示该日为底部暴力K线
    """
    out = pd.Series(False, index=group.index)
    
    if len(group) < 10:  # 至少需要10天数据确保MA60有效
        return out
    
    group = group.sort_values('trade_date')
    ts_code = group['ts_code'].iloc[0]
    
    # 根据板块确定阈值
    is_main = _is_main_board(ts_code)
    min_body_pct = ST.BOTTOM_VK_BODY_PCT_00_60 if is_main else ST.BOTTOM_VK_BODY_PCT_OTHER
    ma60_tolerance = ST.BOTTOM_VK_MA60_TOL_00_60 if is_main else ST.BOTTOM_VK_MA60_TOL_OTHER
    
    # 前一日成交额
    group['amount_prev'] = group['amount'].shift(1)
    
    # 条件1：放量
    volume_surge = group['amount'] >= (group['amount_prev'] * ST.BOTTOM_VK_VOL_MULTIPLIER)
    
    # 条件2：长阳（实体涨幅 = (收盘价-开盘价)/开盘价）
    body_pct = (group['close_qfq'] - group['open_qfq']) / group['open_qfq']
    is_long_yang = body_pct >= min_body_pct
    
    # 条件3：接近60日线（相对底部位置）
    near_ma60 = abs(group['close_qfq'] / group['ma_qfq_60'] - 1) <= ma60_tolerance
    
    out = volume_surge & is_long_yang & near_ma60
    
    if debug and out.any():
        violent_days = group[out]
        board_type = "20%" if not is_main else "10%"
        tol_pct = ma60_tolerance * 100
        print(f"\n[底部暴力K] {ts_code} ({board_type}板, ±{tol_pct:.0f}%) 发现 {len(violent_days)} 个信号:")
        for idx, row in violent_days.iterrows():
            body = (row['close_qfq'] - row['open_qfq']) / row['open_qfq']
            dist_ma60 = (row['close_qfq'] / row['ma_qfq_60'] - 1) * 100
            print(f"  📅 {row['trade_date']}: 实体{body*100:.2f}%, "
                  f"放量{row['amount']/row['amount_prev']:.1f}倍, "
                  f"距60日线{dist_ma60:+.1f}%")
    
    return out


def mark_distribution_signal(group, debug: bool = False) -> pd.Series:
    """标记主力出货信号V1 - 周期最高点放天量大阴线
    
    当日同时满足以下三个条件时标记为出货信号：
    1. 当日最高价 = 回测周期内最高价（创周期新高）
    2. 天量：当日成交额 >= 前一日 × DISTRIBUTION_VOL_MULTIPLIER
    3. 大阴线：开盘价 > 收盘价，且实体跌幅 >= 阈值
       - 主板 >= DISTRIBUTION_YIN_PCT_00_60 (3%)
       - 创业板/科创板 >= DISTRIBUTION_YIN_PCT_OTHER (6%)
    
    Args:
        group: 单只股票的 DataFrame（按 ts_code 分组）
        debug: 是否输出调试信息
    
    Returns:
        布尔 Series，True 表示该日为主力出货信号
    """
    out = pd.Series(False, index=group.index)
    
    if len(group) < 2:  # 至少需要2天数据（当日+前日）
        return out
    
    group = group.sort_values('trade_date')
    ts_code = group['ts_code'].iloc[0]
    
    # 根据板块确定大阴线阈值
    min_yin_pct = ST.DISTRIBUTION_YIN_PCT_00_60 if _is_main_board(ts_code) else ST.DISTRIBUTION_YIN_PCT_OTHER
    
    # 条件1: 当日最高价为周期内最高价
    period_high = group['high_qfq'].max()
    is_period_high = group['high_qfq'] == period_high
    
    # 条件2: 天量 - 当日成交额 >= 前一日 × 2倍
    group['amount_prev'] = group['amount'].shift(1)
    is_volume_surge = group['amount'] >= (group['amount_prev'] * ST.DISTRIBUTION_VOL_MULTIPLIER)
    
    # 条件3: 大阴线
    # 实体跌幅 = (开盘价 - 收盘价) / 开盘价
    yin_pct = (group['open_qfq'] - group['close_qfq']) / group['open_qfq']
    is_big_yin = (group['open_qfq'] > group['close_qfq']) & (yin_pct >= min_yin_pct)
    
    # 综合条件
    out = is_period_high & is_volume_surge & is_big_yin
    
    if debug and out.any():
        signal_days = group[out]
        board_type = "20%" if not _is_main_board(ts_code) else "10%"
        print(f"\n[主力出货信号] {ts_code} ({board_type}板) 发现 {len(signal_days)} 个信号:")
        for idx, row in signal_days.iterrows():
            yin = (row['open_qfq'] - row['close_qfq']) / row['open_qfq'] * 100
            vol_ratio = row['amount'] / row['amount_prev'] if row['amount_prev'] > 0 else 0
            print(f"  📅 {row['trade_date']}: 阴线{yin:.2f}%, 放量{vol_ratio:.1f}倍, 最高价{row['high_qfq']:.2f}")
    
    return out


def mark_distribution_signal_v2(group, debug: bool = False) -> pd.Series:
    """标记主力出货信号V2 - 周期最高点后放量下跌
    
    在周期最高点出现后，检查后续两天是否出现放量下跌：
    1. 当日最高价 = 回测周期内最高价
    2. 后两天成交额均值 > 最高价当天成交额（放量）
    3. 后两天累计跌幅 >= 阈值
       - 主板 >= DISTRIBUTION_V2_DROP_00_60 (8%)
       - 创业板/科创板 >= DISTRIBUTION_V2_DROP_OTHER (12%)
    
    注意：需要至少2天的后续数据才能判断
    
    Args:
        group: 单只股票的 DataFrame（按 ts_code 分组）
        debug: 是否输出调试信息
    
    Returns:
        布尔 Series，True 表示该日为主力出货信号V2
    """
    out = pd.Series(False, index=group.index)
    
    if len(group) < 3:  # 至少需要3天数据（最高点+后2天）
        return out
    
    # 保存原始索引，因为后续会 reset_index
    original_index = group.index
    group = group.sort_values('trade_date').reset_index(drop=True)
    ts_code = group['ts_code'].iloc[0]
    
    # 根据板块确定跌幅阈值
    drop_threshold = ST.DISTRIBUTION_V2_DROP_00_60 if _is_main_board(ts_code) else ST.DISTRIBUTION_V2_DROP_OTHER
    
    # 找到周期最高点
    period_high = group['high_qfq'].max()
    high_days = group[group['high_qfq'] == period_high]
    
    # 遍历每个最高点（通常只有一个）
    for idx in high_days.index:
        # 确保有后两天数据
        if idx + 2 >= len(group):
            continue
        
        # 条件1: 当日最高价 = 周期最高价（已满足）
        
        # 条件2: 后两天成交额均值 > 最高价当天成交额
        current_amount = group.loc[idx, 'amount']
        next_2d_amount_avg = group.loc[idx+1:idx+2, 'amount'].mean()
        is_volume_high = next_2d_amount_avg > current_amount
        
        # 条件3: 后两天累计跌幅 >= 阈值
        # 跌幅 = (最高价当天收盘 - 后两天最低收盘) / 最高价当天收盘
        current_close = group.loc[idx, 'close_qfq']
        next_2d_close_min = group.loc[idx+1:idx+2, 'close_qfq'].min()
        drop_pct = (current_close - next_2d_close_min) / current_close
        is_big_drop = drop_pct >= drop_threshold
        
        # 综合条件
        if is_volume_high and is_big_drop:
            # 映射回原始索引
            original_idx = original_index[idx]
            out.loc[original_idx] = True
            
            if debug:
                print(f"\n[主力出货信号V2] {ts_code}")
                print(f"  最高点日期: {group.loc[idx, 'trade_date']}")
                print(f"  当日成交额: {current_amount:,.0f}")
                print(f"  后两天成交额均值: {next_2d_amount_avg:,.0f}")
                print(f"  后两天累计跌幅: {drop_pct*100:.2f}%")
    
    return out


def mark_distribution_signal_v3(group, debug: bool = False) -> pd.Series:
    """标记主力出货信号V3 - 周期最高点后出现多次放量长阴
    
    在周期最高点出现后，检查后续是否出现 >= DISTRIBUTION_V3_MIN_YIN_COUNT 次放量长阴：
    - 放量：当日成交额 > 前一日成交额
    - 长阴线：开盘价 > 收盘价，实体跌幅 >= 阈值
      - 主板 >= DISTRIBUTION_YIN_PCT_00_60 (3%)
      - 创业板/科创板 >= DISTRIBUTION_YIN_PCT_OTHER (6%)
    
    只有出现足够次数的放量长阴，才标记该最高点日为出货信号。
    
    Args:
        group: 单只股票的 DataFrame（按 ts_code 分组）
        debug: 是否输出调试信息
    
    Returns:
        布尔 Series，True 表示该日为主力出货信号V3
    """
    out = pd.Series(False, index=group.index)
    
    if len(group) < 2:  # 至少需要2天数据
        return out
    
    original_index = group.index
    group = group.sort_values('trade_date').reset_index(drop=True)
    ts_code = group['ts_code'].iloc[0]
    
    min_yin_pct = ST.DISTRIBUTION_YIN_PCT_00_60 if _is_main_board(ts_code) else ST.DISTRIBUTION_YIN_PCT_OTHER
    
    period_high = group['high_qfq'].max()
    high_days = group[group['high_qfq'] == period_high]
    
    for idx in high_days.index:
        if idx + 1 >= len(group):
            continue
        
        after_high = group.loc[idx + 1:].copy()
        
        violent_yin_count = 0
        violent_days_info = []
        
        for after_idx in after_high.index:
            curr_amount = group.loc[after_idx, 'amount']
            prev_amount = group.loc[after_idx - 1, 'amount']
            
            is_volume_up = curr_amount > prev_amount
            
            open_price = group.loc[after_idx, 'open_qfq']
            close_price = group.loc[after_idx, 'close_qfq']
            is_yin = open_price > close_price
            
            if is_yin:
                yin_pct = (open_price - close_price) / open_price
            else:
                yin_pct = 0
            
            is_big_yin = is_yin and (yin_pct >= min_yin_pct)
            
            if is_volume_up and is_big_yin:
                violent_yin_count += 1
                violent_days_info.append({
                    'date': group.loc[after_idx, 'trade_date'],
                    'yin_pct': yin_pct,
                    'vol_ratio': curr_amount / prev_amount if prev_amount > 0 else 0
                })
        
        if violent_yin_count >= ST.DISTRIBUTION_V3_MIN_YIN_COUNT:
            original_idx = original_index[idx]
            out.loc[original_idx] = True
            
            if debug:
                board_type = "20%" if not _is_main_board(ts_code) else "10%"
                print(f"\n[主力出货信号V3] {ts_code} ({board_type}板)")
                print(f"  最高点日期: {group.loc[idx, 'trade_date']}")
                print(f"  放量长阴次数: {violent_yin_count}")
                for i, info in enumerate(violent_days_info[:3], 1):
                    print(f"  第{i}次 - 日期: {info['date']}, 阴线: {info['yin_pct']*100:.2f}%, 放量: {info['vol_ratio']:.2f}倍")
    
    return out


# ========== 回测函数 ==========

def get_next_trade_date(current_date: str, data_manager) -> Optional[str]:
    """获取指定日期之后的下一个交易日
    
    先跳过周末，再从 DataManager 获取交易日历确认。
    
    Args:
        current_date: 当前日期，格式 YYYYMMDD
        data_manager: DataManager 实例
    
    Returns:
        下一个交易日字符串（YYYYMMDD），获取失败返回 None
    """
    current_dt = pd.to_datetime(current_date, format='%Y%m%d')
    
    # 先跳过周末
    days_to_add = 1
    while (current_dt + pd.Timedelta(days=days_to_add)).weekday() in [5, 6]:
        days_to_add += 1
    
    future_start = (current_dt + pd.Timedelta(days=days_to_add)).strftime('%Y%m%d')
    future_end = (current_dt + pd.Timedelta(days=10)).strftime('%Y%m%d')
    
    future_dates = data_manager.get_trade_dates(future_start, future_end)
    
    if future_dates:
        future_dates = sorted(future_dates)
        return future_dates[0]
    return None


def backtest_selected_stocks(selected_stocks, buy_date: str, data_manager, 
                             hold_days: int = 3, detailed: bool = False) -> pd.DataFrame:
    """回测选中的股票
    
    在买入日以开盘价买入，持有指定天数后以收盘价卖出，
    统计最高涨幅和收盘涨幅。
    
    Args:
        selected_stocks: 选中的股票代码列表
        buy_date: 买入日期，格式 YYYYMMDD
        data_manager: DataManager 实例
        hold_days: 持有天数，默认3天
        detailed: 是否打印逐日持仓数据
    
    Returns:
        回测结果 DataFrame，含 buy_price, max_price, final_price, max_gain_pct, final_gain_pct 等
    """
    if not selected_stocks or not buy_date:
        logging.warning("回测参数无效，跳过回测")
        return pd.DataFrame()
    
    print(f"\n{'='*70}")
    print(f"📊 回测设置：买入日期={buy_date} | 持有天数={hold_days}天")
    print(f"待回测股票数量：{len(selected_stocks)}只")
    print(f"{'='*70}")
    
    # 获取持有期间的交易日
    hold_end_date = pd.to_datetime(buy_date, format='%Y%m%d') + pd.Timedelta(days=hold_days+5)
    trade_dates = data_manager.get_trade_dates(buy_date, hold_end_date.strftime('%Y%m%d'))
    
    if len(trade_dates) < 2:
        logging.error("持有期间交易日不足，无法回测")
        return pd.DataFrame()
    
    fields = ['ts_code', 'trade_date', 'open_qfq', 'high_qfq', 'low_qfq', 'close_qfq']
    backtest_df = data_manager.get_stock_factors(trade_dates, fields)
    
    if backtest_df.empty:
        logging.error("未获取到回测数据")
        return pd.DataFrame()
    
    backtest_df = backtest_df[backtest_df['ts_code'].isin(selected_stocks)]
    
    results = []
    for idx, ts_code in enumerate(selected_stocks, 1):
        stock_data = backtest_df[backtest_df['ts_code'] == ts_code].sort_values('trade_date')
        
        if stock_data.empty:
            continue
        
        # 以买入日开盘价作为买入价
        buy_row = stock_data[stock_data['trade_date'] == buy_date]
        if buy_row.empty:
            continue
        
        buy_price = buy_row['open_qfq'].iloc[0]
        if pd.isna(buy_price) or buy_price <= 0:
            continue
        
        # 持有期间数据（含买入日）
        hold_data = stock_data.head(hold_days + 1).copy()
        if len(hold_data) < 2:
            continue
        
        # 计算最高涨幅和收盘涨幅
        max_price = hold_data['high_qfq'].max()
        final_price = hold_data['close_qfq'].iloc[-1]
        
        max_gain = (max_price - buy_price) / buy_price * 100
        final_gain = (final_price - buy_price) / buy_price * 100
        
        results.append({
            'ts_code': ts_code,
            'buy_date': buy_date,
            'buy_price': round(buy_price, 2),
            'max_price': round(max_price, 2),
            'final_price': round(final_price, 2),
            'max_gain_pct': round(max_gain, 2),
            'final_gain_pct': round(final_gain, 2),
            'hold_days': len(hold_data) - 1
        })
    
    return pd.DataFrame(results)


def print_backtest_stats(backtest_df):
    """打印回测统计结果
    
    输出最高涨幅和收盘涨幅的平均值、中位数、极值和胜率。
    
    Args:
        backtest_df: backtest_selected_stocks 返回的回测结果 DataFrame
    """
    if backtest_df.empty:
        print("\n❌ 无有效回测数据")
        return
    
    print("\n" + "="*70)
    print("📈 回测统计结果")
    print("="*70)
    
    total_stocks = len(backtest_df)
    max_gains = backtest_df['max_gain_pct']
    final_gains = backtest_df['final_gain_pct']
    
    print(f"有效回测股票数: {total_stocks}")
    print(f"\n最高涨幅统计:")
    print(f"  平均: {max_gains.mean():.2f}% | 中位数: {max_gains.median():.2f}%")
    print(f"  最高: {max_gains.max():.2f}% | 最低: {max_gains.min():.2f}%")
    
    print(f"\n收盘涨幅统计:")
    print(f"  平均: {final_gains.mean():.2f}% | 中位数: {final_gains.median():.2f}%")
    
    max_win_rate = (max_gains > 0).mean() * 100
    final_win_rate = (final_gains > 0).mean() * 100
    print(f"\n胜率: 最高>{max_win_rate:.1f}% | 收盘>{final_win_rate:.1f}%")


# ========== 数据准备函数 ==========

def prepare_trade_dates(args, data_manager) -> Tuple[str, str, int, list]:
    """准备交易日期范围
    
    根据命令行参数确定回测的日期范围。需要额外向前扩展 ma_max_period + 60 天，
    以确保均线等指标有足够的历史数据来计算。
    
    Args:
        args: 命令行参数（含 date, days）
        data_manager: DataManager 实例
    
    Returns:
        (start_date, end_date, actual_days, trade_dates_range):
            回测起始日期、结束日期、实际交易日数、完整交易日列表
    """
    if args.date:
        end_date = args.date
        today = datetime.strptime(end_date, "%Y%m%d")
    else:
        end_date = get_nearest_trade_date(data_manager)
        today = datetime.strptime(end_date, "%Y%m%d")
    
    # MA最大周期114天，额外加60天缓冲确保指标计算完整
    ma_max_period = 114
    lookback_buffer = args.days + ma_max_period + 60
    lookback_start_dt = today - timedelta(days=lookback_buffer)
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


def fetch_and_prepare_data(data_manager, trade_dates):
    """获取并准备股票数据，计算所有辅助字段
    
    从 DataManager 获取股票因子数据，然后计算策略所需的辅助字段：
    - prev_close / prev_ma60 / prev_high：前一日数据（用于判断上穿、跳空等）
    - cross：当日上穿60日线标志
    - amount_yest / amount_2days_ago：前1日/前2日成交额（用于缩量判断）
    - shrink：缩量标志（成交额低于前1日或前2日）
    - gap_up：跳空高开标志（当日最低价 > 前一日最高价）
    - candle_pattern / candle_rank：K线形态及优先级
    - amplitude / is_amplitude_ok：振幅及振幅是否达标
    - zhixing_mid_duokong：知行中期多空线（EMA10的EMA10）
    - ema_qfq_13：13日EMA
    - ma_qfq_14/28/57/114：各周期均线（用于知行多空线计算）
    - zhixing_duokong：知行多空线（4条均线的均值）
    
    Args:
        data_manager: DataManager 实例
        trade_dates: 交易日列表
    
    Returns:
        处理后的 DataFrame，包含所有辅助字段
    """
    df = data_manager.get_stock_factors(trade_dates, STOCK_FACTOR_FIELDS)
    
    if df.empty:
        logging.error("未获取到数据")
        return df
    
    df = df.sort_values(['ts_code', 'trade_date'])
    
    # 计算辅助字段：前一日数据（按股票分组shift）
    df['prev_close'] = df.groupby('ts_code')['close_qfq'].shift(1)
    df['prev_ma60'] = df.groupby('ts_code')['ma_qfq_60'].shift(1)
    df['prev_high'] = df.groupby('ts_code')['high_qfq'].shift(1)
    
    # 上穿60日线标志：当日收盘>=MA60 且 前一日收盘<前一日MA60
    df['cross'] = (df['close_qfq'] >= df['ma_qfq_60']) & (df['prev_close'] < df['prev_ma60'])
    
    # 缩量判断相关字段
    df['amount_yest'] = df.groupby('ts_code')['amount'].shift(1)
    df['amount_2days_ago'] = df.groupby('ts_code')['amount'].shift(2)
    # 缩量：成交额低于前1日或前2日
    df['shrink'] = (df['amount'] < df['amount_yest']) | (df['amount'] < df['amount_2days_ago'])
    
    # 跳空高开：当日最低价 > 前一日最高价
    df['gap_up'] = df['low_qfq'] > df['prev_high']
    
    # K线形态
    df['candle_pattern'], df['candle_rank'] = identify_candle_pattern(df)
    df['is_acceptable_candle'] = df['candle_pattern'] != 'other'
    
    # 振幅 = (最高价-最低价)/前收盘 × 100，按板块设置不同阈值
    df['amplitude'] = (df['high_qfq'] - df['low_qfq']) / df['prev_close'] * 100
    is_main = df['ts_code'].str.startswith(('60', '00'))  # 向量化板块判断
    df['is_amplitude_ok'] = (
        (is_main & df['amplitude'].lt(ST.AMPLITUDE_00_60)) |
        (~is_main & df['amplitude'].lt(ST.AMPLITUDE_OTHER))
    )
    
    # 知行中期多空线：EMA10的EMA10（短期趋势方向）
    df['zhixing_mid_duokong'] = df.groupby('ts_code')['ema_qfq_10'].transform(
        lambda x: x.ewm(span=10, adjust=False).mean()
    )
    
    # 13日EMA（用于J13策略中的趋势辅助判断）
    df['ema_qfq_13'] = df.groupby('ts_code')['close_qfq'].transform(
        lambda x: x.ewm(span=13, adjust=False).mean()
    )
    
    # 计算知行多空线所需的各周期均线
    for period in [14, 28, 57, 114]:
        df[f'ma_qfq_{period}'] = df.groupby('ts_code')['close_qfq'].transform(
            lambda x: x.rolling(window=period, min_periods=period).mean()
        )
    
    # 知行多空线 = (MA14 + MA28 + MA57 + MA114) / 4
    df['zhixing_duokong'] = (
        df['ma_qfq_14'] + df['ma_qfq_28'] + df['ma_qfq_57'] + df['ma_qfq_114']
    ) / 4
    
    return df


def apply_strategy_marks(df):
    """应用所有策略标记（并行计算）
    
    按股票代码分组，使用全局线程池并行计算以下策略标记：
    - first_j13_step：阶梯放量+J13低吸信号
    - volume_surge / volume_surge_any：放量信号
    - abnormal_movement / has_am_in_period：异动信号
    - bottom_violent_k / has_bottom_violent_k：底部暴力K信号
    - distribution_signal / has_distribution_signal：出货信号V1
    - distribution_signal_v2 / has_distribution_signal_v2：出货信号V2
    - distribution_signal_v3 / has_distribution_signal_v3：出货信号V3
    
    Args:
        df: 含辅助字段的股票数据 DataFrame
    
    Returns:
        添加了策略标记列的 DataFrame
    """
    grouped = df.groupby('ts_code')
    
    print("开始计算 first_j13_step...")
    df['first_j13_step'] = _threaded_apply_grouped(mark_step_vol_price, grouped, "Processing mark_step_vol_price")
    logging.info("全市场 first_j13_step=True 共 %d 条", df['first_j13_step'].sum())
    
    print("开始计算 volume_surge...")
    df['volume_surge'] = _threaded_apply_grouped(mark_volume_surge, grouped, "Processing volume_surge").values
    logging.info("全市场 volume_surge=True 共 %d 条", df['volume_surge'].sum())
    
    df['volume_surge_any'] = df.groupby('ts_code')['volume_surge'].transform('any')
    
    print("开始计算 abnormal_movement...")
    df['abnormal_movement'] = _threaded_apply_grouped(mark_abnormal_movement, grouped, "Processing abnormal_movement")
    df['has_am_in_period'] = df.groupby('ts_code')['abnormal_movement'].any().reindex(df['ts_code']).values
    
    print("开始计算 bottom_violent_k...")
    df['bottom_violent_k'] = _threaded_apply_grouped(mark_bottom_violent_k, grouped, "Processing bottom_violent_k")
    df['has_bottom_violent_k'] = df.groupby('ts_code')['bottom_violent_k'].transform('any')
    logging.info("全市场 bottom_violent_k=True 共 %d 条", df['bottom_violent_k'].sum())
    
    print("开始计算 distribution_signal...")
    df['distribution_signal'] = _threaded_apply_grouped(mark_distribution_signal, grouped, "Processing distribution_signal")
    df['has_distribution_signal'] = df.groupby('ts_code')['distribution_signal'].transform('any')
    logging.info("全市场 distribution_signal=True 共 %d 条", df['distribution_signal'].sum())
    
    print("开始计算 distribution_signal_v2...")
    df['distribution_signal_v2'] = _threaded_apply_grouped(mark_distribution_signal_v2, grouped, "Processing distribution_signal_v2")
    df['has_distribution_signal_v2'] = df.groupby('ts_code')['distribution_signal_v2'].transform('any')
    logging.info("全市场 distribution_signal_v2=True 共 %d 条", df['distribution_signal_v2'].sum())
    
    print("开始计算 distribution_signal_v3...")
    df['distribution_signal_v3'] = _threaded_apply_grouped(mark_distribution_signal_v3, grouped, "Processing distribution_signal_v3")
    df['has_distribution_signal_v3'] = df.groupby('ts_code')['distribution_signal_v3'].transform('any')
    logging.info("全市场 distribution_signal_v3=True 共 %d 条", df['distribution_signal_v3'].sum())
    
    return df


def calculate_trend_indicators(df):
    """计算MA60趋势指标
    
    通过比较MA60在3日、8日、13日前的值，判断MA60是否向上：
    - 3日趋势 > 0 计1分
    - 8日趋势 > 0 计1分
    - 13日趋势 > 0 计1分
    至少2分（即2个周期趋势向上）则判定MA60向上。
    
    Args:
        df: 含 ma_qfq_60 列的 DataFrame
    
    Returns:
        添加了 ma60_upward 列的 DataFrame
    """
    df['ma60_3d_trend'] = df.groupby('ts_code')['ma_qfq_60'].transform(lambda x: (x - x.shift(3)) / 3)
    df['ma60_8d_trend'] = df.groupby('ts_code')['ma_qfq_60'].transform(lambda x: (x - x.shift(8)) / 8)
    df['ma60_13d_trend'] = df.groupby('ts_code')['ma_qfq_60'].transform(lambda x: (x - x.shift(13)) / 13)
    
    df['ma60_upward'] = (
        (df['ma60_3d_trend'] > 0).astype(int) +
        (df['ma60_8d_trend'] > 0).astype(int) +
        (df['ma60_13d_trend'] > 0).astype(int)
    ) >= 2
    
    return df


def calculate_zhixing_brick_indicator(df):
    """
    计算知行砖形图指标（短期砖型图指标V2026）
    
    指标公式：
    VAR1A:=(HHV(HIGH,4)-CLOSE)/(HHV(HIGH,4)-LLV(LOW,4))*100-90;
    VAR2A:=SMA(VAR1A,4,1)+100;
    VAR3A:=(CLOSE-LLV(LOW,4))/(HHV(HIGH,4)-LLV(LOW,4))*100;
    VAR4A:=SMA(VAR3A,6,1);
    VAR5A:=SMA(VAR4A,6,1)+100;
    VAR6A:=VAR5A-VAR2A;
    砖型图:=IF(VAR6A>4,VAR6A-4,0);
    """
    print("开始计算知行砖形图指标...")
    
    grouped = df.groupby('ts_code')
    
    # 计算4日最高价(HHV)和4日最低价(LLV)
    df['hhv_high_4'] = grouped['high_qfq'].transform(lambda x: x.rolling(window=4, min_periods=4).max())
    df['llv_low_4'] = grouped['low_qfq'].transform(lambda x: x.rolling(window=4, min_periods=4).min())
    
    # 计算价格区间，避免除以0
    df['price_range'] = df['hhv_high_4'] - df['llv_low_4']
    df['price_range'] = df['price_range'].replace(0, np.nan)
    
    # VAR1A:=(HHV(HIGH,4)-CLOSE)/(HHV(HIGH,4)-LLV(LOW,4))*100-90
    df['var1a'] = (df['hhv_high_4'] - df['close_qfq']) / df['price_range'] * 100 - 90
    
    # VAR2A:=SMA(VAR1A,4,1)+100
    # SMA(X,N,M) = M*X+(N-M)*SMA(X,N,M)/N，这里N=4, M=1
    df['var2a'] = grouped['var1a'].transform(lambda x: x.ewm(span=4, adjust=False).mean()) + 100
    
    # VAR3A:=(CLOSE-LLV(LOW,4))/(HHV(HIGH,4)-LLV(LOW,4))*100
    df['var3a'] = (df['close_qfq'] - df['llv_low_4']) / df['price_range'] * 100
    
    # VAR4A:=SMA(VAR3A,6,1)
    df['var4a'] = grouped['var3a'].transform(lambda x: x.ewm(span=6, adjust=False).mean())
    
    # VAR5A:=SMA(VAR4A,6,1)+100
    df['var5a'] = grouped['var4a'].transform(lambda x: x.ewm(span=6, adjust=False).mean()) + 100
    
    # VAR6A:=VAR5A-VAR2A
    df['var6a'] = df['var5a'] - df['var2a']
    
    # 砖型图:=IF(VAR6A>4,VAR6A-4,0)
    df['zhixing_brick'] = np.where(df['var6a'] > 4, df['var6a'] - 4, 0)
    
    # 判断砖型图上升/下降（用于绘制红绿柱）
    # AA:=REF(砖型图,1)<砖型图;  (上升，红色)
    # BB:=REF(砖型图,1)>砖型图;  (下降，绿色)
    df['zhixing_brick_prev'] = grouped['zhixing_brick'].shift(1)
    df['zhixing_brick_rising'] = df['zhixing_brick_prev'] < df['zhixing_brick']  # AA
    df['zhixing_brick_falling'] = df['zhixing_brick_prev'] > df['zhixing_brick']  # BB
    
    # XG信号：前一期AA=0 且 当期AA=1（砖型图由绿转红的第一个交易日）
    # CC:=REF(AA,1)=0 AND (AA=1);
    # XG:=CC>0;
    df['zhixing_brick_prev_rising'] = grouped['zhixing_brick_rising'].shift(1)
    df['zhixing_brick_xg'] = (~df['zhixing_brick_prev_rising'].fillna(False).astype(bool)) & df['zhixing_brick_rising']
    
    # 清理中间变量
    df = df.drop(columns=['hhv_high_4', 'llv_low_4', 'price_range', 
                          'var1a', 'var2a', 'var3a', 'var4a', 'var5a', 'var6a',
                          'zhixing_brick_prev', 'zhixing_brick_prev_rising'])
    
    logging.info("知行砖形图指标计算完成")
    logging.info("  - 砖型图上升(红): %d 条", df['zhixing_brick_rising'].sum())
    logging.info("  - 砖型图下降(绿): %d 条", df['zhixing_brick_falling'].sum())
    logging.info("  - XG信号(绿转红): %d 条", df['zhixing_brick_xg'].sum())
    
    return df


def calculate_amount_rank(df):
    """计算每日成交额排名（前AMOUNT_TOP_PERCENT）
    
    对每个交易日，计算全市场成交额的分位数阈值，
    标记成交额 >= 阈值的股票为成交额排名靠前。
    
    Args:
        df: 含 amount 和 trade_date 列的 DataFrame
    
    Returns:
        添加了 is_amount_top30 和 amount_threshold_40pct 列的 DataFrame
    """
    print("\n正在计算每日成交额排名...")
    
    # 计算每日成交额的分位数阈值
    daily_amount_threshold = df.groupby('trade_date')['amount'].quantile(ST.AMOUNT_TOP_PERCENT)
    
    # 将阈值合并回主表
    df = df.merge(
        daily_amount_threshold.rename('amount_threshold_40pct'),
        left_on='trade_date',
        right_index=True,
        how='left'
    )
    
    # 标记成交额是否在阈值以上
    df['is_amount_top30'] = df['amount'] >= df['amount_threshold_40pct']
    
    print(f"成交额前40%标记完成，共 {df['is_amount_top30'].sum()} 条记录满足")
    
    return df


def apply_final_filter(df, end_date, basic):
    """应用最终筛选条件，输出符合所有策略要求的股票
    
    筛选条件（全部为 AND 关系）：
    1. first_j13_step = True（阶梯放量+J13低吸信号）
    2. MACD DIF > 0（多头趋势）
    3. 缩量（当日成交额低于前1日或前2日，回调缩量）
    4. 无跳空高开
    5. 收盘价 > MA60（价格在60日线上方）
    6. MA60向上（趋势向上）
    7. K线形态可接受（阳线/十字星/带下影阴线）
    8. 振幅达标（主板<4%，创业板/科创板<7%）
    9. 周期内有异动信号
    10. 成交额排名靠前（前60%）
    11. 周期内有底部暴力K信号
    12. 无出货信号（V1/V2/V3均无）
    13. 周期内曾放量
    14. 知行中期多空线 > 知行多空线
    15. 收盘价 >= 知行多空线
    16. 非次新股（上市>=180天）
    
    Args:
        df: 含所有策略标记的 DataFrame
        end_date: 回测结束日期
        basic: 股票基本信息 DataFrame（含 list_date）
    
    Returns:
        筛选结果 DataFrame，按 KDJ J 值升序排列
    """
    # 剔除次新股（上市不足 MIN_STOCK_AGE_DAYS 天）
    cutoff_date = pd.to_datetime(end_date) - pd.Timedelta(days=BT.MIN_STOCK_AGE_DAYS)
    basic['list_date'] = pd.to_datetime(basic['list_date'])
    non_new_stocks = basic[basic['list_date'] <= cutoff_date]['ts_code']
    df_filtered = df[df['ts_code'].isin(non_new_stocks)].copy()
    
    # 最终筛选：所有条件取 AND
    cond = (
        df_filtered['first_j13_step'] &
        (df_filtered['macd_dif_qfq'] > 0) &
        df_filtered['shrink'] &
        ~df_filtered['gap_up'] &
        (df_filtered['ts_code'].isin(basic['ts_code'])) &
        (df_filtered['close_qfq'] > df_filtered['ma_qfq_60']) &
        df_filtered['ma60_upward'] &
        df_filtered['is_acceptable_candle'] &
        df_filtered['is_amplitude_ok'] &
        df_filtered['has_am_in_period'] &
        df_filtered['is_amount_top30'] &
        df_filtered['has_bottom_violent_k'] &
        ~df_filtered['has_distribution_signal'] &
        ~df_filtered['has_distribution_signal_v2'] &
        ~df_filtered['has_distribution_signal_v3'] &
        df_filtered.groupby('ts_code')['volume_surge'].transform('any') &
        (df_filtered['zhixing_mid_duokong'] > df_filtered['zhixing_duokong']) &
        (df_filtered['close_qfq'] >= df_filtered['zhixing_duokong'])
    )
    
    latest = df_filtered[df_filtered['trade_date'] == end_date]
    result = latest[cond][[
        'ts_code', 'name', 'industry_name', 'trade_date', 'close_qfq', 'ma_qfq_60',
        'kdj_qfq', 'macd_dif_qfq', 'amount', 'ma60_upward', 'is_amount_top30'
    ]].sort_values('kdj_qfq')
    
    return result


# ========== DTW 模式匹配 ==========

def load_perfect_patterns(pattern_dir: str = 'data') -> dict:
    """加载完美图形模式"""
    patterns = {}
    pattern_files = glob.glob(f"{pattern_dir}/*.csv")
    
    if not pattern_files:
        logging.warning(f"在 {pattern_dir}/ 目录下未找到任何CSV模式文件")
        return patterns
    
    for file_path in pattern_files:
        try:
            pattern_name = os.path.basename(file_path).replace('.csv', '')
            df = pd.read_csv(file_path)
            
            required_cols = ['pct_chg', 'amount']
            if not all(col in df.columns for col in required_cols):
                continue
            
            if 'trade_date' in df.columns:
                df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
                df = df.sort_values('trade_date').reset_index(drop=True)
            
            patterns[pattern_name] = df
            logging.info(f"✅ 加载模式: {pattern_name} ({len(df)} 天)")
        except Exception as e:
            logging.error(f"加载模式文件失败 {file_path}: {e}")
    
    return patterns


def run_dtw_pattern_matching(df, patterns, top_n: int = 10) -> pd.DataFrame:
    """使用DTW算法执行模式匹配"""
    if not patterns:
        return pd.DataFrame()
    
    dtw_analyzer = DTWSimilarityAnalyzer(
        pattern_dir="data",
        weights={'pct_chg': 0.5, 'amount': 0.5},
        max_workers=8
    )
    
    dtw_analyzer.patterns = {}
    dtw_analyzer.scalers = {}
    
    for pattern_name, pattern_data in patterns.items():
        try:
            pattern_normalized = dtw_analyzer._normalize_pattern(pattern_data, pattern_name)
            dtw_analyzer.patterns[pattern_name] = pattern_normalized
        except Exception as e:
            logging.error(f"处理模板 {pattern_name} 失败: {e}")
    
    grouped = df.groupby('ts_code')
    results = []
    
    total_stocks = len(grouped)
    total_templates = len(dtw_analyzer.patterns)
    
    with tqdm(total=total_stocks * total_templates, desc="DTW模式匹配") as pbar:
        for ts_code, stock_group in grouped:
            stock_group = stock_group.sort_values('trade_date')
            
            if len(stock_group) < 20:
                pbar.update(total_templates)
                continue
            
            for pattern_name, pattern_data in dtw_analyzer.patterns.items():
                try:
                    similarity_result = dtw_analyzer.calculate_stock_pattern_similarity(
                        stock_group, pattern_name
                    )
                    
                    if similarity_result.get('similarity_score', 0) > 0:
                        results.append({
                            'ts_code': ts_code,
                            'name': stock_group['name'].iloc[0] if 'name' in stock_group.columns else ts_code,
                            'industry_name': stock_group['industry_name'].iloc[0] if 'industry_name' in stock_group.columns else '未知',
                            'pattern_name': pattern_name,
                            'similarity_score': round(similarity_result['similarity_score'], 4),
                            'trade_date': stock_group['trade_date'].max(),
                        })
                except Exception as e:
                    logging.error(f"处理 {ts_code} 与模板 {pattern_name} 失败: {e}")
                finally:
                    pbar.update(1)
    
    if not results:
        return pd.DataFrame()
    
    result_df = pd.DataFrame(results)
    final_results = []
    
    for pattern_name in dtw_analyzer.patterns.keys():
        pattern_df = result_df[result_df['pattern_name'] == pattern_name]
        if not pattern_df.empty:
            pattern_df = pattern_df.sort_values('similarity_score', ascending=False).head(top_n)
            pattern_df['rank'] = range(1, len(pattern_df) + 1)
            final_results.append(pattern_df)
    
    if final_results:
        final_df = pd.concat(final_results, ignore_index=True)
        return final_df.sort_values(['pattern_name', 'rank'])
    
    return pd.DataFrame()


# ========== 可视化函数 ==========

def generate_industry_visualization(df, daily_stats, end_date):
    """生成行业可视化图表（成交额趋势）
    
    生成 Top N 行业的总成交额趋势图，保存为 HTML 文件。
    
    Args:
        df: 股票数据 DataFrame
        daily_stats: calculate_daily_stats 返回的每日统计列表
        end_date: 回测结束日期
    """
    trend_data = []
    for daily in daily_stats:
        for industry_data in daily['industries']:
            trend_data.append({
                'date': daily['date'],
                'industry': industry_data['industry'],
                'penetration_rate': industry_data['penetration_rate'],
                'activity_rate': industry_data['activity_rate'],
                'total_amount': industry_data['total_amount'],
            })
    
    trend_df = pd.DataFrame(trend_data)
    trend_df['date'] = pd.to_datetime(trend_df['date'], format='%Y%m%d')
    
    # 行业总成交额趋势图
    PLOT_N = 10
    top_n_industries = trend_df.groupby('industry')['total_amount'].mean().nlargest(PLOT_N).index
    
    fig = px.line(
        trend_df[trend_df['industry'].isin(top_n_industries)],
        x='date',
        y='total_amount',
        color='industry',
        title=f'<b>行业总成交额趋势（Top {PLOT_N}）</b>',
        labels={'total_amount': '总成交额 (万元)', 'date': '日期', 'industry': '行业'},
        markers=True,
    )
    
    avg_amount = trend_df['total_amount'].mean()
    fig.add_hline(y=avg_amount, line_dash="dash", line_color="gray",
                  annotation_text=f"平均: {avg_amount:.2f}万", annotation_position="top right")
    
    # 生成美观的 HTML 趋势图
    html_dir = os.path.join('html', end_date)
    os.makedirs(html_dir, exist_ok=True)
    
    html_content = generate_industry_trend_html(trend_df, end_date, top_n=PLOT_N)
    with open(os.path.join(html_dir, "industry_total_amount_trend.html"), 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f"\n📈 已生成行业总成交额趋势图: {html_dir}/industry_total_amount_trend.html")


def generate_j13_trend(df, end_date):
    """生成 first_j13_step 每日出现数量趋势图
    
    统计每日 KDJ J<13 的股票数量，绘制趋势线并标注平均值和峰值。
    
    Args:
        df: 股票数据 DataFrame
        end_date: 回测结束日期
    """
    daily_first_j13_counts = df[df['kdj_qfq'] < 13].groupby('trade_date').size().reset_index(name='count')
    daily_first_j13_counts['trade_date'] = pd.to_datetime(
        daily_first_j13_counts['trade_date'].astype(str), format='%Y%m%d'
    )
    
    if daily_first_j13_counts.empty:
        print("⚠️ 无数据可绘制")
        return
    
    fig = px.line(
        daily_first_j13_counts,
        x='trade_date',
        y='count',
        title='<b>first_j13_step 每日出现总数趋势</b>',
        labels={'trade_date': '日期', 'count': '出现次数（只）'},
        markers=True,
        line_shape='linear',
        color_discrete_sequence=['#1f77b4']
    )
    
    avg_count = daily_first_j13_counts['count'].mean()
    fig.add_hline(y=avg_count, line_dash="dash", line_color="gray",
                  annotation_text=f"平均值: {avg_count:.1f} 只", annotation_position="top right")
    
    max_row = daily_first_j13_counts.loc[daily_first_j13_counts['count'].idxmax()]
    fig.add_annotation(x=max_row['trade_date'], y=max_row['count'],
                       text=f"峰值: {max_row['count']}只", showarrow=True, arrowhead=2)
    
    # 生成美观的 HTML 趋势图
    html_dir = os.path.join('html', end_date)
    os.makedirs(html_dir, exist_ok=True)
    
    html_content = generate_j13_trend_html(daily_first_j13_counts, end_date)
    with open(os.path.join(html_dir, "first_j13_step_daily_count.html"), 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f"📈 已生成趋势图: {html_dir}/first_j13_step_daily_count.html")


def run_sentiment_rebound_strategy(df, end_date, data_manager):
    """
    执行情绪反弹策略
    
    买入：J13数量 > 90%分位数时倍投（2000→4000→8000→16000）
    卖出：砖型图红柱4根卖一半，红转绿全卖
    """
    print('\n' + '='*70)
    print('📊 情绪反弹策略分析')
    print('='*70)
    
    try:
        # 初始化策略
        strategy = SentimentReboundStrategy(
            etf_code='563300.SH',
            investment_levels=[2000, 4000, 8000, 16000],
            percentile_threshold=0.9,
            lookback_days=60
        )
        
        # 准备J13数据
        daily_first_j13_counts = df[df['kdj_qfq'] < 13].groupby('trade_date').size().reset_index(name='count')
        
        if daily_first_j13_counts.empty:
            print("⚠️ 无J13数据，跳过策略分析")
            return
        
        # 计算J13统计
        j13_stats = strategy.calculate_j13_stats(daily_first_j13_counts)
        
        print(f"\n📈 J13市场统计:")
        print(f"  当前J13数量: {j13_stats['current']:.0f} 只")
        print(f"  90%分位数: {j13_stats['percentile_90']:.1f} 只")
        print(f"  平均值: {j13_stats['mean']:.1f} 只")
        print(f"  是否触发买入: {'✅ 是' if j13_stats['is_above_threshold'] else '❌ 否'}")
        
        # 获取ETF数据（563300.SH 中证2000ETF）
        print(f"\n📊 获取ETF数据: {strategy.etf_code}")
        etf_data = None
        try:
            etf_df = pro.fund_daily(ts_code=strategy.etf_code, 
                                   start_date=(datetime.strptime(end_date, '%Y%m%d') - timedelta(days=120)).strftime('%Y%m%d'),
                                   end_date=end_date)
            if etf_df is not None and not etf_df.empty:
                etf_data = etf_df.sort_values('trade_date').reset_index(drop=True)
                etf_data['open_qfq'] = etf_data['open']
                etf_data['close_qfq'] = etf_data['close']
                etf_data['high_qfq'] = etf_data['high']
                etf_data['low_qfq'] = etf_data['low']
                etf_data['ts_code'] = strategy.etf_code
                print(f"  获取到 {len(etf_data)} 条数据")
        except Exception as e:
            print(f"获取ETF数据失败: {e}")
            etf_data = None
        
        # 计算砖型图指标
        brick_data = None
        if etf_data is not None and not etf_data.empty:
            print("\n📊 计算知行砖形图指标...")
            brick_data = calculate_zhixing_brick_indicator(etf_data.copy())
            latest_brick = brick_data.iloc[-1]
            print(f"  最新砖型图值: {latest_brick['zhixing_brick']:.2f}")
            print(f"  是否上升(红柱): {'是' if latest_brick['zhixing_brick_rising'] else '否'}")
            print(f"  XG信号: {'是' if latest_brick['zhixing_brick_xg'] else '否'}")
        
        # 生成当前日期的交易信号
        current_date = end_date
        current_price = etf_data['close'].iloc[-1] if etf_data is not None and not etf_data.empty else 0
        
        if etf_data is not None and not etf_data.empty:
            signals = strategy.generate_current_signal(j13_stats, current_date, brick_data, current_price)
        else:
            # ETF数据获取失败，仅基于J13数据生成买入信号
            print("\n⚠️ ETF数据获取失败，仅基于J13数据生成买入信号")
            signals = strategy.generate_current_signal(j13_stats, current_date, None, 0)
        
        # 显示交易信号
        if signals:
            print(f"\n⚡ 交易信号:")
            for signal in signals:
                action_emoji = '🟢' if signal['action'] == 'BUY' else '🔴'
                print(f"  {action_emoji} {signal['date']} | {signal['action']} | {signal.get('type', '')}")
                if 'amount' in signal:
                    print(f"     金额: ¥{signal['amount']:,.0f} (第{signal['level']}级)")
                print(f"     原因: {signal['reason']}")
        else:
            print(f"\n⚡ 暂无交易信号")
        
        # 显示当前状态
        position_summary = strategy.get_position_summary()
        print(f"\n📊 当前策略状态:")
        print(f"  投资级别: 第 {position_summary['investment_level'] + 1} 级")
        print(f"  持仓数量: {position_summary['position']}")
        print(f"  红柱计数: {position_summary['red_bar_count']}")
        print(f"  历史交易: {position_summary['total_trades']} 笔")
        
        # 生成策略报告
        html_dir = os.path.join('html', end_date)
        os.makedirs(html_dir, exist_ok=True)
        report_file = os.path.join(html_dir, "sentiment_rebound_strategy.html")
        
        from sentiment_rebound_strategy import generate_etf_chart_data
        etf_chart = generate_etf_chart_data(brick_data, strategy.etf_code) if brick_data is not None else generate_etf_chart_data(etf_data, strategy.etf_code) if etf_data is not None else None
        generate_strategy_report(strategy, signals, j13_stats, report_file, etf_chart_data=etf_chart)
        print(f"\n📄 策略报告已生成: {report_file}")
        
        # 保存策略状态
        strategy.save_state()
        
    except Exception as e:
        print(f"❌ 情绪反弹策略执行失败: {e}")
        import traceback
        traceback.print_exc()


# ========== 结果输出函数 ==========

def print_results(result, df, end_date, df_chart=None):
    """打印筛选结果并生成交互式HTML报告
    
    按行业分组展示筛选结果，包含代码、名称、收盘价、60日线、
    J值、MACD-DIF、成交额等关键指标，同时生成交互式HTML报告。
    
    Args:
        result: apply_final_filter 返回的筛选结果 DataFrame
        df: 回测区间数据（用于表格显示和统计）
        end_date: 结束日期
        df_chart: 完整历史数据（用于图表显示），如果为None则使用df
    """
    # 使用完整数据用于图表
    df_for_chart = df_chart if df_chart is not None else df
    
    print('\n========== 最终筛选结果 ==========')
    
    if result.empty:
        print('❌ 没有符合条件的股票')
        return
    
    print(f'共找到 {len(result)} 只符合条件的股票:')
    
    # 行业分布
    industry_count = result['industry_name'].value_counts()
    print(f'\n按行业分布:')
    for industry, count in industry_count.items():
        print(f'  {industry}: {count}只')
    
    # 准备表格
    industry_order = {industry: i for i, industry in enumerate(industry_count.index)}
    result_sorted = result.copy()
    result_sorted['industry_rank'] = result_sorted['industry_name'].map(industry_order)
    result_sorted = result_sorted.sort_values(['industry_rank', 'kdj_qfq'])
    
    table_data = []
    current_industry = None
    
    for _, row in result_sorted.iterrows():
        if row['industry_name'] != current_industry:
            current_industry = row['industry_name']
            table_data.append(['', f'--- {current_industry} ---', '', '', '', '', '', '', '', '', ''])
        
        cycle_data = df[(df['ts_code'] == row['ts_code']) & (df['trade_date'] <= row['trade_date'])]
        cycle_max = cycle_data['amount'].max() if not cycle_data.empty else 0
        today_vol = row['amount']
        is_lowest_volume = today_vol <= cycle_max * 0.30 if cycle_max else False
        
        # 获取底部暴力K次数
        bvk_count = df[(df['ts_code'] == row['ts_code']) & df['bottom_violent_k']].shape[0] if 'bottom_violent_k' in df.columns else 0
        
        table_data.append([
            row['ts_code'],
            row['name'],
            row['industry_name'] if pd.notna(row['industry_name']) else '未知',
            row['trade_date'],
            f'{row["close_qfq"]:.2f}',
            f'{row["ma_qfq_60"]:.2f}',
            f'{row["kdj_qfq"]:.2f}',
            f'{row["macd_dif_qfq"]:.4f}',
            f'{row["amount"]:.2f}',
            '✅' if row['ma60_upward'] else '❌',
            '✅' if is_lowest_volume else '❌',
            '✅' if row['is_amount_top30'] else '❌',
            f'{bvk_count}次' if bvk_count > 0 else '❌'
        ])
    
    headers = ['代码', '名称', '行业', '日期', '收盘价', '60日线', 'J值', 'MACD-DIF', '成交额', '60日线趋势', '回调最低量', '成交额前60%', '底部暴力K']
    print(tabulate(table_data, headers=headers, tablefmt='github'))
    
    # 生成交互式HTML报告（使用完整历史数据df_for_chart）
    generate_stock_selection_html(result_sorted, df_for_chart, end_date, industry_count)


def print_stage_statistics(df, result, args):
    """打印各阶段股票计数统计
    
    展示从全市场到最终筛选的漏斗式统计，包括：
    - 全市场股票数
    - 上穿60日线股票数
    - 出现阶梯放量的股票数
    - 最终满足条件的股票数
    - 底部暴力K / 出货信号等策略标记统计
    
    Args:
        df: 含策略标记的 DataFrame
        result: 最终筛选结果 DataFrame
        args: 命令行参数
    """
    print('\n========== 各阶段股票计数 ==========')
    total = df['ts_code'].nunique()
    print(f'0) 全市场（{args.days} 天内）: {total:>5} 只')
    
    cross_cnt = df[df['cross']]['ts_code'].nunique()
    print(f'1) 出现"上穿 60 日线": {cross_cnt:>5} 只')
    
    has_step = df.groupby('ts_code')['first_j13_step'].max().astype(bool).sum()
    print(f'2) 出现过阶梯放量: {has_step:>5} 只')
    
    final_cnt = result['ts_code'].nunique()
    print(f'3) 最终满足条件: {final_cnt:>5} 只')
    
    # 新增：底部暴力K统计
    bvk_cnt = df.groupby('ts_code')['bottom_violent_k'].any().sum() if 'bottom_violent_k' in df.columns else 0
    print(f'4) 有底部暴力K信号: {bvk_cnt:>5} 只')
    
    # 新增：主力出货信号统计
    dist_cnt = df.groupby('ts_code')['distribution_signal'].any().sum() if 'distribution_signal' in df.columns else 0
    print(f'5) 有主力出货信号: {dist_cnt:>5} 只')
    
    # 新增：主力出货信号V2统计
    dist_v2_cnt = df.groupby('ts_code')['distribution_signal_v2'].any().sum() if 'distribution_signal_v2' in df.columns else 0
    print(f'6) 有主力出货信号V2: {dist_v2_cnt:>5} 只')
    
    # 新增：主力出货信号V3统计
    dist_v3_cnt = df.groupby('ts_code')['distribution_signal_v3'].any().sum() if 'distribution_signal_v3' in df.columns else 0
    print(f'7) 有主力出货信号V3: {dist_v3_cnt:>5} 只')


def calculate_daily_stats(df, basic_info, recent_days: int = 30) -> list:
    """计算每日行业统计
    
    对每日出现 first_j13_step 且 J<13 的股票，按行业统计数量、
    活跃度（占当日总数比例）、渗透率（占行业总数比例）和总成交额。
    
    Args:
        df: 含策略标记的 DataFrame
        basic_info: 股票基本信息（含 industry_name）
        recent_days: 统计最近多少个交易日，默认30天
    
    Returns:
        每日统计列表，每项含 date, total, industries
    """
    filtered_df = df[df['first_j13_step'].fillna(False) & (df['kdj_qfq'] < 13)]
    recent_dates = sorted(filtered_df['trade_date'].unique())[-recent_days:]
    
    total_by_industry = basic_info['industry_name'].value_counts()
    daily_stats = []
    
    for trade_date in recent_dates:
        group = filtered_df[filtered_df['trade_date'] == trade_date]
        daily_total = group['ts_code'].nunique()
        industry_counts = group['industry_name'].value_counts()
        industry_amounts = group.groupby('industry_name')['amount'].sum() / 10000
        
        industry_stats = []
        for industry, count in industry_counts.items():
            industry_stats.append({
                'industry': industry,
                'count': count,
                'activity_rate': count / daily_total * 100,
                'penetration_rate': count / total_by_industry.get(industry, 1) * 100,
                'total_amount': industry_amounts.get(industry, 0),
            })
        
        industry_stats.sort(key=lambda x: x['total_amount'], reverse=True)
        daily_stats.append({'date': trade_date, 'total': daily_total, 'industries': industry_stats})
    
    return daily_stats


def print_daily_stats(daily_stats, recent_count: int = 10):
    """打印每日行业统计
    
    按日期展示各行业的股票数量、总成交额、活跃度和渗透率。
    
    Args:
        daily_stats: calculate_daily_stats 返回的统计列表
        recent_count: 显示最近多少个交易日，默认10天
    """
    print('\n========== 按日分布统计 ==========')
    
    for daily in daily_stats[-recent_count:]:
        print(f"\n📅 {daily['date']} (共{daily['total']}只)")
        
        for i, industry_data in enumerate(daily['industries'][:8], 1):
            print(f"   {i}. {industry_data['industry']:<12} "
                  f"{industry_data['count']:>2}只 "
                  f"总成交: {industry_data['total_amount']:>8.2f}万 "
                  f"(活跃度: {industry_data['activity_rate']:>5.1f}%, "
                  f"渗透率: {industry_data['penetration_rate']:>5.2f}%)")


# ========== 调试函数 ==========

def debug_stock_strategy_detailed(df, ts_code: str, end_date: str, basic: pd.DataFrame = None) -> bool:
    """详细调试单只股票的策略条件（与主策略完全一致）
    
    逐项检查11项策略条件，输出每项的通过/未通过状态和详细数值，
    便于排查某只股票为何未被选中。
    
    检查项：
    1. 基础技术指标（MACD>0, 收盘>MA60, MA60向上, 无跳空, 缩量）
    2. K线形态
    3. 振幅
    4. 阶梯放量策略（first_j13_step）
    5. 放量
    6. 异动
    7. 成交额排名
    8. 底部暴力K
    9. 派发信号（V1/V2/V3）
    10. 知行多空线
    11. 次新股
    
    Args:
        df: 含所有策略标记的 DataFrame
        ts_code: 股票代码
        end_date: 回测结束日期
        basic: 股票基本信息 DataFrame
    
    Returns:
        True 表示该股票符合所有条件，False 表示不符合
    """
    print(f'\n{"="*70}')
    print(f'📊 详细调试: {ts_code}')
    print(f'{"="*70}')
    
    dbg = df[df.ts_code == ts_code].copy()
    if dbg.empty:
        print(f'❌ 未找到股票 {ts_code} 的数据')
        return False
    
    dbg = dbg.sort_values('trade_date').reset_index(drop=True)
    latest = dbg.iloc[-1]
    
    print(f"\n📅 数据范围: {dbg['trade_date'].min()} 至 {dbg['trade_date'].max()}")
    print(f"📈 最新日期: {latest['trade_date']}")
    print(f"🏢 股票名称: {latest.get('name', 'N/A')}")
    print(f"🏭 所属行业: {latest.get('industry_name', 'N/A')}")
    
    # ========== 1. 基础技术指标检查 ==========
    print(f'\n{"-"*70}')
    print('📌 1. 基础技术指标检查')
    print(f'{"-"*70}')
    
    basic_checks = {
        'MACD DIF > 0': ('macd_dif_qfq', lambda x: x > 0, f"{latest.get('macd_dif_qfq', 0):.4f}"),
        '收盘价 > 60日线': ('close_qfq', lambda x: x > latest.get('ma_qfq_60', 0), 
                         f"收盘:{latest.get('close_qfq', 0):.2f} vs 60日:{latest.get('ma_qfq_60', 0):.2f}"),
        '60日线向上': ('ma60_upward', lambda x: x, '趋势向上'),
        '无跳空': ('gap_up', lambda x: not x, '无跳空缺口'),
        '缩量': ('shrink', lambda x: x, '成交量萎缩'),
    }
    
    basic_results = {}
    for name, (col, check_func, detail) in basic_checks.items():
        value = latest.get(col, None)
        if value is None:
            result = False
            status = '⚠️ 数据缺失'
        else:
            result = check_func(value)
            status = '✅ 通过' if result else '❌ 未通过'
        basic_results[name] = result
        print(f"  {status} | {name:<15} | {detail}")
    
    # ========== 2. K线形态检查 ==========
    print(f'\n{"-"*70}')
    print('📌 2. K线形态检查')
    print(f'{"-"*70}')
    
    candle_pattern = latest.get('candle_pattern', 'other')
    candle_rank = latest.get('candle_rank', 4)
    is_acceptable = latest.get('is_acceptable_candle', False)
    
    pattern_names = {
        'yang': '小阳线 ✅',
        'doji': '十字星 ⚠️',
        'yin_with_shadow': '带下影阴线 🔽',
        'other': '其他形态 ❌'
    }
    
    print(f"  形态: {pattern_names.get(candle_pattern, candle_pattern)} (优先级:{candle_rank})")
    print(f"  {'✅ 通过' if is_acceptable else '❌ 未通过'} | K线形态可接受")
    
    # ========== 3. 振幅检查 ==========
    print(f'\n{"-"*70}')
    print('📌 3. 振幅检查')
    print(f'{"-"*70}')
    
    amplitude = latest.get('amplitude', 0)
    is_amplitude_ok = latest.get('is_amplitude_ok', False)
    is_mb = _is_main_board(ts_code)
    threshold = ST.AMPLITUDE_00_60 if is_mb else ST.AMPLITUDE_OTHER
    
    print(f"  股票类型: {'主板' if is_mb else '其他'} ({'60/00' if is_mb else '其他'}开头)")
    print(f"  振幅阈值: < {threshold}%")
    print(f"  实际振幅: {amplitude:.2f}%")
    print(f"  {'✅ 通过' if is_amplitude_ok else '❌ 未通过'} | 振幅符合要求")
    
    # ========== 4. 阶梯放量策略检查 (first_j13_step) ==========
    print(f'\n{"-"*70}')
    print('📌 4. 阶梯放量策略检查 (first_j13_step)')
    print(f'{"-"*70}')
    
    # 计算前导数据
    dbg['prev_close'] = dbg['close_qfq'].shift(1)
    dbg['prev_ma60'] = dbg['ma_qfq_60'].shift(1)
    dbg['prev_high'] = dbg['high_qfq'].shift(1)
    dbg['cross'] = (dbg['close_qfq'] >= dbg['ma_qfq_60']) & (dbg['prev_close'] < dbg['prev_ma60'])
    dbg['gap_up'] = dbg['low_qfq'] > dbg['prev_high']
    
    # 检查是否有上穿记录
    cross_rows = dbg[dbg['cross'] & ~dbg['gap_up']]
    print(f"  上穿60日线且不跳空次数: {len(cross_rows)}")
    
    if not cross_rows.empty:
        print(f"  上穿日期列表:")
        for _, row in cross_rows.tail(3).iterrows():
            print(f"    - {row['trade_date']}: 收盘{row['close_qfq']:.2f}, J值{row.get('kdj_qfq', 0):.2f}")
    
    # 检查 first_j13_step
    latest_j13 = latest.get('first_j13_step', False)
    print(f"  {'✅ 通过' if latest_j13 else '❌ 未通过'} | first_j13_step 标记")
    
    # J值详情
    kdj_j = latest.get('kdj_qfq', 0)
    print(f"  当前J值: {kdj_j:.2f} {'(J<13 ✅)' if kdj_j < 13 else '(J>=13 ❌)'}")
    
    # ========== 5. 放量检查 ==========
    print(f'\n{"-"*70}')
    print('📌 5. 放量检查')
    print(f'{"-"*70}')
    
    volume_surge_any = dbg['volume_surge'].any() if 'volume_surge' in dbg.columns else False
    surge_count = dbg['volume_surge'].sum() if 'volume_surge' in dbg.columns else 0
    
    print(f"  周期内放量次数: {surge_count}")
    print(f"  {'✅ 通过' if volume_surge_any else '❌ 未通过'} | 周期内曾放量")
    
    # ========== 6. 异动检查 ==========
    print(f'\n{"-"*70}')
    print('📌 6. 异动检查')
    print(f'{"-"*70}')
    
    has_am = latest.get('has_am_in_period', False)
    am_count = dbg['abnormal_movement'].sum() if 'abnormal_movement' in dbg.columns else 0
    
    print(f"  周期内异动次数: {am_count}")
    print(f"  {'✅ 通过' if has_am else '❌ 未通过'} | 周期内曾异动")
    
    # ========== 7. 成交额排名检查 ==========
    print(f'\n{"-"*70}')
    print('📌 7. 成交额排名检查')
    print(f'{"-"*70}')
    
    is_top30 = latest.get('is_amount_top30', False)
    amount = latest.get('amount', 0)
    threshold_val = latest.get('amount_threshold_40pct', 0)
    
    print(f"  当日成交额: {amount:,.0f}")
    print(f"  前60%阈值: {threshold_val:,.0f}")
    print(f"  {'✅ 通过' if is_top30 else '❌ 未通过'} | 成交额在前60%")
    
    # ========== 8. 底部暴力K检查 ==========
    print(f'\n{"-"*70}')
    print('📌 8. 底部暴力K检查')
    print(f'{"-"*70}')
    
    has_bvk = latest.get('has_bottom_violent_k', False)
    bvk_count = dbg['bottom_violent_k'].sum() if 'bottom_violent_k' in dbg.columns else 0
    
    is_mb2 = _is_main_board(ts_code)
    board_type = "10%" if is_mb2 else "20%"
    min_body_pct = ST.BOTTOM_VK_BODY_PCT_00_60 if is_mb2 else ST.BOTTOM_VK_BODY_PCT_OTHER
    ma60_tol = ST.BOTTOM_VK_MA60_TOL_00_60 if is_mb2 else ST.BOTTOM_VK_MA60_TOL_OTHER
    ma60_tol_pct = ma60_tol * 100
    
    print(f"  股票板块: {'主板' if is_mb2 else '创业板/科创板'} ({board_type}涨停)")
    print(f"  长阳阈值: 实体涨幅 >= {min_body_pct*100:.0f}%")
    print(f"  放量阈值: 成交额 >= 前日 × {ST.BOTTOM_VK_VOL_MULTIPLIER:.1f}")
    print(f"  60日线范围: 收盘价在60日线 ±{ma60_tol_pct:.0f}% 范围内")
    print(f"  周期内底部暴力K次数: {bvk_count}")
    
    if bvk_count > 0:
        bvk_days = dbg[dbg['bottom_violent_k']]
        print(f"  信号详情:")
        for idx, row in bvk_days.iterrows():
            body = (row['close_qfq'] - row['open_qfq']) / row['open_qfq'] * 100
            dist_ma60 = (row['close_qfq'] / row['ma_qfq_60'] - 1) * 100
            print(f"    📅 {row['trade_date']}: 实体{body:.2f}%, 距60日线{dist_ma60:+.1f}%")
    else:
        # 详细诊断：显示哪些日子接近满足条件
        print(f"\n  🔍 详细诊断（最近10个交易日）:")
        print(f"  {'日期':<12} {'实体涨幅':<10} {'放量倍数':<10} {'距60日线':<12} {'结果'}")
        print(f"  {'-'*60}")
        
        # 计算需要的字段
        dbg['amount_prev'] = dbg['amount'].shift(1)
        dbg['body_pct'] = (dbg['close_qfq'] - dbg['open_qfq']) / dbg['open_qfq']
        dbg['volume_ratio'] = dbg['amount'] / dbg['amount_prev']
        dbg['dist_ma60_pct'] = (dbg['close_qfq'] / dbg['ma_qfq_60'] - 1) * 100
        
        # 计算60天价格位置
        dbg['price_high_60'] = dbg['close_qfq'].rolling(window=60, min_periods=60).max()
        dbg['price_low_60'] = dbg['close_qfq'].rolling(window=60, min_periods=60).min()
        price_range = dbg['price_high_60'] - dbg['price_low_60']
        dbg['price_position'] = (dbg['close_qfq'] - dbg['price_low_60']) / price_range.replace(0, np.nan)
        
        # 检查最近10个交易日
        recent_days = dbg.tail(10)
        for idx, row in recent_days.iterrows():
            date = row['trade_date']
            body = row['body_pct'] * 100 if pd.notna(row['body_pct']) else 0
            vol_ratio = row['volume_ratio'] if pd.notna(row['volume_ratio']) else 0
            dist_ma60 = row['dist_ma60_pct'] if pd.notna(row['dist_ma60_pct']) else 999
            
            # 检查每个条件
            is_long_yang = body >= min_body_pct * 100
            is_volume_surge = vol_ratio >= ST.BOTTOM_VK_VOL_MULTIPLIER
            is_near_ma60 = abs(dist_ma60) <= ma60_tol_pct
            status = []
            if is_long_yang:
                status.append('✅长阳')
            else:
                status.append(f'❌实体{body:.1f}%')
            
            if is_volume_surge:
                status.append('✅放量')
            else:
                status.append(f'❌{vol_ratio:.1f}倍')
            
            if is_near_ma60:
                status.append('✅近60日')
            else:
                status.append(f'❌距60日{dist_ma60:+.1f}%')
            
            # 如果满足所有条件，标记为🎯
            all_met = is_long_yang and is_volume_surge and is_near_ma60
            prefix = '🎯' if all_met else '  '
            
            print(f"  {prefix}{date} {body:>7.2f}%  {vol_ratio:>7.1f}x   {dist_ma60:>+9.1f}%     {', '.join(status)}")
    
    print(f"\n  {'✅ 通过' if has_bvk else '❌ 未通过'} | 周期内有底部暴力K")
    
    # ========== 9. 派发信号检查 ==========
    print(f'\n{"-"*70}')
    print('📌 9. 派发信号检查')
    print(f'{"-"*70}')
    
    has_dist = latest.get('has_distribution_signal', False)
    has_dist_v2 = latest.get('has_distribution_signal_v2', False)
    has_dist_v3 = latest.get('has_distribution_signal_v3', False)
    dist_count = dbg['distribution_signal'].sum() if 'distribution_signal' in dbg.columns else 0
    dist_v2_count = dbg['distribution_signal_v2'].sum() if 'distribution_signal_v2' in dbg.columns else 0
    dist_v3_count = dbg['distribution_signal_v3'].sum() if 'distribution_signal_v3' in dbg.columns else 0
    
    print(f"  周期内派发信号次数: {dist_count}")
    print(f"  周期内派发信号V2次数: {dist_v2_count}")
    print(f"  周期内派发信号V3次数: {dist_v3_count} (需最高点后出现2次及以上放量长阴)")
    print(f"  {'✅ 通过' if not has_dist else '❌ 未通过'} | 无派发信号")
    print(f"  {'✅ 通过' if not has_dist_v2 else '❌ 未通过'} | 无派发信号V2")
    print(f"  {'✅ 通过' if not has_dist_v3 else '❌ 未通过'} | 无派发信号V3 (2次及以上放量长阴)")
    
    # ========== 10. 知行多空线检查 ==========
    print(f'\n{"-"*70}')
    print('📌 10. 知行多空线检查')
    print(f'{"-"*70}')
    
    zhixing_mid = latest.get('zhixing_mid_duokong', None)
    zhixing = latest.get('zhixing_duokong', None)
    
    close_price = latest.get('close_qfq', None)
    
    if zhixing_mid is not None and zhixing is not None:
        zhixing_ok = zhixing_mid > zhixing
        print(f"  知行中期多空线: {zhixing_mid:.2f}")
        print(f"  知行多空线: {zhixing:.2f}")
        print(f"  差值: {zhixing_mid - zhixing:.2f}")
        print(f"  {'✅ 通过' if zhixing_ok else '❌ 未通过'} | 知行中期多空线 > 知行多空线")
        
        # 新增：收盘价不低于知行多空线检查
        if close_price is not None:
            close_above_zhixing = close_price >= zhixing
            print(f"  收盘价: {close_price:.2f}")
            print(f"  {'✅ 通过' if close_above_zhixing else '❌ 未通过'} | 收盘价 >= 知行多空线")
        else:
            close_above_zhixing = False
            print(f"  ⚠️ 收盘价数据缺失")
            print(f"  ❌ 未通过 | 收盘价数据不足")
    else:
        zhixing_ok = False
        close_above_zhixing = False
        print(f"  ⚠️ 数据缺失: 知行中期多空线={zhixing_mid}, 知行多空线={zhixing}")
        print(f"  ❌ 未通过 | 数据不足（可能需要更多历史数据计算MA114）")
    
    # ========== 11. 次新股检查 ==========
    print(f'\n{"-"*70}')
    print('📌 11. 次新股检查')
    print(f'{"-"*70}')
    
    in_basic = ts_code in basic['ts_code'].values if basic is not None else True
    print(f"  {'✅ 通过' if in_basic else '❌ 未通过'} | 非次新股（上市>=180天）")
    
    # ========== 最终汇总 ==========
    print(f'\n{"="*70}')
    print('📋 最终条件汇总')
    print(f'{"="*70}')
    
    all_conditions = {
        **basic_results,
        'K线形态可接受': is_acceptable,
        '振幅符合': is_amplitude_ok,
        'first_j13_step': latest_j13,
        '周期内曾放量': volume_surge_any,
        '周期内曾异动': has_am,
        '成交额前60%': is_top30,
        '周期内有底部暴力K': has_bvk,
        '无派发信号': not has_dist,
        '无派发信号V2': not has_dist_v2,
        '知行中期>知行多空': zhixing_ok,
        '非次新股': in_basic,
    }
    
    passed = sum(all_conditions.values())
    total = len(all_conditions)
    
    print(f"\n通过: {passed}/{total} 项")
    print(f"\n未通过条件:")
    failed_count = 0
    for name, result in all_conditions.items():
        if not result:
            print(f"  ❌ {name}")
            failed_count += 1
    
    if failed_count == 0:
        print("  无 - 所有条件均通过！")
    
    all_met = all(all_conditions.values())
    print(f'\n{"="*70}')
    print(f'🎯 最终结果: {"✅ 符合所有条件" if all_met else "❌ 不符合条件"}')
    print(f'{"="*70}')
    
    return all_met


# ========== 主程序 ==========

def main():
    """主函数 - 执行完整的选股策略流程
    
    执行步骤：
    1. 解析命令行参数
    2. 准备交易日期范围
    3. 获取并准备股票数据（含辅助字段计算）
    4. 合并股票基本信息（名称、行业）
    5. 应用策略标记（并行计算）
    6. 计算趋势指标（MA60方向）
    7. 计算知行砖形图指标
    8. 计算成交额排名
    9. 应用最终筛选条件
    10. 打印筛选结果
    11. 执行回测（如指定 --backtest）
    12. 生成每日统计和可视化
    13. 执行情绪反弹策略
    14. 调试模式（如指定 --debug）
    """
    args = parse_args()
    data_manager = DataManager()
    
    try:
        # 步骤1-2：准备交易日期
        start_date, end_date, actual_days, trade_dates_range = prepare_trade_dates(args, data_manager)
        
        # 步骤3：获取并准备股票数据
        df_full = fetch_and_prepare_data(data_manager, trade_dates_range)
        if df_full.empty:
            logging.error("未获取到数据，退出程序")
            return
        
        # 保存完整数据供图表使用（含回测区间之前的历史数据）
        df_chart = df_full.copy()
        
        # 仅保留回测区间内的数据用于策略计算
        df = df_full[df_full['trade_date'] >= start_date].copy()
        logging.info("数据筛选后: %d 条记录 (回测区间 %s ~ %s)", len(df), start_date, end_date)
        
        # 步骤4：获取股票基本信息（名称、行业、上市日期）
        basic_info = data_manager.get_stock_basic_info()
        
        # 补全基本信息缺失字段
        if 'name' not in basic_info.columns:
            basic_info['name'] = basic_info['ts_code']
        if 'industry_name' not in basic_info.columns:
            basic_info['industry_name'] = '未知行业'
        
        basic_info['industry_name'] = basic_info['industry_name'].fillna('未知行业')
        basic_info['name'] = basic_info['name'].fillna(basic_info['ts_code'])
        
        # 过滤掉上市日期缺失的记录
        basic = basic_info[basic_info['list_date'].notna()].copy()
        
        # 合并名称和行业信息到主数据
        df = df.merge(basic[['ts_code', 'name', 'industry_name']], on='ts_code', how='left')
        
        # 6. 应用策略标记
        df = apply_strategy_marks(df)
        
        # 7. 计算趋势指标
        df = calculate_trend_indicators(df)
        
        # 8. 计算知行砖形图指标
        df = calculate_zhixing_brick_indicator(df)
        
        # 9. 计算成交额排名
        df = calculate_amount_rank(df)
        
        # 步骤9：应用最终筛选条件
        result = apply_final_filter(df, end_date, basic)
        
        # 步骤10：打印筛选结果和阶段统计
        print_results(result, df, end_date, df_chart)
        print_stage_statistics(df, result, args)
        
        # 步骤11：回测（需指定 --backtest 参数）
        if args.backtest and not result.empty:
            buy_date = get_next_trade_date(end_date, data_manager)
            if buy_date:
                backtest_results = backtest_selected_stocks(
                    result['ts_code'].tolist(),
                    buy_date,
                    data_manager,
                    hold_days=args.hold_days,
                    detailed=args.detailed
                )
                print_backtest_stats(backtest_results)
        
        # 步骤12：每日统计和可视化
        daily_stats = calculate_daily_stats(df, basic_info)
        print_daily_stats(daily_stats)
        generate_industry_visualization(df, daily_stats, end_date)
        generate_j13_trend(df, end_date)
        
        # 步骤13：情绪反弹策略
        run_sentiment_rebound_strategy(df, end_date, data_manager)
        
        # 步骤14：DTW模式匹配（已注释，需 dtw_similarity 模块）
        print('\n========== 完美图形模式匹配分析 ==========')
        # patterns = load_perfect_patterns('data')
        # if patterns:
        #     dtw_results = run_dtw_pattern_matching(df, patterns, top_n=5)
        #     if not dtw_results.empty:
        #         print(f"找到 {len(dtw_results)} 个DTW匹配结果")
        #         dtw_results.to_csv(f'dtw_pattern_match_{end_date}.csv', index=False, encoding='utf-8-sig')
        
        # 步骤15：调试模式（需指定 --debug 参数）
        if args.debug:
            for ts_code in [c.strip() for c in args.debug.split(',')]:
                debug_stock_strategy_detailed(df, ts_code, end_date, basic)
    
    finally:
        data_manager.close()


if __name__ == "__main__":
    main()
