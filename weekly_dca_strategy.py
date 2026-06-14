#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
周线KDJ定投策略 - 纳斯达克指数 & 红利低波指数

策略逻辑：
1. 周线 J < 13 时，开始每周定投
2. 亏损达到 5% 时，定投金额翻倍（基础金额 → 2倍 → 4倍）
3. 亏损达到 10% 时，定投金额再翻倍
4. 周线 J > 100 时，卖出半仓
5. 日线知行多空线死叉（中期多空线下穿多空线）时，全部卖出

数据源：
- 纳斯达克100ETF：Tushare fund_daily（513100.SH）
- 红利低波ETF：Tushare fund_daily（512890.SH）
"""

import os
import json
import argparse
import numpy as np
import pandas as pd
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s | %(message)s')

try:
    import tushare as ts
    from config import APIConfig
except ImportError:
    ts = None
    logging.warning("tushare 未安装，数据获取将不可用")


def get_nasdaq_data(years: int = 5) -> pd.DataFrame:
    end_date = datetime.now()
    start_date = end_date - timedelta(days=years * 365)
    return _get_etf_data('159941.SZ', start_date, end_date)


def _get_etf_data(ts_code: str, start_date, end_date) -> pd.DataFrame:
    if ts is None:
        raise ImportError("请先安装 tushare 并配置 TUSHARE_TOKEN")
    ts.set_token(APIConfig.get_token())
    pro = ts.pro_api()
    start_str = start_date.strftime('%Y%m%d')
    end_str = end_date.strftime('%Y%m%d')
    all_dfs = []
    batch_start = start_str
    batch_end = end_str
    while True:
        df_batch = pro.fund_daily(ts_code=ts_code, start_date=batch_start, end_date=batch_end)
        if df_batch is None or df_batch.empty:
            break
        all_dfs.append(df_batch)
        if len(df_batch) < 5000:
            break
        earliest = df_batch['trade_date'].min()
        batch_end = (pd.to_datetime(earliest) - timedelta(days=1)).strftime('%Y%m%d')
    if not all_dfs:
        raise ValueError(f"未获取到 {ts_code} ETF数据")
    df = pd.concat(all_dfs, ignore_index=True).drop_duplicates(subset=['trade_date']).reset_index(drop=True)
    df = df.sort_values('trade_date').reset_index(drop=True)
    for col in ['open', 'high', 'low', 'close', 'vol', 'amount']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    adj_dfs = []
    adj_start = start_str
    adj_end = end_str
    while True:
        df_adj = pro.fund_adj(ts_code=ts_code, start_date=adj_start, end_date=adj_end)
        if df_adj is None or df_adj.empty:
            break
        adj_dfs.append(df_adj)
        if len(df_adj) < 2000:
            break
        earliest = df_adj['trade_date'].min()
        adj_end = (pd.to_datetime(earliest) - timedelta(days=1)).strftime('%Y%m%d')
    if adj_dfs:
        df_adj_all = pd.concat(adj_dfs, ignore_index=True).drop_duplicates(subset=['trade_date']).reset_index(drop=True)
        df = df.merge(df_adj_all[['trade_date', 'adj_factor']], on='trade_date', how='left')
        df['adj_factor'] = df['adj_factor'].ffill().bfill()
        latest_adj = df['adj_factor'].iloc[-1]
        df['open_qfq'] = df['open'] * df['adj_factor'] / latest_adj
        df['high_qfq'] = df['high'] * df['adj_factor'] / latest_adj
        df['low_qfq'] = df['low'] * df['adj_factor'] / latest_adj
        df['close_qfq'] = df['close'] * df['adj_factor'] / latest_adj
    else:
        logging.warning(f"未获取到 {ts_code} 复权因子，使用原始价格")
        df['open_qfq'] = df['open']
        df['high_qfq'] = df['high']
        df['low_qfq'] = df['low']
        df['close_qfq'] = df['close']
    logging.info(f"获取ETF数据 {ts_code}: {len(df)} 条, "
                 f"{df['trade_date'].iloc[0]} ~ {df['trade_date'].iloc[-1]}")
    return df


def resample_to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df['date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
    df['week_key'] = df['date'].dt.isocalendar().year.astype(str) + '_' + \
                     df['date'].dt.isocalendar().week.astype(str).str.zfill(2)
    weekly = df.groupby('week_key').agg({
        'open_qfq': 'first',
        'high_qfq': 'max',
        'low_qfq': 'min',
        'close_qfq': 'last',
        'trade_date': 'last',
        'date': 'max'
    }).reset_index(drop=True)
    weekly = weekly.sort_values('date').reset_index(drop=True)
    weekly = weekly.dropna(subset=['open_qfq', 'high_qfq', 'low_qfq', 'close_qfq'])
    return weekly


def calc_kdj(df: pd.DataFrame, n: int = 9) -> pd.DataFrame:
    df = df.copy()
    low_min = df['low_qfq'].rolling(window=n, min_periods=n).min()
    high_max = df['high_qfq'].rolling(window=n, min_periods=n).max()
    denom = high_max - low_min
    denom = denom.replace(0, np.nan)
    rsv = (df['close_qfq'] - low_min) / denom * 100
    rsv = rsv.replace([np.inf, -np.inf], 0).fillna(0)
    df['K'] = rsv.ewm(com=2, adjust=False).mean()
    df['D'] = df['K'].ewm(com=2, adjust=False).mean()
    df['J'] = 3 * df['K'] - 2 * df['D']
    return df


def calc_zhixing_duokong(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for period in [14, 28, 57, 114]:
        df[f'ma_{period}'] = df['close_qfq'].rolling(window=period, min_periods=period).mean()
    df['zhixing_duokong'] = (
        df['ma_14'] + df['ma_28'] + df['ma_57'] + df['ma_114']
    ) / 4
    df['ema_10'] = df['close_qfq'].ewm(span=10, adjust=False).mean()
    df['zhixing_mid'] = df['ema_10'].ewm(span=10, adjust=False).mean()
    df['zhixing_cross'] = np.nan
    for i in range(1, len(df)):
        prev_diff = df.loc[i - 1, 'zhixing_mid'] - df.loc[i - 1, 'zhixing_duokong']
        curr_diff = df.loc[i, 'zhixing_mid'] - df.loc[i, 'zhixing_duokong']
        if pd.notna(prev_diff) and pd.notna(curr_diff):
            if prev_diff >= 0 and curr_diff < 0:
                df.loc[i, 'zhixing_cross'] = -1
            elif prev_diff <= 0 and curr_diff > 0:
                df.loc[i, 'zhixing_cross'] = 1
    return df


class Position:
    """独立仓位，每轮定投的买入/卖出独立管理"""

    def __init__(self, round_id: int, base_amount: float,
                 loss_threshold_1: float, loss_threshold_2: float,
                 j_buy_threshold: float, j_sell_half_threshold: float,
                 j_peak_min: float, j_pullback: float,
                 round_budget: float = 5000, round_periods: int = 5):
        self.round_id = round_id
        self.base_amount = base_amount
        self.loss_threshold_1 = loss_threshold_1
        self.loss_threshold_2 = loss_threshold_2
        self.j_buy_threshold = j_buy_threshold
        self.j_sell_half_threshold = j_sell_half_threshold
        self.j_peak_min = j_peak_min
        self.j_pullback = j_pullback
        # 预算制定投
        self.round_budget = round_budget
        self.round_periods = round_periods
        self.buy_count = 0  # 本轮已买期数
        self.dca_exited = False  # 是否已触发过 J>13 退出（一把投入剩余预算）
        # 仓位状态
        self.shares = 0.0
        self.total_cost = 0.0
        self.total_invested = 0.0
        self.total_sell_amount = 0.0
        self.dca_active = False
        self.j_peak = 0.0
        self.pullback_sold = False
        # 卖出阶段
        self.sell_stage = 0  # 0=定投中, 1=已卖1/3, 2=已卖2/3
        self.waiting_golden = False
        self.golden_confirmed = False
        self.price_below_dk_sold = False
        # 交易记录
        self.trades = []
        self.week_invested = set()

    @property
    def is_active(self) -> bool:
        return self.shares > 0 or self.dca_active

    @property
    def is_fully_cleared(self) -> bool:
        return self.shares <= 0 and not self.dca_active

    def get_avg_cost(self) -> float:
        if self.shares <= 0:
            return 0
        return self.total_cost / self.shares

    def get_current_loss_pct(self, current_price: float) -> float:
        avg = self.get_avg_cost()
        if avg <= 0:
            return 0
        return (avg - current_price) / avg

    def get_invest_amount(self, current_price: float) -> float:
        """预算制计算买入金额:
        - 预算期内(< round_periods): 动态均摊剩余预算
        - 超过预算期(>= round_periods): 按 round_budget/round_periods 基础金额
        - 亏损加码照常，但受剩余预算约束
        """
        base_per_period = self.round_budget / self.round_periods if self.round_periods > 0 else self.base_amount
        if self.buy_count < self.round_periods:
            # 预算期内：动态均摊剩余预算
            remaining = max(self.round_budget - self.total_invested, 0)
            remaining_periods = max(self.round_periods - self.buy_count, 1)
            base = remaining / remaining_periods
        else:
            # 超过预算期：固定基础金额，不受budget上限
            base = base_per_period
        # 亏损加码
        if self.shares > 0:
            loss_pct = self.get_current_loss_pct(current_price)
            if loss_pct >= self.loss_threshold_2:
                base = base * 4
            elif loss_pct >= self.loss_threshold_1:
                base = base * 2
        # 预算期内受剩余预算约束
        if self.buy_count < self.round_periods:
            remaining = max(self.round_budget - self.total_invested, 0)
            base = min(base, remaining)
        return max(base, 0)

    def get_budget_remaining(self) -> float:
        """本轮剩余预算"""
        return max(self.round_budget - self.total_invested, 0)

    def buy(self, date: str, price: float, amount: float, reason: str):
        if amount <= 0:
            return
        buy_shares = amount / price
        self.shares += buy_shares
        self.total_cost += amount
        self.total_invested += amount
        self.buy_count += 1
        self.trades.append({
            'date': date, 'action': 'BUY', 'price': round(price, 4),
            'amount': round(amount, 2), 'shares': round(buy_shares, 4),
            'round': self.round_id,
            'reason': reason, 'total_shares': round(self.shares, 4),
            'avg_cost': round(self.total_cost / self.shares, 4) if self.shares > 0 else 0,
        })

    def sell(self, date: str, price: float, ratio: float, reason: str):
        if self.shares <= 0:
            return
        sell_shares = self.shares * ratio
        sell_amount = sell_shares * price
        cost_part = self.total_cost * ratio
        self.shares -= sell_shares
        self.total_cost -= cost_part
        self.total_sell_amount += sell_amount
        profit = sell_amount - cost_part
        self.trades.append({
            'date': date, 'action': 'SELL', 'price': round(price, 4),
            'amount': round(sell_amount, 2), 'shares': round(sell_shares, 4),
            'round': self.round_id,
            'reason': reason, 'total_shares': round(self.shares, 4),
            'profit': round(profit, 2),
        })
        if ratio >= 1.0:
            self._reset()

    def _reset(self):
        self.shares = 0
        self.total_cost = 0
        self.dca_active = False
        self.j_peak = 0.0
        self.pullback_sold = False
        self.sell_stage = 0
        self.waiting_golden = False
        self.golden_confirmed = False
        self.price_below_dk_sold = False
        self.buy_count = 0
        self.dca_exited = False

    def to_dict(self) -> dict:
        return {
            'round_id': self.round_id,
            'shares': self.shares,
            'total_cost': self.total_cost,
            'total_invested': self.total_invested,
            'total_sell_amount': self.total_sell_amount,
            'dca_active': self.dca_active,
            'j_peak': self.j_peak,
            'pullback_sold': self.pullback_sold,
            'sell_stage': self.sell_stage,
            'waiting_golden': self.waiting_golden,
            'golden_confirmed': self.golden_confirmed,
            'price_below_dk_sold': self.price_below_dk_sold,
            'round_budget': self.round_budget,
            'round_periods': self.round_periods,
            'buy_count': self.buy_count,
            'dca_exited': self.dca_exited,
            'trades': self.trades,
            'week_invested': list(self.week_invested),
        }

    @classmethod
    def from_dict(cls, data: dict, base_amount: float,
                  loss_threshold_1: float, loss_threshold_2: float,
                  j_buy_threshold: float, j_sell_half_threshold: float,
                  j_peak_min: float, j_pullback: float,
                  round_budget: float = 5000, round_periods: int = 5):
        p = cls(
            round_id=data.get('round_id', 0),
            base_amount=base_amount,
            loss_threshold_1=loss_threshold_1,
            loss_threshold_2=loss_threshold_2,
            j_buy_threshold=j_buy_threshold,
            j_sell_half_threshold=j_sell_half_threshold,
            j_peak_min=j_peak_min,
            j_pullback=j_pullback,
            round_budget=data.get('round_budget', round_budget),
            round_periods=data.get('round_periods', round_periods),
        )
        p.shares = data.get('shares', 0)
        p.total_cost = data.get('total_cost', 0)
        p.total_invested = data.get('total_invested', 0)
        p.total_sell_amount = data.get('total_sell_amount', 0)
        p.dca_active = data.get('dca_active', False)
        p.j_peak = data.get('j_peak', 0)
        p.pullback_sold = data.get('pullback_sold', False)
        p.sell_stage = data.get('sell_stage', 0)
        p.waiting_golden = data.get('waiting_golden', False)
        p.golden_confirmed = data.get('golden_confirmed', False)
        p.price_below_dk_sold = data.get('price_below_dk_sold', False)
        p.buy_count = data.get('buy_count', 0)
        p.dca_exited = data.get('dca_exited', False)
        p.trades = data.get('trades', [])
        p.week_invested = set(data.get('week_invested', []))
        return p


class WeeklyDCAStrategy:
    def __init__(self,
                 name: str,
                 base_amount: float = 1000,
                 loss_threshold_1: float = 0.05,
                 loss_threshold_2: float = 0.10,
                 j_buy_threshold: float = 13,
                 j_sell_half_threshold: float = 93,
                 j_peak_min: float = 50,
                 j_pullback: float = 20,
                 round_budget: float = 5000,
                 round_periods: int = 5,
                 sell_version: str = 'v1',
                 state_file: str = None):
        self.name = name
        self.base_amount = base_amount
        self.loss_threshold_1 = loss_threshold_1
        self.loss_threshold_2 = loss_threshold_2
        self.j_buy_threshold = j_buy_threshold
        self.j_sell_half_threshold = j_sell_half_threshold
        self.j_peak_min = j_peak_min
        self.j_pullback = j_pullback
        self.round_budget = round_budget
        self.round_periods = round_periods
        self.sell_version = sell_version
        self.state_file = state_file or f'dca_state_{name}.json'
        # 多仓位管理
        self.positions: List[Position] = []
        self.next_round_id = 1
        self.total_sell_amount = 0.0
        self.trades = []
        self.load_state()

    # ---- 汇总属性（兼容旧接口） ----
    @property
    def shares(self) -> float:
        return sum(p.shares for p in self.positions)

    @property
    def total_cost(self) -> float:
        return sum(p.total_cost for p in self.positions)

    @property
    def total_invested(self) -> float:
        return sum(p.total_invested for p in self.positions)

    @property
    def dca_active(self) -> bool:
        return any(p.dca_active for p in self.positions)

    @property
    def half_sold(self) -> bool:
        return any(p.sell_stage > 0 for p in self.positions)

    @property
    def sell_stage(self) -> int:
        stages = [p.sell_stage for p in self.positions if p.is_active]
        return max(stages) if stages else 0

    def get_avg_cost(self) -> float:
        s = self.shares
        if s <= 0:
            return 0
        return self.total_cost / s

    def get_current_loss_pct(self, current_price: float) -> float:
        avg = self.get_avg_cost()
        if avg <= 0:
            return 0
        return (avg - current_price) / avg

    # ---- 仓位管理 ----
    def _new_position(self) -> Position:
        pos = Position(
            round_id=self.next_round_id,
            base_amount=self.base_amount,
            loss_threshold_1=self.loss_threshold_1,
            loss_threshold_2=self.loss_threshold_2,
            j_buy_threshold=self.j_buy_threshold,
            j_sell_half_threshold=self.j_sell_half_threshold,
            j_peak_min=self.j_peak_min,
            j_pullback=self.j_pullback,
            round_budget=self.round_budget,
            round_periods=self.round_periods,
        )
        self.next_round_id += 1
        self.positions.append(pos)
        return pos

    def _active_positions(self) -> List[Position]:
        return [p for p in self.positions if p.is_active]

    def _dca_position(self) -> Optional[Position]:
        """找当前正在定投中的仓位（sell_stage==0 且 dca_active）"""
        for p in self.positions:
            if p.dca_active and p.sell_stage == 0:
                return p
        return None

    def _cleanup_cleared(self):
        """移除已清仓且无交易的仓位"""
        self.positions = [p for p in self.positions
                          if p.is_active or p.trades]

    # ---- 状态持久化 ----
    def load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                self.next_round_id = state.get('next_round_id', 1)
                self.total_sell_amount = state.get('total_sell_amount', 0)
                self.trades = state.get('trades', [])
                if 'sell_version' in state:
                    self.sell_version = state['sell_version']
                self.positions = []
                for pd_data in state.get('positions', []):
                    p = Position.from_dict(
                        pd_data, self.base_amount,
                        self.loss_threshold_1, self.loss_threshold_2,
                        self.j_buy_threshold, self.j_sell_half_threshold,
                        self.j_peak_min, self.j_pullback,
                        self.round_budget, self.round_periods,
                    )
                    self.positions.append(p)
                if self.positions:
                    self.next_round_id = max(self.next_round_id,
                                             max(p.round_id for p in self.positions) + 1)
                logging.info(f"[{self.name}] 加载状态: {len(self._active_positions())}个活跃仓位, "
                             f"总持仓={self.shares:.2f}")
            except Exception as e:
                logging.warning(f"[{self.name}] 加载状态失败: {e}")

    def save_state(self):
        def convert(obj):
            if isinstance(obj, (np.integer,)):
                return int(obj)
            elif isinstance(obj, (np.floating,)):
                return float(obj)
            elif isinstance(obj, np.ndarray):
                return obj.tolist()
            elif isinstance(obj, set):
                return list(obj)
            return obj
        state = {
            'next_round_id': self.next_round_id,
            'total_sell_amount': self.total_sell_amount,
            'trades': self.trades,
            'positions': [p.to_dict() for p in self.positions],
            'sell_version': self.sell_version,
        }
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, ensure_ascii=False, indent=2, default=convert)
        except Exception as e:
            logging.error(f"[{self.name}] 保存状态失败: {e}")

    def backtest(self, daily_df: pd.DataFrame, weekly_df: pd.DataFrame,
                 backtest_start: str = None):
        logging.info(f"\n{'='*70}")
        logging.info(f"  回测策略: {self.name}")
        logging.info(f"  基础定投金额: {self.base_amount}")
        logging.info(f"  亏损翻倍阈值: {self.loss_threshold_1*100}% / {self.loss_threshold_2*100}%")
        logging.info(f"  周线 J<={self.j_buy_threshold} 定投, J>={self.j_sell_half_threshold} 卖1/3")
        logging.info(f"  J峰值回撤>={self.j_pullback}(peak>={self.j_peak_min}) 卖1/3")
        if self.sell_version == 'v1':
            logging.info(f"  [V1] 收盘<多空线: 中期>多空卖1/3, 中期<多空全清")
            logging.info(f"  [V1] 死叉全清")
        else:
            logging.info(f"  [V2] 收盘<中期线: 中期>多空卖1/3, 中期<多空全清")
            logging.info(f"  [V2] 已卖2/3后: 收盘<多空线全清")
        logging.info(f"  多仓位独立管理: 每轮定投/卖出独立跟踪")
        if backtest_start:
            logging.info(f"  预热后回测起始: {backtest_start}")
        logging.info(f"{'='*70}")
        daily_with_duokong = calc_zhixing_duokong(daily_df)
        daily_dict = {}
        for _, row in daily_with_duokong.iterrows():
            daily_dict[row['trade_date']] = row.to_dict()
        weekly_kdj = calc_kdj(weekly_df)
        dead_cross_dates = set()
        golden_cross_dates = set()
        prev_mid = None
        prev_dk = None
        for _, row in daily_with_duokong.iterrows():
            mid = row.get('zhixing_mid')
            dk = row.get('zhixing_duokong')
            if pd.notna(mid) and pd.notna(dk) and prev_mid is not None and prev_dk is not None:
                if prev_mid >= prev_dk and mid < dk:
                    dead_cross_dates.add(row['trade_date'])
                if prev_mid <= prev_dk and mid > dk:
                    golden_cross_dates.add(row['trade_date'])
            prev_mid = mid
            prev_dk = dk
        all_dates = sorted(daily_dict.keys())
        weekly_dates_map = {}
        for _, row in weekly_kdj.iterrows():
            wd = row['trade_date']
            if pd.notna(row.get('J')):
                weekly_dates_map[wd] = row.to_dict()
        daily_to_weekly = {}
        sorted_weekly_dates = sorted(weekly_dates_map.keys())
        if not sorted_weekly_dates:
            logging.warning(f"[{self.name}] 无周线KDJ数据")
            return
        for d in all_dates:
            best_wd = None
            for wd in sorted_weekly_dates:
                if wd <= d:
                    best_wd = wd
                else:
                    break
            if best_wd:
                daily_to_weekly[d] = best_wd
        # 重置所有状态
        self.positions = []
        self.next_round_id = 1
        self.total_sell_amount = 0
        self.trades = []
        prev_week_key = None

        for trade_date in all_dates:
            if backtest_start and trade_date < backtest_start:
                continue
            if trade_date not in daily_dict:
                continue
            daily_row = daily_dict[trade_date]
            price = daily_row['close_qfq']
            dk_val_today = daily_row.get('zhixing_duokong')
            mid_val_today = daily_row.get('zhixing_mid')
            week_key = daily_to_weekly.get(trade_date)
            if week_key is None:
                continue
            week_data = weekly_dates_map.get(week_key, {})
            weekly_j = week_data.get('J')
            if weekly_j is None or pd.isna(weekly_j):
                continue
            if week_key != prev_week_key:
                is_new_week = True
                prev_week_key = week_key
            else:
                is_new_week = False

            # ===== 买入逻辑：找 sell_stage==0 的仓位定投 =====
            dca_pos = self._dca_position()
            if weekly_j <= self.j_buy_threshold:
                # 没有定投中的仓位，新建一个
                if dca_pos is None:
                    dca_pos = self._new_position()
                    dca_pos.dca_active = True
                    logging.info(f"[{self.name}] R{dca_pos.round_id} {trade_date} "
                                 f"周线J={weekly_j:.2f}<={self.j_buy_threshold}, 启动定投")
                if is_new_week and trade_date not in dca_pos.week_invested:
                    invest_amount = dca_pos.get_invest_amount(price)
                    if invest_amount > 0:
                        dca_pos.buy(trade_date, price, invest_amount,
                                    f"周线J={weekly_j:.2f}, 金额={invest_amount:.0f}")
                        dca_pos.week_invested.add(trade_date)
                        logging.info(f"[{self.name}] R{dca_pos.round_id} {trade_date} 买入: "
                                     f"价格={price:.4f}, 金额={invest_amount:.0f}")
            else:
                # J > 买入阈值：检查是否有定投仓位需要投入剩余预算
                if dca_pos is not None and not dca_pos.dca_exited:
                    remaining = dca_pos.get_budget_remaining()
                    if remaining > 0:
                        dca_pos.buy(trade_date, price, remaining,
                                    f"J回升={weekly_j:.2f}, 剩余预算投入={remaining:.0f}")
                        dca_pos.week_invested.add(trade_date)
                        logging.info(f"[{self.name}] R{dca_pos.round_id} {trade_date} "
                                     f"J回升投入剩余预算: 价格={price:.4f}, 金额={remaining:.0f}")
                    dca_pos.dca_exited = True

            # ===== 遍历每个活跃仓位，独立判断卖出 =====
            for pos in self._active_positions():
                # 追踪J峰值
                if pos.shares > 0 and pos.sell_stage == 0:
                    if weekly_j > pos.j_peak:
                        pos.j_peak = weekly_j

                # 金叉检测
                if pos.waiting_golden and trade_date in golden_cross_dates:
                    pos.waiting_golden = False
                    pos.golden_confirmed = True
                    pos.price_below_dk_sold = False
                    logging.info(f"[{self.name}] R{pos.round_id} {trade_date} "
                                 f"日线知行多空金叉，监控收盘<多空线或等死叉" if self.sell_version == 'v1'
                                 else f"日线知行多空金叉，监控收盘<中期线或等收盘<多空线")

                if pos.sell_stage == 0:
                    # 回撤止盈
                    if (pos.shares > 0 and not pos.pullback_sold
                            and pos.j_peak >= self.j_peak_min
                            and weekly_j <= pos.j_peak - self.j_pullback):
                        pos.sell(trade_date, price, 1.0 / 3,
                                 f"R{pos.round_id} J峰值回撤(J_peak={pos.j_peak:.2f}, "
                                 f"J={weekly_j:.2f}, 回撤={pos.j_peak - weekly_j:.2f})")
                        pos.sell_stage = 1
                        pos.pullback_sold = True
                        pos.waiting_golden = True
                        mid_val = daily_row.get('zhixing_mid')
                        dk_val = daily_row.get('zhixing_duokong')
                        if pd.notna(mid_val) and pd.notna(dk_val) and mid_val > dk_val:
                            pos.golden_confirmed = True
                            pos.waiting_golden = False
                            logging.info(f"[{self.name}] R{pos.round_id} {trade_date} "
                                         + ("回撤止盈卖1/3，多头区间，监控收盘<多空线"
                                            if self.sell_version == 'v1'
                                            else "回撤止盈卖1/3，多头区间，监控收盘<中期线"))
                        else:
                            pos.golden_confirmed = False
                            logging.info(f"[{self.name}] R{pos.round_id} {trade_date} "
                                         f"回撤止盈卖1/3，空头区间，等金叉")
                        self.total_sell_amount += pos.total_sell_amount

                    # J≥93 卖1/3
                    elif (pos.shares > 0 and weekly_j >= self.j_sell_half_threshold
                            and pos.sell_stage == 0):
                        pos.sell(trade_date, price, 1.0 / 3,
                                 f"R{pos.round_id} 周线J={weekly_j:.2f}"
                                 f">={self.j_sell_half_threshold}")
                        pos.sell_stage = 1
                        pos.waiting_golden = True
                        mid_val = daily_row.get('zhixing_mid')
                        dk_val = daily_row.get('zhixing_duokong')
                        if pd.notna(mid_val) and pd.notna(dk_val) and mid_val > dk_val:
                            pos.golden_confirmed = True
                            pos.waiting_golden = False
                            logging.info(f"[{self.name}] R{pos.round_id} {trade_date} "
                                         + ("卖1/3，多头区间，监控收盘<多空线"
                                            if self.sell_version == 'v1'
                                            else "卖1/3，多头区间，监控收盘<中期线"))
                        else:
                            pos.golden_confirmed = False
                            logging.info(f"[{self.name}] R{pos.round_id} {trade_date} "
                                         f"卖1/3，空头区间，等金叉")

                # 收盘<多空线(V1) 或 收盘<中期线(V2)：双空全清 vs 多头回调
                trigger_price = dk_val_today if self.sell_version == 'v1' else mid_val_today
                trigger_name = "多空线" if self.sell_version == 'v1' else "中期线"
                if (pos.shares > 0 and pos.golden_confirmed
                        and not pos.price_below_dk_sold
                        and pd.notna(trigger_price) and price < trigger_price):
                    mid_below_dk = (pd.notna(mid_val_today) and mid_val_today < dk_val_today)
                    if mid_below_dk:
                        pos.sell(trade_date, price, 1.0,
                                 f"R{pos.round_id} 双空全清: "
                                 f"收盘({price:.4f})<{trigger_name}({trigger_price:.4f}) "
                                 f"且中期({mid_val_today:.4f})<多空线")
                        logging.info(f"[{self.name}] R{pos.round_id} {trade_date} 双空全清")
                    else:
                        pos.sell(trade_date, price, 0.5,
                                 f"R{pos.round_id} 多头回调: "
                                 f"收盘({price:.4f})<{trigger_name}({trigger_price:.4f}), "
                                 f"中期({mid_val_today:.4f})>多空线")
                        pos.sell_stage = 2
                        pos.price_below_dk_sold = True
                        if self.sell_version == 'v1':
                            logging.info(f"[{self.name}] R{pos.round_id} {trade_date} "
                                         f"多头回调卖1/3，等死叉全清")
                        else:
                            logging.info(f"[{self.name}] R{pos.round_id} {trade_date} "
                                         f"多头回调卖1/3，等收盘<多空线全清")

                # V1: 死叉全清 | V2: 收盘<多空线全清
                if self.sell_version == 'v1':
                    clear_cond = (pos.shares > 0 and pos.golden_confirmed
                                  and pos.price_below_dk_sold
                                  and trade_date in dead_cross_dates)
                else:
                    clear_cond = (pos.shares > 0 and pos.golden_confirmed
                                  and pos.price_below_dk_sold
                                  and pd.notna(dk_val_today) and price < dk_val_today)
                if clear_cond:
                    if self.sell_version == 'v1':
                        pos.sell(trade_date, price, 1.0,
                                 f"R{pos.round_id} 日线知行多空死叉(全清剩余)")
                        logging.info(f"[{self.name}] R{pos.round_id} {trade_date} 死叉全清")
                    else:
                        pos.sell(trade_date, price, 1.0,
                                 f"R{pos.round_id} 收盘({price:.4f})<多空线({dk_val_today:.4f})全清")
                        logging.info(f"[{self.name}] R{pos.round_id} {trade_date} 收盘<多空线全清")

            # 更新汇总卖出金额和交易记录
            total_sell = 0
            all_trades = []
            for p in self.positions:
                total_sell += p.total_sell_amount
                all_trades.extend(p.trades)
            self.total_sell_amount = total_sell
            self.trades = sorted(all_trades, key=lambda t: (t['date'], t.get('round', 0)))
            self._cleanup_cleared()

        self._print_summary(daily_dict)

    def _print_summary(self, daily_dict: dict):
        if not daily_dict:
            return
        last_date = max(daily_dict.keys())
        last_price = daily_dict[last_date]['close_qfq']
        logging.info(f"\n{'='*70}")
        logging.info(f"  [{self.name}] 回测结果汇总")
        logging.info(f"{'='*70}")
        logging.info(f"  最后日期: {last_date}")
        logging.info(f"  最后价格: {last_price:.4f}")
        logging.info(f"  总投入: {self.total_invested:.2f}")
        logging.info(f"  总卖出: {self.total_sell_amount:.2f}")
        logging.info(f"  剩余持仓: {self.shares:.4f} 份")
        if self.shares > 0:
            market_value = self.shares * last_price
            total_return = market_value + self.total_sell_amount - self.total_invested
            return_pct = total_return / self.total_invested * 100
            avg_cost = self.get_avg_cost()
            logging.info(f"  持仓市值: {market_value:.2f}")
            logging.info(f"  平均成本: {avg_cost:.4f}")
            position_pnl_pct = (last_price - avg_cost) / avg_cost * 100
            logging.info(f"  持仓盈亏: {position_pnl_pct:+.2f}%")
            logging.info(f"  总收益: {total_return:.2f} ({return_pct:.2f}%)")
        else:
            total_return = self.total_sell_amount - self.total_invested
            if self.total_invested > 0:
                return_pct = total_return / self.total_invested * 100
            else:
                return_pct = 0
            logging.info(f"  已清仓, 总收益: {total_return:.2f} ({return_pct:.2f}%)")
        logging.info(f"  交易次数: {len(self.trades)}")
        buy_count = sum(1 for t in self.trades if t['action'] == 'BUY')
        sell_count = sum(1 for t in self.trades if t['action'] == 'SELL')
        logging.info(f"  买入: {buy_count} 次, 卖出: {sell_count} 次")
        logging.info(f"{'='*70}")
        self.save_state()

    def get_next_action(self, daily_df: pd.DataFrame,
                        weekly_df: pd.DataFrame) -> dict:
        daily_with_dk = calc_zhixing_duokong(daily_df)
        weekly_kdj = calc_kdj(weekly_df)
        last_daily = daily_with_dk.iloc[-1]
        last_weekly = weekly_kdj.iloc[-1]
        last_price = last_daily['close_qfq']
        last_date = last_daily['trade_date']
        weekly_j = last_weekly.get('J')
        if weekly_j is None or pd.isna(weekly_j):
            weekly_j = 50.0
        mid_val = last_daily.get('zhixing_mid')
        dk_val = last_daily.get('zhixing_duokong')
        mid_above_dk = False
        if pd.notna(mid_val) and pd.notna(dk_val):
            mid_above_dk = mid_val > dk_val
        loss_pct = self.get_current_loss_pct(last_price)
        profit_pct = -loss_pct
        avg_cost = self.get_avg_cost()
        result = {
            'last_date': last_date,
            'last_price': round(last_price, 4),
            'weekly_j': round(float(weekly_j), 2),
            'weekly_k': round(float(last_weekly.get('K', 0)), 2),
            'weekly_d': round(float(last_weekly.get('D', 0)), 2),
            'mid_value': round(float(mid_val), 4) if pd.notna(mid_val) else None,
            'dk_value': round(float(dk_val), 4) if pd.notna(dk_val) else None,
            'mid_above_dk': mid_above_dk,
            'shares': round(self.shares, 4),
            'avg_cost': round(avg_cost, 4) if avg_cost > 0 else None,
            'loss_pct': round(profit_pct * 100, 2),
            'dca_active': self.dca_active,
            'half_sold': self.half_sold,
            'sell_stage': self.sell_stage,
            'positions': [],
        }
        # 收集每个活跃仓位的操作建议
        active = self._active_positions()
        for pos in active:
            pos_info = {
                'round_id': pos.round_id,
                'shares': round(pos.shares, 4),
                'avg_cost': round(pos.get_avg_cost(), 4) if pos.shares > 0 else None,
                'sell_stage': pos.sell_stage,
                'j_peak': round(pos.j_peak, 2),
                'dca_active': pos.dca_active,
            }
            pos_loss = pos.get_current_loss_pct(last_price)
            pos_profit = -pos_loss

            # 计算每轮收益率：当前市值 + 已卖出金额 - 投入金额) / 投入金额
            if pos.total_invested > 0:
                pos_market_value = pos.shares * last_price
                pos_total_return = pos_market_value + pos.total_sell_amount - pos.total_invested
                pos_return_pct = round(pos_total_return / pos.total_invested * 100, 2)
                pos_info['total_invested'] = round(pos.total_invested, 2)
                pos_info['total_return'] = round(pos_total_return, 2)
                pos_info['return_pct'] = pos_return_pct
                pos_info['loss_pct'] = round(pos_profit * 100, 2)
            invest_amount = pos.get_invest_amount(last_price)
            multiplier = int(invest_amount / self.base_amount) if self.base_amount > 0 else 1

            if pos.sell_stage == 0:
                # 回撤止盈
                if (pos.shares > 0 and not pos.pullback_sold
                        and pos.j_peak >= self.j_peak_min
                        and weekly_j <= pos.j_peak - self.j_pullback):
                    pos_info['action'] = 'sell_third_pullback'
                    pos_info['action_label'] = f'R{pos.round_id} 建议卖出1/3(回撤止盈)'
                    pos_info['action_color'] = '#2ecc71'
                    pullback_pts = pos.j_peak - weekly_j
                    pos_info['action_detail'] = (
                        f"R{pos.round_id} J峰值回撤: J_peak={pos.j_peak:.2f}, "
                        f"J={weekly_j:.2f}, 回撤{pullback_pts:.2f}点"
                    )
                elif pos.shares > 0 and weekly_j >= self.j_sell_half_threshold:
                    pos_info['action'] = 'sell_third'
                    pos_info['action_label'] = f'R{pos.round_id} 建议卖出1/3'
                    pos_info['action_color'] = '#2ecc71'
                    pos_info['action_detail'] = (
                        f"R{pos.round_id} 周线J={weekly_j:.2f}"
                        f">={self.j_sell_half_threshold}，短期过热"
                    )
                elif pos.dca_active and weekly_j <= self.j_buy_threshold:
                    pos_info['action'] = 'buy'
                    pos_info['action_label'] = f'R{pos.round_id} 建议定投({multiplier}x)'
                    pos_info['action_color'] = '#e74c3c'
                    pnl_label = "浮盈" if pos_profit > 0 else "浮亏"
                    pos_info['action_detail'] = (
                        f"R{pos.round_id} 定投中，{pnl_label}{abs(pos_profit)*100:.2f}%，"
                        f"建议买入{invest_amount:.0f}元({multiplier}x)"
                    )
                elif pos.dca_active:
                    pos_info['action'] = 'hold'
                    pos_info['action_label'] = f'R{pos.round_id} 定投暂停·持有观望'
                    pos_info['action_color'] = '#f39c12'
                    pos_info['action_detail'] = f"R{pos.round_id} J={weekly_j:.2f}，等卖出或买入信号"
                elif pos.shares > 0:
                    pos_info['action'] = 'hold'
                    pos_info['action_label'] = f'R{pos.round_id} 持有观望'
                    pos_info['action_color'] = '#f39c12'
                    pos_info['action_detail'] = f"R{pos.round_id} 等待卖出信号"
            elif pos.sell_stage == 1:
                if not mid_above_dk:
                    pos_info['action'] = 'wait_golden'
                    pos_info['action_label'] = f'R{pos.round_id} 已卖1/3·等金叉'
                    pos_info['action_color'] = '#9b59b6'
                    pos_info['action_detail'] = f"R{pos.round_id} 空头区间，等金叉"
                else:
                    dk_v = dk_val if pd.notna(dk_val) else 0
                    mid_v = mid_val if pd.notna(mid_val) else 0
                    if self.sell_version == 'v1':
                        pos_info['action'] = 'wait_price_below_dk'
                        pos_info['action_label'] = f'R{pos.round_id} 已卖1/3·等收盘<多空线'
                        pos_info['action_color'] = '#e67e22'
                        pos_info['action_detail'] = (
                            f"R{pos.round_id} 监控收盘<多空线({dk_v:.4f})，"
                            f"中期>多空卖1/3，中期<多空全清"
                        )
                    else:
                        pos_info['action'] = 'wait_price_below_mid'
                        pos_info['action_label'] = f'R{pos.round_id} 已卖1/3·等收盘<中期线'
                        pos_info['action_color'] = '#e67e22'
                        pos_info['action_detail'] = (
                            f"R{pos.round_id} 监控收盘<中期线({mid_v:.4f})，"
                            f"中期>多空卖1/3，中期<多空全清"
                        )
            elif pos.sell_stage == 2:
                if self.sell_version == 'v1':
                    pos_info['action'] = 'wait_dead_cross'
                    pos_info['action_label'] = f'R{pos.round_id} 已卖2/3·等死叉'
                    pos_info['action_color'] = '#f39c12'
                    pos_info['action_detail'] = f"R{pos.round_id} 等死叉全清"
                else:
                    dk_v = dk_val if pd.notna(dk_val) else 0
                    pos_info['action'] = 'wait_price_below_dk'
                    pos_info['action_label'] = f'R{pos.round_id} 已卖2/3·等收盘<多空线'
                    pos_info['action_color'] = '#f39c12'
                    pos_info['action_detail'] = f"R{pos.round_id} 等收盘<多空线全清"
            else:
                pos_info['action'] = 'unknown'
                pos_info['action_label'] = f'R{pos.round_id} 未知'
                pos_info['action_color'] = '#95a5a6'
            result['positions'].append(pos_info)

        # 生成汇总操作建议（取第一个活跃仓位或空仓提示）
        if not active:
            if weekly_j <= self.j_buy_threshold:
                result['action'] = 'buy'
                result['action_label'] = '建议买入'
                result['action_color'] = '#e74c3c'
                result['action_detail'] = (
                    f"周线J={weekly_j:.2f}<={self.j_buy_threshold}，"
                    f"建议以{self.base_amount:.0f}元定投买入"
                )
            else:
                result['action'] = 'wait'
                result['action_label'] = '空仓等待'
                result['action_color'] = '#95a5a6'
                result['action_detail'] = (
                    f"周线J={weekly_j:.2f}，空仓等待J<={self.j_buy_threshold}"
                )
        else:
            # 取优先级最高的操作
            first = result['positions'][0]
            result['action'] = first.get('action', 'unknown')
            result['action_label'] = first.get('action_label', '未知')
            result['action_color'] = first.get('action_color', '#95a5a6')
            details = [p.get('action_detail', '') for p in result['positions']]
            result['action_detail'] = ' | '.join(details)
        return result

    def calc_nav_curve(self, daily_df: pd.DataFrame) -> pd.DataFrame:
        trade_map = {}
        for t in self.trades:
            d = t['date']
            if d not in trade_map:
                trade_map[d] = {'buy_amount': 0, 'sell_amount': 0, 'sell_profit': 0}
            if t['action'] == 'BUY':
                trade_map[d]['buy_amount'] += t.get('amount', 0)
            else:
                trade_map[d]['sell_amount'] += t.get('amount', 0)
                trade_map[d]['sell_profit'] += t.get('profit', 0)
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
                    avg_cost = (old_cost + tm['buy_amount']) / shares if shares > 0 else 0
                if tm['sell_amount'] > 0:
                    sell_ratio = tm['sell_amount'] / (shares * price) if shares * price > 0 else 0
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


def _safe_list(lst):
    return [None if pd.isna(v) else v for v in lst]


def _darken_color(hex_color: str) -> str:
    """将hex颜色加深，用于区分同一轮次买入(亮)和卖出(暗)"""
    hex_color = hex_color.lstrip('#')
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    r, g, b = int(r * 0.55), int(g * 0.55), int(b * 0.55)
    return f'#{r:02x}{g:02x}{b:02x}'


def generate_backtest_report(strategy: WeeklyDCAStrategy,
                             daily_df: pd.DataFrame,
                             weekly_df: pd.DataFrame,
                             output_file: str = None) -> str:
    if output_file is None:
        output_file = f'dca_report_{strategy.name}.html'
    daily_kdj = calc_kdj(daily_df)
    weekly_kdj = calc_kdj(weekly_df)
    dates = daily_kdj['trade_date'].tolist()
    prices = _safe_list(daily_kdj['close_qfq'].tolist())
    weekly_dates_list = weekly_kdj['trade_date'].tolist()
    weekly_k = _safe_list(weekly_kdj['K'].tolist())
    weekly_d = _safe_list(weekly_kdj['D'].tolist())
    weekly_j = _safe_list(weekly_kdj['J'].tolist())
    # 按轮次分配颜色
    round_colors = {}
    palette = ['#e74c3c', '#3498db', '#9b59b6', '#e67e22', '#1abc9c',
               '#f39c12', '#2ecc71', '#e84393', '#00b894', '#6c5ce7',
               '#fd79a8', '#0984e3', '#d63031', '#00cec9', '#fdcb6e']
    # 按轮次生成标记点（带颜色）
    mark_points = []
    for t in strategy.trades:
        rid = t.get('round', 0)
        if rid not in round_colors:
            round_colors[rid] = palette[len(round_colors) % len(palette)]
        color = round_colors[rid]
        if t['action'] == 'BUY':
            label = f'R{rid}买'
            symbol = 'circle'
        else:
            label = f'R{rid}卖'
            symbol = 'diamond'
            # 卖出用更深的颜色变体
            color = _darken_color(color)
        mark_points.append(
            f"{{xAxis: '{t['date']}', yAxis: {t['price']:.4f}, "
            f"name: '{label}', itemStyle: {{color: '{color}'}}, "
            f"symbol: '{symbol}', symbolSize: 30,"
            f"label: {{formatter: '{label}', color: '#fff', fontSize: 9}}}}"
        )
    daily_with_dk = calc_zhixing_duokong(daily_df)
    dk_values = _safe_list(daily_with_dk['zhixing_duokong'].tolist())
    mid_values = _safe_list(daily_with_dk['zhixing_mid'].tolist())
    dead_cross_list = []
    prev_mid_v = None
    prev_dk_v = None
    for i, row in daily_with_dk.iterrows():
        mid_v = row.get('zhixing_mid')
        dk_v = row.get('zhixing_duokong')
        if pd.notna(mid_v) and pd.notna(dk_v) and prev_mid_v is not None and prev_dk_v is not None:
            if prev_mid_v >= prev_dk_v and mid_v < dk_v:
                dead_cross_list.append({'date': row['trade_date'], 'price': row['close_qfq']})
        prev_mid_v = mid_v
        prev_dk_v = dk_v
    next_action = strategy.get_next_action(daily_df, weekly_df)
    na = next_action
    mid_dk_diff = ''
    if na['mid_value'] is not None and na['dk_value'] is not None:
        mid_dk_diff = f"中期={na['mid_value']:.4f} 多空={na['dk_value']:.4f} " \
                      f"{'(多头)' if na['mid_above_dk'] else '(空头)'}"
    position_info = ''
    if na['shares'] > 0:
        pnl = na['loss_pct']
        pnl_label = "浮盈" if pnl > 0 else "浮亏"
        position_info = f"持仓={na['shares']:.2f}份 成本={na['avg_cost']:.4f} {pnl_label}={abs(pnl):.2f}%"
    # 多仓位卡片HTML
    position_cards_html = ''
    for pos_data in na.get('positions', []):
        rid = pos_data.get('round_id', '?')
        color = round_colors.get(rid, '#95a5a6')
        stage_labels = {0: '定投中', 1: '已卖1/3', 2: '已卖2/3'}
        stage_text = stage_labels.get(pos_data.get('sell_stage', 0), '已清仓')
        pos_shares = pos_data.get('shares', 0)
        pos_cost = pos_data.get('avg_cost', 0)
        pos_j_peak = pos_data.get('j_peak', 0)
        position_cards_html += f"""
<div class="pos-card" style="border-left: 4px solid {color};">
    <div class="pos-header">
        <span class="pos-round" style="background: {color};">R{rid}</span>
        <span class="pos-stage">{stage_text}</span>
    </div>
    <div class="pos-detail">{pos_data.get('action_label', '')}</div>
    <div class="pos-metrics">
        <span>持仓: {pos_shares:.2f}份</span>
        <span>成本: {pos_cost:.4f}</span>
        <span>J峰值: {pos_j_peak:.2f}</span>
    </div>
</div>"""
    sell_desc = ("收盘<多空线:中期>多空卖1/3,中期<多空全清 | 死叉全清"
                 if strategy.sell_version == 'v1'
                 else "收盘<中期线:中期>多空卖1/3,中期<多空全清 | 已卖2/3后收盘<多空线全清")
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>周线KDJ定投策略 - {strategy.name}</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>
body {{ font-family: 'Microsoft YaHei', Arial, sans-serif; background: #f0f2f5; margin: 0; padding: 20px; }}
.container {{ max-width: 1200px; margin: 0 auto; }}
h1 {{ text-align: center; color: #333; font-size: 24px; margin-bottom: 5px; }}
h2 {{ color: #333; font-size: 18px; margin: 15px 0 10px; }}
.subtitle {{ text-align: center; color: #666; margin-bottom: 20px; font-size: 14px; }}
.summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 20px; }}
.card {{ background: white; border-radius: 8px; padding: 15px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
.card-label {{ color: #666; font-size: 12px; margin-bottom: 5px; }}
.card-value {{ font-size: 20px; font-weight: bold; color: #333; }}
.action-box {{ background: white; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); border-left: 5px solid {na['action_color']}; }}
.action-header {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }}
.action-label {{ font-size: 22px; font-weight: bold; color: {na['action_color']}; }}
.action-date {{ color: #999; font-size: 13px; }}
.action-detail {{ font-size: 14px; color: #555; line-height: 1.8; padding: 10px; background: #fafafa; border-radius: 4px; }}
.action-metrics {{ display: flex; flex-wrap: wrap; gap: 15px; margin-top: 12px; }}
.action-metric {{ padding: 6px 12px; background: #f0f2f5; border-radius: 4px; font-size: 13px; color: #333; }}
.positions-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; margin-bottom: 20px; }}
.pos-card {{ background: white; border-radius: 8px; padding: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
.pos-header {{ display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }}
.pos-round {{ color: white; padding: 2px 8px; border-radius: 3px; font-size: 12px; font-weight: bold; }}
.pos-stage {{ font-size: 12px; color: #666; }}
.pos-detail {{ font-size: 14px; font-weight: bold; color: #333; margin-bottom: 6px; }}
.pos-metrics {{ display: flex; flex-wrap: wrap; gap: 8px; }}
.pos-metrics span {{ font-size: 11px; color: #888; background: #f5f5f5; padding: 2px 6px; border-radius: 3px; }}
.chart {{ background: white; border-radius: 8px; padding: 15px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
.trade-table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
.trade-table th {{ background: #f5f7fa; padding: 8px; text-align: left; border-bottom: 2px solid #ddd; }}
.trade-table td {{ padding: 6px 8px; border-bottom: 1px solid #eee; }}
.trade-table tr:hover {{ background: #f9f9f9; }}
.buy-tag {{ background: #e74c3c; color: white; padding: 2px 6px; border-radius: 3px; font-size: 11px; }}
.sell-tag {{ background: #2ecc71; color: white; padding: 2px 6px; border-radius: 3px; font-size: 11px; }}
.round-tag {{ color: white; padding: 2px 6px; border-radius: 3px; font-size: 11px; }}
</style>
</head>
<body>
<div class="container">
<h1>周线KDJ定投策略回测报告</h1>
<div class="subtitle">{strategy.name} | 基础金额: {strategy.base_amount} | J&lt;={strategy.j_buy_threshold}定投 | J&gt;={strategy.j_sell_half_threshold}卖1/3 | 回撤止盈(peak&gt;={strategy.j_peak_min},回落&gt;={strategy.j_pullback}) | {sell_desc}</div>
<div class="action-box">
<div class="action-header">
    <div class="action-label">下一交易日: {na['action_label']}</div>
    <div class="action-date">数据截至: {na['last_date']} 收盘价: {na['last_price']:.4f}</div>
</div>
<div class="action-detail">{na['action_detail']}</div>
<div class="action-metrics">
    <div class="action-metric">周线K={na['weekly_k']:.2f}</div>
    <div class="action-metric">周线D={na['weekly_d']:.2f}</div>
    <div class="action-metric">周线J={na['weekly_j']:.2f}</div>
    {"<div class='action-metric'>" + mid_dk_diff + "</div>" if mid_dk_diff else ""}
    {"<div class='action-metric'>" + position_info + "</div>" if position_info else ""}
</div>
</div>
<div class="summary">
<div class="card">
    <div class="card-label">总投入</div>
    <div class="card-value">{strategy.total_invested:.2f}</div>
</div>
<div class="card">
    <div class="card-label">总卖出</div>
    <div class="card-value">{strategy.total_sell_amount:.2f}</div>
</div>
<div class="card">
    <div class="card-label">剩余持仓</div>
    <div class="card-value">{strategy.shares:.4f} 份</div>
</div>
<div class="card">
    <div class="card-label">交易次数</div>
    <div class="card-value">{len(strategy.trades)}</div>
</div>
<div class="card">
    <div class="card-label">轮次数</div>
    <div class="card-value">{len(strategy.positions)}</div>
</div>
</div>
<h2>各仓位状态</h2>
<div class="positions-grid">
{position_cards_html}
</div>
<div class="chart">
<h3 style="margin-top:0">周线 KDJ</h3>
<div id="weekly_kdj_chart" style="height:300px;"></div>
</div>
<div class="chart">
<h3 style="margin-top:0">日线走势 + 知行多空线 + 买卖标记</h3>
<div id="daily_chart" style="height:500px;"></div>
</div>
<div class="chart">
<h3 style="margin-top:0">交易记录</h3>
<div style="overflow-x:auto;">
<table class="trade-table">
<tr>
    <th>日期</th><th>轮次</th><th>操作</th><th>价格</th><th>金额</th><th>份额</th>
    <th>原因</th><th>持仓</th><th>盈亏</th>
</tr>"""
    for t in strategy.trades:
        rid = t.get('round', 0)
        rid_color = round_colors.get(rid, '#95a5a6')
        tag = '<span class="buy-tag">买入</span>' if t['action'] == 'BUY' else '<span class="sell-tag">卖出</span>'
        tag_bg = rid_color if t['action'] == 'BUY' else _darken_color(rid_color)
        round_tag = f'<span class="round-tag" style="background: {tag_bg};">R{rid}</span>'
        profit_str = f"{t.get('profit', ''):.2f}" if 'profit' in t and t.get('profit') is not None else '-'
        html += f"""
<tr>
    <td>{t['date']}</td>
    <td>{round_tag}</td>
    <td>{tag}</td>
    <td>{t['price']:.4f}</td>
    <td>{t.get('amount', 0):.2f}</td>
    <td>{t.get('shares', 0):.4f}</td>
    <td>{t.get('reason', '-')}</td>
    <td>{t.get('total_shares', 0):.4f}</td>
    <td>{profit_str}</td>
</tr>"""
    html += """
</table>
</div>
</div>
</div>
<script>
"""
    html += f"""
var weeklyDates = {json.dumps(weekly_dates_list)};
var weeklyK = {json.dumps(weekly_k)};
var weeklyD = {json.dumps(weekly_d)};
var weeklyJ = {json.dumps(weekly_j)};
var wkChart = echarts.init(document.getElementById('weekly_kdj_chart'));
wkChart.setOption({{
    tooltip: {{ trigger: 'axis' }},
    legend: {{ data: ['K','D','J'], bottom: 0 }},
    grid: {{ left: 60, right: 30, top: 20, bottom: 40 }},
    xAxis: {{ type: 'category', data: weeklyDates, axisLabel: {{ rotate: 45, fontSize: 10 }} }},
    yAxis: {{ type: 'value', scale: true }},
    series: [
        {{ name: 'K', type: 'line', data: weeklyK, lineStyle: {{ width: 1 }},
           markLine: {{ silent: true, data: [
               {{ yAxis: 13, lineStyle: {{ color: '#e74c3c', type: 'dashed' }} }},
               {{ yAxis: 100, lineStyle: {{ color: '#2ecc71', type: 'dashed' }} }}
           ] }} }},
        {{ name: 'D', type: 'line', data: weeklyD, lineStyle: {{ width: 1 }} }},
        {{ name: 'J', type: 'line', data: weeklyJ, lineStyle: {{ width: 2 }},
           itemStyle: {{ color: '#5470c6' }} }}
    ]
}});
var dailyDates = {json.dumps(dates)};
var dailyPrices = {json.dumps(prices)};
var dkValues = {json.dumps(dk_values)};
var midValues = {json.dumps(mid_values)};
var deadCross = {json.dumps(dead_cross_list)};
var dChart = echarts.init(document.getElementById('daily_chart'));
"""
    mark_data = '[' + ','.join(mark_points) + ']'
    html += f"""
var mainSeries = {{
    name: '收盘价', type: 'line', data: dailyPrices,
    lineStyle: {{ width: 1 }}, itemStyle: {{ color: '#333' }},
    markPoint: {{
        symbol: 'pin', symbolSize: 40, animation: false,
        label: {{ show: true, fontSize: 10, color: '#fff' }},
        data: {mark_data}
    }}
}};
"""
    html += """
var seriesList = [
    mainSeries,
    { name: '知行多空', type: 'line', data: dkValues, lineStyle: { width: 1.5 },
       itemStyle: { color: '#e74c3c' }, connectNulls: true },
    { name: '知行中期', type: 'line', data: midValues, lineStyle: { width: 1.5 },
       itemStyle: { color: '#f39c12' }, connectNulls: true }
];
dChart.setOption({
    tooltip: {
        trigger: 'axis',
        formatter: function(params) {
            var s = params[0].axisValue + '<br/>';
            params.forEach(function(p) {
                if (p.seriesName === '收盘价') {
                    s += '收盘: ' + p.value.toFixed(4) + '<br/>';
                } else {
                    s += p.marker + p.seriesName + ': ' + (p.value ? p.value.toFixed(2) : '-') + '<br/>';
                }
            });
            return s;
        }
    },
    legend: { data: ['收盘价','知行多空','知行中期'], bottom: 0 },
    grid: { left: 60, right: 30, top: 30, bottom: 40 },
    xAxis: { type: 'category', data: dailyDates, axisLabel: { rotate: 45, fontSize: 10 } },
    yAxis: { type: 'value', scale: true },
    dataZoom: [
        { type: 'inside', start: 70, end: 100 },
        { type: 'slider', start: 70, end: 100 }
    ],
    series: seriesList
});
window.addEventListener('resize', function() { wkChart.resize(); dChart.resize(); });
</script>
</body>
</html>"""
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html)
    logging.info(f"回测报告已保存: {output_file}")
    return output_file


def run_backtest(target: str = 'all',
                 base_amount: float = 1000,
                 years: int = 5,
                 output_dir: str = 'html/dca',
                 custom_ts_code: str = None,
                 custom_name: str = None,
                 sell_version: str = None):
    os.makedirs(output_dir, exist_ok=True)
    backtest_start = (datetime.now() - timedelta(days=years * 365)).strftime('%Y%m%d')
    results = {}

    etf_configs = {
        'nasdaq': {
            'name': 'NASDAQ100',
            'report_name': 'NASDAQ100',
            'data_func': lambda: get_nasdaq_data(years=years + 1),
        },
        'dividend': {
            'name': '红利低波',
            'report_name': '红利低波',
            'ts_code': '512890.SH',
        },
        'sz50': {
            'name': '上证50',
            'report_name': '上证50',
            'ts_code': '510050.SH',
        },
        'hs300': {
            'name': '沪深300',
            'report_name': '沪深300',
            'ts_code': '510300.SH',
        },
        'zz2000': {
            'name': '中证2000',
            'report_name': '中证2000',
            'ts_code': '563300.SH',
        },
        'telecom': {
            'name': '通信',
            'report_name': '通信',
            'ts_code': '515880.SH',
        },
        'innovative_medicine': {
            'name': '创新药',
            'report_name': '创新药',
            'ts_code': '516080.SH',
        },
        'electricity': {
            'name': '电力',
            'report_name': '电力',
            'ts_code': '159611.SZ',
        },
        'kechuang_semi': {
            'name': '科创半导体',
            'report_name': '科创半导体',
            'ts_code': '588170.SH',
        },
        'satellite': {
            'name': '卫星ETF富国',
            'report_name': '卫星ETF富国',
            'ts_code': '563230.SH',
        },
        'nonferrous_metals': {
            'name': '有色金属',
            'report_name': '有色金属',
            'ts_code': '512400.SH',
        },
    }

    if target == 'custom' and custom_ts_code:
        etf_configs['custom'] = {
            'name': custom_name or custom_ts_code,
            'report_name': custom_name or custom_ts_code,
            'ts_code': custom_ts_code,
        }
        targets = ['custom']
    elif target == 'all':
        targets = list(etf_configs.keys())
    elif target == 'both':
        targets = ['nasdaq', 'dividend']
    else:
        targets = [target]

    for tgt in targets:
        if tgt not in etf_configs:
            logging.warning(f"未知标的: {tgt}")
            continue
        cfg = etf_configs[tgt]
        logging.info("\n" + "=" * 70)
        logging.info(f"  获取{cfg['name']}数据...")
        logging.info("=" * 70)
        try:
            if 'data_func' in cfg:
                daily_warmup = cfg['data_func']()
            else:
                daily_warmup = _get_etf_data(
                    cfg['ts_code'],
                    datetime.now() - timedelta(days=(years + 1) * 365),
                    datetime.now())
            weekly = resample_to_weekly(daily_warmup)
            actual_data_start = daily_warmup['trade_date'].min()
            effective_start = backtest_start
            warmup_weeks_needed = 30
            if len(weekly) > warmup_weeks_needed:
                warmup_end_date = weekly.iloc[warmup_weeks_needed]['trade_date']
                if warmup_end_date > backtest_start:
                    effective_start = warmup_end_date
                    data_years = (datetime.now() - pd.to_datetime(actual_data_start)).days / 365
                    logging.info(f"  {cfg['name']} 数据起始: {actual_data_start} ({data_years:.1f}年), "
                                 f"预热{warmup_weeks_needed}周后回测起始: {effective_start}")
            sv = sell_version or ('v1' if tgt == 'nasdaq' else 'v2')
            strategy = WeeklyDCAStrategy(
                name=cfg['name'],
                base_amount=base_amount,
                sell_version=sv,
            )
            strategy.backtest(daily_warmup, weekly,
                              backtest_start=effective_start)
            strategy._nav_curve = strategy.calc_nav_curve(daily_warmup)
            strategy._daily_df = daily_warmup
            strategy._weekly_df = weekly
            report_file = os.path.join(output_dir, f"dca_report_{cfg['report_name']}.html")
            generate_backtest_report(strategy, daily_warmup, weekly, report_file)
            results[cfg['name']] = strategy
        except Exception as e:
            logging.error(f"{cfg['name']}策略回测失败: {e}")

    if len(results) >= 2:
        _generate_comparison_chart(results, output_dir)

    summary = _generate_dca_summary(results, output_dir)

    return results


def _generate_comparison_chart(results: dict, output_dir: str):
    for name, strategy in results.items():
        if not hasattr(strategy, '_nav_curve'):
            return
    chart_file = os.path.join(output_dir, 'dca_comparison.html')
    series_data = {}
    for name, strategy in results.items():
        nav_df = strategy._nav_curve
        if nav_df is not None and not nav_df.empty:
            series_data[name] = {
                'dates': nav_df['trade_date'].tolist(),
                'nav': nav_df['nav'].tolist(),
            }
    if len(series_data) < 2:
        return
    names = list(series_data.keys())
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>周线KDJ定投策略 - 收益率对比</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>
body {{ font-family: 'Microsoft YaHei', Arial, sans-serif; background: #f0f2f5; margin: 0; padding: 20px; }}
.container {{ max-width: 1200px; margin: 0 auto; }}
h1 {{ text-align: center; color: #333; }}
.chart {{ background: white; border-radius: 8px; padding: 15px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
</style>
</head>
<body>
<div class="container">
<h1>周线KDJ定投策略 - 收益率对比</h1>
<div class="chart">
<div id="nav_chart" style="height:500px;"></div>
</div>
</div>
<script>
var chart = echarts.init(document.getElementById('nav_chart'));
var series = [];"""
    colors = ['#e74c3c', '#3498db']
    for i, (name, data) in enumerate(series_data.items()):
        pct_data = [round((v - 1) * 100, 2) for v in data['nav']]
        html += f"""
series.push({{
    name: '{name}',
    type: 'line',
    data: {pct_data},
    lineStyle: {{ width: 2, color: '{colors[i % len(colors)]}' }},
    itemStyle: {{ color: '{colors[i % len(colors)]}' }},
    connectNulls: true
}});"""
    html += f"""
chart.setOption({{
    title: {{ text: '累计收益率对比 (%)', left: 'center' }},
    tooltip: {{
        trigger: 'axis',
        formatter: function(params) {{
            var s = params[0].axisValue + '<br/>';
            params.forEach(function(p) {{
                s += p.marker + p.seriesName + ': ' + p.value.toFixed(2) + '%<br/>';
            }});
            return s;
        }}
    }},
    legend: {{ data: {names}, bottom: 0 }},
    grid: {{ left: 80, right: 30, top: 50, bottom: 40 }},
    xAxis: {{
        type: 'category',
        data: {series_data[names[0]]['dates']},
        axisLabel: {{ rotate: 45, fontSize: 10 }}
    }},
    yAxis: {{
        type: 'value',
        axisLabel: {{ formatter: '{{value}}%' }}
    }},
    series: series
}});
window.addEventListener('resize', function() {{ chart.resize(); }});
</script>
</body>
</html>"""
    with open(chart_file, 'w', encoding='utf-8') as f:
        f.write(html)
    logging.info(f"收益率对比图已保存: {chart_file}")


def _generate_dca_summary(results: dict, output_dir: str) -> dict:
    name_to_tscode = {}
    try:
        with open('etf_config.json', 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        for etf in cfg.get('etf_list', []):
            name_to_tscode[etf['name']] = etf.get('ts_code', '')
    except Exception:
        pass
    summary_items = []
    for name, strategy in results.items():
        try:
            report_file = os.path.join(output_dir, f"dca_report_{strategy.name}.html")
            if not os.path.exists(report_file):
                for fn in os.listdir(output_dir):
                    if fn.startswith('dca_report_') and strategy.name in fn:
                        report_file = os.path.join(output_dir, fn)
                        break
            nav_curve = getattr(strategy, '_nav_curve', None)
            last_date = ''
            if nav_curve is not None and not nav_curve.empty:
                last_date = str(nav_curve['trade_date'].iloc[-1])
            nav_last = nav_curve.iloc[-1] if nav_curve is not None and not nav_curve.empty else None
            cum_invested = nav_last.get('cum_invested', strategy.total_invested) if nav_last is not None else strategy.total_invested
            total_value = nav_last.get('total_value', 0) if nav_last is not None else 0
            total_return = total_value + strategy.total_sell_amount - cum_invested
            return_pct = (total_return / cum_invested * 100) if cum_invested > 0 else 0
            action_info = {}
            daily_df = getattr(strategy, '_daily_df', None)
            weekly_df = getattr(strategy, '_weekly_df', None)
            if daily_df is not None and weekly_df is not None:
                try:
                    action_info = strategy.get_next_action(daily_df, weekly_df)
                except Exception as e:
                    logging.warning(f"获取{name}操作提示失败: {e}")
                    action_info = {}
            item = {
                'name': strategy.name,
                'ts_code': name_to_tscode.get(strategy.name, ''),
                'report_file': os.path.basename(report_file),
                'action_label': action_info.get('action_label', '未知'),
                'action_color': action_info.get('action_color', '#95a5a6'),
                'action': action_info.get('action', 'unknown'),
                'action_detail': action_info.get('action_detail', ''),
                'positions': action_info.get('positions', []),
                'last_date': action_info.get('last_date', last_date),
                'last_price': action_info.get('last_price', 0),
                'weekly_j': action_info.get('weekly_j', 0),
                'weekly_k': action_info.get('weekly_k', 0),
                'weekly_d': action_info.get('weekly_d', 0),
                'shares': round(strategy.shares, 4),
                'avg_cost': action_info.get('avg_cost'),
                'loss_pct': action_info.get('loss_pct', 0),
                'total_invested': round(strategy.total_invested, 2),
                'total_return': round(total_return, 2),
                'return_pct': round(return_pct, 2),
                'trade_count': len(strategy.trades),
            }
            summary_items.append(item)
        except Exception as e:
            logging.error(f"生成{name}汇总数据失败: {e}")
    summary_data = {
        'lastUpdate': datetime.now().strftime('%Y-%m-%d'),
        'etf_list': summary_items,
    }
    summary_file = os.path.join(output_dir, 'dca_summary.json')
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(summary_data, f, ensure_ascii=False, indent=2)
    logging.info(f"ETF汇总数据已保存: {summary_file}")
    return summary_data


def run_backtest_from_config(config_path: str = 'etf_config.json',
                              output_dir: str = 'html/dca'):
    if not os.path.exists(config_path):
        logging.error(f"配置文件不存在: {config_path}")
        return None
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
    etf_list = config.get('etf_list', [])
    base_amount = config.get('base_amount', 1000)
    years = config.get('years', 5)
    enabled_etfs = [e for e in etf_list if e.get('enabled', True)]
    if not enabled_etfs:
        logging.error("没有启用的ETF")
        return None
    all_results = {}
    backtest_start = (datetime.now() - timedelta(days=years * 365)).strftime('%Y%m%d')
    os.makedirs(output_dir, exist_ok=True)
    WARMUP_WEEKS = 30
    WARMUP_DAYS = WARMUP_WEEKS * 7 + 30
    for etf in enabled_etfs:
        tgt = etf['target']
        name = etf['name']
        ts_code = etf.get('ts_code', '')
        logging.info("\n" + "=" * 70)
        logging.info(f"  [{name}] 从配置文件加载，开始回测...")
        logging.info("=" * 70)
        try:
            if tgt == 'nasdaq':
                daily_warmup = get_nasdaq_data(years=years + 1)
            else:
                daily_warmup = _get_etf_data(
                    ts_code,
                    datetime.now() - timedelta(days=(years + 1) * 365),
                    datetime.now())
            weekly = resample_to_weekly(daily_warmup)
            actual_data_start = daily_warmup['trade_date'].min()
            warmup_end = None
            for i in range(len(weekly)):
                if i >= WARMUP_WEEKS:
                    warmup_end = weekly.iloc[i]['trade_date']
                    break
            effective_start = backtest_start
            if warmup_end and warmup_end > backtest_start:
                effective_start = warmup_end
                data_years = (datetime.now() - pd.to_datetime(actual_data_start)).days / 365
                logging.info(f"  [{name}] 数据起始: {actual_data_start} ({data_years:.1f}年), "
                             f"预热{WARMUP_WEEKS}周后回测起始: {effective_start}")
            else:
                logging.info(f"  [{name}] 数据充足, 回测起始: {effective_start}")
            strategy = WeeklyDCAStrategy(
                name=name,
                base_amount=base_amount,
                sell_version=etf.get('strategy', 'v2'),
            )
            strategy.backtest(daily_warmup, weekly, backtest_start=effective_start)
            strategy._nav_curve = strategy.calc_nav_curve(daily_warmup)
            strategy._daily_df = daily_warmup
            strategy._weekly_df = weekly
            report_file = os.path.join(output_dir, f"dca_report_{name}.html")
            generate_backtest_report(strategy, daily_warmup, weekly, report_file)
            all_results[name] = strategy
        except Exception as e:
            logging.error(f"[{name}]回测失败: {e}")
    if len(all_results) >= 2:
        _generate_comparison_chart(all_results, output_dir)
    summary = _generate_dca_summary(all_results, output_dir)
    return all_results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='周线KDJ定投策略回测')
    parser.add_argument('--target', type=str, default='all',
                        choices=['nasdaq', 'dividend', 'sz50', 'hs300', 'zz2000', 'telecom', 'innovative_medicine', 'electricity', 'kechuang_semi', 'satellite', 'nonferrous_metals', 'both', 'all', 'config'],
                        help='回测标的: nasdaq/dividend/sz50/hs300/zz2000/telecom/innovative_medicine/electricity/kechuang_semi/satellite/nonferrous_metals/both/all/config')

    parser.add_argument('--etf', type=str, default=None,
                        help='自定义ETF代码回测，如 510050.SH 或 159941.SZ')
    parser.add_argument('--etf-name', type=str, default=None,
                        help='自定义ETF显示名称（配合--etf使用）')
    parser.add_argument('--amount', type=float, default=1000,
                        help='基础定投金额（默认1000）')
    parser.add_argument('--years', type=int, default=5,
                        help='回测年数（默认5年）')
    parser.add_argument('--output-dir', type=str, default='html/dca',
                        help='报告输出目录')
    args = parser.parse_args()
    if args.etf:
        ts_code = args.etf.strip()
        if not ts_code[-3] == '.':
            if ts_code.isdigit() and len(ts_code) == 6:
                if ts_code.startswith(('5', '6')):
                    ts_code += '.SH'
                else:
                    ts_code += '.SZ'
        display_name = args.etf_name if args.etf_name else ts_code
        run_backtest(
            target='custom',
            base_amount=args.amount,
            years=args.years,
            output_dir=args.output_dir,
            custom_ts_code=ts_code,
            custom_name=display_name,
        )
    elif args.target == 'config':
        run_backtest_from_config(
            config_path='etf_config.json',
            output_dir=args.output_dir,
        )
    else:
        run_backtest(
            target=args.target,
            base_amount=args.amount,
            years=args.years,
            output_dir=args.output_dir,
        )
