#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
知行砖形图指标测试脚本 - 使用真实股票数据
"""

import numpy as np
import pandas as pd
import logging
import tushare as ts
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format='%(message)s')


def calculate_zhixing_brick_indicator(df):
    """
    计算知行砖形图指标（短期砖型图指标V2026）
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
    
    # 判断砖型图上升/下降
    df['zhixing_brick_prev'] = grouped['zhixing_brick'].shift(1)
    df['zhixing_brick_rising'] = df['zhixing_brick_prev'] < df['zhixing_brick']
    df['zhixing_brick_falling'] = df['zhixing_brick_prev'] > df['zhixing_brick']
    
    # XG信号：前一期AA=0 且 当期AA=1
    df['zhixing_brick_prev_rising'] = grouped['zhixing_brick_rising'].shift(1)
    df['zhixing_brick_xg'] = (~df['zhixing_brick_prev_rising'].fillna(False).astype(bool)) & df['zhixing_brick_rising']
    
    return df


def get_real_stock_data(ts_code='000001.SZ', days=60):
    """
    从Tushare获取真实股票数据
    
    Args:
        ts_code: 股票代码，如 '000001.SZ'
        days: 获取多少天的数据
    
    Returns:
        DataFrame with columns: ts_code, trade_date, close_qfq, high_qfq, low_qfq
    """
    print(f"正在从Tushare获取 {ts_code} 的真实数据...")
    
    try:
        # 尝试从环境变量获取token
        import os
        token = os.environ.get('TUSHARE_TOKEN')
        
        if not token:
            print("警告: 未找到 TUSHARE_TOKEN 环境变量")
            print("尝试从 config.py 获取...")
            try:
                from config import APIConfig
                token = APIConfig.get_token()
            except Exception as e:
                print(f"从config获取token失败: {e}")
                return None
        
        ts.set_token(token)
        pro = ts.pro_api()
        
        # 计算日期范围
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days * 2)  # 多取一些天数确保有足够交易日
        
        end_date_str = end_date.strftime('%Y%m%d')
        start_date_str = start_date.strftime('%Y%m%d')
        
        print(f"日期范围: {start_date_str} 至 {end_date_str}")
        
        # 获取日线数据（前复权）
        df = pro.daily(ts_code=ts_code, start_date=start_date_str, end_date=end_date_str)
        
        if df is None or df.empty:
            print(f"未获取到 {ts_code} 的数据")
            return None
        
        # 按日期排序
        df = df.sort_values('trade_date').reset_index(drop=True)
        
        # 获取前复权因子
        adj_df = pro.adj_factor(ts_code=ts_code, start_date=start_date_str, end_date=end_date_str)
        if adj_df is not None and not adj_df.empty:
            adj_df = adj_df.sort_values('trade_date').reset_index(drop=True)
            df = df.merge(adj_df[['trade_date', 'adj_factor']], on='trade_date', how='left')
            
            # 计算前复权价格
            # 最新复权因子 / 当日复权因子 * 当日价格
            latest_adj = df['adj_factor'].iloc[-1]
            df['close_qfq'] = df['close'] * df['adj_factor'] / latest_adj
            df['high_qfq'] = df['high'] * df['adj_factor'] / latest_adj
            df['low_qfq'] = df['low'] * df['adj_factor'] / latest_adj
        else:
            # 如果没有复权因子，使用原始价格
            print("警告: 未获取到复权因子，使用原始价格")
            df['close_qfq'] = df['close']
            df['high_qfq'] = df['high']
            df['low_qfq'] = df['low']
        
        # 选择需要的列
        result = df[['ts_code', 'trade_date', 'close_qfq', 'high_qfq', 'low_qfq']].copy()
        
        print(f"成功获取 {len(result)} 条数据")
        return result
        
    except Exception as e:
        print(f"获取数据失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def test_with_real_data(ts_code='000001.SZ', days=60):
    """使用真实数据测试知行砖形图指标"""
    print("=" * 80)
    print(f"知行砖形图指标测试 - 真实数据 ({ts_code})")
    print("=" * 80)
    
    # 获取真实数据
    df = get_real_stock_data(ts_code, days)
    
    if df is None or df.empty:
        print("获取数据失败，无法进行测试")
        return None
    
    print(f"\n数据概览:")
    print(f"  股票代码: {ts_code}")
    print(f"  数据条数: {len(df)}")
    print(f"  日期范围: {df['trade_date'].iloc[0]} 至 {df['trade_date'].iloc[-1]}")
    print(f"  价格范围: {df['close_qfq'].min():.2f} ~ {df['close_qfq'].max():.2f}")
    
    # 显示最近10天的原始数据
    print("\n最近10天原始数据:")
    print(df.tail(10).to_string(index=False))
    
    # 计算指标
    df_result = calculate_zhixing_brick_indicator(df.copy())
    
    # 显示计算结果（最近20天）
    print("\n" + "=" * 80)
    print("计算结果（最近20天）")
    print("=" * 80)
    
    display_cols = ['trade_date', 'close_qfq', 'high_qfq', 'low_qfq', 
                    'zhixing_brick', 'zhixing_brick_rising', 'zhixing_brick_falling', 'zhixing_brick_xg']
    
    recent_data = df_result[display_cols].tail(20)
    print(recent_data.to_string(index=False))
    
    # 统计信息
    print("\n" + "=" * 80)
    print("统计信息")
    print("=" * 80)
    print(f"砖型图数值范围: {df_result['zhixing_brick'].min():.2f} ~ {df_result['zhixing_brick'].max():.2f}")
    print(f"砖型图平均值: {df_result['zhixing_brick'].mean():.2f}")
    print(f"上升(红柱)天数: {df_result['zhixing_brick_rising'].sum()}")
    print(f"下降(绿柱)天数: {df_result['zhixing_brick_falling'].sum()}")
    print(f"XG信号(绿转红): {df_result['zhixing_brick_xg'].sum()}")
    
    # 显示XG信号出现的位置
    xg_signals = df_result[df_result['zhixing_brick_xg'] == True]
    if not xg_signals.empty:
        print("\nXG信号出现日期:")
        for _, row in xg_signals.iterrows():
            print(f"  - {row['trade_date']}: 收盘价={row['close_qfq']:.2f}, 砖型图={row['zhixing_brick']:.2f}")
    else:
        print("\n最近期间未出现XG信号")
    
    # 保存详细结果到CSV
    output_file = f'zhixing_brick_{ts_code.replace(".", "_")}_{df_result["trade_date"].iloc[-1]}.csv'
    df_result.to_csv(output_file, index=False, encoding='utf-8-sig')
    print(f"\n详细结果已保存到: {output_file}")
    
    print("\n" + "=" * 80)
    print("测试完成!")
    print("=" * 80)
    
    return df_result


def compare_multiple_stocks(stock_list=['000001.SZ', '000002.SZ', '000858.SZ'], days=60):
    """对比多只股票的结果"""
    print("\n" + "=" * 80)
    print("多只股票对比测试")
    print("=" * 80)
    
    results = []
    for ts_code in stock_list:
        print(f"\n正在分析 {ts_code}...")
        df = get_real_stock_data(ts_code, days)
        if df is not None and not df.empty:
            df_result = calculate_zhixing_brick_indicator(df)
            latest = df_result.iloc[-1]
            results.append({
                'ts_code': ts_code,
                'trade_date': latest['trade_date'],
                'close': latest['close_qfq'],
                'zhixing_brick': latest['zhixing_brick'],
                'rising': latest['zhixing_brick_rising'],
                'falling': latest['zhixing_brick_falling'],
                'xg_count': df_result['zhixing_brick_xg'].sum()
            })
    
    if results:
        compare_df = pd.DataFrame(results)
        print("\n对比结果:")
        print(compare_df.to_string(index=False))


if __name__ == "__main__":
    import sys
    
    # 获取命令行参数
    stock_code = sys.argv[1] if len(sys.argv) > 1 else '000001.SZ'
    
    # 测试单只股票
    result = test_with_real_data(stock_code, days=60)
    
    # 对比多只股票（可选）
    # compare_multiple_stocks(['000001.SZ', '000002.SZ', '000858.SZ', '600519.SH'])
