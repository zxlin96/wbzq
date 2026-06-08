#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
情绪反弹策略回测脚本
===================
独立回测情绪反弹策略的历史表现，不依赖 strategy_state.json。

策略逻辑：
  标的：中证2000ETF（563300.SH）
  买入：当日 J13数量 ≥ 过去60天的90%分位数 → 倍投入场（2000→4000→8000→16000）
  卖出：
    - 红柱满4根 → 卖一半
    - 连续3根绿柱 → 全卖（容忍2天回调）

用法：
  python backtest_sentiment_rebound.py --days 250
  python backtest_sentiment_rebound.py --days 120 --output html/backtest_sentiment
"""

import os
import sys
import json
import argparse
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

# ─── 项目模块 ───
from data_manager import DataManager
from config import APIConfig
import tushare as ts

ts.set_token(APIConfig.get_token())
pro = ts.pro_api()

logging.basicConfig(level=logging.INFO, format='%(message)s')


# ═══════════════════════════════════════════════════════════════
#  1. 数据获取
# ═══════════════════════════════════════════════════════════════

def get_j13_daily_counts(dm: DataManager, end_date: str, days: int) -> pd.DataFrame:
    """
    获取每日 J13（KDJ的J值<13）的股票数量。

    Returns:
        DataFrame with columns [trade_date, count], sorted by trade_date
    """
    # 获取交易日历
    start_dt = pd.to_datetime(end_date, format='%Y%m%d') - timedelta(days=int(days * 1.8))
    trade_dates = dm.get_trade_dates(start_dt.strftime('%Y%m%d'), end_date)
    if not trade_dates:
        logging.error("无法获取交易日历")
        return pd.DataFrame()

    # 交易日历可能降序返回，统一升序后再取最近 days 个
    trade_dates = sorted(trade_dates)
    trade_dates = trade_dates[-days:]

    logging.info(f"获取 {len(trade_dates)} 个交易日的因子数据...")
    fields = ['ts_code', 'trade_date', 'kdj_qfq']
    df = dm.get_stock_factors(trade_dates, fields)

    if df.empty:
        logging.error("因子数据为空")
        return pd.DataFrame()

    # 统计每日 J13 数量
    daily_counts = df[df['kdj_qfq'] < 13].groupby('trade_date').size().reset_index(name='count')
    daily_counts = daily_counts.sort_values('trade_date').reset_index(drop=True)

    logging.info(f"J13数据: {len(daily_counts)} 个交易日, "
                 f"J13均值={daily_counts['count'].mean():.0f}, "
                 f"J13最大={daily_counts['count'].max()}")

    return daily_counts


def get_etf_data(etf_code: str, end_date: str, days: int) -> pd.DataFrame:
    """
    获取ETF日线数据并计算前复权价格。
    """
    start_date = (pd.to_datetime(end_date, format='%Y%m%d') - timedelta(days=int(days * 1.8))).strftime('%Y%m%d')
    etf_df = pro.fund_daily(ts_code=etf_code, start_date=start_date, end_date=end_date)
    if etf_df is None or etf_df.empty:
        logging.warning(f"无法获取ETF数据: {etf_code}")
        return pd.DataFrame()

    etf_df = etf_df.sort_values('trade_date').reset_index(drop=True)
    # 只取最近 days 条
    etf_df = etf_df.tail(days).reset_index(drop=True)

    # 前复权
    etf_df['open_qfq'] = etf_df['open']
    etf_df['close_qfq'] = etf_df['close']
    etf_df['high_qfq'] = etf_df['high']
    etf_df['low_qfq'] = etf_df['low']
    etf_df['ts_code'] = etf_code

    logging.info(f"ETF数据: {len(etf_df)} 条, {etf_df['trade_date'].iloc[0]} ~ {etf_df['trade_date'].iloc[-1]}")
    return etf_df


def calculate_zhixing_brick(etf_data: pd.DataFrame) -> pd.DataFrame:
    """
    计算知行砖形图指标（单标的版）。
    """
    df = etf_data.copy()
    # 4日最高/最低
    df['hhv_high_4'] = df['high_qfq'].rolling(window=4, min_periods=4).max()
    df['llv_low_4'] = df['low_qfq'].rolling(window=4, min_periods=4).min()
    df['price_range'] = df['hhv_high_4'] - df['llv_low_4']
    df['price_range'] = df['price_range'].replace(0, np.nan)

    # VAR1A~VAR6A
    df['var1a'] = (df['hhv_high_4'] - df['close_qfq']) / df['price_range'] * 100 - 90
    df['var2a'] = df['var1a'].ewm(span=4, adjust=False).mean() + 100
    df['var3a'] = (df['close_qfq'] - df['llv_low_4']) / df['price_range'] * 100
    df['var4a'] = df['var3a'].ewm(span=6, adjust=False).mean()
    df['var5a'] = df['var4a'].ewm(span=6, adjust=False).mean() + 100
    df['var6a'] = df['var5a'] - df['var2a']

    df['zhixing_brick'] = np.where(df['var6a'] > 4, df['var6a'] - 4, 0)

    df['brick_prev'] = df['zhixing_brick'].shift(1)
    df['zhixing_brick_rising'] = df['brick_prev'] < df['zhixing_brick']
    df['zhixing_brick_falling'] = df['brick_prev'] > df['zhixing_brick']

    df = df.drop(columns=['hhv_high_4', 'llv_low_4', 'price_range',
                           'var1a', 'var2a', 'var3a', 'var4a', 'var5a', 'var6a', 'brick_prev'], errors='ignore')
    return df


# ═══════════════════════════════════════════════════════════════
#  2. 回测引擎
# ═══════════════════════════════════════════════════════════════

class BacktestEngine:
    """情绪反弹策略回测引擎"""

    def __init__(self,
                 etf_code: str = '563300.SH',
                 investment_levels: List[float] = None,
                 percentile_threshold: float = 0.9,
                 lookback_days: int = 60):
        self.etf_code = etf_code
        self.investment_levels = investment_levels or [2000, 4000, 8000, 16000]
        self.percentile_threshold = percentile_threshold
        self.lookback_days = lookback_days

        # 持仓状态
        self.position_shares = 0.0   # 持仓份数（每2000元=1份）
        self.position_cost = 0.0     # 平均成本价
        self.invest_level_idx = 0    # 当前投资级别
        self.red_bar_count = 0       # 连续红柱计数
        self.green_bar_count = 0     # 连续绿柱计数
        self.hold_value = 0.0        # 累计投入金额

        # 交易记录
        self.trades: List[Dict] = []
        # 完整交易对（买入→卖出）
        self.round_trips: List[Dict] = []
        # 当前未平仓的买入记录
        self.open_buys: List[Dict] = []

    def reset(self):
        self.position_shares = 0
        self.position_cost = 0
        self.invest_level_idx = 0
        self.red_bar_count = 0
        self.green_bar_count = 0
        self.hold_value = 0
        self.trades = []
        self.round_trips = []
        self.open_buys = []

    def run(self, j13_data: pd.DataFrame, etf_data: pd.DataFrame, brick_data: pd.DataFrame):
        """
        运行回测。

        Args:
            j13_data: columns [trade_date, count]
            etf_data: columns [trade_date, open, close, high, low, ...]
            brick_data: etf_data with zhixing_brick, zhixing_brick_rising
        """
        self.reset()

        # 建立日期→ETF/砖型图索引
        etf_idx = {str(r['trade_date']): r for _, r in etf_data.iterrows()}
        brick_idx = {str(r['trade_date']): r for _, r in brick_data.iterrows()}

        # 只在 j13_data 和 etf_data 都有的日期上回测
        common_dates = sorted(set(j13_data['trade_date'].astype(str)) & set(etf_idx.keys()))

        logging.info(f"回测交易日数: {len(common_dates)}, "
                     f"从 {common_dates[0]} 到 {common_dates[-1]}")

        for i, date in enumerate(common_dates):
            # 当日ETF数据
            etf_row = etf_idx[date]
            current_price = float(etf_row.get('close_qfq', etf_row.get('close', 0)))

            # 当日J13数量
            j13_row = j13_data[j13_data['trade_date'].astype(str) == date]
            if j13_row.empty:
                continue
            current_j13 = int(j13_row['count'].iloc[0])

            # 用过去 lookback_days 天的 J13 计算分位数
            past_j13 = j13_data[j13_data['trade_date'].astype(str) < date].tail(self.lookback_days)
            if len(past_j13) < self.lookback_days // 2:
                continue
            threshold = past_j13['count'].quantile(self.percentile_threshold)

            # ─── 买入逻辑 ───
            if current_j13 >= threshold:
                buy_amount = self.investment_levels[min(self.invest_level_idx, len(self.investment_levels) - 1)]
                shares = buy_amount / 2000
                # 用当日收盘价模拟买入
                buy_price = current_price
                if buy_price <= 0:
                    continue

                # 更新持仓
                old_shares = self.position_shares
                old_cost = self.position_cost
                self.position_shares += shares
                if self.position_shares > 0:
                    self.position_cost = (old_cost * old_shares + buy_price * shares) / self.position_shares
                self.hold_value += buy_amount

                # 升级投资级别
                self.invest_level_idx = min(self.invest_level_idx + 1, len(self.investment_levels) - 1)

                trade = {
                    'date': date,
                    'action': 'BUY',
                    'type': '倍投',
                    'amount': buy_amount,
                    'level': self.invest_level_idx,
                    'price': buy_price,
                    'shares': shares,
                    'j13_count': current_j13,
                    'threshold': threshold,
                }
                self.trades.append(trade)
                self.open_buys.append(trade)
                continue  # 买入日不检查卖出

            # ─── J13低于阈值 → 重置投资级别 ───
            if self.invest_level_idx > 0:
                self.invest_level_idx = 0

            # ─── 卖出逻辑（需要有持仓且有砖型图数据）───
            if self.position_shares <= 0:
                continue

            brick_row = brick_idx.get(date)
            if brick_row is None:
                continue

            is_rising = bool(brick_row.get('zhixing_brick_rising', False))

            if is_rising:
                self.red_bar_count += 1
                self.green_bar_count = 0
            else:
                self.green_bar_count += 1

            # 连续3根绿柱 → 全卖
            if self.green_bar_count >= 3 and self.position_shares > 0:
                sell_price = current_price
                sell_value = self.position_shares * 2000 * (sell_price / self.position_cost) if self.position_cost > 0 else 0
                pnl = sell_value - self.hold_value

                trade = {
                    'date': date,
                    'action': 'SELL',
                    'type': '全卖',
                    'price': sell_price,
                    'shares': self.position_shares,
                    'green_bars': self.green_bar_count,
                    'sell_value': sell_value,
                    'pnl': pnl,
                    'reason': f'连续{self.green_bar_count}根绿柱，确认趋势反转',
                }
                self.trades.append(trade)
                self._close_round_trips(sell_price, date, trade)

                self.position_shares = 0
                self.position_cost = 0
                self.hold_value = 0
                self.invest_level_idx = 0
                self.red_bar_count = 0
                self.green_bar_count = 0
                continue

            # 红柱满4根 → 卖一半
            if is_rising and self.red_bar_count == 4 and self.position_shares > 0:
                sell_shares = self.position_shares * 0.5
                sell_price = current_price
                sell_value = sell_shares * 2000 * (sell_price / self.position_cost) if self.position_cost > 0 else 0
                half_hold = self.hold_value * 0.5
                pnl = sell_value - half_hold

                trade = {
                    'date': date,
                    'action': 'SELL',
                    'type': '卖一半',
                    'price': sell_price,
                    'shares': sell_shares,
                    'red_bars': self.red_bar_count,
                    'sell_value': sell_value,
                    'pnl': pnl,
                    'reason': '连续红柱达到4根，卖出一半',
                }
                self.trades.append(trade)
                self._close_round_trips(sell_price, date, trade, ratio=0.5)

                self.position_shares -= sell_shares
                self.hold_value -= half_hold
                if self.position_shares < 0.01:
                    self.position_shares = 0
                    self.hold_value = 0
                    self.position_cost = 0
                    self.invest_level_idx = 0
                self.red_bar_count = 0
                self.green_bar_count = 0

            # 绿柱时重置红柱计数
            if not is_rising:
                self.red_bar_count = 0

    def _close_round_trips(self, sell_price: float, sell_date: str, sell_trade: Dict, ratio: float = 1.0):
        """将未平仓的买入记录配对为完整交易"""
        if ratio >= 1.0:
            # 全部平仓
            for buy in self.open_buys:
                buy_price = buy['price']
                buy_value = buy['amount']
                sell_value_for_this = buy['shares'] * 2000 * (sell_price / buy_price)
                pnl = sell_value_for_this - buy_value
                hold_days = self._calc_hold_days(buy['date'], sell_date)
                self.round_trips.append({
                    'buy_date': buy['date'],
                    'buy_price': buy_price,
                    'buy_amount': buy_value,
                    'sell_date': sell_date,
                    'sell_price': sell_price,
                    'pnl': pnl,
                    'pnl_pct': (pnl / buy_value * 100) if buy_value > 0 else 0,
                    'hold_days': hold_days,
                    'level': buy.get('level', 1),
                })
            self.open_buys = []
        else:
            # 按比例平仓（简化：平掉最早的一半）
            total_shares = sum(b['shares'] for b in self.open_buys)
            to_sell = total_shares * ratio
            remaining = []
            sold = 0
            for buy in self.open_buys:
                if sold + buy['shares'] <= to_sell:
                    buy_price = buy['price']
                    buy_value = buy['amount']
                    sell_value_for_this = buy['shares'] * 2000 * (sell_price / buy_price)
                    pnl = sell_value_for_this - buy_value
                    hold_days = self._calc_hold_days(buy['date'], sell_date)
                    self.round_trips.append({
                        'buy_date': buy['date'],
                        'buy_price': buy_price,
                        'buy_amount': buy_value,
                        'sell_date': sell_date,
                        'sell_price': sell_price,
                        'pnl': pnl,
                        'pnl_pct': (pnl / buy_value * 100) if buy_value > 0 else 0,
                        'hold_days': hold_days,
                        'level': buy.get('level', 1),
                    })
                    sold += buy['shares']
                else:
                    remaining.append(buy)
            self.open_buys = remaining

    @staticmethod
    def _calc_hold_days(buy_date: str, sell_date: str) -> int:
        try:
            bd = pd.to_datetime(buy_date, format='%Y%m%d')
            sd = pd.to_datetime(sell_date, format='%Y%m%d')
            return (sd - bd).days
        except Exception:
            return 0


# ═══════════════════════════════════════════════════════════════
#  3. 统计指标
# ═══════════════════════════════════════════════════════════════

def calc_statistics(round_trips: List[Dict], trades: List[Dict]) -> Dict:
    """计算回测统计指标"""
    if not round_trips:
        return {
            'total_trades': 0,
            'win_count': 0,
            'lose_count': 0,
            'win_rate': 0,
            'total_pnl': 0,
            'avg_pnl': 0,
            'avg_pnl_pct': 0,
            'max_win': 0,
            'max_loss': 0,
            'max_drawdown': 0,
            'avg_hold_days': 0,
            'buy_signal_count': sum(1 for t in trades if t['action'] == 'BUY'),
            'total_invested': sum(t.get('amount', 0) for t in trades if t['action'] == 'BUY'),
        }

    pnls = [rt['pnl'] for rt in round_trips]
    pnl_pcts = [rt['pnl_pct'] for rt in round_trips]
    hold_days = [rt['hold_days'] for rt in round_trips]

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    # 累计收益曲线 → 最大回撤
    cum = np.cumsum(pnls)
    peak = np.maximum.accumulate(cum)
    drawdown = cum - peak
    max_dd = abs(drawdown.min()) if len(drawdown) > 0 else 0

    total_invested = sum(t.get('amount', 0) for t in trades if t['action'] == 'BUY')

    return {
        'total_trades': len(round_trips),
        'win_count': len(wins),
        'lose_count': len(losses),
        'win_rate': len(wins) / len(round_trips) * 100 if round_trips else 0,
        'total_pnl': sum(pnls),
        'avg_pnl': np.mean(pnls),
        'avg_pnl_pct': np.mean(pnl_pcts),
        'max_win': max(pnls) if pnls else 0,
        'max_loss': min(pnls) if pnls else 0,
        'max_drawdown': max_dd,
        'avg_hold_days': np.mean(hold_days),
        'buy_signal_count': sum(1 for t in trades if t['action'] == 'BUY'),
        'total_invested': total_invested,
    }


# ═══════════════════════════════════════════════════════════════
#  4. HTML 报告生成
# ═══════════════════════════════════════════════════════════════

def generate_backtest_report(engine: BacktestEngine, stats: Dict,
                             j13_data: pd.DataFrame, etf_data: pd.DataFrame,
                             brick_data: pd.DataFrame,
                             end_date: str, output_path: str):
    """生成回测HTML报告"""

    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)

    # ─── 图表数据 ───
    # J13数量走势
    j13_dates = j13_data['trade_date'].astype(str).tolist()
    j13_vals = j13_data['count'].tolist()

    # 累计收益曲线
    cum_pnl = [0]
    for rt in engine.round_trips:
        cum_pnl.append(cum_pnl[-1] + rt['pnl'])

    # 买卖点标记
    buy_points = []
    sell_points = []
    for t in engine.trades:
        if t['action'] == 'BUY':
            buy_points.append({'date': str(t['date']), 'price': t['price'], 'level': t.get('level', 1)})
        else:
            sell_points.append({'date': str(t['date']), 'price': t['price']})

    # ETF K线数据
    etf_dates = etf_data['trade_date'].astype(str).tolist()
    candlestick = []
    for _, row in etf_data.iterrows():
        op = float(row.get('open_qfq', row.get('open', 0)))
        cl = float(row.get('close_qfq', row.get('close', 0)))
        hi = float(row.get('high_qfq', row.get('high', 0)))
        lo = float(row.get('low_qfq', row.get('low', 0)))
        if op == 0: op = cl
        if hi == 0: hi = cl
        if lo == 0: lo = cl
        candlestick.append([op, cl, lo, hi])

    # ─── 交易明细表 ───
    round_trip_rows = ""
    for i, rt in enumerate(engine.round_trips, 1):
        pnl_color = 'text-green-600' if rt['pnl'] > 0 else 'text-red-600'
        pnl_sign = '+' if rt['pnl'] > 0 else ''
        round_trip_rows += f"""
        <tr class="hover:bg-gray-50">
            <td class="px-4 py-3">{i}</td>
            <td class="px-4 py-3">{rt['buy_date']}</td>
            <td class="px-4 py-3">{rt['buy_price']:.3f}</td>
            <td class="px-4 py-3">¥{rt['buy_amount']:,.0f}</td>
            <td class="px-4 py-3">{rt['sell_date']}</td>
            <td class="px-4 py-3">{rt['sell_price']:.3f}</td>
            <td class="px-4 py-3 font-bold {pnl_color}">{pnl_sign}{rt['pnl']:.2f}</td>
            <td class="px-4 py-3 {pnl_color}">{pnl_sign}{rt['pnl_pct']:.2f}%</td>
            <td class="px-4 py-3">{rt['hold_days']}天</td>
        </tr>
        """

    # ─── 信号明细表 ───
    signal_rows = ""
    for t in engine.trades:
        color = 'text-green-600' if t['action'] == 'BUY' else 'text-red-600'
        bg = 'bg-green-50' if t['action'] == 'BUY' else 'bg-red-50'
        signal_rows += f"""
        <tr class="hover:bg-gray-50 {bg}">
            <td class="px-4 py-3">{t['date']}</td>
            <td class="px-4 py-3 font-bold {color}">{t['action']}</td>
            <td class="px-4 py-3">{t.get('type', '')}</td>
            <td class="px-4 py-3">{t.get('price', 0):.3f}</td>
            <td class="px-4 py-3">{'¥' + f"{t.get('amount', 0):,.0f}" if t.get('amount') else ''}</td>
            <td class="px-4 py-3 text-sm text-gray-600">{t.get('reason', '')}</td>
        </tr>
        """

    # 统计卡片颜色
    pnl_color = 'text-green-600' if stats['total_pnl'] >= 0 else 'text-red-600'
    wr_color = 'text-green-600' if stats['win_rate'] >= 50 else 'text-red-600'

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>情绪反弹策略回测报告</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        body {{ font-family: 'Inter', sans-serif; background: linear-gradient(135deg, #1e3a5f 0%, #2d1b69 100%); min-height: 100vh; }}
        .glass {{ background: rgba(255, 255, 255, 0.95); backdrop-filter: blur(10px); border-radius: 16px; box-shadow: 0 8px 32px rgba(0,0,0,0.1); }}
        .stat-card {{ transition: transform 0.2s; }}
        .stat-card:hover {{ transform: translateY(-2px); }}
        .chart-container {{ height: 400px; }}
    </style>
</head>
<body class="p-4 md:p-8">
    <div class="max-w-7xl mx-auto">
        <!-- 标题 -->
        <div class="glass p-6 mb-6">
            <div class="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
                <div>
                    <h1 class="text-2xl md:text-3xl font-bold text-gray-800">📊 情绪反弹策略回测报告</h1>
                    <p class="text-gray-500 mt-1">标的: {engine.etf_code} (中证2000ETF) | 回测截止: {end_date}</p>
                </div>
                <div class="flex gap-3">
                    <a href="../index.html" class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">← 返回首页</a>
                </div>
            </div>
        </div>

        <!-- 策略说明 -->
        <div class="glass p-6 mb-6">
            <h2 class="text-xl font-bold text-gray-800 mb-4">📋 策略逻辑</h2>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div>
                    <h3 class="font-semibold text-green-700 mb-2">🟢 买入</h3>
                    <ul class="text-gray-600 space-y-1 text-sm">
                        <li>• 当日 J13数量 ≥ 过去{engine.lookback_days}天的{engine.percentile_threshold*100:.0f}%分位数</li>
                        <li>• 倍投阶梯: {' → '.join([f'¥{int(x):,}' for x in engine.investment_levels])}</li>
                        <li>• 用当日收盘价模拟买入</li>
                    </ul>
                </div>
                <div>
                    <h3 class="font-semibold text-red-700 mb-2">🔴 卖出</h3>
                    <ul class="text-gray-600 space-y-1 text-sm">
                        <li>• 红柱满4根 → 卖一半</li>
                        <li>• 连续3根绿柱 → 全卖（容忍2天回调）</li>
                        <li>• 基于知行砖形图指标判断红/绿</li>
                    </ul>
                </div>
            </div>
        </div>

        <!-- 关键指标 -->
        <div class="glass p-6 mb-6">
            <h2 class="text-xl font-bold text-gray-800 mb-4">📈 回测关键指标</h2>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div class="glass p-4 stat-card border-l-4 border-blue-500">
                    <div class="text-sm text-gray-500">完整交易次数</div>
                    <div class="text-2xl font-bold text-blue-600">{stats['total_trades']}</div>
                </div>
                <div class="glass p-4 stat-card border-l-4 {('border-green-500' if stats['win_rate'] >= 50 else 'border-red-500')}">
                    <div class="text-sm text-gray-500">胜率</div>
                    <div class="text-2xl font-bold {wr_color}">{stats['win_rate']:.1f}%</div>
                </div>
                <div class="glass p-4 stat-card border-l-4 {('border-green-500' if stats['total_pnl'] >= 0 else 'border-red-500')}">
                    <div class="text-sm text-gray-500">总收益</div>
                    <div class="text-2xl font-bold {pnl_color}">{'+' if stats['total_pnl'] >= 0 else ''}{stats['total_pnl']:.2f} 元</div>
                </div>
                <div class="glass p-4 stat-card border-l-4 border-purple-500">
                    <div class="text-sm text-gray-500">最大回撤</div>
                    <div class="text-2xl font-bold text-red-600">{stats['max_drawdown']:.2f} 元</div>
                </div>
                <div class="glass p-4 stat-card border-l-4 border-orange-500">
                    <div class="text-sm text-gray-500">平均每笔收益</div>
                    <div class="text-2xl font-bold {'text-green-600' if stats['avg_pnl'] >= 0 else 'text-red-600'}">{'+' if stats['avg_pnl'] >= 0 else ''}{stats['avg_pnl']:.2f} 元</div>
                </div>
                <div class="glass p-4 stat-card border-l-4 border-teal-500">
                    <div class="text-sm text-gray-500">平均持仓天数</div>
                    <div class="text-2xl font-bold text-teal-600">{stats['avg_hold_days']:.1f} 天</div>
                </div>
                <div class="glass p-4 stat-card border-l-4 border-green-500">
                    <div class="text-sm text-gray-500">最大单笔盈利</div>
                    <div class="text-2xl font-bold text-green-600">+{stats['max_win']:.2f} 元</div>
                </div>
                <div class="glass p-4 stat-card border-l-4 border-red-500">
                    <div class="text-sm text-gray-500">最大单笔亏损</div>
                    <div class="text-2xl font-bold text-red-600">{stats['max_loss']:.2f} 元</div>
                </div>
            </div>
            <div class="mt-4 grid grid-cols-2 md:grid-cols-3 gap-4">
                <div class="glass p-3 stat-card">
                    <div class="text-sm text-gray-500">买入信号触发</div>
                    <div class="text-lg font-bold text-gray-700">{stats['buy_signal_count']} 次</div>
                </div>
                <div class="glass p-3 stat-card">
                    <div class="text-sm text-gray-500">累计投入金额</div>
                    <div class="text-lg font-bold text-gray-700">¥{stats['total_invested']:,.0f}</div>
                </div>
                <div class="glass p-3 stat-card">
                    <div class="text-sm text-gray-500">平均每笔收益率</div>
                    <div class="text-lg font-bold {'text-green-600' if stats['avg_pnl_pct'] >= 0 else 'text-red-600'}">{'+' if stats['avg_pnl_pct'] >= 0 else ''}{stats['avg_pnl_pct']:.2f}%</div>
                </div>
            </div>
        </div>

        <!-- 累计收益曲线 -->
        <div class="glass p-6 mb-6">
            <h2 class="text-xl font-bold text-gray-800 mb-4">📈 累计收益曲线</h2>
            <div id="pnlChart" class="chart-container"></div>
        </div>

        <!-- J13数量走势 -->
        <div class="glass p-6 mb-6">
            <h2 class="text-xl font-bold text-gray-800 mb-4">📉 J13每日数量走势</h2>
            <div id="j13Chart" class="chart-container"></div>
        </div>

        <!-- ETF K线 + 买卖点 -->
        <div class="glass p-6 mb-6">
            <h2 class="text-xl font-bold text-gray-800 mb-4">📈 {engine.etf_code} K线 + 交易标记</h2>
            <div id="klineChart" class="chart-container" style="height:450px;"></div>
        </div>

        <!-- 完整交易明细 -->
        <div class="glass p-6 mb-6">
            <h2 class="text-xl font-bold text-gray-800 mb-4">📋 完整交易明细（买入→卖出配对）</h2>
            {f'<div class="overflow-x-auto"><table class="w-full text-sm"><thead class="bg-gray-50"><tr><th class="px-4 py-3 text-left">#</th><th class="px-4 py-3 text-left">买入日期</th><th class="px-4 py-3 text-left">买入价</th><th class="px-4 py-3 text-left">投入金额</th><th class="px-4 py-3 text-left">卖出日期</th><th class="px-4 py-3 text-left">卖出价</th><th class="px-4 py-3 text-left">盈亏</th><th class="px-4 py-3 text-left">收益率</th><th class="px-4 py-3 text-left">持仓天数</th></tr></thead><tbody class="divide-y divide-gray-200">{round_trip_rows}</tbody></table></div>' if engine.round_trips else '<p class="text-gray-500">暂无完整交易记录</p>'}
        </div>

        <!-- 全部信号明细 -->
        <div class="glass p-6 mb-6">
            <h2 class="text-xl font-bold text-gray-800 mb-4">📜 全部交易信号</h2>
            {f'<div class="overflow-x-auto"><table class="w-full text-sm"><thead class="bg-gray-50"><tr><th class="px-4 py-3 text-left">日期</th><th class="px-4 py-3 text-left">操作</th><th class="px-4 py-3 text-left">类型</th><th class="px-4 py-3 text-left">价格</th><th class="px-4 py-3 text-left">金额</th><th class="px-4 py-3 text-left">原因</th></tr></thead><tbody class="divide-y divide-gray-200">{signal_rows}</tbody></table></div>' if engine.trades else '<p class="text-gray-500">暂无交易信号</p>'}
        </div>

        <!-- 砖形图 -->
        <div class="glass p-6 mb-6">
            <h2 class="text-xl font-bold text-gray-800 mb-4">🧱 知行砖形图</h2>
            <div id="brickChart" class="chart-container"></div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
    <script>
    // ─── 累计收益曲线 ───
    var pc = echarts.init(document.getElementById('pnlChart'));
    pc.setOption({{
        title:{{text:'累计收益(元)',left:'center',textStyle:{{fontSize:14}}}},
        tooltip:{{trigger:'axis'}},
        grid:{{left:'10%',right:'5%',top:'50px',bottom:'40px'}},
        xAxis:{{type:'category',data:{json.dumps([f'第{i}笔' for i in range(len(engine.round_trips)+1)])}}},
        yAxis:{{type:'value'}},
        series:[{{type:'line',data:{json.dumps([round(v, 2) for v in cum_pnl])},
            areaStyle:{{opacity:0.15}},
            lineStyle:{{width:2}},
            itemStyle:{{color:'#3b82f6'}}}}],
        markLine:{{data:[{{yAxis:0,lineStyle:{{color:'#999',type:'dashed'}}}}]}}
    }});
    window.addEventListener('resize',function(){{pc.resize();}});

    // ─── J13数量走势 ───
    var jc = echarts.init(document.getElementById('j13Chart'));
    var j13Dates = {json.dumps(j13_dates)};
    var j13Vals = {json.dumps([int(v) for v in j13_vals])};
    // 计算90%分位数阈值线
    var j13Arr = j13Vals.slice();
    j13Arr.sort(function(a,b){{return a-b;}});
    var p90Idx = Math.floor(j13Arr.length * 0.9);
    var p90Val = j13Arr[Math.min(p90Idx, j13Arr.length-1)];
    jc.setOption({{
        title:{{text:'J13每日数量(只)',left:'center',textStyle:{{fontSize:14}}}},
        tooltip:{{trigger:'axis'}},
        grid:{{left:'10%',right:'5%',top:'50px',bottom:'60px'}},
        dataZoom:[{{type:'inside'}},{{type:'slider',start:70,end:100}}],
        xAxis:{{type:'category',data:j13Dates}},
        yAxis:{{type:'value'}},
        series:[{{
            type:'bar',
            data:j13Vals,
            itemStyle:{{
                color: function(params) {{
                    return params.value >= p90Val ? '#ef4444' : '#3b82f6';
                }}
            }}
        }}],
        markLine:{{data:[{{yAxis:p90Val,name:'90%分位',lineStyle:{{color:'#f59e0b',type:'dashed'}}}}]}}
    }});
    window.addEventListener('resize',function(){{jc.resize();}});

    // ─── ETF K线 + 买卖标记 ───
    var kc = echarts.init(document.getElementById('klineChart'));
    var etfDates = {json.dumps(etf_dates)};
    var candleData = {json.dumps(candlestick)};
    // 买卖标记
    var buyMarks = {json.dumps(buy_points)};
    var sellMarks = {json.dumps(sell_points)};
    var markPoints = [];
    buyMarks.forEach(function(b){{
        var idx = etfDates.indexOf(b.date);
        if(idx>=0) markPoints.push({{name:'买入L'+b.level,coord:[idx, b.price],value:'B'+b.level,
            itemStyle:{{color:'#22c55e'}},symbol:'triangle',symbolSize:12}});
    }});
    sellMarks.forEach(function(s){{
        var idx = etfDates.indexOf(s.date);
        if(idx>=0) markPoints.push({{name:'卖出',coord:[idx, s.price],value:'S',
            itemStyle:{{color:'#ef4444'}},symbol:'path://M-10,10L10,-10M10,10L-10,-10',symbolSize:12}});
    }});
    kc.setOption({{
        title:{{text:'{engine.etf_code} K线走势 + 交易标记',left:'center',textStyle:{{fontSize:14}}}},
        tooltip:{{trigger:'axis',axisPointer:{{type:'cross'}},
            formatter:function(params){{
                var c=params[0];if(!c)return '';
                var d=c.dataIndex;var raw=candleData[d];
                return c.axisValue+'<br/>开:'+raw[0].toFixed(3)+' 收:'+raw[1].toFixed(3)+'<br/>低:'+raw[2].toFixed(3)+' 高:'+raw[3].toFixed(3);
            }}
        }},
        grid:{{left:'10%',right:'5%',top:'50px',bottom:'60px'}},
        dataZoom:[{{type:'inside'}},{{type:'slider',start:70,end:100}}],
        xAxis:{{type:'category',data:etfDates}},
        yAxis:{{type:'value',scale:true}},
        series:[{{
            type:'candlestick',
            data:candleData,
            itemStyle:{{color:'#ef4444',color0:'#22c55e',borderColor:'#ef4444',borderColor0:'#22c55e'}},
            markPoint:{{
                data:markPoints,
                label:{{fontSize:10}}
            }}
        }}]
    }});
    window.addEventListener('resize',function(){{kc.resize();}});

    // ─── 砖形图 ───
    var bc = echarts.init(document.getElementById('brickChart'));
    var bVals = {json.dumps([round(float(v), 4) for v in brick_data['zhixing_brick'].tolist()] if 'zhixing_brick' in brick_data.columns else [])};
    var bRise = {json.dumps([bool(v) for v in brick_data['zhixing_brick_rising'].tolist()] if 'zhixing_brick_rising' in brick_data.columns else [])};
    var bDates = etfDates;
    var brickData = [];
    for (var i = 0; i < bVals.length; i++) {{
        var prev = i > 0 ? bVals[i-1] : bVals[i];
        brickData.push({{value: [i, Math.min(prev, bVals[i]), Math.max(prev, bVals[i]), bRise[i], bDates[i]]}});
    }}
    bc.setOption({{
        title:{{text:'知行砖形图',left:'center',textStyle:{{fontSize:14}}}},
        tooltip:{{formatter:function(p){{var v=p.value;return v[4]+'<br/>砖值:'+bVals[p.dataIndex].toFixed(3)+'<br/>方向:'+(v[3]?'<span style="color:#ef4444">红(上升)</span>':'<span style="color:#22c55e">绿(下降)</span>');}}}},
        grid:{{left:'8%',right:'8%',top:'40px',bottom:'40px'}},
        xAxis:{{type:'category',data:bDates,axisLabel:{{show:false}},axisTick:{{show:false}}}},
        yAxis:{{type:'value',scale:true}},
        dataZoom:[{{type:'inside'}},{{type:'slider',start:Math.max(0,100-30/bVals.length*100),end:100}}],
        series:[{{
            type:'custom',name:'砖形图',data:brickData,
            renderItem:function(params,api){{
                var idx=api.value(0),low=api.value(1),high=api.value(2),rise=api.value(3);
                var pLow=api.coord([idx,low]);
                var pHigh=api.coord([idx,high]);
                var bw=api.size([1,0])[0]*0.82;
                if(bw<4)bw=4;
                var x0=pLow[0]-bw/2;
                var yTop=pHigh[1];
                var h=pLow[1]-pHigh[1];
                if(h<2)h=2;
                var fillColor=rise?'#ef4444':'#22c55e';
                return{{type:'rect',shape:{{x:x0,y:yTop,width:bw,height:Math.max(h,2)}},
                    style:{{fill:fillColor,stroke:rise?'#dc2626':'#16a34a',lineWidth:1}},
                    emphasis:{{style:{{fill:rise?'#fca5a5':'#86efac'}}}}}};
            }}
        }}]
    }});
    window.addEventListener('resize',function(){{bc.resize();}});
    </script>
</body>
</html>"""

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    logging.info(f"回测报告已生成: {output_path}")


# ═══════════════════════════════════════════════════════════════
#  5. 主流程
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='情绪反弹策略回测')
    parser.add_argument('--days', type=int, default=250, help='回测交易日数（默认250，约一年）')
    parser.add_argument('--etf', type=str, default='563300.SH', help='ETF代码')
    parser.add_argument('--output', type=str, default=None, help='报告输出路径')
    args = parser.parse_args()

    end_date = datetime.now().strftime('%Y%m%d')
    output_path = args.output or os.path.join('html', f'backtest_sentiment_{end_date}', 'index.html')

    print('\n' + '=' * 70)
    print(f'📊 情绪反弹策略回测 | 标的: {args.etf} | 回测天数: {args.days}')
    print('=' * 70)

    # 初始化数据管理器
    dm = DataManager()

    # 1. 获取J13数据
    print('\n[1/4] 获取J13每日数量...')
    j13_data = get_j13_daily_counts(dm, end_date, args.days)
    if j13_data.empty:
        print('❌ J13数据为空，无法继续')
        return

    # 2. 获取ETF数据
    print('\n[2/4] 获取ETF数据...')
    etf_data = get_etf_data(args.etf, end_date, args.days)
    if etf_data.empty:
        print('❌ ETF数据为空，无法继续')
        return

    # 3. 计算砖型图
    print('\n[3/4] 计算知行砖形图...')
    brick_data = calculate_zhixing_brick(etf_data)

    # 4. 运行回测
    print('\n[4/4] 运行回测...')
    engine = BacktestEngine(etf_code=args.etf)
    engine.run(j13_data, etf_data, brick_data)

    # 统计
    stats = calc_statistics(engine.round_trips, engine.trades)

    # 打印结果
    print('\n' + '=' * 70)
    print('📊 回测结果')
    print('=' * 70)
    print(f"  回测区间: {j13_data['trade_date'].iloc[0]} ~ {j13_data['trade_date'].iloc[-1]}")
    print(f"  完整交易: {stats['total_trades']} 笔 (盈利 {stats['win_count']}, 亏损 {stats['lose_count']})")
    print(f"  胜率: {stats['win_rate']:.1f}%")
    print(f"  总收益: {'+' if stats['total_pnl']>=0 else ''}{stats['total_pnl']:.2f} 元")
    print(f"  平均每笔: {'+' if stats['avg_pnl']>=0 else ''}{stats['avg_pnl']:.2f} 元 ({'+' if stats['avg_pnl_pct']>=0 else ''}{stats['avg_pnl_pct']:.2f}%)")
    print(f"  最大单笔盈利: +{stats['max_win']:.2f} 元")
    print(f"  最大单笔亏损: {stats['max_loss']:.2f} 元")
    print(f"  最大回撤: {stats['max_drawdown']:.2f} 元")
    print(f"  平均持仓: {stats['avg_hold_days']:.1f} 天")
    print(f"  买入信号: {stats['buy_signal_count']} 次")
    print(f"  累计投入: ¥{stats['total_invested']:,.0f}")

    # 生成报告
    generate_backtest_report(engine, stats, j13_data, etf_data, brick_data, end_date, output_path)

    print(f"\n✅ 回测完成! 报告已保存: {output_path}")


if __name__ == '__main__':
    main()
