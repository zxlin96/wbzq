#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
存量资金 · 组合仓位策略（Portfolio Trim）—— "满仓打底 + 过热减半 + 超卖补满"

与 V1~V6H 的根本区别
-------------------
现有策略都是**定投现金流**框架：每期固定金额、多轮次、每轮独立预算。
本策略是**存量资金**框架：一笔初始本金，仓位在 100% / keep_ratio 之间切换，
没有轮次、没有定投、不看 base_amount。

规则
----
1. 回测起点：以初始本金一次性满仓买入（建仓成本计入）
2. 周线 J > trim_j（默认 100） 且 当前仓位 > keep_ratio → 减到 keep_ratio（默认 50%）
3. 周线 J < refill_j（默认 0） 且 当前仓位 < 100%          → 补满
4. 其余时间持有不动

执行口径
--------
- 信号只用**已收盘周线**，次周首个交易日**开盘**成交（与生产同口径）
- 单边成本 cost（默认 0.05%）
- 减仓/补满按"目标仓位比例"计算，不是按份额数

接口对齐
--------
`run_backtest_from_config` / `_generate_dca_summary` / 邮件汇总依赖以下成员：
name / shares / total_invested / total_sell_amount / trades /
backtest() / calc_nav_curve() / get_next_action()

其中 calc_nav_curve 的 `total_value` 做了口径适配：
    total_value = 组合总市值(现金+持仓) − 累计卖出金额
这样汇总侧 `total_value + total_sell_amount − cum_invested` 才等于真实利润
（V2 家族的 total_value 是"市值+已实现利润"，两者语义不同，勿混用）。
"""

import logging
import os

import pandas as pd

from weekly_dca_strategy import calc_kdj


class PortfolioTrimStrategy:
    # 净值口径标记：组合净值（本金固定），与定投系的"利润/累计投入"不可直接对比。
    # _generate_comparison_chart 会跳过该标记，避免两种口径画进同一张图。
    nav_kind = 'portfolio'

    def __init__(self,
                 name: str,
                 initial_capital: float = 100000.0,
                 trim_j: float = 100.0,
                 refill_j: float = 0.0,
                 keep_ratio: float = 0.5,
                 cost: float = 0.0005):
        self.name = name
        self.initial_capital = float(initial_capital)
        self.trim_j = float(trim_j)
        self.refill_j = float(refill_j)
        self.keep_ratio = float(keep_ratio)
        self.cost = float(cost)
        self.reset()

    # ------------------------------------------------------------ 状态
    def reset(self):
        self.shares = 0.0
        self.cash = self.initial_capital
        self.total_invested = self.initial_capital   # 存量口径：投入=初始本金
        self.total_sell_amount = 0.0
        self.total_buy_amount = 0.0
        self.trades = []
        self.start_date = None
        self.last_price = None

    @property
    def market_value(self):
        return self.shares * (self.last_price or 0.0)

    @property
    def portfolio_value(self):
        return self.cash + self.market_value

    @property
    def position_ratio(self):
        pv = self.portfolio_value
        return self.market_value / pv if pv > 0 else 0.0

    @property
    def avg_cost(self):
        bought = sum(t['shares'] for t in self.trades if t['action'] == 'BUY')
        amount = sum(t['amount'] for t in self.trades if t['action'] == 'BUY')
        return amount / bought if bought > 0 else 0.0

    # ------------------------------------------------------------ 交易
    def _trade(self, date, price, target_ratio, reason):
        """把仓位调整到 target_ratio（按组合总市值计算）。"""
        if price <= 0:
            return
        self.last_price = price    # 保证仓位计算用当前成交价，而不是上一日收盘
        total = self.cash + self.shares * price
        target_value = total * target_ratio
        cur_value = self.shares * price
        diff = target_value - cur_value
        if abs(diff) < 1.0:      # 1 元以内不折腾
            return
        if diff > 0:             # 买入
            amount = min(diff, self.cash)
            if amount <= 1.0:
                return
            shares = amount * (1 - self.cost) / price
            self.shares += shares
            self.cash -= amount
            self.total_buy_amount += amount
            self.trades.append({
                'date': date, 'action': 'BUY', 'price': round(price, 4),
                'amount': round(amount, 2), 'shares': round(shares, 4),
                'reason': reason, 'total_shares': round(self.shares, 4),
                'ratio_after': round(self.position_ratio, 4),
            })
        else:                    # 卖出
            shares = min(-diff, self.shares * price) / price
            if shares <= 0:
                return
            amount = shares * price * (1 - self.cost)
            self.shares -= shares
            self.cash += amount
            self.total_sell_amount += amount
            self.trades.append({
                'date': date, 'action': 'SELL', 'price': round(price, 4),
                'amount': round(amount, 2), 'shares': round(shares, 4),
                'reason': reason, 'total_shares': round(self.shares, 4),
                'ratio_after': round(self.position_ratio, 4),
            })

    # ------------------------------------------------------------ 回测
    @staticmethod
    def _prepare(daily_df, weekly_df):
        d = daily_df.copy()
        d['date'] = pd.to_datetime(d['trade_date'].astype(str), format='%Y%m%d')
        d = d.sort_values('date').reset_index(drop=True)
        d['week_key'] = (d['date'].dt.isocalendar().year.astype(str) + '_'
                         + d['date'].dt.isocalendar().week.astype(str).str.zfill(2))
        first_day = d.groupby('week_key')['date'].min()
        keys = list(first_day.index)
        nxt = {k: (first_day[keys[i + 1]] if i + 1 < len(keys) else pd.NaT)
               for i, k in enumerate(keys)}

        w = weekly_df.copy()
        w['date'] = pd.to_datetime(w['trade_date'].astype(str), format='%Y%m%d')
        w = calc_kdj(w).sort_values('date').reset_index(drop=True)
        w['week_key'] = (w['date'].dt.isocalendar().year.astype(str) + '_'
                         + w['date'].dt.isocalendar().week.astype(str).str.zfill(2))
        w['exec_date'] = w['week_key'].map(nxt)
        if 'week_completed' not in w.columns:
            w['week_completed'] = True
        return d, w

    def backtest(self, daily_df: pd.DataFrame, weekly_df: pd.DataFrame,
                 backtest_start: str = None):
        self.reset()
        d, w = self._prepare(daily_df, weekly_df)

        start = pd.Timestamp(backtest_start) if backtest_start else d['date'].iloc[0]
        d = d[d['date'] >= start].reset_index(drop=True)

        # 信号：已完成周线 → 次周首个交易日开盘执行
        sig = w[w['week_completed'] & w['exec_date'].notna() & w['J'].notna()]
        plan = {}
        for _, r in sig.iterrows():
            plan[r['exec_date'].normalize()] = float(r['J'])

        if d.empty:
            logging.warning(f"[{self.name}] 回测区间为空")
            return

        # 建仓：首个交易日开盘满仓
        first = d.iloc[0]
        self.start_date = first['trade_date']
        self.last_price = float(first['open_qfq'])
        self._trade(first['trade_date'], float(first['open_qfq']), 1.0,
                    f"建仓：{self.initial_capital:.0f}元满仓")

        for _, row in d.iterrows():
            td = row['date'].normalize()
            o = float(row['open_qfq'])
            c = float(row['close_qfq'])
            j = plan.get(td)
            if j is not None:
                ratio = self.position_ratio
                if j > self.trim_j and ratio > self.keep_ratio + 0.01:
                    self._trade(row['trade_date'], o, self.keep_ratio,
                                f"周线J={j:.2f}>{self.trim_j:.0f}，减到{self.keep_ratio:.0%}")
                elif j < self.refill_j and ratio < 0.99:
                    self._trade(row['trade_date'], o, 1.0,
                                f"周线J={j:.2f}<{self.refill_j:.0f}，补满")
            self.last_price = c

        # 汇总
        pv = self.portfolio_value
        profit = pv - self.initial_capital
        ret = profit / self.initial_capital * 100 if self.initial_capital else 0
        logging.info(f"[{self.name}] 组合策略回测完成："
                     f"初始{self.initial_capital:.0f} 期末{pv:.0f} "
                     f"利润{profit:.0f}({ret:.2f}%) 交易{len(self.trades)}笔 "
                     f"当前仓位{self.position_ratio:.0%}")

    # ------------------------------------------------------------ 曲线
    def calc_nav_curve(self, daily_df: pd.DataFrame) -> pd.DataFrame:
        """逐日净值。total_value 口径见模块 docstring（已适配汇总公式）。"""
        trade_map = {}
        for t in self.trades:
            k = str(t['date'])
            tm = trade_map.setdefault(k, {'buy': 0.0, 'sell': 0.0, 'buy_sh': 0.0,
                                          'sell_sh': 0.0})
            if t['action'] == 'BUY':
                tm['buy'] += t['amount']
                tm['buy_sh'] += t['shares']
            else:
                tm['sell'] += t['amount']
                tm['sell_sh'] += t['shares']

        d = daily_df.copy()
        d['date'] = pd.to_datetime(d['trade_date'].astype(str), format='%Y%m%d')
        d = d.sort_values('date').reset_index(drop=True)

        shares = 0.0
        cash = self.initial_capital
        sell_cum = 0.0
        started = False
        rows = []
        for _, row in d.iterrows():
            k = str(row['trade_date'])
            if k in trade_map and not started:
                started = True
            if started:
                tm = trade_map.get(k)
                if tm:
                    shares += tm['buy_sh'] - tm['sell_sh']
                    # 用现金流近似还原 cash（含成本）
                    cash -= tm['buy']
                    cash += tm['sell']
                    sell_cum += tm['sell']
                mv = shares * float(row['close_qfq'])
                total = cash + mv
                rows.append({
                    'trade_date': row['trade_date'],
                    'nav': round(total / self.initial_capital, 6),
                    'cum_invested': round(self.initial_capital, 2),
                    'market_value': round(mv, 2),
                    'total_value': round(total - sell_cum, 2),  # 适配汇总公式
                })
        return pd.DataFrame(rows)

    # ------------------------------------------------------------ 建议
    def get_next_action(self, daily_df: pd.DataFrame, weekly_df: pd.DataFrame):
        d, w = self._prepare(daily_df, weekly_df)
        done = w[w['week_completed'] & w['J'].notna()]
        if done.empty:
            return {}
        last_w = done.iloc[-1]
        j = float(last_w['J'])
        price = float(d['close_qfq'].iloc[-1])
        self.last_price = price

        ratio = self.position_ratio
        if j > self.trim_j and ratio > self.keep_ratio + 0.01:
            action, label, color = 'trim', f"建议减仓至{self.keep_ratio:.0%}", '#2ecc71'
            detail = (f"周线J={j:.2f}>{self.trim_j:.0f}，当前仓位{ratio:.0%}，"
                      f"下一交易日开盘减到{self.keep_ratio:.0%}")
        elif j < self.refill_j and ratio < 0.99:
            action, label, color = 'refill', '建议补满仓', '#e74c3c'
            detail = (f"周线J={j:.2f}<{self.refill_j:.0f}，当前仓位{ratio:.0%}，"
                      f"下一交易日开盘补满")
        else:
            action, label, color = 'hold', f"持有（仓位{ratio:.0%}）", '#3498db'
            detail = (f"周线J={j:.2f}，未触发{self.trim_j:.0f}/{self.refill_j:.0f}，"
                      f"维持当前仓位{ratio:.0%}")

        ret_pct = (self.portfolio_value - self.initial_capital) / self.initial_capital * 100
        pos = {
            'round_id': 1, 'shares': round(self.shares, 4), 'avg_cost': round(self.avg_cost, 4),
            'sell_stage': 0, 'j_peak': round(j, 2), 'dca_active': False,
            'round_budget': 0, 'budget_remaining': 0,
            'action': action, 'action_label': label, 'action_color': color,
            'action_detail': detail, 'return_pct': round(ret_pct, 2),
            'total_invested': self.initial_capital,
            # 组合口径：邮件/报表用它显示"组合总值"，避免只显示持仓市值而漏掉现金
            'portfolio_value': round(self.portfolio_value, 2),
        }
        return {
            'last_date': str(d['trade_date'].iloc[-1]),
            'last_price': round(price, 4),
            'weekly_j': round(j, 2), 'weekly_k': round(float(last_w.get('K', 0)), 2),
            'weekly_d': round(float(last_w.get('D', 0)), 2),
            'signal_week_date': str(last_w['trade_date']),
            'preview_weekly_j': None,
            'shares': round(self.shares, 4), 'avg_cost': round(self.avg_cost, 4),
            'loss_pct': round(ret_pct, 2),
            'action': action, 'action_label': label, 'action_color': color,
            'action_detail': detail,
            'positions': [pos],
        }


# ---------------------------------------------------------------- 报表
def _sparkline(nav_series, width=760, height=180):
    """内联 SVG 净值曲线，不依赖 CDN。"""
    if len(nav_series) < 2:
        return '<p style="color:#999">数据不足</p>'
    lo, hi = min(nav_series), max(nav_series)
    pad = (hi - lo) * 0.08 or 0.01
    lo, hi = lo - pad, hi + pad
    n = len(nav_series)
    pts = []
    for i, v in enumerate(nav_series):
        x = i / (n - 1) * width
        y = height - (v - lo) / (hi - lo) * height
        pts.append(f"{x:.1f},{y:.1f}")
    base_y = height - (1.0 - lo) / (hi - lo) * height
    return f'''<svg viewBox="0 0 {width} {height}" width="100%" height="{height}"
 style="background:#fafbfc;border-radius:6px">
<line x1="0" y1="{base_y:.1f}" x2="{width}" y2="{base_y:.1f}"
 stroke="#bdc3c7" stroke-dasharray="4 4" stroke-width="1"/>
<polyline points="{' '.join(pts)}" fill="none" stroke="#16a085" stroke-width="2"/>
</svg>'''


def generate_trim_report(strategy, daily_df, weekly_df, output_path: str):
    """生成组合仓位策略的 HTML 报告（与现有报告风格保持一致）。"""
    nav_df = strategy.calc_nav_curve(daily_df)
    if nav_df.empty:
        nav_ser = [1.0]
        metrics = dict(total=0, cagr=0, mdd=0, calmar=0)
    else:
        nav_ser = nav_df['nav'].tolist()
        days = max((pd.to_datetime(str(nav_df['trade_date'].iloc[-1]), format='%Y%m%d')
                    - pd.to_datetime(str(nav_df['trade_date'].iloc[0]), format='%Y%m%d')).days, 1)
        total = nav_ser[-1] - 1
        cagr = (1 + total) ** (365.25 / days) - 1
        peak, mdd = 0.0, 0.0
        for v in nav_ser:
            peak = max(peak, v)
            mdd = min(mdd, v / peak - 1)
        metrics = dict(total=total, cagr=cagr, mdd=mdd,
                       calmar=(cagr / abs(mdd)) if mdd < 0 else 0)

    action = strategy.get_next_action(daily_df, weekly_df)
    j = action.get('weekly_j', 0)
    ratio = strategy.position_ratio
    pv = strategy.portfolio_value
    profit = pv - strategy.initial_capital

    trades_html = ''
    for t in strategy.trades[-15:][::-1]:
        color = '#e74c3c' if t['action'] == 'BUY' else '#2ecc71'
        trades_html += (f"<tr><td>{t['date']}</td>"
                        f"<td style='color:{color};font-weight:600'>"
                        f"{'买入' if t['action'] == 'BUY' else '卖出'}</td>"
                        f"<td>{t['price']:.4f}</td><td>{t['amount']:,.0f}</td>"
                        f"<td style='color:#666'>{t['reason']}</td></tr>")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<title>{strategy.name} · 组合仓位策略</title>
<style>
body{{font-family:-apple-system,"Microsoft YaHei",sans-serif;background:#f5f6fa;margin:0;padding:24px;color:#2c3e50}}
.card{{background:#fff;border-radius:10px;padding:20px 24px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
h1{{font-size:20px;margin:0 0 4px}} .sub{{color:#95a5a6;font-size:12px;margin-bottom:16px}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}}
.stat{{background:#f8f9fb;border-radius:8px;padding:12px}}
.stat .k{{font-size:11px;color:#95a5a6}} .stat .v{{font-size:19px;font-weight:600;margin-top:4px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{padding:8px 6px;text-align:left;border-bottom:1px solid #eef0f4}}
th{{color:#95a5a6;font-weight:500;font-size:12px}}
</style></head><body>
<div class="card">
  <h1>{strategy.name} · 存量资金组合策略</h1>
  <div class="sub">满仓打底 · 周线J&gt;{strategy.trim_j:.0f} 减到 {strategy.keep_ratio:.0%} ·
    周线J&lt;{strategy.refill_j:.0f} 补满 · 数据截至 {action.get('last_date','')}</div>
  <div class="stats">
    <div class="stat"><div class="k">组合总值</div><div class="v">{pv:,.0f}</div></div>
    <div class="stat"><div class="k">累计收益</div><div class="v" style="color:{'#c0392b' if profit>=0 else '#27ae60'}">{profit:+,.0f}（{metrics['total']*100:+.1f}%）</div></div>
    <div class="stat"><div class="k">年化</div><div class="v">{metrics['cagr']*100:.1f}%</div></div>
    <div class="stat"><div class="k">最大回撤</div><div class="v">{metrics['mdd']*100:.1f}%</div></div>
    <div class="stat"><div class="k">Calmar</div><div class="v">{metrics['calmar']:.2f}</div></div>
    <div class="stat"><div class="k">当前仓位</div><div class="v">{ratio:.0%}</div></div>
  </div>
</div>
<div class="card">
  <h1 style="font-size:15px">当前建议</h1>
  <div style="margin-top:8px;font-size:15px;font-weight:600;color:{action.get('action_color','#333')}">
    {action.get('action_label','')}</div>
  <div style="color:#666;font-size:13px;margin-top:6px">{action.get('action_detail','')}</div>
  <div style="color:#95a5a6;font-size:12px;margin-top:6px">
    周线 J={j:.1f} ｜ 持仓 {strategy.shares:,.0f} 份 ｜ 现金 {strategy.cash:,.0f} 元</div>
</div>
<div class="card">
  <h1 style="font-size:15px">净值曲线</h1>
  <div style="margin-top:12px">{_sparkline(nav_ser)}</div>
</div>
<div class="card">
  <h1 style="font-size:15px">最近交易（共 {len(strategy.trades)} 笔）</h1>
  <table><tr><th>日期</th><th>方向</th><th>价格</th><th>金额</th><th>原因</th></tr>
  {trades_html}</table>
</div>
</body></html>"""
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return output_path
