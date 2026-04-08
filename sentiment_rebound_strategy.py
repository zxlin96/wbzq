#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
情绪反弹策略 - 基于J13数量和知行砖形图指标的ETF交易策略

买入逻辑：
- 监控中证2000ETF（或同花顺全A）
- 当J13每日数量 > 90%分位数时，进行倍投
- 投资金额阶梯：2000、4000、8000、16000

卖出逻辑（砖型图方案）：
- 方案1：红柱4根卖一半，红转绿后全卖
- 方案2：未到4根红柱，红转绿也全卖
"""

import os
import json
import pandas as pd
import numpy as np
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional

logging.basicConfig(level=logging.INFO, format='%(message)s')


class SentimentReboundStrategy:
    """情绪反弹策略类"""
    
    def __init__(self, 
                 etf_code: str = '563300.SH',
                 investment_levels: List[float] = [2000, 4000, 8000, 16000],
                 percentile_threshold: float = 0.9,
                 lookback_days: int = 60,
                 state_file: str = 'strategy_state.json'):
        """
        初始化策略
        
        Args:
            etf_code: ETF代码，默认563300.SH（中证2000ETF）
            investment_levels: 投资金额阶梯
            percentile_threshold: 分位数阈值（默认90%）
            lookback_days: 计算分位数的历史天数
            state_file: 状态保存文件路径
        """
        self.etf_code = etf_code
        self.investment_levels = investment_levels
        self.percentile_threshold = percentile_threshold
        self.lookback_days = lookback_days
        self.state_file = state_file
        
        # 持仓状态
        self.position = 0  # 当前持仓份数
        self.position_cost = 0  # 持仓成本
        self.investment_level_idx = 0  # 当前投资级别索引
        self.red_bar_count = 0  # 连续红柱计数
        self.last_signal = None  # 上次信号
        self.last_trade_date = None  # 上次交易日期
        
        # 交易记录
        self.trades = []
        
        # 加载之前的状态
        self.load_state()
    
    def load_state(self):
        """从文件加载策略状态"""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                
                self.position = state.get('position', 0)
                self.position_cost = state.get('position_cost', 0)
                self.investment_level_idx = state.get('investment_level_idx', 0)
                self.red_bar_count = state.get('red_bar_count', 0)
                self.last_trade_date = state.get('last_trade_date')
                self.trades = state.get('trades', [])
                
                logging.info(f"已加载策略状态: 级别={self.investment_level_idx}, 持仓={self.position}")
            except Exception as e:
                logging.warning(f"加载策略状态失败: {e}")
    
    def save_state(self):
        """保存策略状态到文件"""
        # 转换trades中的numpy类型为标准Python类型
        def convert_to_serializable(obj):
            if isinstance(obj, dict):
                return {k: convert_to_serializable(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_to_serializable(item) for item in obj]
            elif isinstance(obj, np.integer):
                return int(obj)
            elif isinstance(obj, np.floating):
                return float(obj)
            elif isinstance(obj, np.ndarray):
                return obj.tolist()
            else:
                return obj
        
        state = {
            'position': int(self.position),
            'position_cost': float(self.position_cost),
            'investment_level_idx': int(self.investment_level_idx),
            'red_bar_count': int(self.red_bar_count),
            'last_trade_date': self.last_trade_date,
            'trades': convert_to_serializable(self.trades),
            'saved_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.warning(f"保存策略状态失败: {e}")
        
    def calculate_j13_stats(self, df: pd.DataFrame) -> Dict:
        """
        计算J13统计数据
        
        Args:
            df: DataFrame包含每日J13数量数据
            
        Returns:
            Dict包含统计信息
        """
        if df.empty or 'count' not in df.columns:
            return {}
        
        stats = {
            'mean': df['count'].mean(),
            'median': df['count'].median(),
            'std': df['count'].std(),
            'min': df['count'].min(),
            'max': df['count'].max(),
            'percentile_90': df['count'].quantile(self.percentile_threshold),
            'current': df['count'].iloc[-1] if not df.empty else 0,
            'is_above_threshold': df['count'].iloc[-1] >= df['count'].quantile(self.percentile_threshold) if not df.empty else False
        }
        
        return stats
    
    def generate_buy_signal(self, j13_stats: Dict, current_date: str) -> Optional[Dict]:
        """
        生成买入信号
        
        Args:
            j13_stats: J13统计数据
            current_date: 当前日期
            
        Returns:
            买入信号字典或None
        """
        if not j13_stats or not j13_stats.get('is_above_threshold'):
            return None
        
        # 确定投资金额
        investment_amount = self.investment_levels[min(self.investment_level_idx, len(self.investment_levels) - 1)]
        
        signal = {
            'date': current_date,
            'action': 'BUY',
            'type': '倍投',
            'amount': investment_amount,
            'level': self.investment_level_idx + 1,
            'j13_count': j13_stats['current'],
            'threshold': j13_stats['percentile_90'],
            'reason': f"J13数量({j13_stats['current']:.0f})超过{self.percentile_threshold*100:.0f}%分位数({j13_stats['percentile_90']:.1f})"
        }
        
        # 升级投资级别
        self.investment_level_idx = min(self.investment_level_idx + 1, len(self.investment_levels) - 1)
        self.last_trade_date = current_date
        
        # 保存状态
        self.save_state()
        
        return signal
    
    def generate_sell_signal(self, 
                            brick_data: pd.DataFrame, 
                            current_date: str,
                            current_price: float) -> Optional[Dict]:
        """
        生成卖出信号（基于砖型图）
        
        Args:
            brick_data: 包含砖型图指标的DataFrame
            current_date: 当前日期
            current_price: 当前价格
            
        Returns:
            卖出信号字典或None
        """
        if brick_data.empty or 'zhixing_brick_rising' not in brick_data.columns:
            return None
        
        latest = brick_data.iloc[-1]
        is_rising = latest['zhixing_brick_rising']
        
        signal = None
        
        # 红柱计数
        if is_rising:
            self.red_bar_count += 1
        else:
            # 红转绿
            if self.red_bar_count > 0:
                # 方案2：未到4根红柱，红转绿也全卖
                if self.red_bar_count < 4:
                    signal = {
                        'date': current_date,
                        'action': 'SELL',
                        'type': '全卖',
                        'ratio': 1.0,
                        'red_bars': self.red_bar_count,
                        'price': current_price,
                        'reason': f"红转绿，连续红柱{self.red_bar_count}根（未满4根）"
                    }
                    self.red_bar_count = 0
                    self.investment_level_idx = 0  # 重置投资级别
        
        # 方案1：红柱4根卖一半
        if self.red_bar_count == 4 and self.position > 0:
            signal = {
                'date': current_date,
                'action': 'SELL',
                'type': '卖一半',
                'ratio': 0.5,
                'red_bars': self.red_bar_count,
                'price': current_price,
                'reason': "连续红柱达到4根，卖出一半"
            }
        
        return signal
    
    def generate_current_signal(self,
                               j13_stats: Dict,
                               current_date: str,
                               brick_data: pd.DataFrame = None,
                               current_price: float = 0) -> List[Dict]:
        """
        生成当前日期的交易信号（用于实时/最新一天）
        
        Args:
            j13_stats: J13统计数据（包含current和is_above_threshold）
            current_date: 当前日期
            brick_data: 砖型图指标数据（可选）
            current_price: 当前价格
            
        Returns:
            交易信号列表
        """
        signals = []
        
        # 检查日期顺序，防止回退到过去
        if self.last_trade_date and current_date < self.last_trade_date:
            logging.warning(f"当前日期({current_date})早于上次交易日期({self.last_trade_date})，跳过信号生成")
            logging.warning(f"如需重新运行历史日期，请删除 {self.state_file} 文件")
            return signals
        
        # 检查是否同一日期重复运行
        if self.last_trade_date and current_date == self.last_trade_date:
            logging.info(f"当前日期({current_date})与上次交易日期相同，已处理过")
            return signals
        
        # 生成买入信号
        if j13_stats.get('is_above_threshold'):
            buy_signal = self.generate_buy_signal(j13_stats, current_date)
            if buy_signal:
                signals.append(buy_signal)
                self.trades.append(buy_signal)
                logging.info(f"买入信号: {current_date} - 金额¥{buy_signal['amount']:,.0f} (第{buy_signal['level']}级)")
        else:
            # 如果J13数量低于阈值，重置投资级别
            if self.investment_level_idx > 0:
                logging.info(f"J13数量({j13_stats['current']:.0f})低于阈值({j13_stats['percentile_90']:.1f})，重置投资级别")
            self.investment_level_idx = 0
            self.last_trade_date = current_date
            self.save_state()
        
        # 生成卖出信号（如果有持仓且提供了砖型图数据）
        if self.position > 0 and brick_data is not None and not brick_data.empty:
            sell_signal = self.generate_sell_signal(brick_data, current_date, current_price)
            if sell_signal:
                signals.append(sell_signal)
                self.trades.append(sell_signal)
                logging.info(f"卖出信号: {current_date} - {sell_signal['type']}")
        
        return signals
    
    def execute_strategy(self, 
                        j13_data: pd.DataFrame, 
                        etf_data: pd.DataFrame,
                        brick_data: pd.DataFrame = None) -> List[Dict]:
        """
        执行策略 - 遍历历史数据（用于回测）
        
        Args:
            j13_data: J13每日数量数据
            etf_data: ETF价格数据
            brick_data: 砖型图指标数据（可选）
            
        Returns:
            交易信号列表
        """
        signals = []
        
        if j13_data.empty or etf_data.empty:
            logging.warning("数据为空，无法执行策略")
            return signals
        
        # 确保日期格式一致
        j13_data = j13_data.copy()
        etf_data = etf_data.copy()
        
        if 'trade_date' in j13_data.columns:
            j13_data['trade_date'] = pd.to_datetime(j13_data['trade_date']).dt.strftime('%Y%m%d')
        if 'trade_date' in etf_data.columns:
            etf_data['trade_date'] = pd.to_datetime(etf_data['trade_date']).dt.strftime('%Y%m%d')
        
        # 遍历每一天（从lookback_days开始，确保有足够的历史数据计算分位数）
        for i in range(self.lookback_days, len(j13_data)):
            current_date = j13_data['trade_date'].iloc[i]
            
            # 使用过去lookback_days天的数据计算分位数
            historical_data = j13_data.iloc[max(0, i-self.lookback_days):i]
            
            if len(historical_data) < self.lookback_days // 2:  # 确保有足够的历史数据
                continue
            
            # 计算当前日期的J13统计
            j13_stats = self.calculate_j13_stats(historical_data)
            j13_stats['current'] = j13_data['count'].iloc[i]
            j13_stats['is_above_threshold'] = j13_stats['current'] >= j13_stats['percentile_90']
            
            # 生成买入信号
            if j13_stats['is_above_threshold']:
                buy_signal = self.generate_buy_signal(j13_stats, current_date)
                if buy_signal:
                    signals.append(buy_signal)
                    self.trades.append(buy_signal)
                    logging.info(f"买入信号: {current_date} - 金额¥{buy_signal['amount']:,.0f} (第{buy_signal['level']}级)")
            else:
                # 如果J13数量低于阈值，重置投资级别
                if self.investment_level_idx > 0:
                    logging.info(f"J13数量({j13_stats['current']:.0f})低于阈值({j13_stats['percentile_90']:.1f})，重置投资级别")
                self.investment_level_idx = 0
            
            # 生成卖出信号（如果有持仓且提供了砖型图数据）
            if self.position > 0 and brick_data is not None and not brick_data.empty:
                # 找到对应日期的砖型图数据
                date_brick = brick_data[brick_data['trade_date'] == current_date]
                if not date_brick.empty:
                    current_price = date_brick['close'].iloc[0] if 'close' in date_brick.columns else 0
                    sell_signal = self.generate_sell_signal(date_brick, current_date, current_price)
                    if sell_signal:
                        signals.append(sell_signal)
                        self.trades.append(sell_signal)
                        logging.info(f"卖出信号: {current_date} - {sell_signal['type']}")
        
        return signals
    
    def get_position_summary(self) -> Dict:
        """获取持仓摘要"""
        return {
            'position': self.position,
            'cost': self.position_cost,
            'investment_level': self.investment_level_idx,
            'red_bar_count': self.red_bar_count,
            'total_trades': len(self.trades)
        }
    
    def reset(self):
        """重置策略状态"""
        self.position = 0
        self.position_cost = 0
        self.investment_level_idx = 0
        self.red_bar_count = 0
        self.last_signal = None
        self.trades = []


def generate_strategy_report(strategy: SentimentReboundStrategy, 
                            signals: List[Dict],
                            j13_stats: Dict,
                            output_file: str = None) -> str:
    """
    生成策略报告HTML
    
    Args:
        strategy: 策略实例
        signals: 交易信号列表
        j13_stats: J13统计数据
        output_file: 输出文件路径
        
    Returns:
        HTML内容字符串
    """
    
    position_summary = strategy.get_position_summary()
    
    # 生成交易记录表格
    trades_html = ""
    for trade in strategy.trades:
        color = "text-green-600" if trade['action'] == 'BUY' else "text-red-600"
        trades_html += f"""
        <tr class="hover:bg-gray-50">
            <td class="px-4 py-3">{trade['date']}</td>
            <td class="px-4 py-3 font-bold {color}">{trade['action']}</td>
            <td class="px-4 py-3">{trade.get('type', '')}</td>
            <td class="px-4 py-3">{trade.get('amount', trade.get('price', ''))}</td>
            <td class="px-4 py-3 text-sm text-gray-600">{trade['reason']}</td>
        </tr>
        """
    
    # 生成当前信号表格
    signals_html = ""
    for signal in signals:
        color = "text-green-600" if signal['action'] == 'BUY' else "text-red-600"
        signals_html += f"""
        <tr class="hover:bg-gray-50 bg-yellow-50">
            <td class="px-4 py-3">{signal['date']}</td>
            <td class="px-4 py-3 font-bold {color}">{signal['action']}</td>
            <td class="px-4 py-3">{signal.get('type', '')}</td>
            <td class="px-4 py-3">{signal.get('amount', signal.get('price', ''))}</td>
            <td class="px-4 py-3 text-sm text-gray-600">{signal['reason']}</td>
        </tr>
        """
    
    html_content = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>情绪反弹策略分析报告</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        body {{ font-family: 'Inter', sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; }}
        .glass {{ background: rgba(255, 255, 255, 0.95); backdrop-filter: blur(10px); border-radius: 16px; box-shadow: 0 8px 32px rgba(0,0,0,0.1); }}
        .stat-card {{ transition: transform 0.2s; }}
        .stat-card:hover {{ transform: translateY(-2px); }}
    </style>
</head>
<body class="p-4 md:p-8">
    <div class="max-w-7xl mx-auto">
        <!-- 标题 -->
        <div class="glass p-6 mb-6">
            <div class="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
                <div>
                    <h1 class="text-2xl md:text-3xl font-bold text-gray-800">📊 情绪反弹策略分析报告</h1>
                    <p class="text-gray-500 mt-1">ETF: {strategy.etf_code} | 策略版本: v1.0</p>
                </div>
                <div class="flex gap-3">
                    <a href="index.html" class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">
                        ← 返回首页
                    </a>
                </div>
            </div>
        </div>
        
        <!-- 策略说明 -->
        <div class="glass p-6 mb-6">
            <h2 class="text-xl font-bold text-gray-800 mb-4">📋 策略说明</h2>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div>
                    <h3 class="font-semibold text-green-700 mb-2">买入逻辑</h3>
                    <ul class="text-gray-600 space-y-1 text-sm">
                        <li>• 监控标的: {strategy.etf_code}</li>
                        <li>• 触发条件: J13数量 > {strategy.percentile_threshold*100:.0f}%分位数</li>
                        <li>• 投资阶梯: {' → '.join([f'¥{x:,}' for x in strategy.investment_levels])}</li>
                        <li>• 当前级别: 第 {position_summary['investment_level'] + 1} 级</li>
                    </ul>
                </div>
                <div>
                    <h3 class="font-semibold text-red-700 mb-2">卖出逻辑（砖型图）</h3>
                    <ul class="text-gray-600 space-y-1 text-sm">
                        <li>• 方案1: 红柱4根卖一半，红转绿全卖</li>
                        <li>• 方案2: 未满4根红柱，红转绿也全卖</li>
                        <li>• 当前红柱: {position_summary['red_bar_count']} 根</li>
                        <li>• 持仓状态: {'有持仓' if position_summary['position'] > 0 else '空仓'}</li>
                    </ul>
                </div>
            </div>
        </div>
        
        <!-- J13统计 -->
        <div class="glass p-6 mb-6">
            <h2 class="text-xl font-bold text-gray-800 mb-4">📈 J13市场统计</h2>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div class="glass p-4 stat-card">
                    <div class="text-sm text-gray-500">当前J13数量</div>
                    <div class="text-2xl font-bold {'text-red-600' if j13_stats.get('is_above_threshold') else 'text-blue-600'}">{j13_stats.get('current', 0):.0f} 只</div>
                </div>
                <div class="glass p-4 stat-card">
                    <div class="text-sm text-gray-500">{strategy.percentile_threshold*100:.0f}%分位数</div>
                    <div class="text-2xl font-bold text-purple-600">{j13_stats.get('percentile_90', 0):.1f} 只</div>
                </div>
                <div class="glass p-4 stat-card">
                    <div class="text-sm text-gray-500">平均值</div>
                    <div class="text-2xl font-bold text-green-600">{j13_stats.get('mean', 0):.1f} 只</div>
                </div>
                <div class="glass p-4 stat-card">
                    <div class="text-sm text-gray-500">是否触发买入</div>
                    <div class="text-2xl font-bold {'text-red-600' if j13_stats.get('is_above_threshold') else 'text-gray-400'}">{'是' if j13_stats.get('is_above_threshold') else '否'}</div>
                </div>
            </div>
        </div>
        
        <!-- 当前信号 -->
        <div class="glass p-6 mb-6">
            <h2 class="text-xl font-bold text-gray-800 mb-4">⚡ 当前交易信号</h2>
            {f'<table class="w-full"><thead class="bg-gray-50"><tr><th class="px-4 py-3 text-left">日期</th><th class="px-4 py-3 text-left">操作</th><th class="px-4 py-3 text-left">类型</th><th class="px-4 py-3 text-left">金额/价格</th><th class="px-4 py-3 text-left">原因</th></tr></thead><tbody class="divide-y divide-gray-200">' + signals_html + '</tbody></table>' if signals else '<p class="text-gray-500">暂无交易信号</p>'}
        </div>
        
        <!-- 历史交易记录 -->
        <div class="glass p-6">
            <h2 class="text-xl font-bold text-gray-800 mb-4">📜 历史交易记录</h2>
            {f'<table class="w-full"><thead class="bg-gray-50"><tr><th class="px-4 py-3 text-left">日期</th><th class="px-4 py-3 text-left">操作</th><th class="px-4 py-3 text-left">类型</th><th class="px-4 py-3 text-left">金额/价格</th><th class="px-4 py-3 text-left">原因</th></tr></thead><tbody class="divide-y divide-gray-200">' + trades_html + '</tbody></table>' if strategy.trades else '<p class="text-gray-500">暂无交易记录</p>'}
        </div>
    </div>
</body>
</html>'''
    
    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(html_content)
        logging.info(f"策略报告已保存: {output_file}")
    
    return html_content


if __name__ == "__main__":
    # 测试代码
    print("情绪反弹策略模块已加载")
    print(f"默认ETF: 563300.SH (中证2000ETF)")
    print(f"投资阶梯: 2000 → 4000 → 8000 → 16000")
