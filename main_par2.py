#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ========== 标准库导入 ==========
import argparse
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
from sklearn.preprocessing import MinMaxScaler
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
    # 1. 基础价格（未复权）
    'ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'pre_close', 'change', 'pct_chg', 'vol', 'amount',
    # 2. 换手 & 量比
    'turnover_rate', 'turnover_rate_f', 'volume_ratio',
    # 3. 前复权价格
    'open_qfq', 'high_qfq', 'low_qfq', 'close_qfq',
    # 4. 后复权价格
    'open_hfq', 'high_hfq', 'low_hfq', 'close_hfq',
    # 5. 估值 & 股本 & 复权因子
    'pe', 'pe_ttm', 'pb', 'ps', 'ps_ttm', 'dv_ratio', 'dv_ttm',
    'total_share', 'float_share', 'free_share', 'total_mv', 'circ_mv', 'adj_factor',
    # 6. 技术指标（未复权）
    'asi_bfq', 'asit_bfq', 'atr_bfq', 'bbi_bfq', 'bias1_bfq', 'bias2_bfq', 'bias3_bfq',
    'boll_lower_bfq', 'boll_mid_bfq', 'boll_upper_bfq', 'brar_ar_bfq', 'brar_br_bfq', 'cci_bfq', 'cr_bfq',
    'dfma_dif_bfq', 'dfma_difma_bfq', 'dmi_adx_bfq', 'dmi_adxr_bfq', 'dmi_mdi_bfq', 'dmi_pdi_bfq',
    'dpo_bfq', 'madpo_bfq', 'ema_bfq_5', 'ema_bfq_10', 'ema_bfq_20', 'ema_bfq_30', 'ema_bfq_60', 'ema_bfq_90', 'ema_bfq_250',
    'emv_bfq', 'maemv_bfq', 'expma_12_bfq', 'expma_50_bfq',
    'kdj_bfq', 'kdj_d_bfq', 'kdj_k_bfq', 'ktn_down_bfq', 'ktn_mid_bfq', 'ktn_upper_bfq',
    'mass_bfq', 'ma_mass_bfq', 'mfi_bfq', 'mtm_bfq', 'mtmma_bfq', 'obv_bfq', 'psy_bfq', 'psyma_bfq',
    'roc_bfq', 'maroc_bfq', 'rsi_bfq_6', 'rsi_bfq_12', 'rsi_bfq_24',
    'taq_down_bfq', 'taq_mid_bfq', 'taq_up_bfq', 'trix_bfq', 'trma_bfq', 'vr_bfq', 'wr_bfq', 'wr1_bfq',
    'xsii_td1_bfq', 'xsii_td2_bfq', 'xsii_td3_bfq', 'xsii_td4_bfq',
    # 7. 技术指标（前复权）
    'asi_qfq', 'asit_qfq', 'atr_qfq', 'bbi_qfq', 'bias1_qfq', 'bias2_qfq', 'bias3_qfq',
    'boll_lower_qfq', 'boll_mid_qfq', 'boll_upper_qfq', 'brar_ar_qfq', 'brar_br_qfq', 'cci_qfq', 'cr_qfq',
    'dfma_dif_qfq', 'dfma_difma_qfq', 'dmi_adx_qfq', 'dmi_adxr_qfq', 'dmi_mdi_qfq', 'dmi_pdi_qfq',
    'dpo_qfq', 'madpo_qfq', 'ema_qfq_5', 'ema_qfq_10', 'ema_qfq_20', 'ema_qfq_30', 'ema_qfq_60', 'ema_qfq_90', 'ema_qfq_250',
    'emv_qfq', 'maemv_qfq', 'expma_12_qfq', 'expma_50_qfq',
    'kdj_qfq', 'kdj_d_qfq', 'kdj_k_qfq', 'ktn_down_qfq', 'ktn_mid_qfq', 'ktn_upper_qfq',
    'mass_qfq', 'ma_mass_qfq', 'mfi_qfq', 'mtm_qfq', 'mtmma_qfq', 'obv_qfq', 'psy_qfq', 'psyma_qfq',
    'roc_qfq', 'maroc_qfq', 'rsi_qfq_6', 'rsi_qfq_12', 'rsi_qfq_24',
    'taq_down_qfq', 'taq_mid_qfq', 'taq_up_qfq', 'trix_qfq', 'trma_qfq', 'vr_qfq', 'wr_qfq', 'wr1_qfq',
    'xsii_td1_qfq', 'xsii_td2_qfq', 'xsii_td3_qfq', 'xsii_td4_qfq',
    # 8. 技术指标（后复权）
    'asi_hfq', 'asit_hfq', 'atr_hfq', 'bbi_hfq', 'bias1_hfq', 'bias2_hfq', 'bias3_hfq',
    'boll_lower_hfq', 'boll_mid_hfq', 'boll_upper_hfq', 'brar_ar_hfq', 'brar_br_hfq', 'cci_hfq', 'cr_hfq',
    'dfma_dif_hfq', 'dfma_difma_hfq', 'dmi_adx_hfq', 'dmi_adxr_hfq', 'dmi_mdi_hfq', 'dmi_pdi_hfq',
    'dpo_hfq', 'madpo_hfq', 'ema_hfq_5', 'ema_hfq_10', 'ema_hfq_20', 'ema_hfq_30', 'ema_hfq_60', 'ema_hfq_90', 'ema_hfq_250',
    'emv_hfq', 'maemv_hfq', 'expma_12_hfq', 'expma_50_hfq',
    'kdj_hfq', 'kdj_d_hfq', 'kdj_k_hfq', 'ktn_down_hfq', 'ktn_mid_hfq', 'ktn_upper_hfq',
    'mass_hfq', 'ma_mass_hfq', 'mfi_hfq', 'mtm_hfq', 'mtmma_hfq', 'obv_hfq', 'psy_hfq', 'psyma_hfq',
    'roc_hfq', 'maroc_hfq', 'rsi_hfq_6', 'rsi_hfq_12', 'rsi_hfq_24',
    'taq_down_hfq', 'taq_mid_hfq', 'taq_up_hfq', 'trix_hfq', 'trma_hfq', 'vr_hfq', 'wr_hfq', 'wr1_hfq',
    'xsii_td1_hfq', 'xsii_td2_hfq', 'xsii_td3_hfq', 'xsii_td4_hfq',
    # 9. 简单均线（未复权）
    'ma_bfq_5', 'ma_bfq_10', 'ma_bfq_20', 'ma_bfq_30', 'ma_bfq_60', 'ma_bfq_90', 'ma_bfq_250',
    # 10. 简单均线（前复权）
    'ma_qfq_5', 'ma_qfq_10', 'ma_qfq_20', 'ma_qfq_30', 'ma_qfq_60', 'ma_qfq_90', 'ma_qfq_250',
    # 11. 简单均线（后复权）
    'ma_hfq_5', 'ma_hfq_10', 'ma_hfq_20', 'ma_hfq_30', 'ma_hfq_60', 'ma_hfq_90', 'ma_hfq_250',
    # 12. 状态类
    'updays', 'downdays', 'lowdays', 'topdays',
    # 13. MACD 细分
    'macd_dif_bfq', 'macd_dea_bfq', 'macd_bfq',
    'macd_dif_qfq', 'macd_dea_qfq', 'macd_qfq',
    'macd_dif_hfq', 'macd_dea_hfq', 'macd_hfq'
]


# ========== 工具函数 ==========

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
    """使用线程池的并行处理"""
    results = []
    
    # 使用全局线程池
    global global_thread_pool
    future_to_key = {global_thread_pool.submit(func, group): name for name, group in grouped_data}
    
    for future in tqdm(concurrent.futures.as_completed(future_to_key), 
                      total=len(future_to_key), desc=desc):
        ts_code = future_to_key[future]
        try:
            result = future.result()
            results.append(result)
        except Exception as e:
            print(f'{ts_code} 处理失败: {e}')
            group = grouped_data.get_group(ts_code)
            results.append(pd.Series(False, index=group.index))
    
    if results:
        return pd.concat(results).sort_index()
    return pd.Series([], dtype=bool)


# 添加缺少的导入
import concurrent.futures


# ========== 分析函数 ==========

def is_ignorable_gap(gap_row, after_gap, debug: bool = False) -> bool:
    """判断跳空缺口是否可忽略"""
    gap_size = gap_row['low_qfq'] - gap_row['prev_high']
    gap_percent = gap_size / max(gap_row['prev_high'], 0.01) * 100
    
    if debug:
        print(f"跳空幅度: {gap_size:.4f} ({gap_percent:.2f}%)")
    
    if gap_percent < 0.5:
        max_vol = after_gap['amount'].max()
        vol_rate = max_vol / max(gap_row['amount'], 1)
        max_prc = after_gap['close_qfq'].max()
        prc_rate = max_prc / max(gap_row['close_qfq'], 0.01)
        return (vol_rate > 1.5) and (prc_rate > 1.15)
    
    below_ma60 = gap_row['close_qfq'] < gap_row['ma_qfq_60']
    max_volume_after = after_gap['amount'].max()
    volume_ratio = max_volume_after / max(gap_row['amount'], 1)
    max_price_after = after_gap['close_qfq'].max()
    price_ratio = max_price_after / max(gap_row['close_qfq'], 0.01)
    
    conditions = {
        '跳空幅度<2.5%': gap_percent < 2.5,
        '位于60日线下': below_ma60,
        '量能放大>3x': volume_ratio > 3,
        '价格上涨>15%': price_ratio > 1.15
    }
    
    return all(conditions.values())


def identify_candle_pattern(df, body_ratio_threshold: float = 0.2, min_shadow_ratio: float = 0.4) -> Tuple[pd.Series, pd.Series]:
    """识别三类可接受K线形态"""
    open_ = df['open_qfq']
    close = df['close_qfq']
    high = df['high_qfq']
    low = df['low_qfq']
    body = (close - open_).abs()
    range_ = high - low
    range_ = np.where(range_ == 0, 1e-6, range_)
    body_ratio = body / range_
    
    in_range = df['pct_chg'].between(-2, 2)
    is_yang = (close > open_) & in_range
    is_doji = (body_ratio < body_ratio_threshold) & in_range
    
    lower_shadow = np.minimum(open_, close) - low
    lower_shadow_ratio = lower_shadow / range_
    is_yin_shadow = (close < open_) & in_range & (lower_shadow_ratio >= min_shadow_ratio)
    
    condition_list = [is_yang, (~is_yang) & is_doji, (~is_yang) & (~is_doji) & is_yin_shadow]
    choice_label = ['yang', 'doji', 'yin_with_shadow']
    choice_rank = [1, 2, 3]
    
    candle_label = np.select(condition_list, choice_label, default='other')
    candle_rank = np.select(condition_list, choice_rank, default=4)
    
    return pd.Series(candle_label, index=df.index), pd.Series(candle_rank, index=df.index)


def mark_step_vol_price(group, debug: bool = False) -> pd.Series:
    """标记阶梯放量+价升条件"""
    out = pd.Series(False, index=group.index)
    
    basic_mask = (~group['name'].str.contains('ST|st', na=False)) & (~group['ts_code'].str.endswith('.BJ'))
    group = group.loc[basic_mask]
    
    if group.empty:
        return out
    
    cross_rows = group[group['cross'] & ~group['gap_up']]
    if cross_rows.empty:
        return out
    
    for _, cross_row in cross_rows.iterrows():
        cross_idx = cross_row.name
        after = group.loc[group.index >= cross_idx].copy()
        
        # 跳空回补检查
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
        
        # 连续价随量升检查
        consecutive_rise = 0
        max_consecutive = 0
        
        for i in range(1, len(after)):
            if (after['amount'].iloc[i] > after['amount'].iloc[i-1]) and \
               (after['close_qfq'].iloc[i] > after['close_qfq'].iloc[i-1]):
                consecutive_rise += 1
                max_consecutive = max(max_consecutive, consecutive_rise)
            else:
                consecutive_rise = 0
        
        if max_consecutive < 1:
            continue
        
        # 下跌日量能检查
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
        
        # 标记J值<13的日期
        j_below_13_mask = after['kdj_qfq'] < 13
        if j_below_13_mask.any():
            for idx in after[j_below_13_mask].index:
                out.loc[idx] = True
    
    return out


def mark_volume_surge(group, debug: bool = False) -> pd.Series:
    """标记是否存在成交额≥前5天均值4倍的交易日"""
    out = pd.Series(False, index=group.index)
    
    if len(group) < 6:
        return out
    
    group = group.sort_values('trade_date').reset_index(drop=True)
    
    for i in range(5, len(group)):
        prev_5_avg = group.loc[i-5:i-1, 'amount'].mean()
        curr_amount = group.loc[i, 'amount']
        is_surge = curr_amount >= (prev_5_avg * 3)
        out.iloc[i] = is_surge
    
    return out


def mark_abnormal_movement(group, debug: bool = False, max_life: int = 60) -> pd.Series:
    """标记异动 - 基于上穿60日线判断收集区"""
    out = pd.Series(False, index=group.index)
    
    if len(group) < 20:
        return out
    
    group = group.sort_values('trade_date')
    group['trade_date'] = pd.to_datetime(group['trade_date'], format='%Y%m%d')
    group['amount_ma5'] = group['amount'].rolling(5).mean()
    
    ts_code = group['ts_code'].iloc[0]
    pct_threshold = 3.8 if ts_code.startswith(('00', '60')) else 7.0
    
    # 类型1：收集区异动
    up_crosses = group[
        (group['close_qfq'] >= group['ma_qfq_60']) &
        (group['close_qfq'].shift(1) < group['ma_qfq_60'].shift(1))
    ]
    
    if not up_crosses.empty:
        latest_up = up_crosses.index.max()
        up_date = group.loc[latest_up, 'trade_date']
        
        after_up = group.loc[latest_up:]
        pullback_mask = after_up[after_up['close_qfq'] < after_up['ma_qfq_60']].index
        
        if pullback_mask.empty:
            life_end = up_date + pd.Timedelta(days=max_life)
        else:
            life_end = group.loc[pullback_mask[0], 'trade_date']
        
        collect_mask = (
            (group['trade_date'] >= up_date) &
            (group['trade_date'] <= life_end) &
            (group['close_qfq'] <= group['ma_qfq_60'] * 1.15)
        )
        
        cond1 = (
            collect_mask &
            (group['amount'] > group['amount_ma5'] * 2.4) &
            (group['pct_chg'] >= pct_threshold)
        )
        out |= cond1
    
    # 类型2：堆量建仓 ≥ 2次
    if not up_crosses.empty:
        latest2_up = up_crosses.index.max()
        up2_date = group.loc[latest2_up, 'trade_date']
        life2_end = life_end if 'life_end' in locals() else up2_date + pd.Timedelta(days=max_life)
        
        sub2_df = group[
            (group['trade_date'] >= up2_date) &
            (group['trade_date'] <= life2_end) &
            (group['close_qfq'] <= group['ma_qfq_60'] * 1.15)
        ].copy()
        
        vol_mul_threshold = 1.9
        pct_threshold2 = 2.5 if ts_code.startswith(('00', '60')) else 5.0
        
        day_hit2 = (
            (sub2_df['amount'] > sub2_df['amount_ma5'] * vol_mul_threshold) &
            (sub2_df['pct_chg'] >= pct_threshold2)
        )
        
        if day_hit2.sum() >= 2:
            out.loc[sub2_df.index] = True
    
    # 类型3：突破放量
    break_through = (
        (group['close_qfq'] >= group['ma_qfq_60']) &
        (group['close_qfq'].shift(1) < group['ma_qfq_60'].shift(1)) &
        (group['amount'] > group['amount_ma5'] * 2.0) &
        (group['pct_chg'] >= 3)
    )
    out |= break_through
    
    return out


def mark_bottom_violent_k(group, volume_multiplier: float = 2.0, debug: bool = False) -> pd.Series:
    """
    标记底部暴力K线
    
    条件：
    1. 相对底部：收盘价接近60日线
       - 主板（60/00开头）：±10%
       - 创业板/科创板（30/68开头）：±20%
    2. 放量：成交额 >= 前一日 × 2.0
    3. 长阳：实体涨幅根据股票类型不同
       - 主板（10%涨停）：>= 3%
       - 创业板/科创板（20%涨停）：>= 6%
    """
    out = pd.Series(False, index=group.index)
    
    if len(group) < 10:  # 至少10天数据
        return out
    
    group = group.sort_values('trade_date')
    ts_code = group['ts_code'].iloc[0]
    
    # 判断股票类型确定阈值
    if ts_code.startswith(('30', '68')):  # 创业板/科创板 20%涨停
        min_body_pct = 0.06
        ma60_tolerance = 0.20  # ±20%
    else:  # 主板 10%涨停
        min_body_pct = 0.03
        ma60_tolerance = 0.10  # ±10%
    
    # 计算前一日成交额
    group['amount_prev'] = group['amount'].shift(1)
    
    # 条件1: 放量（成交额>=前一日2倍）
    volume_surge = group['amount'] >= (group['amount_prev'] * volume_multiplier)
    
    # 条件2: 长阳（实体涨幅 = (收盘价-开盘价)/开盘价）
    body_pct = (group['close_qfq'] - group['open_qfq']) / group['open_qfq']
    is_long_yang = body_pct >= min_body_pct
    
    # 条件3: 接近60日线（根据板块不同阈值不同）
    near_ma60 = abs(group['close_qfq'] / group['ma_qfq_60'] - 1) <= ma60_tolerance
    
    # 综合条件（已移除价格位置条件）
    out = volume_surge & is_long_yang & near_ma60
    
    if debug and out.any():
        violent_days = group[out]
        board_type = "20%" if ts_code.startswith(('30', '68')) else "10%"
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
    """
    标记主力出货信号 - 周期最高点放天量大阴线
    
    条件：
    1. 当日最高价 = 回测周期内最高价
    2. 天量：当日成交额 >= 前一日 × 2倍
    3. 大阴线：
       - 开盘价 > 收盘价
       - 实体跌幅 = (开盘价 - 收盘价) / 开盘价
       - 主板 >= 3%，创业板/科创板 >= 6%
    """
    out = pd.Series(False, index=group.index)
    
    if len(group) < 2:
        return out
    
    group = group.sort_values('trade_date')
    ts_code = group['ts_code'].iloc[0]
    
    # 判断股票类型确定阴线阈值
    if ts_code.startswith(('30', '68')):  # 创业板/科创板 20%涨停
        min_yin_pct = 0.06  # 6%
    else:  # 主板 10%涨停
        min_yin_pct = 0.03  # 3%
    
    # 条件1: 当日最高价为周期内最高价
    period_high = group['high_qfq'].max()
    is_period_high = group['high_qfq'] == period_high
    
    # 条件2: 天量 - 当日成交额 >= 前一日 × 2倍
    group['amount_prev'] = group['amount'].shift(1)
    is_volume_surge = group['amount'] >= (group['amount_prev'] * 2)
    
    # 条件3: 大阴线
    # 实体跌幅 = (开盘价 - 收盘价) / 开盘价
    yin_pct = (group['open_qfq'] - group['close_qfq']) / group['open_qfq']
    is_big_yin = (group['open_qfq'] > group['close_qfq']) & (yin_pct >= min_yin_pct)
    
    # 综合条件
    out = is_period_high & is_volume_surge & is_big_yin
    
    if debug and out.any():
        signal_days = group[out]
        board_type = "20%" if ts_code.startswith(('30', '68')) else "10%"
        print(f"\n[主力出货信号] {ts_code} ({board_type}板) 发现 {len(signal_days)} 个信号:")
        for idx, row in signal_days.iterrows():
            yin = (row['open_qfq'] - row['close_qfq']) / row['open_qfq'] * 100
            vol_ratio = row['amount'] / row['amount_prev'] if row['amount_prev'] > 0 else 0
            print(f"  📅 {row['trade_date']}: 阴线{yin:.2f}%, 放量{vol_ratio:.1f}倍, 最高价{row['high_qfq']:.2f}")
    
    return out


def mark_distribution_signal_v2(group, debug: bool = False) -> pd.Series:
    """
    标记主力出货信号V2 - 周期最高点后放量下跌
    
    条件：
    1. 当日最高价 = 回测周期内最高价
    2. 后两天成交额均值 > 最高价当天成交额
    3. 后两天累计跌幅 >= 阈值
       - 主板 >= 8%
       - 创业板/科创板 >= 12%
    
    注意：需要至少2天的后续数据才能判断
    """
    out = pd.Series(False, index=group.index)
    
    if len(group) < 3:  # 至少需要3天数据
        return out
    
    # 保存原始索引
    original_index = group.index
    group = group.sort_values('trade_date').reset_index(drop=True)
    ts_code = group['ts_code'].iloc[0]
    
    # 判断股票类型确定跌幅阈值
    if ts_code.startswith(('30', '68')):  # 创业板/科创板
        drop_threshold = 0.12  # 12%
    else:  # 主板
        drop_threshold = 0.08  # 8%
    
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
    """
    标记主力出货信号V3 - 周期最高点后出现放量长阴（出现2次及以上）
    
    条件：
    1. 当日最高价 = 回测周期内最高价
    2. 最高点出现后，后续出现2次及以上放量长阴：
       - 成交额 > 前一天成交额
       - 长阴线：开盘价 > 收盘价
       - 实体跌幅：主板 >= 3%，创业板/科创板 >= 6%
    
    最高点后出现2次及以上符合条件的放量长阴，才标记该最高点日为出货信号
    """
    out = pd.Series(False, index=group.index)
    
    if len(group) < 2:
        return out
    
    original_index = group.index
    group = group.sort_values('trade_date').reset_index(drop=True)
    ts_code = group['ts_code'].iloc[0]
    
    if ts_code.startswith(('30', '68')):
        min_yin_pct = 0.06
    else:
        min_yin_pct = 0.03
    
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
        
        if violent_yin_count >= 2:
            original_idx = original_index[idx]
            out.loc[original_idx] = True
            
            if debug:
                board_type = "20%" if ts_code.startswith(('30', '68')) else "10%"
                print(f"\n[主力出货信号V3] {ts_code} ({board_type}板)")
                print(f"  最高点日期: {group.loc[idx, 'trade_date']}")
                print(f"  放量长阴次数: {violent_yin_count}")
                for i, info in enumerate(violent_days_info[:3], 1):
                    print(f"  第{i}次 - 日期: {info['date']}, 阴线: {info['yin_pct']*100:.2f}%, 放量: {info['vol_ratio']:.2f}倍")
    
    return out


# ========== 回测函数 ==========

def get_next_trade_date(current_date: str, data_manager) -> Optional[str]:
    """获取下一个交易日"""
    current_dt = pd.to_datetime(current_date, format='%Y%m%d')
    
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
    """回测选中的股票"""
    if not selected_stocks or not buy_date:
        logging.warning("回测参数无效，跳过回测")
        return pd.DataFrame()
    
    print(f"\n{'='*70}")
    print(f"📊 回测设置：买入日期={buy_date} | 持有天数={hold_days}天")
    print(f"待回测股票数量：{len(selected_stocks)}只")
    print(f"{'='*70}")
    
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
        
        buy_row = stock_data[stock_data['trade_date'] == buy_date]
        if buy_row.empty:
            continue
        
        buy_price = buy_row['open_qfq'].iloc[0]
        if pd.isna(buy_price) or buy_price <= 0:
            continue
        
        hold_data = stock_data.head(hold_days + 1).copy()
        if len(hold_data) < 2:
            continue
        
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
    """打印回测统计结果"""
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
    """准备交易日期范围"""
    if args.date:
        end_date = args.date
        today = datetime.strptime(end_date, "%Y%m%d")
    else:
        end_date = get_nearest_trade_date(data_manager)
        today = datetime.strptime(end_date, "%Y%m%d")
    
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
    """获取并准备股票数据"""
    df = data_manager.get_stock_factors(trade_dates, STOCK_FACTOR_FIELDS)
    
    if df.empty:
        logging.error("未获取到数据")
        return df
    
    df = df.sort_values(['ts_code', 'trade_date'])
    
    # 计算辅助字段
    df['prev_close'] = df.groupby('ts_code')['close_qfq'].shift(1)
    df['prev_ma60'] = df.groupby('ts_code')['ma_qfq_60'].shift(1)
    df['prev_high'] = df.groupby('ts_code')['high_qfq'].shift(1)
    
    df['cross'] = (df['close_qfq'] >= df['ma_qfq_60']) & (df['prev_close'] < df['prev_ma60'])
    df['amount_yest'] = df.groupby('ts_code')['amount'].shift(1)
    df['amount_2days_ago'] = df.groupby('ts_code')['amount'].shift(2)
    df['shrink'] = (df['amount'] < df['amount_yest']) | (df['amount'] < df['amount_2days_ago'])
    df['gap_up'] = df['low_qfq'] > df['prev_high']
    
    # K线形态
    df['candle_pattern'], df['candle_rank'] = identify_candle_pattern(df)
    df['is_acceptable_candle'] = df['candle_pattern'] != 'other'
    
    # 振幅
    df['amplitude'] = (df['high_qfq'] - df['low_qfq']) / df['prev_close'] * 100
    df['is_amplitude_ok'] = (
        (df['ts_code'].str.startswith(('60', '00')) & df['amplitude'].lt(4)) |
        (~df['ts_code'].str.startswith(('60', '00')) & df['amplitude'].lt(7))
    )
    
    df['zhixing_mid_duokong'] = df.groupby('ts_code')['ema_qfq_10'].transform(
        lambda x: x.ewm(span=10, adjust=False).mean()
    )
    
    df['ema_qfq_13'] = df.groupby('ts_code')['close_qfq'].transform(
        lambda x: x.ewm(span=13, adjust=False).mean()
    )
    
    for period in [14, 28, 57, 114]:
        df[f'ma_qfq_{period}'] = df.groupby('ts_code')['close_qfq'].transform(
            lambda x: x.rolling(window=period, min_periods=period).mean()
        )
    
    df['zhixing_duokong'] = (
        df['ma_qfq_14'] + df['ma_qfq_28'] + df['ma_qfq_57'] + df['ma_qfq_114']
    ) / 4
    
    return df


def apply_strategy_marks(df):
    """应用策略标记"""
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
    """计算趋势指标"""
    df['ma60_3d_trend'] = df.groupby('ts_code')['ma_qfq_60'].transform(lambda x: (x - x.shift(3)) / 3)
    df['ma60_8d_trend'] = df.groupby('ts_code')['ma_qfq_60'].transform(lambda x: (x - x.shift(8)) / 8)
    df['ma60_13d_trend'] = df.groupby('ts_code')['ma_qfq_60'].transform(lambda x: (x - x.shift(13)) / 13)
    
    df['ma60_upward'] = (
        (df['ma60_3d_trend'] > 0).astype(int) +
        (df['ma60_8d_trend'] > 0).astype(int) +
        (df['ma60_13d_trend'] > 0).astype(int)
    ) >= 2
    
    return df


def calculate_amount_rank(df):
    """计算每日成交额排名（前40%）"""
    print("\n正在计算每日成交额排名...")
    
    # 计算每日成交额的分位数（40%阈值）
    daily_amount_threshold = df.groupby('trade_date')['amount'].quantile(0.4)
    
    # 将阈值合并回主表
    df = df.merge(
        daily_amount_threshold.rename('amount_threshold_40pct'),
        left_on='trade_date',
        right_index=True,
        how='left'
    )
    
    # 添加布尔标记：是否在前40%
    df['is_amount_top30'] = df['amount'] >= df['amount_threshold_40pct']
    
    print(f"成交额前40%标记完成，共 {df['is_amount_top30'].sum()} 条记录满足")
    
    return df


def apply_final_filter(df, end_date, basic):
    """应用最终筛选条件"""
    # 剔除次新股
    cutoff_date = pd.to_datetime(end_date) - pd.Timedelta(days=180)
    basic['list_date'] = pd.to_datetime(basic['list_date'])
    non_new_stocks = basic[basic['list_date'] <= cutoff_date]['ts_code']
    df_filtered = df[df['ts_code'].isin(non_new_stocks)].copy()
    
    # 最终筛选
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
    """生成行业可视化图表"""
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
    
    fig.update_layout(hovermode='x unified', title_x=0.5, height=650, width=1100)
    fig.update_xaxes(tickformat='%m-%d', tickangle=-45)
    fig.update_yaxes(rangemode='tozero')
    
    fig.write_html("industry_total_amount_trend.html", auto_open=False)
    print(f"\n📈 已生成行业总成交额趋势图: industry_total_amount_trend.html")


def generate_j13_trend(df):
    """生成 first_j13_step 每日趋势图"""
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
    
    fig.update_layout(title_x=0.5, height=500, width=1100, hovermode='x unified')
    fig.update_xaxes(tickformat='%m-%d', tickangle=-45)
    fig.update_yaxes(rangemode='tozero')
    
    fig.write_html("first_j13_step_daily_count.html", auto_open=False)
    print(f"📈 已生成趋势图: first_j13_step_daily_count.html")


# ========== 结果输出函数 ==========

def print_results(result, df, end_date):
    """打印筛选结果"""
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


def print_stage_statistics(df, result, args):
    """打印各阶段统计"""
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
    """计算每日行业统计"""
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
    """打印每日统计"""
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
    """详细调试单只股票的策略条件（与主策略完全一致）"""
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
    is_main_board = ts_code.startswith(('60', '00'))
    threshold = 4 if is_main_board else 7
    
    print(f"  股票类型: {'主板' if is_main_board else '其他'} ({'60/00' if is_main_board else '其他'}开头)")
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
    
    # 根据板块确定阈值
    is_cy_kc = ts_code.startswith(('30', '68'))
    board_type = "20%" if is_cy_kc else "10%"
    min_body_pct = 0.06 if is_cy_kc else 0.03
    ma60_tol_pct = 12 if is_cy_kc else 6
    
    print(f"  股票板块: {'创业板/科创板' if is_cy_kc else '主板'} ({board_type}涨停)")
    print(f"  长阳阈值: 实体涨幅 >= {min_body_pct*100:.0f}%")
    print(f"  放量阈值: 成交额 >= 前日 × 2.0")
    print(f"  60日线范围: 收盘价在60日线 ±{ma60_tol_pct}% 范围内")
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
            is_volume_surge = vol_ratio >= 2.0
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
    """主函数"""
    args = parse_args()
    data_manager = DataManager()
    
    try:
        start_date, end_date, actual_days, trade_dates_range = prepare_trade_dates(args, data_manager)
        
        df = fetch_and_prepare_data(data_manager, trade_dates_range)
        if df.empty:
            logging.error("未获取到数据，退出程序")
            return
        
        df = df[df['trade_date'] >= start_date].copy()
        logging.info("数据筛选后: %d 条记录 (回测区间 %s ~ %s)", len(df), start_date, end_date)
        
        basic_info = data_manager.get_stock_basic_info()
        
        if 'name' not in basic_info.columns:
            basic_info['name'] = basic_info['ts_code']
        if 'industry_name' not in basic_info.columns:
            basic_info['industry_name'] = '未知行业'
        
        basic_info['industry_name'] = basic_info['industry_name'].fillna('未知行业')
        basic_info['name'] = basic_info['name'].fillna(basic_info['ts_code'])
        
        basic = basic_info[basic_info['list_date'].notna()].copy()
        
        df = df.merge(basic[['ts_code', 'name', 'industry_name']], on='ts_code', how='left')
        
        # 6. 应用策略标记
        df = apply_strategy_marks(df)
        
        # 7. 计算趋势指标
        df = calculate_trend_indicators(df)
        
        # 8. 计算成交额排名
        df = calculate_amount_rank(df)
        
        # 9. 应用最终筛选
        result = apply_final_filter(df, end_date, basic)
        
        # 10. 打印结果
        print_results(result, df, end_date)
        print_stage_statistics(df, result, args)
        
        # 11. 回测
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
        
        # 12. 每日统计和可视化
        daily_stats = calculate_daily_stats(df, basic_info)
        print_daily_stats(daily_stats)
        generate_industry_visualization(df, daily_stats, end_date)
        generate_j13_trend(df)
        
        # 13. DTW模式匹配
        print('\n========== 完美图形模式匹配分析 ==========')
        # patterns = load_perfect_patterns('data')
        # if patterns:
        #     dtw_results = run_dtw_pattern_matching(df, patterns, top_n=5)
        #     if not dtw_results.empty:
        #         print(f"找到 {len(dtw_results)} 个DTW匹配结果")
        #         dtw_results.to_csv(f'dtw_pattern_match_{end_date}.csv', index=False, encoding='utf-8-sig')
        
        # 14. 调试模式
        if args.debug:
            for ts_code in [c.strip() for c in args.debug.split(',')]:
                debug_stock_strategy_detailed(df, ts_code, end_date, basic)
    
    finally:
        data_manager.close()


if __name__ == "__main__":
    main()
