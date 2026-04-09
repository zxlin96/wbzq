#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多日期倍投策略测试
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from sentiment_rebound_strategy import SentimentReboundStrategy


def create_multi_day_test_data():
    """创建多日期测试数据，模拟连续多天J13超过阈值的情况"""
    # 创建30天的数据
    dates = pd.date_range(end=datetime.now(), periods=30, freq='D')
    
    # 模拟J13数量：前20天正常，后10天连续超过90%分位数
    counts = []
    for i in range(30):
        if i < 20:
            # 正常市场：50-150只
            counts.append(np.random.randint(50, 150))
        else:
            # 恐慌市场：超过200只（高于90%分位数）
            counts.append(np.random.randint(200, 300))
    
    j13_df = pd.DataFrame({
        'trade_date': dates,
        'count': counts
    })
    
    # 创建ETF数据
    etf_df = pd.DataFrame({
        'trade_date': dates.strftime('%Y%m%d'),
        'close': np.linspace(1.4, 1.35, 30) + np.random.uniform(-0.02, 0.02, 30),
        'high': np.linspace(1.42, 1.37, 30) + np.random.uniform(-0.01, 0.03, 30),
        'low': np.linspace(1.38, 1.33, 30) + np.random.uniform(-0.03, 0.01, 30),
    })
    
    return j13_df, etf_df


def test_multi_day_investment():
    """测试多日期倍投逻辑"""
    print("=" * 80)
    print("多日期倍投策略测试")
    print("=" * 80)
    
    # 创建测试数据
    j13_df, etf_df = create_multi_day_test_data()
    
    print(f"\n测试数据:")
    print(f"  J13数据: {len(j13_df)} 天")
    print(f"  ETF数据: {len(etf_df)} 天")
    print(f"\n最近10天J13数量:")
    print(j13_df.tail(10).to_string(index=False))
    
    # 初始化策略
    strategy = SentimentReboundStrategy(
        etf_code='563300.SH',
        investment_levels=[2000, 4000, 8000, 16000],
        percentile_threshold=0.9,
        lookback_days=20
    )
    
    # 准备砖型图数据（简化版）
    brick_df = etf_df.copy()
    brick_df['zhixing_brick'] = np.random.uniform(0, 100, len(brick_df))
    brick_df['zhixing_brick_rising'] = [True, True, True, False, False, True, True, True, True, False] * 3
    
    # 执行策略
    print(f"\n执行策略...")
    signals = strategy.execute_strategy(j13_df, etf_df, brick_df)
    
    print(f"\n交易信号 ({len(signals)} 个):")
    for i, signal in enumerate(signals, 1):
        emoji = "🟢" if signal['action'] == 'BUY' else "🔴"
        print(f"  {i}. {emoji} {signal['date']} | {signal['action']} | {signal.get('type', '')}")
        if 'amount' in signal:
            print(f"      金额: ¥{signal['amount']:,.0f} (第{signal['level']}级)")
        print(f"      原因: {signal['reason']}")
    
    # 统计
    buy_signals = [s for s in signals if s['action'] == 'BUY']
    sell_signals = [s for s in signals if s['action'] == 'SELL']
    
    print(f"\n统计:")
    print(f"  买入信号: {len(buy_signals)} 个")
    print(f"  卖出信号: {len(sell_signals)} 个")
    
    if buy_signals:
        total_investment = sum(s['amount'] for s in buy_signals)
        print(f"  总投资金额: ¥{total_investment:,.0f}")
        level_changes = ' → '.join([f"第{s['level']}级" for s in buy_signals])
        print(f"  投资级别变化: {level_changes}")
    
    # 验证倍投逻辑
    print(f"\n倍投验证:")
    expected_levels = [1, 2, 3, 4]  # 期望的投资级别序列
    actual_levels = [s['level'] for s in buy_signals[:4]]
    
    if actual_levels == expected_levels[:len(actual_levels)]:
        print(f"  ✅ 倍投逻辑正确: {' → '.join([f'第{l}级' for l in actual_levels])}")
    else:
        print(f"  ❌ 倍投逻辑异常")
        print(f"     期望: {' → '.join([f'第{l}级' for l in expected_levels[:len(actual_levels)]])}")
        print(f"     实际: {' → '.join([f'第{l}级' for l in actual_levels])}")
    
    return signals


def test_reset_on_below_threshold():
    """测试低于阈值时重置投资级别"""
    print("\n" + "=" * 80)
    print("低于阈值重置测试")
    print("=" * 80)
    
    # 创建数据：高-低-高
    dates = pd.date_range(end=datetime.now(), periods=30, freq='D')
    
    counts = []
    for i in range(30):
        if i < 10:
            counts.append(np.random.randint(200, 250))  # 高
        elif i < 20:
            counts.append(np.random.randint(50, 100))   # 低
        else:
            counts.append(np.random.randint(200, 250))  # 高
    
    j13_df = pd.DataFrame({
        'trade_date': dates,
        'count': counts
    })
    
    etf_df = pd.DataFrame({
        'trade_date': dates.strftime('%Y%m%d'),
        'close': np.ones(30) * 1.4,
        'high': np.ones(30) * 1.42,
        'low': np.ones(30) * 1.38,
    })
    
    strategy = SentimentReboundStrategy(
        investment_levels=[2000, 4000, 8000, 16000],
        lookback_days=10
    )
    
    signals = strategy.execute_strategy(j13_df, etf_df)
    
    print(f"\nJ13数量走势: 高(1-10天) → 低(11-20天) → 高(21-30天)")
    print(f"\n交易信号 ({len(signals)} 个):")
    
    buy_signals = [s for s in signals if s['action'] == 'BUY']
    for signal in buy_signals:
        print(f"  🟢 {signal['date']} | 第{signal['level']}级 | ¥{signal['amount']:,.0f}")
    
    # 验证重置
    if len(buy_signals) >= 5:
        # 前几个应该是递增的，后面的应该重置为1级
        first_group = [s['level'] for s in buy_signals[:3]]
        second_group = [s['level'] for s in buy_signals[-3:]]
        
        print(f"\n重置验证:")
        print(f"  第一组级别: {first_group}")
        print(f"  第二组级别: {second_group}")
        
        if second_group[0] == 1:
            print(f"  ✅ 投资级别已正确重置")
        else:
            print(f"  ❌ 投资级别未重置")


if __name__ == "__main__":
    print("开始多日期倍投策略测试...\n")
    
    # 测试1: 多日期倍投
    signals = test_multi_day_investment()
    
    # 测试2: 低于阈值重置
    test_reset_on_below_threshold()
    
    print("\n" + "=" * 80)
    print("测试完成!")
    print("=" * 80)
