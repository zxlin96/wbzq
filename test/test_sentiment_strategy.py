#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
情绪反弹策略测试脚本
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from sentiment_rebound_strategy import SentimentReboundStrategy, generate_strategy_report


def create_test_j13_data():
    """创建测试J13数据"""
    # 模拟60天的J13数量数据
    dates = pd.date_range(end=datetime.now(), periods=60, freq='D')
    
    # 生成有波动的J13数量（大部分时间在50-150之间，偶尔超过200）
    np.random.seed(42)
    counts = []
    for i in range(60):
        if i in [55, 56, 57, 58, 59]:  # 最近5天超过90%分位数
            count = np.random.randint(180, 250)
        else:
            count = np.random.randint(50, 150)
        counts.append(count)
    
    df = pd.DataFrame({
        'trade_date': dates,
        'count': counts
    })
    
    return df


def create_test_etf_data():
    """创建测试ETF数据"""
    dates = pd.date_range(end=datetime.now(), periods=30, freq='D')
    
    # 模拟ETF价格走势
    np.random.seed(42)
    base_price = 1.45
    prices = []
    for i in range(30):
        change = np.random.uniform(-0.03, 0.03)
        base_price += change
        prices.append(base_price)
    
    df = pd.DataFrame({
        'trade_date': dates.strftime('%Y%m%d'),
        'close': prices,
        'high': [p + np.random.uniform(0, 0.02) for p in prices],
        'low': [p - np.random.uniform(0, 0.02) for p in prices],
    })
    
    return df


def test_strategy_basic():
    """测试策略基本功能"""
    print("=" * 80)
    print("情绪反弹策略 - 基本功能测试")
    print("=" * 80)
    
    # 初始化策略
    strategy = SentimentReboundStrategy(
        etf_code='563300.SH',
        investment_levels=[2000, 4000, 8000, 16000],
        percentile_threshold=0.9
    )
    
    print(f"\n策略配置:")
    print(f"  ETF代码: {strategy.etf_code}")
    print(f"  投资阶梯: {strategy.investment_levels}")
    print(f"  分位数阈值: {strategy.percentile_threshold * 100}%")
    
    # 创建测试数据
    j13_data = create_test_j13_data()
    etf_data = create_test_etf_data()
    
    print(f"\n测试数据:")
    print(f"  J13数据: {len(j13_data)} 条")
    print(f"  ETF数据: {len(etf_data)} 条")
    
    # 计算J13统计
    j13_stats = strategy.calculate_j13_stats(j13_data)
    
    print(f"\nJ13统计:")
    print(f"  当前数量: {j13_stats['current']:.0f} 只")
    print(f"  平均值: {j13_stats['mean']:.1f} 只")
    print(f"  90%分位数: {j13_stats['percentile_90']:.1f} 只")
    print(f"  是否触发买入: {'是' if j13_stats['is_above_threshold'] else '否'}")
    
    # 生成买入信号
    buy_signal = strategy.generate_buy_signal(j13_stats, datetime.now().strftime('%Y%m%d'))
    
    if buy_signal:
        print(f"\n买入信号:")
        print(f"  日期: {buy_signal['date']}")
        print(f"  操作: {buy_signal['action']}")
        print(f"  类型: {buy_signal['type']}")
        print(f"  金额: ¥{buy_signal['amount']:,.0f}")
        print(f"  级别: 第 {buy_signal['level']} 级")
        print(f"  原因: {buy_signal['reason']}")
    else:
        print(f"\n买入信号: 无")
    
    return strategy, j13_stats


def test_strategy_with_brick():
    """测试带砖型图指标的策略"""
    print("\n" + "=" * 80)
    print("情绪反弹策略 - 带砖型图指标测试")
    print("=" * 80)
    
    from test_zhixing_brick_real import calculate_zhixing_brick_indicator
    
    # 初始化策略
    strategy = SentimentReboundStrategy()
    
    # 创建测试数据
    j13_data = create_test_j13_data()
    etf_data = create_test_etf_data()
    
    # 添加必要的列用于砖型图计算
    etf_data['close_qfq'] = etf_data['close']
    etf_data['high_qfq'] = etf_data['high']
    etf_data['low_qfq'] = etf_data['low']
    etf_data['ts_code'] = '563300.SH'
    
    # 计算砖型图指标
    print("\n计算知行砖形图指标...")
    brick_data = calculate_zhixing_brick_indicator(etf_data)
    
    print(f"砖型图统计:")
    print(f"  最新值: {brick_data['zhixing_brick'].iloc[-1]:.2f}")
    print(f"  是否上升: {brick_data['zhixing_brick_rising'].iloc[-1]}")
    print(f"  XG信号: {brick_data['zhixing_brick_xg'].iloc[-1]}")
    
    # 执行策略
    j13_stats = strategy.calculate_j13_stats(j13_data)
    signals = strategy.execute_strategy(j13_data, etf_data, brick_data)
    
    print(f"\n交易信号 ({len(signals)} 个):")
    for i, signal in enumerate(signals, 1):
        emoji = "🟢" if signal['action'] == 'BUY' else "🔴"
        print(f"  {i}. {emoji} {signal['date']} | {signal['action']} | {signal.get('type', '')}")
        print(f"      原因: {signal['reason']}")
    
    return strategy, signals, j13_stats


def test_strategy_report():
    """测试策略报告生成"""
    print("\n" + "=" * 80)
    print("情绪反弹策略 - 报告生成测试")
    print("=" * 80)
    
    # 初始化策略并执行
    strategy = SentimentReboundStrategy()
    j13_data = create_test_j13_data()
    etf_data = create_test_etf_data()
    
    # 添加砖型图数据
    from test_zhixing_brick_real import calculate_zhixing_brick_indicator
    etf_data['close_qfq'] = etf_data['close']
    etf_data['high_qfq'] = etf_data['high']
    etf_data['low_qfq'] = etf_data['low']
    etf_data['ts_code'] = '563300.SH'
    brick_data = calculate_zhixing_brick_indicator(etf_data)
    
    # 执行策略
    j13_stats = strategy.calculate_j13_stats(j13_data)
    signals = strategy.execute_strategy(j13_data, etf_data, brick_data)
    
    # 生成报告
    output_file = 'test_sentiment_strategy_report.html'
    html_content = generate_strategy_report(strategy, signals, j13_stats, output_file)
    
    print(f"\n报告已生成: {output_file}")
    print(f"报告大小: {len(html_content)} 字符")
    
    return output_file


def test_multiple_scenarios():
    """测试多种场景"""
    print("\n" + "=" * 80)
    print("情绪反弹策略 - 多场景测试")
    print("=" * 80)
    
    scenarios = [
        {"name": "正常市场", "j13_factor": 1.0},
        {"name": "恐慌市场（J13高）", "j13_factor": 2.0},
        {"name": "牛市（J13低）", "j13_factor": 0.5},
    ]
    
    for scenario in scenarios:
        print(f"\n场景: {scenario['name']}")
        
        strategy = SentimentReboundStrategy()
        
        # 创建该场景的J13数据
        dates = pd.date_range(end=datetime.now(), periods=60, freq='D')
        base_count = 100 * scenario['j13_factor']
        counts = np.random.normal(base_count, 20, 60).clip(10, 500)
        
        j13_data = pd.DataFrame({
            'trade_date': dates,
            'count': counts.astype(int)
        })
        
        j13_stats = strategy.calculate_j13_stats(j13_data)
        
        print(f"  J13平均: {j13_stats['mean']:.1f}")
        print(f"  90%分位: {j13_stats['percentile_90']:.1f}")
        print(f"  当前值: {j13_stats['current']:.1f}")
        print(f"  触发买入: {'是' if j13_stats['is_above_threshold'] else '否'}")


if __name__ == "__main__":
    print("开始测试情绪反弹策略...\n")
    
    # 测试1: 基本功能
    strategy1, j13_stats1 = test_strategy_basic()
    
    # 测试2: 带砖型图指标
    strategy2, signals2, j13_stats2 = test_strategy_with_brick()
    
    # 测试3: 报告生成
    report_file = test_strategy_report()
    
    # 测试4: 多场景
    test_multiple_scenarios()
    
    print("\n" + "=" * 80)
    print("所有测试完成!")
    print("=" * 80)
