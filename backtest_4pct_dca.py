#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
雷牛牛"4%定投法"回测验证框架

策略规则（还原自B站视频《【亲测有效】4%定投法，真的很有用！》）：
  1. 建仓期：每轮预算平分10份，先投第1份
  2. 投入期：以上次买入价为台阶锚点（买入前锚点冻结，不随阴跌下移），
     每再跌4%投入1份；单日跨多档按跨过档数补足
     —— 只买跌，横盘/上涨不操作；10份投完后继续下跌则持有等待
     （10份 × 4% 台阶 ≈ 覆盖40%的跌幅，与视频原意一致）
  3. 大涨脱离成本期：涨超平均成本15%后进入止盈观察期（停止补仓），
     自观察期最高收盘价回撤4%时一次性全部止盈；止盈后开始新一轮（盈利滚动投入）
  规则解释说明：二手资料对"涨回成本但未超15%"是否停止补仓存在歧义。
  本框架默认按"只买跌"核心规则处理（观察期前创新低仍补仓），另提供
  严格解读（涨回成本即锁投）作为消融变体 freeze_on_cost=True 对比。

资金模型（保证公平对比）：
  三种策略均以相同初始资金池 P（默认20000元）起步：
  - 4%定投法：每轮预算=min(P, 钱包余额)，止盈后资金滚动进入下一轮
  - 每周定投：P 平分到 deploy_weeks 周内每周买入
  - 一次性买入：首日全部买入
  主对比指标为 期末总资产（钱包容器口径一致）与资金加权年化 XIRR，
  收益率（收益/累计投入）仅作参考（各策略投入节奏不同，直接比收益率有误导性）。

用法：
  python backtest_4pct_dca.py                 # 5年窗口 + 基准参数 + 消融
  python backtest_4pct_dca.py --refresh      # 强制重新拉取行情
  python backtest_4pct_dca.py --drop 3 --detach 20 --exit-drop 3
  python backtest_4pct_dca.py --no-ablation --no-report

输出：
  - 控制台汇总表
  - html/dca_4pct/comparison.html 图文报告
  - html/dca_4pct/results.json 明细数据
"""

import os
import json
import math
import time
import logging
import argparse
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from typing import List, Optional

import numpy as np
import pandas as pd

from weekly_dca_strategy_v2 import _get_etf_data

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s | %(message)s')
logger = logging.getLogger(__name__)

DATA_CACHE_DIR = os.path.join('data_cache', 'bt4pct')
REPORT_DIR = os.path.join('html', 'dca_4pct')

DEFAULT_ETFS = [
    ('512890.SH', '红利低波'),
    ('510050.SH', '上证50'),
    ('510300.SH', '沪深300'),
    ('159941.SZ', 'NASDAQ100'),
    ('563300.SH', '中证2000'),
    ('515880.SH', '通信'),
    ('516080.SH', '创新药'),
    ('159611.SZ', '电力'),
    ('588170.SH', '科创半导体'),
    ('563230.SH', '卫星ETF富国'),
    ('512400.SH', '有色金属'),
    ('159819.SZ', '人工智能'),
    ('159387.SZ', '创业板新能源'),
    ('562500.SH', '机器人'),
]

# 宽基标的池：A股宽基 + 可场内买入的QDII宽基（覆盖美/港/日/德/法/沙特等市场），
# 每个指数选取流动性靠前的代表产品（日均成交额见回测输出）
BROAD_ETFS = [
    ('510300.SH', '沪深300'),
    ('510050.SH', '上证50'),
    ('510500.SH', '中证500'),
    ('512100.SH', '中证1000'),
    ('563300.SH', '中证2000'),
    ('159915.SZ', '创业板指'),
    ('588000.SH', '科创50'),
    ('159338.SZ', '中证A500'),
    ('159901.SZ', '深证100'),
    ('159920.SZ', '恒生指数'),
    ('510900.SH', '恒生国企'),
    ('159941.SZ', '纳指100'),
    ('513500.SH', '标普500'),
    ('513400.SH', '道琼斯'),
    ('513520.SH', '日经225'),
    ('513030.SH', '德国DAX'),
    ('513080.SH', '法国CAC40'),
    ('159329.SZ', '沙特ETF'),
]


# ============================ 数据层 ============================

def load_etf_daily(ts_code: str, start: str, end: str, refresh: bool = False) -> pd.DataFrame:
    """获取ETF日线（前复权），本地缓存避免重复请求API"""
    os.makedirs(DATA_CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(DATA_CACHE_DIR, f'{ts_code.replace(".", "_")}.csv')
    stale_days = 5
    if not refresh and os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file, dtype={'trade_date': str})
            last = df['trade_date'].iloc[-1]
            if (datetime.now() - datetime.strptime(last, '%Y%m%d')).days <= stale_days:
                return df[(df['trade_date'] >= start) & (df['trade_date'] <= end)].reset_index(drop=True)
        except Exception as e:
            logger.warning(f'{ts_code} 缓存读取失败，重新拉取: {e}')
    # 缓存缺失/过期：从上市最早可能日期拉全量（上市日不可知，从 start 提前 60 天即可，
    # 但为了让老ETF缓存可长期复用，统一拉 2015 年至今）
    df = _get_etf_data(ts_code, datetime(2015, 1, 1), datetime.now())
    df.to_csv(cache_file, index=False)
    return df[(df['trade_date'] >= start) & (df['trade_date'] <= end)].reset_index(drop=True)


# ============================ 通用指标 ============================

def xirr(cashflows: List[tuple]) -> Optional[float]:
    """资金加权年化收益率。cashflows: [(date_str '%Y%m%d', amount)]，流入为正。
    XIRR口径：所有策略在期初投入全部本金P（-P），期末资产(钱包+持仓市值)为唯一流入，
    期间卖出资金留在钱包内不作为现金流，避免重复计算。"""
    if not cashflows:
        return None
    t0 = datetime.strptime(min(d for d, _ in cashflows), '%Y%m%d')
    ts = np.array([(datetime.strptime(d, '%Y%m%d') - t0).days / 365.0 for d, _ in cashflows])
    cf = np.array([a for _, a in cashflows])
    if (cf > 0).sum() == 0 or (cf < 0).sum() == 0:
        return None

    def f(r):
        return np.sum(cf / np.power(1.0 + r, ts))

    lo, hi = -0.9999, 100.0
    flo, fhi = f(lo), f(hi)
    if flo * fhi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        fm = f(mid)
        if abs(fm) < 1e-9:
            break
        if flo * fm < 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2


def max_drawdown(values: np.ndarray) -> float:
    """最大回撤（正数，百分数）"""
    peak = np.maximum.accumulate(values)
    dd = 1.0 - values / peak
    return float(dd.max() * 100) if len(dd) else 0.0


# ============================ 策略实现 ============================

@dataclass
class FourPctConfig:
    """4%定投法参数"""
    initial_capital: float = 20000.0   # 初始资金池
    round_budget: float = 20000.0      # 每轮预算（实际=min(预算, 钱包余额)）
    n_parts: int = 10                  # 每轮份数
    drop_pct: float = 4.0              # 加仓台阶：较上次买入价锚点再跌4%补1份
    detach_pct: float = 15.0           # 脱离成本阈值：涨超成本15%进入止盈观察期
    exit_drop_pct: float = 4.0         # 止盈台阶：观察期内自最高点回撤4%全卖
    freeze_on_cost: bool = False       # True=严格版：涨回成本即锁定投入（巴蜀养基场解读）
                                       # False=核心版：观察期前只要创新低就补仓（"只买跌"核心规则）
    commission: float = 0.0            # 单边手续费比例


class FourPctDCA:
    """雷牛牛4%定投法（日线收盘价驱动）

    gate: 可选逐日布尔序列（与df行对齐）。True=允许开启新一轮（如估值分位低于阈值）。
    提供gate时：空仓期间只要门开即可开新轮；门关则持币等待（轮内台阶补仓不受门限制，
    因为补仓只发生在比上次买入更低的位置，天然是"越跌越便宜"）。
    """

    def __init__(self, cfg: FourPctConfig, gate=None):
        self.cfg = cfg
        self.gate = gate

    def run(self, df: pd.DataFrame) -> dict:
        c = self.cfg
        closes = df['close_qfq'].values
        dates = df['trade_date'].values

        wallet = c.initial_capital
        shares = cost = 0.0
        flat_days = 0
        trades: List[dict] = []
        rounds: List[dict] = []
        value_curve = np.empty(len(closes))

        # 轮次状态
        in_round = False
        pending_new_round = False   # 止盈后次日开启新一轮
        parts_left = 0
        part_amt = 0.0
        round_invested = 0.0
        round_start = None
        last_low = None             # 上次买入以来最低收盘价
        avg_cost = 0.0
        detached = False            # 涨回成本后锁定投入
        phase = ''
        watch_max = None            # 观察期最高收盘价

        def do_buy(i, px):
            nonlocal wallet, shares, cost, parts_left, round_invested, last_low, avg_cost
            amt = min(part_amt, wallet)
            if amt <= 1e-9:
                return
            fee = amt * c.commission
            n = (amt - fee) / px
            shares += n
            cost += amt
            wallet -= amt
            round_invested += amt
            parts_left -= 1
            avg_cost = cost / shares
            last_low = px
            trades.append({'date': dates[i], 'action': 'BUY', 'price': round(px, 4),
                           'amount': round(amt, 2), 'shares': round(n, 4),
                           'avg_cost': round(avg_cost, 4),
                           'reason': f'新低台阶{c.drop_pct}%, 剩{parts_left}份' if parts_left else '第1份建仓'})

        def do_sell(i, px):
            nonlocal wallet, shares, cost, in_round, pending_new_round, round_invested
            nonlocal parts_left, part_amt, round_start, last_low, avg_cost, detached, phase, watch_max
            proceeds = shares * px * (1 - c.commission)
            wallet += proceeds
            rounds.append({'start': round_start, 'end': dates[i],
                           'invested': round(float(round_invested), 2),
                           'proceeds': round(float(proceeds), 2),
                           'return_pct': round(float(proceeds / round_invested - 1) * 100, 2) if round_invested else 0,
                           'days': int(np.sum((dates >= round_start) & (dates <= dates[i]))) if round_start else 0})
            trades.append({'date': dates[i], 'action': 'SELL', 'price': round(px, 4),
                           'amount': round(proceeds, 2), 'shares': round(shares, 4),
                           'reason': f'脱离成本{c.detach_pct}%后自高点回撤{c.exit_drop_pct}%止盈'})
            shares = cost = 0.0
            in_round = False
            pending_new_round = True
            parts_left = 0
            part_amt = round_invested = 0.0
            round_start = None
            last_low = avg_cost = 0.0
            detached = False
            phase = ''
            watch_max = None

        for i, px in enumerate(closes):
            d = dates[i]
            # ---- 开启新一轮（止盈次日 / 首日 / 估值门开启时空仓即可开）----
            if not in_round:
                gate_ok = True if self.gate is None else bool(self.gate[i])
                should_start = ((i == 0) or pending_new_round or (self.gate is not None)) and gate_ok
                if should_start:
                    pending_new_round = False
                    budget = min(c.round_budget, wallet)
                    if budget > 1e-6:
                        in_round = True
                        parts_left = c.n_parts
                        part_amt = budget / c.n_parts
                        round_start = d
                        round_invested = 0.0
                        last_low = None
                        avg_cost = 0.0
                        detached = False
                        phase = 'ACCUM'
                        watch_max = None
                        do_buy(i, px)   # 建仓期：第1份

            # ---- 投入期：台阶加仓 ----
            # 锚点=上次买入价（买入前冻结，不随阴跌下移），每再跌drop_pct%补1份；
            # 单日跨多档（如跳空-12%）按跨过档数补足对应份数
            # 锁投条件：已进入止盈观察期；或（严格版）涨回成本后锁定
            locked = (phase == 'WATCH') or (c.freeze_on_cost and detached)
            if in_round and not locked and parts_left > 0 and last_low is not None:
                if px <= last_low * (1 - c.drop_pct / 100.0):
                    steps = int((last_low - px) / (last_low * c.drop_pct / 100.0))
                    for _ in range(max(1, min(steps, parts_left))):
                        do_buy(i, px)
                        if parts_left == 0:
                            break
            # ---- 状态转移 ----
            if in_round and avg_cost > 0:
                if px > avg_cost * (1 + c.detach_pct / 100.0):
                    if phase != 'WATCH':
                        phase = 'WATCH'
                        watch_max = px
                    else:
                        watch_max = max(watch_max, px)
                elif px > avg_cost:
                    if not detached:
                        detached = True   # 涨回成本（严格版从此锁投）
                    phase = 'HOLD'
                else:
                    phase = 'ACCUM'
                if phase == 'WATCH':
                    watch_max = max(watch_max, px)
                    if px <= watch_max * (1 - c.exit_drop_pct / 100.0):
                        do_sell(i, px)

            value_curve[i] = wallet + shares * px
            if shares <= 1e-9:
                flat_days += 1

        # ---- 期末统计 ----
        last_px = closes[-1]
        final_assets = wallet + shares * last_px
        total_invested = c.initial_capital  # 全部本金都以XIRR口径计入
        realized = sum(r['proceeds'] for r in rounds)
        open_cost = cost  # 未平仓轮次的已投入
        cf = [(dates[0], -c.initial_capital), (dates[-1], final_assets)]
        r_xirr = xirr(cf)
        years = (datetime.strptime(dates[-1], '%Y%m%d') - datetime.strptime(dates[0], '%Y%m%d')).days / 365.0

        return {
            'strategy': '4%定投法',
            'final_assets': round(final_assets, 2),
            'abs_profit': round(final_assets - c.initial_capital, 2),
            'total_profit_pct': round((final_assets / c.initial_capital - 1) * 100, 2),
            'xirr_pct': round(r_xirr * 100, 2) if r_xirr is not None else None,
            'mdd_pct': round(max_drawdown(value_curve), 2),
            'n_trades': len(trades),
            'n_rounds': len(rounds),
            'realized': round(realized, 2),
            'open_cost': round(open_cost, 2),
            'open_value': round(shares * last_px, 2),
            'open_shares': round(shares, 4),
            'open_avg_cost': round(avg_cost, 4) if shares > 0 else None,
            'years': round(years, 2),
            'flat_days': flat_days,
            'rounds_detail': rounds,
            'trades': trades,
            'value_curve': value_curve,
            'dates': dates,
            # 期末状态机快照（供策略模块读取当前阶段/下一触发位）
            'state': {
                'in_round': bool(in_round),
                'phase': phase,
                'parts_left': int(parts_left),
                'part_amt': round(float(part_amt), 2) if part_amt else 0.0,
                'last_low': float(last_low) if last_low else None,
                'avg_cost': round(float(avg_cost), 4) if avg_cost else None,
                'watch_max': float(watch_max) if watch_max is not None else None,
                'wallet': round(float(wallet), 2),
                'shares': round(float(shares), 4),
                'detached': bool(detached),
                'pending_new_round': bool(pending_new_round),
            },
        }


class PoolWeeklyDCA:
    """对照：每周定投（初始资金池P平分到deploy_weeks周）"""

    def __init__(self, initial_capital: float = 20000.0, deploy_weeks: int = 104,
                 commission: float = 0.0):
        self.initial_capital = initial_capital
        self.deploy_weeks = deploy_weeks
        self.commission = commission

    def run(self, df: pd.DataFrame) -> dict:
        closes = df['close_qfq'].values
        dates = pd.to_datetime(df['trade_date'].values, format='%Y%m%d')
        wallet = self.initial_capital
        weekly_amt = self.initial_capital / self.deploy_weeks
        shares = 0.0
        n_buys = 0
        flat_days = 0
        value_curve = np.empty(len(closes))
        cur_week = None
        for i in range(len(closes)):
            wk = dates[i].isocalendar()[:2]
            if wk != cur_week:
                cur_week = wk
                amt = min(weekly_amt, wallet)
                if amt > 1e-9:
                    fee = amt * self.commission
                    shares += (amt - fee) / closes[i]
                    wallet -= amt
                    n_buys += 1
            value_curve[i] = wallet + shares * closes[i]
            if shares <= 1e-9:
                flat_days += 1
        final_assets = wallet + shares * closes[-1]
        r_xirr = xirr([(df['trade_date'].iloc[0], -self.initial_capital),
                       (df['trade_date'].iloc[-1], final_assets)])
        years = (dates[-1] - dates[0]).days / 365.0
        return {
            'strategy': '每周定投',
            'final_assets': round(final_assets, 2),
            'abs_profit': round(final_assets - self.initial_capital, 2),
            'total_profit_pct': round((final_assets / self.initial_capital - 1) * 100, 2),
            'xirr_pct': round(r_xirr * 100, 2) if r_xirr is not None else None,
            'mdd_pct': round(max_drawdown(value_curve), 2),
            'n_trades': n_buys,
            'n_rounds': 0,
            'realized': 0.0,
            'open_cost': round(self.initial_capital - wallet, 2),
            'open_value': round(shares * closes[-1], 2),
            'open_shares': round(shares, 4),
            'open_avg_cost': None,
            'years': round(years, 2),
            'flat_days': flat_days,
            'rounds_detail': [],
            'trades': [],
            'value_curve': value_curve,
            'dates': df['trade_date'].values,
        }


class LumpSum:
    """对照：一次性买入"""

    def __init__(self, initial_capital: float = 20000.0, commission: float = 0.0):
        self.initial_capital = initial_capital
        self.commission = commission

    def run(self, df: pd.DataFrame) -> dict:
        closes = df['close_qfq'].values
        wallet = 0.0
        fee = self.initial_capital * self.commission
        shares = (self.initial_capital - fee) / closes[0]
        value_curve = shares * closes
        final_assets = shares * closes[-1]
        r_xirr = xirr([(df['trade_date'].iloc[0], -self.initial_capital),
                       (df['trade_date'].iloc[-1], final_assets)])
        years = (pd.to_datetime(df['trade_date'].iloc[-1]) -
                 pd.to_datetime(df['trade_date'].iloc[0])).days / 365.0
        return {
            'strategy': '一次性买入',
            'final_assets': round(final_assets, 2),
            'abs_profit': round(final_assets - self.initial_capital, 2),
            'total_profit_pct': round((final_assets / self.initial_capital - 1) * 100, 2),
            'xirr_pct': round(r_xirr * 100, 2) if r_xirr is not None else None,
            'mdd_pct': round(max_drawdown(value_curve), 2),
            'n_trades': 1,
            'n_rounds': 0,
            'realized': 0.0,
            'open_cost': round(self.initial_capital, 2),
            'open_value': round(shares * closes[-1], 2),
            'open_shares': round(shares, 4),
            'open_avg_cost': None,
            'years': round(years, 2),
            'flat_days': 0,
            'rounds_detail': [],
            'trades': [],
            'value_curve': value_curve,
            'dates': df['trade_date'].values,
        }


# ============================ 回测调度 ============================

def run_fourpct(df, gate=None, **kw) -> dict:
    cfg = FourPctConfig(**kw)
    return FourPctDCA(cfg, gate=gate).run(df)


VARIANTS = {
    '基准 drop4/detach15/exit4': {},
    '严格版：涨回成本即停投': {'freeze_on_cost': True},
    'drop=3': {'drop_pct': 3.0},
    'drop=5': {'drop_pct': 5.0},
    'detach=10': {'detach_pct': 10.0},
    'detach=20': {'detach_pct': 20.0},
    'exit_drop=3': {'exit_drop_pct': 3.0},
    'exit_drop=5': {'exit_drop_pct': 5.0},
    'n_parts=5': {'n_parts': 5},
    'n_parts=20': {'n_parts': 20},
}


def main():
    ap = argparse.ArgumentParser(description='雷牛牛4%定投法回测')
    ap.add_argument('--start', default=(datetime.now() - timedelta(days=5 * 365)).strftime('%Y%m%d'))
    ap.add_argument('--budget', type=float, default=20000.0)
    ap.add_argument('--deploy-weeks', type=int, default=104)
    ap.add_argument('--drop', type=float, default=4.0)
    ap.add_argument('--detach', type=float, default=15.0)
    ap.add_argument('--exit-drop', type=float, default=4.0)
    ap.add_argument('--n-parts', type=int, default=10)
    ap.add_argument('--commission', type=float, default=0.0)
    ap.add_argument('--universe', choices=['default', 'broad'], default='default',
                    help='default=原14只(含行业) / broad=18只宽基(A股+QDII跨市场)')
    ap.add_argument('--min-amount-yi', type=float, default=0.2,
                    help='日均成交额门槛(亿元)，低于则跳过')
    ap.add_argument('--refresh', action='store_true', help='强制重新拉取行情')
    ap.add_argument('--no-ablation', action='store_true')
    ap.add_argument('--no-report', action='store_true')
    args = ap.parse_args()

    etfs = BROAD_ETFS if args.universe == 'broad' else DEFAULT_ETFS
    suffix = '_broad' if args.universe == 'broad' else ''
    universe_label = ('宽基全集（A股宽基 + 场内QDII，覆盖美/港/日/德/法/沙特市场，共%d只）' % len(etfs)
                      if args.universe == 'broad'
                      else '原14只（宽基+行业混合）')

    results = {}   # name -> {'meta':..., 'strategies': {label: metrics}, 'fourpct_base': full}
    ablation_rows = []

    for ts_code, name in etfs:
        try:
            raw = load_etf_daily(ts_code, args.start, datetime.now().strftime('%Y%m%d'), refresh=args.refresh)
        except Exception as e:
            logger.error(f'{name}({ts_code}) 数据获取失败: {e}')
            continue
        df = raw.reset_index(drop=True)
        if len(df) < 120:
            logger.warning(f'{name} 数据不足({len(df)}行)，跳过')
            continue
        avg_amt_yi = df['amount'].tail(60).mean() / 1e5 if 'amount' in df else 0  # 千元→亿元
        if avg_amt_yi < args.min_amount_yi:
            logger.warning(f'{name} 日均成交额{avg_amt_yi:.2f}亿 低于门槛{args.min_amount_yi}亿，跳过')
            continue
        sim_start, sim_end = df['trade_date'].iloc[0], df['trade_date'].iloc[-1]
        logger.info(f'{name}: {len(df)}个交易日 {sim_start}~{sim_end} 日均成交{avg_amt_yi:.2f}亿')

        base_kw = dict(initial_capital=args.budget, round_budget=args.budget,
                       n_parts=args.n_parts, drop_pct=args.drop, detach_pct=args.detach,
                       exit_drop_pct=args.exit_drop, commission=args.commission)
        fourpct = run_fourpct(df, **base_kw)
        weekly = PoolWeeklyDCA(args.budget, args.deploy_weeks, args.commission).run(df)
        lump = LumpSum(args.budget, args.commission).run(df)

        results[name] = {
            'meta': {'ts_code': ts_code, 'sim_start': sim_start, 'sim_end': sim_end,
                     'n_days': len(df), 'years': fourpct['years'],
                     'avg_amount_yi': round(avg_amt_yi, 2)},
            'strategies': {'4%定投法': fourpct, '每周定投': weekly, '一次性买入': lump},
        }

        if not args.no_ablation:
            arow = {'name': name}
            for vname, vkw in VARIANTS.items():
                kw = dict(base_kw)
                kw.update(vkw)
                r = run_fourpct(df, **kw)
                arow[vname] = r['xirr_pct']
            ablation_rows.append(arow)

    if not results:
        logger.error('没有任何ETF回测成功')
        return

    # ---- 控制台汇总 ----
    summary_cols = ['期末资产', '绝对收益', '总收益率%', 'XIRR年化%', '最大回撤%', '交易次数']
    print('\n' + '=' * 110)
    print(f'回测区间: {args.start} ~ 今天 | 初始资金 {args.budget:.0f} 元 | 手续费 {args.commission * 100:.2f}%')
    print('=' * 110)
    header = f'{"ETF":<10}{"区间":<20}{"策略":<10}{"期末资产":>12}{"绝对收益":>12}{"XIRR年化%":>12}{"回撤%":>8}{"轮次":>6}{"交易":>6}'
    print(header)
    print('-' * 110)
    for name, r in results.items():
        m = r['meta']
        for label in ['4%定投法', '每周定投', '一次性买入']:
            s = r['strategies'][label]
            print(f"{name:<10}{m['sim_start'] + '~' + m['sim_end']:<20}{label:<10}"
                  f"{s['final_assets']:>12,.0f}{s['abs_profit']:>12,.0f}"
                  f"{str(s['xirr_pct']):>12}{s['mdd_pct']:>8}{s['n_rounds']:>6}{s['n_trades']:>6}")
        print('-' * 110)

    if ablation_rows:
        print('\n---- 参数消融（XIRR年化%，一行=一只ETF） ----')
        adf = pd.DataFrame(ablation_rows).set_index('name')
        with pd.option_context('display.width', 200):
            print(adf.round(2).to_string())
            print('\n各参数组平均 XIRR%:')
            print(adf.mean().round(2).to_string())

    # ---- 输出 JSON + HTML ----
    os.makedirs(REPORT_DIR, exist_ok=True)
    json_path = os.path.join(REPORT_DIR, f'results{suffix}.json')

    def py(v):
        """numpy 标量转原生类型，保证可JSON序列化"""
        if isinstance(v, (np.floating, np.integer)):
            return float(v)
        return v

    slim = {}
    for name, r in results.items():
        slim[name] = {'meta': r['meta']}
        for label, s in r['strategies'].items():
            slim[name][label] = {k: py(v) for k, v in s.items()
                                 if k not in ('value_curve', 'dates', 'trades')}
            slim[name][label + '_rounds'] = s['rounds_detail']
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump({'params': vars(args), 'ablation': ablation_rows, 'results': slim},
                  f, ensure_ascii=False, indent=1, default=str)
    logger.info(f'明细已保存 {json_path}')

    if not args.no_report:
        html_path = os.path.join(REPORT_DIR, f'comparison{suffix}.html')
        generate_report(results, ablation_rows, vars(args), html_path, universe_label)
        logger.info(f'报告已生成 {html_path}')


# ============================ HTML 报告 ============================

def generate_report(results: dict, ablation_rows: list, params: dict, out_path: str,
                    universe_label: str = ''):
    """生成ECharts对比报告（结构对齐 html/dca/dca_comparison.html 风格）"""
    etf_names = list(results.keys())
    strategies = ['4%定投法', '每周定投', '一次性买入']
    colors = {'4%定投法': '#e74c3c', '每周定投': '#3498db', '一次性买入': '#95a5a6'}

    drop = params.get('drop', 4.0)
    detach = params.get('detach', 15.0)
    exit_drop = params.get('exit_drop', 4.0)
    n_parts = params.get('n_parts', 10)
    budget = params.get('budget', 20000.0)
    deploy_weeks = params.get('deploy_weeks', 104)
    commission_pct = params.get('commission', 0.0) * 100
    start = params.get('start', '')
    gen_time = datetime.now().strftime('%Y-%m-%d %H:%M')

    def table_strategies():
        rows = []
        for name in etf_names:
            r = results[name]
            m = r['meta']
            best_xirr = max(s['xirr_pct'] if s['xirr_pct'] is not None else -999
                            for s in r['strategies'].values())
            for idx, label in enumerate(strategies):
                s = r['strategies'][label]
                xirr_v = s['xirr_pct']
                star = ' ⭐' if xirr_v is not None and xirr_v == best_xirr else ''
                xirr_txt = f'{xirr_v}%' if xirr_v is not None else '—'
                cls = 'pos' if (xirr_v or 0) >= 0 else 'neg'
                etf_td = (f'<td rowspan="{len(strategies)}" class="etf">{name}<br><small>{m["ts_code"]}<br>'
                          f'{m["sim_start"]}~{m["sim_end"]}<br>日均{m.get("avg_amount_yi", 0):.1f}亿</small></td>') if idx == 0 else ''
                rows.append(
                    f'<tr>{etf_td}'
                    f'<td style="color:{colors[label]}">{label}{star}</td>'
                    f'<td>{s["final_assets"]:,.0f}</td><td class="{"pos" if s["abs_profit"] >= 0 else "neg"}">'
                    f'{s["abs_profit"]:+,.0f}</td><td class="{cls}">{xirr_txt}</td>'
                    f'<td>{s["mdd_pct"]}%</td>'
                    f'<td>{s["n_rounds"]}</td><td>{s["n_trades"]}</td></tr>')
        return '\n'.join(rows)

    def chart_xirr():
        series = []
        for label in strategies:
            data = [results[n]['strategies'][label]['xirr_pct'] for n in etf_names]
            series.append({'name': label, 'type': 'bar', 'data': data,
                           'itemStyle': {'color': colors[label]}})
        return json.dumps({'tooltip': {'trigger': 'axis'},
                           'legend': {'data': strategies},
                           'grid': {'left': 60, 'right': 20, 'bottom': 90},
                           'xAxis': {'type': 'category', 'data': etf_names,
                                     'axisLabel': {'rotate': 40}},
                           'yAxis': {'type': 'value', 'name': 'XIRR 年化 %'},
                           'series': series}, ensure_ascii=False)

    def chart_curve(name):
        r = results[name]
        step = max(1, r['meta']['n_days'] // 250)  # 降采样
        series = []
        for label in strategies:
            s = r['strategies'][label]
            vals = [round(float(v), 0) for v in s['value_curve'][::step]]
            series.append({'name': label, 'type': 'line', 'data': vals,
                           'showSymbol': False, 'itemStyle': {'color': colors[label]}})
        x = [f'{d[:4]}-{d[4:6]}-{d[6:]}' for d in r['strategies']['4%定投法']['dates'][::step]]
        return {'title': {'text': f'{name}（{r["meta"]["ts_code"]}）',
                          'left': 'center', 'textStyle': {'fontSize': 14}},
                'tooltip': {'trigger': 'axis'},
                'legend': {'data': strategies, 'top': 28},
                'grid': {'left': 60, 'right': 20, 'top': 60, 'bottom': 60},
                'xAxis': {'type': 'category', 'data': x},
                'yAxis': {'type': 'value', 'name': '组合总资产(元)'},
                'series': series}

    def table_ablation():
        if not ablation_rows:
            return '<p>未启用消融。</p>'
        variants = [k for k in ablation_rows[0].keys() if k != 'name']
        head = ''.join(f'<th>{v}</th>' for v in variants)
        rows = []
        for row in ablation_rows:
            tds = ''.join((f'<td>{row[v]:.2f}</td>' if row[v] is not None else '<td>—</td>')
                          for v in variants)
            rows.append(f'<tr><td class="etf">{row["name"]}</td>{tds}</tr>')
        mean_tds = ''.join(
            (f'<td>{np.mean([r[v] for r in ablation_rows if r[v] is not None]):.2f}</td>'
             if any(r[v] is not None for r in ablation_rows) else '<td>—</td>')
            for v in variants)
        rows.append(f'<tr class="mean"><td class="etf">平均</td>{mean_tds}</tr>')
        return (f'<table><thead><tr><th>ETF \\ 参数组</th>{head}</tr></thead>'
                f'<tbody>{"".join(rows)}</tbody></table>')

    curves_html = '\n'.join(
        f'<div class="card"><div class="chart" id="cv_{i}"></div></div>'
        for i in range(len(etf_names)))

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>4%定投法回测对比报告</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>
body {{ font-family: "Microsoft YaHei", sans-serif; margin: 24px auto; max-width: 1200px;
       background: #f7f8fa; color: #2c3e50; }}
h1 {{ font-size: 22px; }} h2 {{ font-size: 18px; margin-top: 32px;
       border-left: 4px solid #e74c3c; padding-left: 10px; }}
table {{ border-collapse: collapse; width: 100%; background: #fff; font-size: 13px; }}
th, td {{ border: 1px solid #e0e4e8; padding: 6px 10px; text-align: right; }}
th {{ background: #34495e; color: #fff; font-weight: normal; }}
td.etf {{ text-align: center; font-weight: bold; background: #fbfbfc; }}
tr.mean td {{ background: #fff8e1; font-weight: bold; }}
td.pos {{ color: #c0392b; }} td.neg {{ color: #27ae60; }}
.desc {{ background: #fff; border: 1px solid #e0e4e8; padding: 14px 18px;
         font-size: 13px; line-height: 1.9; }}
.chart {{ width: 100%; height: 420px; }}
.card {{ background: #fff; border: 1px solid #e0e4e8; margin-top: 16px; padding: 8px; }}
small {{ color: #7f8c8d; }}
</style>
</head>
<body>
<h1>雷牛牛"4%定投法"回测验证报告</h1>
<div class="desc">
<b>策略规则</b>：每轮预算平分{n_parts}份 → 第1份建仓；以上次买入价为锚点，每再跌<b>{drop}%</b>补1份（只买跌，不买涨）；
涨超成本<b>{detach}%</b>进入止盈观察期（停止补仓），自观察期最高价回撤<b>{exit_drop}%</b>时一次性全卖；止盈后滚动开新一轮。
另有"严格版"变体：涨回成本（未超{detach}%）即停止补仓——该解读下建仓后价格只要瞬时涨过成本，本轮就永久停止加仓，作为对照一并回测。<br>
<b>资金模型</b>：三种策略均以 <b>{budget:.0f}元</b> 初始资金池起步（4%法滚动复投 / 每周定投分{deploy_weeks}周投入 / 一次性首日买入），
手续费 {commission_pct}%。区间 {start} ~ 2026-10（各ETF按实际数据起始日）。<br>
<b>标的池</b>：{universe_label}；QDII按场内收盘价成交模拟，未计溢价/限购影响（QDII实际买入需关注 IOPV 溢价率）。<br>
<b>口径说明</b>：期末资产=钱包+持仓市值，三者可直接比较；XIRR为资金加权年化（期初投入、期末资产两点现金流）。
各策略投入节奏不同，"收益率"不可直接比较，以期末资产与XIRR为准。<small>报告生成于 {gen_time}，仅供研究参考，不构成投资建议。</small>
</div>

<h2>一、三策略 XIRR 年化对比</h2>
<div class="card"><div class="chart" id="chart_xirr"></div></div>

<h2>二、明细表</h2>
<table>
<thead><tr><th>ETF</th><th>策略</th><th>期末资产(元)</th><th>绝对收益(元)</th>
<th>XIRR年化</th><th>最大回撤</th><th>完成轮次</th><th>交易次数</th></tr></thead>
<tbody>
{table_strategies()}
</tbody>
</table>

<h2>三、参数消融（4%定投法，XIRR年化%）</h2>
<p style="font-size:13px">以基准参数（drop=4 / detach=15 / exit=4 / 10份）为轴，每次只改一个参数，观察稳健性。</p>
{table_ablation()}

<h2>四、各ETF资产曲线</h2>
{curves_html}

<script>
var xirr_opt = {chart_xirr()};
echarts.init(document.getElementById('chart_xirr')).setOption(xirr_opt);
var curve_opts = {json.dumps([chart_curve(n) for n in etf_names], ensure_ascii=False)};
curve_opts.forEach(function(opt, i) {{
    var el = document.getElementById('cv_' + i);
    if (el) echarts.init(el).setOption(opt);
}});
window.addEventListener('resize', function() {{
    document.querySelectorAll('.chart').forEach(function(el) {{
        var inst = echarts.getInstanceByDom(el);
        if (inst) inst.resize();
    }});
}});
</script>
</body>
</html>"""
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)


if __name__ == '__main__':
    main()
