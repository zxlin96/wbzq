#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
563300基金日线数据获取测试
接口：fund_daily
"""

import pandas as pd
import tushare as ts
import logging
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format='%(message)s')


def get_fund_daily(ts_code='563300.SH', start_date=None, end_date=None):
    """
    获取ETF基金日线行情数据
    
    参数:
        ts_code: 基金代码，如 '563300.SH'
        start_date: 开始日期 (YYYYMMDD格式)
        end_date: 结束日期 (YYYYMMDD格式)
    
    返回:
        DataFrame with columns: ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount
    """
    print(f"正在从Tushare获取 {ts_code} 的ETF日线数据...")
    
    try:
        # 尝试从环境变量获取token
        import os
        token = os.environ.get('TUSHARE_TOKEN')
        
        if not token:
            print("尝试从 config.py 获取token...")
            try:
                from config import APIConfig
                token = APIConfig.get_token()
            except Exception as e:
                print(f"获取token失败: {e}")
                return None
        
        ts.set_token(token)
        pro = ts.pro_api()
        
        # 设置默认日期范围
        if end_date is None:
            end_date = datetime.now().strftime('%Y%m%d')
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=180)).strftime('%Y%m%d')
        
        print(f"日期范围: {start_date} 至 {end_date}")
        
        # 调用fund_daily接口获取ETF日线数据
        df = pro.fund_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        
        if df is None or df.empty:
            print(f"未获取到 {ts_code} 的数据")
            return None
        
        # 按日期排序
        df = df.sort_values('trade_date').reset_index(drop=True)
        
        print(f"成功获取 {len(df)} 条数据")
        return df
        
    except Exception as e:
        print(f"获取数据失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def test_fund_563300():
    """测试获取563300基金数据"""
    print("=" * 80)
    print("563300基金日线数据获取测试")
    print("=" * 80)
    
    # 获取最近6个月的数据
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=180)).strftime('%Y%m%d')
    
    df = get_fund_daily('563300.SH', start_date=start_date, end_date=end_date)
    
    if df is None or df.empty:
        print("获取数据失败")
        return None
    
    # 显示数据概览
    print("\n" + "=" * 80)
    print("数据概览")
    print("=" * 80)
    print(f"基金代码: 563300.SH")
    print(f"数据条数: {len(df)}")
    print(f"日期范围: {df['trade_date'].iloc[0]} 至 {df['trade_date'].iloc[-1]}")
    print(f"价格范围: {df['close'].min():.3f} ~ {df['close'].max():.3f}")
    print(f"成交量范围: {df['vol'].min():.0f} ~ {df['vol'].max():.0f}")
    print(f"成交额范围: {df['amount'].min():.0f} ~ {df['amount'].max():.0f} 千元")
    
    # 显示最近20天数据
    print("\n" + "=" * 80)
    print("最近20天数据")
    print("=" * 80)
    display_cols = ['trade_date', 'open', 'high', 'low', 'close', 'change', 'pct_chg', 'vol', 'amount']
    print(df[display_cols].tail(20).to_string(index=False))
    
    # 计算一些统计指标
    print("\n" + "=" * 80)
    print("统计指标")
    print("=" * 80)
    print(f"平均收盘价: {df['close'].mean():.3f}")
    print(f"收盘价标准差: {df['close'].std():.3f}")
    print(f"最大单日涨幅: {df['pct_chg'].max():.2f}%")
    print(f"最大单日跌幅: {df['pct_chg'].min():.2f}%")
    print(f"平均成交量: {df['vol'].mean():.0f}")
    print(f"平均成交额: {df['amount'].mean():.0f} 千元")
    
    # 保存到CSV
    output_file = f'fund_563300_{df["trade_date"].iloc[-1]}.csv'
    df.to_csv(output_file, index=False, encoding='utf-8-sig')
    print(f"\n数据已保存到: {output_file}")
    
    print("\n" + "=" * 80)
    print("测试完成!")
    print("=" * 80)
    
    return df


def test_with_zhixing_brick():
    """测试563300基金并计算知行砖形图指标"""
    print("\n" + "=" * 80)
    print("563300基金 + 知行砖形图指标测试")
    print("=" * 80)
    
    # 获取基金数据（最近60个交易日）
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=120)).strftime('%Y%m%d')
    df = get_fund_daily('563300.SH', start_date=start_date, end_date=end_date)
    
    if df is None or df.empty:
        return None
    
    # 基金数据没有复权，直接使用原始价格
    df['close_qfq'] = df['close']
    df['high_qfq'] = df['high']
    df['low_qfq'] = df['low']
    df['ts_code'] = '563300.SH'
    
    # 导入知行砖形图指标计算函数
    from test_zhixing_brick_real import calculate_zhixing_brick_indicator
    
    # 计算指标
    df_result = calculate_zhixing_brick_indicator(df)
    
    # 显示结果
    print("\n" + "=" * 80)
    print("知行砖形图指标结果（最近20天）")
    print("=" * 80)
    display_cols = ['trade_date', 'close', 'zhixing_brick', 'zhixing_brick_rising', 'zhixing_brick_falling', 'zhixing_brick_xg']
    print(df_result[display_cols].tail(20).to_string(index=False))
    
    # 统计
    print("\n" + "=" * 80)
    print("统计信息")
    print("=" * 80)
    print(f"砖型图范围: {df_result['zhixing_brick'].min():.2f} ~ {df_result['zhixing_brick'].max():.2f}")
    print(f"上升天数: {df_result['zhixing_brick_rising'].sum()}")
    print(f"下降天数: {df_result['zhixing_brick_falling'].sum()}")
    print(f"XG信号: {df_result['zhixing_brick_xg'].sum()} 次")
    
    # 保存结果
    output_file = f'fund_563300_zhixing_brick_{df_result["trade_date"].iloc[-1]}.csv'
    df_result.to_csv(output_file, index=False, encoding='utf-8-sig')
    print(f"\n结果已保存到: {output_file}")
    
    return df_result


if __name__ == "__main__":
    import sys
    
    # 获取命令行参数
    test_type = sys.argv[1] if len(sys.argv) > 1 else 'basic'
    
    if test_type == 'brick':
        # 测试基金数据 + 知行砖形图指标
        test_with_zhixing_brick()
    else:
        # 默认测试基金数据获取
        test_fund_563300()
