#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
直接每周定投策略 (Plain Weekly DCA)

无条件定投：不判断周线 J 值、不做止盈止损卖出，
每周首个交易日以开盘价买入固定金额，长期买入持有。

适用标的：海外宽基 QDII ETF（纳指100、标普500、日经225、德国DAX）。
这些标的跟踪海外市场，A 股择时指标（周线KDJ/知行多空线）参考意义有限，
故采用纯定投、买入持有模式。

接口与 V1/V6 基座保持一致（供 run_backtest_from_config 调用）：
- backtest(daily_df, weekly_df, backtest_start)
- calc_nav_curve(daily_df)
- get_next_action(daily_df, weekly_df)
- 状态持久化 save_state / load_state
"""

from static_site import write_report

import json
import logging
import os

import numpy as np
import pandas as pd


class PlainWeeklyDCA:
    """直接每周定投：每周首个交易日开盘买入固定金额，不止盈不止损"""

    def __init__(self, name: str, base_amount: float = 1000,
                 state_file: str = None):
        self.name = name
        self.base_amount = base_amount
        self.state_file = state_file or f'dca_state_{name}.json'
        self.trades = []
        self.shares = 0.0
        self.total_invested = 0.0
        self.total_sell_amount = 0.0
        self.load_state()

    @property
    def avg_cost(self) -> float:
        if self.shares <= 0:
            return 0.0
        return self.total_invested / self.shares

    def get_avg_cost(self) -> float:
        return self.avg_cost

    # ---- 状态持久化 ----
    def load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                self.trades = state.get('trades', [])
                self.shares = state.get('shares', 0.0)
                self.total_invested = state.get('total_invested', 0.0)
                self.total_sell_amount = state.get('total_sell_amount', 0.0)
                logging.info(f"[{self.name}] 加载状态: {len(self.trades)}笔交易, "
                             f"总投入={self.total_invested:.2f}")
            except Exception as e:
                logging.warning(f"[{self.name}] 加载状态失败: {e}")

    def save_state(self):
        state = {
            'name': self.name,
            'base_amount': self.base_amount,
            'trades': self.trades,
            'shares': self.shares,
            'total_invested': self.total_invested,
            'total_sell_amount': self.total_sell_amount,
            'sell_version': 'plain',
        }
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, ensure_ascii=False, indent=2, default=float)
        except Exception as e:
            logging.error(f"[{self.name}] 保存状态失败: {e}")

    @staticmethod
    def _week_key(date_str: str) -> str:
        dt = pd.to_datetime(str(date_str), format='%Y%m%d')
        iso = dt.isocalendar()
        return f"{iso.year}_{iso.week:02d}"

    def _buy(self, date: str, price: float, amount: float):
        buy_shares = amount / price
        self.shares += buy_shares
        self.total_invested += amount
        self.trades.append({
            'date': date, 'action': 'BUY', 'price': round(price, 4),
            'amount': round(amount, 2), 'shares': round(buy_shares, 4),
            'round': 1, 'reason': '每周定投',
            'total_shares': round(self.shares, 4),
            'avg_cost': round(self.total_invested / self.shares, 4),
        })

    def backtest(self, daily_df: pd.DataFrame, weekly_df: pd.DataFrame = None,
                 backtest_start: str = None):
        logging.info(f"\n{'='*70}")
        logging.info(f"  回测策略: {self.name} (直接每周定投)")
        logging.info(f"  每周首个交易日开盘买入 {self.base_amount} 元，不止盈不止损")
        if backtest_start:
            logging.info(f"  回测起始: {backtest_start}")
        logging.info(f"{'='*70}")

        df = daily_df.copy()
        if backtest_start:
            df = df[df['trade_date'] >= backtest_start]
        df = df.sort_values('trade_date').reset_index(drop=True)
        if df.empty:
            logging.warning(f"[{self.name}] 回测区间内无数据")
            return

        dt = pd.to_datetime(df['trade_date'], format='%Y%m%d')
        iso = dt.dt.isocalendar()
        df['_week_key'] = iso.year.astype(str) + '_' + \
            iso.week.astype(str).str.zfill(2)

        # 重置状态（与 V1/V6 一致：backtest 从头完整重放）
        self.trades = []
        self.shares = 0.0
        self.total_invested = 0.0
        self.total_sell_amount = 0.0

        buy_count = 0
        for _, grp in df.groupby('_week_key', sort=True):
            first = grp.sort_values('trade_date').iloc[0]
            price = first.get('open_qfq')
            if pd.isna(price) or price <= 0:
                price = first.get('close_qfq')
            if pd.isna(price) or price <= 0:
                continue
            self._buy(str(first['trade_date']), float(price),
                      float(self.base_amount))
            buy_count += 1

        last_date = str(df['trade_date'].iloc[-1])
        last_price = float(df['close_qfq'].iloc[-1])
        market_value = self.shares * last_price
        total_return = market_value - self.total_invested
        return_pct = total_return / self.total_invested * 100 \
            if self.total_invested > 0 else 0
        logging.info(f"[{self.name}] 回测完成: {buy_count}周买入, "
                     f"总投入={self.total_invested:.2f}, "
                     f"持有={self.shares:.4f}份, 市值={market_value:.2f}, "
                     f"收益率={return_pct:+.2f}%")
        self.save_state()

    def calc_nav_curve(self, daily_df: pd.DataFrame) -> pd.DataFrame:
        trade_map = {}
        for t in self.trades:
            d = t['date']
            if d not in trade_map:
                trade_map[d] = {'buy_amount': 0, 'sell_amount': 0,
                                'sell_profit': 0}
            trade_map[d]['buy_amount'] += t.get('amount', 0)
        result = []
        cum_invested = 0
        cum_sell_profit = 0
        shares = 0
        avg_cost = 0
        for _, row in daily_df.iterrows():
            d = row['trade_date']
            price = row['close_qfq']
            if d in trade_map:
                tm = trade_map[d]
                if tm['buy_amount'] > 0:
                    buy_shares = tm['buy_amount'] / price
                    old_cost = shares * avg_cost
                    shares += buy_shares
                    cum_invested += tm['buy_amount']
                    avg_cost = (old_cost + tm['buy_amount']) / shares \
                        if shares > 0 else 0
                if tm['sell_amount'] > 0:
                    sell_ratio = tm['sell_amount'] / (shares * price) \
                        if shares * price > 0 else 0
                    cost_removed = shares * avg_cost * sell_ratio
                    shares *= (1 - sell_ratio)
                    cum_sell_profit += tm['sell_profit']
                    if shares > 0:
                        avg_cost = (shares * avg_cost - cost_removed) / shares
                    else:
                        avg_cost = 0
            market_value = shares * price
            total_value = market_value + cum_sell_profit
            nav = total_value / cum_invested if cum_invested > 0 else 1.0
            result.append({
                'trade_date': d,
                'nav': round(nav, 6),
                'cum_invested': round(cum_invested, 2),
                'market_value': round(market_value, 2),
                'total_value': round(total_value, 2),
            })
        return pd.DataFrame(result)

    def get_next_action(self, daily_df: pd.DataFrame,
                        weekly_df: pd.DataFrame = None) -> dict:
        if daily_df is None or daily_df.empty:
            return {
                'action': 'unknown', 'action_label': '无数据',
                'action_color': '#95a5a6', 'action_detail': '无行情数据',
                'last_date': '', 'last_price': 0,
                'weekly_j': None, 'weekly_k': None, 'weekly_d': None,
                'avg_cost': None, 'loss_pct': 0, 'positions': [],
            }
        df = daily_df.sort_values('trade_date').reset_index(drop=True)
        last = df.iloc[-1]
        last_date = str(last['trade_date'])
        last_price = float(last['close_qfq'])
        current_week = self._week_key(last_date)
        bought_this_week = any(self._week_key(t['date']) == current_week
                               for t in self.trades)
        market_value = self.shares * last_price
        total_return = market_value - self.total_invested
        return_pct = total_return / self.total_invested * 100 \
            if self.total_invested > 0 else 0
        profit_pct = (last_price - self.avg_cost) / self.avg_cost * 100 \
            if self.avg_cost > 0 else 0
        avg_cost = self.avg_cost

        if bought_this_week:
            action_label = f'每周定投·本周已买入'
        else:
            action_label = f'每周定投·本周待买入'
        action_detail = (
            f"直接每周定投：每周首个交易日开盘买入{self.base_amount:.0f}元，"
            f"无J值门控、不止盈止损，长期持有。"
            f"累计投入{self.total_invested:.0f}元，"
            f"持有市值{market_value:.0f}元({return_pct:+.2f}%)"
        )
        pos_info = {
            'round_id': 1,
            'shares': round(self.shares, 4),
            'avg_cost': round(avg_cost, 4) if avg_cost > 0 else None,
            'sell_stage': 0,
            'j_peak': 0,
            'dca_active': True,
            'action': 'buy',
            'action_label': action_label,
            'action_color': '#e74c3c',
            'action_detail': action_detail,
            'total_invested': round(self.total_invested, 2),
            'total_return': round(total_return, 2),
            'return_pct': round(return_pct, 2),
            'last_price': round(last_price, 4),
        }
        result = {
            'last_date': last_date,
            'last_price': round(last_price, 4),
            'weekly_j': None,
            'weekly_k': None,
            'weekly_d': None,
            'avg_cost': round(avg_cost, 4) if avg_cost > 0 else None,
            'loss_pct': round(profit_pct, 2),
            'shares': round(self.shares, 4),
            'action': 'buy',
            'action_label': action_label,
            'action_color': '#e74c3c',
            'action_detail': action_detail,
            'positions': [pos_info],
        }
        return result


def _darken_color(hex_color: str) -> str:
    hex_color = hex_color.lstrip('#')
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), \
        int(hex_color[4:6], 16)
    r, g, b = int(r * 0.55), int(g * 0.55), int(b * 0.55)
    return f'#{r:02x}{g:02x}{b:02x}'


def generate_plain_dca_report(strategy: PlainWeeklyDCA,
                              daily_df: pd.DataFrame,
                              weekly_df: pd.DataFrame,
                              output_file: str = None) -> str:
    """直接每周定投回测报告（价格走势 + 投入/市值曲线 + 交易记录）"""
    if output_file is None:
        output_file = f'dca_report_{strategy.name}.html'
    df = daily_df.sort_values('trade_date').reset_index(drop=True)
    dates = df['trade_date'].tolist()
    prices = [None if pd.isna(v) else round(float(v), 4)
              for v in df['close_qfq'].tolist()]
    buy_points = [[t['date'], t['price']] for t in strategy.trades]

    nav_df = strategy.calc_nav_curve(daily_df)
    nav_dates = nav_df['trade_date'].tolist()
    nav_invested = [round(float(v), 2) for v in nav_df['cum_invested'].tolist()]
    nav_value = [round(float(v), 2) for v in nav_df['total_value'].tolist()]

    na = strategy.get_next_action(daily_df, weekly_df)
    market_value = strategy.shares * na['last_price']
    total_return = market_value - strategy.total_invested
    return_pct = total_return / strategy.total_invested * 100 \
        if strategy.total_invested > 0 else 0
    first_buy = strategy.trades[0]['date'] if strategy.trades else '-'
    ret_color = '#e74c3c' if return_pct < 0 else '#27ae60'

    trade_rows = ''
    for t in strategy.trades:
        trade_rows += f"""
<tr>
    <td>{t['date']}</td>
    <td><span class="buy-tag">买入</span></td>
    <td>{t['price']:.4f}</td>
    <td>{t.get('amount', 0):.2f}</td>
    <td>{t.get('shares', 0):.4f}</td>
    <td>{t.get('total_shares', 0):.4f}</td>
    <td>{t.get('avg_cost', 0):.4f}</td>
</tr>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>直接每周定投 - {strategy.name}</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>
body {{ font-family: 'Microsoft YaHei', Arial, sans-serif; background: #f0f2f5; margin: 0; padding: 20px; }}
.container {{ max-width: 1200px; margin: 0 auto; }}
h1 {{ text-align: center; color: #333; font-size: 24px; margin-bottom: 5px; }}
.subtitle {{ text-align: center; color: #666; margin-bottom: 20px; font-size: 14px; }}
.summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 20px; }}
.card {{ background: white; border-radius: 8px; padding: 15px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
.card-label {{ color: #666; font-size: 12px; margin-bottom: 5px; }}
.card-value {{ font-size: 20px; font-weight: bold; color: #333; }}
.action-box {{ background: white; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); border-left: 5px solid #e74c3c; }}
.action-label {{ font-size: 22px; font-weight: bold; color: #e74c3c; }}
.action-detail {{ font-size: 14px; color: #555; line-height: 1.8; padding: 10px; background: #fafafa; border-radius: 4px; margin-top: 10px; }}
.chart {{ background: white; border-radius: 8px; padding: 15px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
.trade-table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
.trade-table th {{ background: #f5f7fa; padding: 8px; text-align: left; border-bottom: 2px solid #ddd; }}
.trade-table td {{ padding: 6px 8px; border-bottom: 1px solid #eee; }}
.trade-table tr:hover {{ background: #f9f9f9; }}
.buy-tag {{ background: #e74c3c; color: white; padding: 2px 6px; border-radius: 3px; font-size: 11px; }}
</style>
</head>
<body>
<div class="container">
<h1>直接每周定投回测报告 — {strategy.name}</h1>
<div class="subtitle">{strategy.name} | 每周首个交易日开盘买入 {strategy.base_amount:.0f} 元 | 无J值门控 · 不止盈止损 · 长期持有</div>
<div class="action-box">
    <div class="action-label">{na['action_label']}</div>
    <div class="action-detail">{na['action_detail']}</div>
</div>
<div class="summary">
<div class="card">
    <div class="card-label">总投入</div>
    <div class="card-value">{strategy.total_invested:.2f}</div>
</div>
<div class="card">
    <div class="card-label">持有市值</div>
    <div class="card-value">{market_value:.2f}</div>
</div>
<div class="card">
    <div class="card-label">累计收益率</div>
    <div class="card-value" style="color: {ret_color};">{return_pct:+.2f}%</div>
</div>
<div class="card">
    <div class="card-label">持有份额</div>
    <div class="card-value">{strategy.shares:.2f} 份</div>
</div>
<div class="card">
    <div class="card-label">平均成本</div>
    <div class="card-value">{strategy.avg_cost:.4f}</div>
</div>
<div class="card">
    <div class="card-label">买入次数</div>
    <div class="card-value">{len(strategy.trades)}</div>
</div>
<div class="card">
    <div class="card-label">首次买入</div>
    <div class="card-value">{first_buy}</div>
</div>
</div>
<div class="chart">
<h3 style="margin-top:0">累计投入 vs 持有市值（含浮盈）</h3>
<div id="nav_chart" style="height:400px;"></div>
</div>
<div class="chart">
<h3 style="margin-top:0">价格走势 + 每周买入标记</h3>
<div id="price_chart" style="height:450px;"></div>
</div>
<div class="chart">
<h3 style="margin-top:0">交易记录</h3>
<div style="overflow-x:auto; max-height: 600px; overflow-y: auto;">
<table class="trade-table">
<tr>
    <th>日期</th><th>操作</th><th>价格</th><th>金额</th>
    <th>份额</th><th>累计持仓</th><th>平均成本</th>
</tr>{trade_rows}
</table>
</div>
</div>
</div>
<script>
var navDates = {json.dumps(nav_dates)};
var navInvested = {json.dumps(nav_invested)};
var navValue = {json.dumps(nav_value)};
var navChart = echarts.init(document.getElementById('nav_chart'));
navChart.setOption({{
    tooltip: {{ trigger: 'axis' }},
    legend: {{ data: ['累计投入', '持有市值'], bottom: 0 }},
    grid: {{ left: 80, right: 30, top: 30, bottom: 60 }},
    xAxis: {{ type: 'category', data: navDates, axisLabel: {{ rotate: 45, fontSize: 10 }} }},
    yAxis: {{ type: 'value', scale: true }},
    dataZoom: [
        {{ type: 'inside', start: 0, end: 100 }},
        {{ type: 'slider', start: 0, end: 100 }}
    ],
    series: [
        {{ name: '累计投入', type: 'line', data: navInvested,
           lineStyle: {{ width: 2, color: '#f39c12' }}, itemStyle: {{ color: '#f39c12' }}}},
        {{ name: '持有市值', type: 'line', data: navValue,
           lineStyle: {{ width: 2, color: '#3498db' }}, itemStyle: {{ color: '#3498db' }}}}
    ]
}});
var priceDates = {json.dumps(dates)};
var priceData = {json.dumps(prices)};
var buyPoints = {json.dumps(buy_points)};
var priceChart = echarts.init(document.getElementById('price_chart'));
priceChart.setOption({{
    tooltip: {{ trigger: 'axis' }},
    legend: {{ data: ['收盘价', '每周买入'], bottom: 0 }},
    grid: {{ left: 80, right: 30, top: 30, bottom: 60 }},
    xAxis: {{ type: 'category', data: priceDates, axisLabel: {{ rotate: 45, fontSize: 10 }} }},
    yAxis: {{ type: 'value', scale: true }},
    dataZoom: [
        {{ type: 'inside', start: 0, end: 100 }},
        {{ type: 'slider', start: 0, end: 100 }}
    ],
    series: [
        {{ name: '收盘价', type: 'line', data: priceData,
           lineStyle: {{ width: 1.2, color: '#333' }}, itemStyle: {{ color: '#333' }},
           showSymbol: false }},
        {{ name: '每周买入', type: 'scatter', data: buyPoints,
           symbolSize: 7, itemStyle: {{ color: '#e74c3c', opacity: 0.75 }} }}
    ]
}});
window.addEventListener('resize', function() {{ navChart.resize(); priceChart.resize(); }});
</script>
</body>
</html>"""
    write_report(output_file, html)
    logging.info(f"直接每周定投报告已保存: {output_file}")
    return output_file


__all__ = ['PlainWeeklyDCA', 'generate_plain_dca_report']
