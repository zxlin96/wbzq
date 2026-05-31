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


class WeeklyDCAStrategy:
    def __init__(self,
                 name: str,
                 base_amount: float = 1000,
                 loss_threshold_1: float = 0.05,
                 loss_threshold_2: float = 0.10,
                 j_buy_threshold: float = 13,
                 j_sell_half_threshold: float = 93,
                 state_file: str = None):
        self.name = name
        self.base_amount = base_amount
        self.loss_threshold_1 = loss_threshold_1
        self.loss_threshold_2 = loss_threshold_2
        self.j_buy_threshold = j_buy_threshold
        self.j_sell_half_threshold = j_sell_half_threshold
        self.state_file = state_file or f'dca_state_{name}.json'
        self.shares = 0.0
        self.total_cost = 0.0
        self.total_invested = 0.0
        self.total_sell_amount = 0.0
        self.half_sold = False
        self.dca_active = False
        self.current_multiplier = 1
        self.trades = []
        self.week_invested = set()
        self.load_state()

    def load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                self.shares = state.get('shares', 0)
                self.total_cost = state.get('total_cost', 0)
                self.total_invested = state.get('total_invested', 0)
                self.total_sell_amount = state.get('total_sell_amount', 0)
                self.half_sold = state.get('half_sold', False)
                self.dca_active = state.get('dca_active', False)
                self.current_multiplier = state.get('current_multiplier', 1)
                self.trades = state.get('trades', [])
                self.week_invested = set(state.get('week_invested', []))
                logging.info(f"[{self.name}] 加载状态: 持仓={self.shares:.2f}, "
                             f"成本={self.total_cost:.2f}")
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
            'shares': self.shares,
            'total_cost': self.total_cost,
            'total_invested': self.total_invested,
            'total_sell_amount': self.total_sell_amount,
            'half_sold': self.half_sold,
            'dca_active': self.dca_active,
            'current_multiplier': self.current_multiplier,
            'trades': self.trades,
            'week_invested': list(self.week_invested),
        }
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, ensure_ascii=False, indent=2, default=convert)
        except Exception as e:
            logging.error(f"[{self.name}] 保存状态失败: {e}")

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
        if self.shares <= 0:
            return self.base_amount
        loss_pct = self.get_current_loss_pct(current_price)
        if loss_pct >= self.loss_threshold_2:
            return self.base_amount * 4
        elif loss_pct >= self.loss_threshold_1:
            return self.base_amount * 2
        return self.base_amount

    def buy(self, date: str, price: float, amount: float, reason: str):
        buy_shares = amount / price
        self.shares += buy_shares
        self.total_cost += amount
        self.total_invested += amount
        self.trades.append({
            'date': date, 'action': 'BUY', 'price': round(price, 4),
            'amount': round(amount, 2), 'shares': round(buy_shares, 4),
            'reason': reason, 'total_shares': round(self.shares, 4),
            'avg_cost': round(self.total_cost / self.shares, 4) if self.shares > 0 else 0,
        })
        logging.info(f"[{self.name}] {date} 买入: 价格={price:.4f}, "
                     f"金额={amount:.0f}, 份额={buy_shares:.4f}, "
                     f"原因={reason}")

    def sell(self, date: str, price: float, ratio: float, reason: str):
        if self.shares <= 0:
            return
        sell_shares = self.shares * ratio
        sell_amount = sell_shares * price
        avg_cost = self.get_avg_cost()
        cost_part = self.total_cost * ratio
        self.shares -= sell_shares
        self.total_cost -= cost_part
        self.total_sell_amount += sell_amount
        profit = sell_amount - cost_part
        self.trades.append({
            'date': date, 'action': 'SELL', 'price': round(price, 4),
            'amount': round(sell_amount, 2), 'shares': round(sell_shares, 4),
            'reason': reason, 'total_shares': round(self.shares, 4),
            'profit': round(profit, 2),
        })
        logging.info(f"[{self.name}] {date} 卖出{ratio*100:.0f}%: "
                     f"价格={price:.4f}, 金额={sell_amount:.0f}, "
                     f"盈亏={profit:.0f}, 原因={reason}")
        if ratio >= 1.0:
            self.half_sold = False
            self.dca_active = False
            self.total_cost = 0

    def backtest(self, daily_df: pd.DataFrame, weekly_df: pd.DataFrame,
                 backtest_start: str = None):
        logging.info(f"\n{'='*70}")
        logging.info(f"  回测策略: {self.name}")
        logging.info(f"  基础定投金额: {self.base_amount}")
        logging.info(f"  亏损翻倍阈值: {self.loss_threshold_1*100}% / {self.loss_threshold_2*100}%")
        logging.info(f"  周线 J<={self.j_buy_threshold} 定投, J>={self.j_sell_half_threshold} 卖1/3")
        logging.info(f"  金叉后收盘<多空线再卖1/3, 死叉全清")
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
        self.trades = []
        self.shares = 0
        self.total_cost = 0
        self.total_invested = 0
        self.total_sell_amount = 0
        self.half_sold = False
        self.sell_stage = 0
        self.dca_active = False
        self.current_multiplier = 1
        self.week_invested = set()
        waiting_golden = False
        golden_confirmed = False
        price_below_dk_sold = False
        prev_week_key = None
        for trade_date in all_dates:
            if backtest_start and trade_date < backtest_start:
                continue
            if trade_date not in daily_dict:
                continue
            daily_row = daily_dict[trade_date]
            price = daily_row['close_qfq']
            dk_val_today = daily_row.get('zhixing_duokong')
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
            if waiting_golden and trade_date in golden_cross_dates:
                waiting_golden = False
                golden_confirmed = True
                price_below_dk_sold = False
                logging.info(f"[{self.name}] {trade_date} 日线知行多空金叉(中期上穿多空)，"
                             f"监控收盘价<多空线或等死叉")
            if self.sell_stage == 0:
                if weekly_j <= self.j_buy_threshold:
                    if not self.dca_active:
                        self.dca_active = True
                        logging.info(f"[{self.name}] {trade_date} 周线J={weekly_j:.2f}<={self.j_buy_threshold}, "
                                     f"启动定投")
                    if is_new_week and trade_date not in self.week_invested:
                        invest_amount = self.get_invest_amount(price)
                        self.buy(trade_date, price, invest_amount,
                                 f"周线J={weekly_j:.2f}, 金额={invest_amount:.0f}")
                        self.week_invested.add(trade_date)
            if (self.shares > 0 and weekly_j >= self.j_sell_half_threshold
                    and self.sell_stage == 0):
                self.sell(trade_date, price, 1.0 / 3,
                          f"周线J={weekly_j:.2f}>={self.j_sell_half_threshold}")
                self.sell_stage = 1
                self.half_sold = True
                waiting_golden = True
                mid_val = daily_row.get('zhixing_mid')
                dk_val = daily_row.get('zhixing_duokong')
                if pd.notna(mid_val) and pd.notna(dk_val) and mid_val > dk_val:
                    golden_confirmed = True
                    waiting_golden = False
                    price_below_dk_sold = False
                    logging.info(f"[{self.name}] {trade_date} 卖1/3时已在多头区间(中期>多空),"
                                 f"监控收盘价<多空线或等死叉")
                else:
                    golden_confirmed = False
            if (self.shares > 0 and golden_confirmed and not price_below_dk_sold
                    and pd.notna(dk_val_today) and price < dk_val_today):
                self.sell(trade_date, price, 0.5,
                          f"收盘价({price:.4f})<知行多空线({dk_val_today:.4f})")
                self.sell_stage = 2
                price_below_dk_sold = True
                logging.info(f"[{self.name}] {trade_date} 金叉后收盘跌破多空线，"
                             f"卖1/3，剩余等死叉全清")
            if (self.shares > 0 and golden_confirmed and price_below_dk_sold
                    and trade_date in dead_cross_dates):
                self.sell(trade_date, price, 1.0,
                          "日线知行多空死叉(全清剩余)")
                self.sell_stage = 0
                self.half_sold = False
                self.dca_active = False
                waiting_golden = False
                golden_confirmed = False
                price_below_dk_sold = False
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
        daily_kdj = calc_kdj(daily_df)
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
            'sell_stage': getattr(self, 'sell_stage', 1 if self.half_sold else 0),
        }
        if self.shares <= 0 and not self.dca_active:
            if weekly_j <= self.j_buy_threshold:
                result['action'] = 'buy'
                result['action_label'] = '建议买入'
                result['action_color'] = '#e74c3c'
                result['action_detail'] = (
                    f"周线J={weekly_j:.2f}<={self.j_buy_threshold}，处于超卖区间，"
                    f"下一交易日建议以基础金额{self.base_amount:.0f}元定投买入"
                )
            else:
                result['action'] = 'wait'
                result['action_label'] = '空仓等待'
                result['action_color'] = '#95a5a6'
                result['action_detail'] = (
                    f"周线J={weekly_j:.2f}，未到超卖区间(J<={self.j_buy_threshold})，"
                    f"继续空仓等待定投信号"
                )
        elif self.shares > 0 and not self.half_sold:
            if weekly_j >= self.j_sell_half_threshold:
                result['action'] = 'sell_third'
                result['action_label'] = '建议卖出1/3'
                result['action_color'] = '#2ecc71'
                if mid_above_dk:
                    result['action_detail'] = (
                        f"周线J={weekly_j:.2f}>={self.j_sell_half_threshold}，短期过热，"
                        f"建议卖出1/3仓位。当前多头区间，后续监控收盘<多空线再卖1/3，死叉全清"
                    )
                else:
                    result['action_detail'] = (
                        f"周线J={weekly_j:.2f}>={self.j_sell_half_threshold}，短期过热，"
                        f"建议卖出1/3仓位。当前空头区间，先等金叉，"
                        f"再监控收盘<多空线卖1/3，最后死叉全清"
                    )
            elif self.dca_active:
                invest_amount = self.get_invest_amount(last_price)
                multiplier = int(invest_amount / self.base_amount)
                result['action'] = 'buy'
                result['action_label'] = f'建议定投({multiplier}x)'
                result['action_color'] = '#e74c3c'
                pnl_label = "浮盈" if profit_pct > 0 else "浮亏"
                result['action_detail'] = (
                    f"定投进行中，当前{pnl_label}{abs(profit_pct)*100:.2f}%，"
                    f"下一交易日建议买入{invest_amount:.0f}元"
                    f"(基础{self.base_amount:.0f}x{multiplier})"
                )
            else:
                result['action'] = 'hold'
                result['action_label'] = '持有观望'
                result['action_color'] = '#f39c12'
                result['action_detail'] = (
                    f"有持仓但未在定投周期，周线J={weekly_j:.2f}，"
                    f"继续持有等待卖出信号(J>={self.j_sell_half_threshold})"
                )
        elif self.half_sold and self.shares > 0:
            sell_stage = getattr(self, 'sell_stage', 1)
            if sell_stage == 1:
                if not mid_above_dk:
                    result['action'] = 'wait_golden'
                    result['action_label'] = '已卖1/3·等金叉'
                    result['action_color'] = '#9b59b6'
                    result['action_detail'] = (
                        f"已卖1/3仓位，当前中期线<多空线(空头)，"
                        f"等金叉后监控收盘<多空线再卖1/3，死叉全清"
                    )
                else:
                    dk_v = dk_val if pd.notna(dk_val) else 0
                    result['action'] = 'wait_price_below_dk'
                    result['action_label'] = '已卖1/3·等收盘<多空线'
                    result['action_color'] = '#e67e22'
                    result['action_detail'] = (
                        f"已卖1/3仓位，金叉后监控收盘价是否跌破多空线({dk_v:.4f})，"
                        f"跌破则再卖1/3，然后等死叉全清"
                    )
            elif sell_stage == 2:
                result['action'] = 'wait_dead_cross'
                result['action_label'] = '已卖2/3·等死叉'
                result['action_color'] = '#f39c12'
                result['action_detail'] = (
                    f"已卖2/3仓位，等日线知行多空死叉时清仓剩余1/3"
                )
            else:
                result['action'] = 'hold_wait_dead'
                result['action_label'] = '等死叉全清'
                result['action_color'] = '#f39c12'
                result['action_detail'] = (
                    f"已卖部分仓位，等日线知行多空死叉时全部清仓"
                )
        else:
            result['action'] = 'unknown'
            result['action_label'] = '未知状态'
            result['action_color'] = '#95a5a6'
            result['action_detail'] = '策略状态异常，请检查'
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


def generate_backtest_report(strategy: WeeklyDCAStrategy,
                             daily_df: pd.DataFrame,
                             weekly_df: pd.DataFrame,
                             output_file: str = None) -> str:
    if output_file is None:
        output_file = f'dca_report_{strategy.name}.html'
    trades_df = pd.DataFrame(strategy.trades) if strategy.trades else pd.DataFrame()
    daily_kdj = calc_kdj(daily_df)
    weekly_kdj = calc_kdj(weekly_df)
    dates = daily_kdj['trade_date'].tolist()
    prices = _safe_list(daily_kdj['close_qfq'].tolist())
    daily_j = _safe_list(daily_kdj['J'].tolist())
    weekly_dates_list = weekly_kdj['trade_date'].tolist()
    weekly_prices = _safe_list(weekly_kdj['close_qfq'].tolist())
    weekly_k = _safe_list(weekly_kdj['K'].tolist())
    weekly_d = _safe_list(weekly_kdj['D'].tolist())
    weekly_j = _safe_list(weekly_kdj['J'].tolist())
    buy_markers = []
    sell_markers = []
    for t in strategy.trades:
        marker = {
            'date': t['date'],
            'price': t['price'],
            'reason': t.get('reason', ''),
            'amount': t.get('amount', 0),
        }
        if t['action'] == 'BUY':
            buy_markers.append(marker)
        else:
            sell_markers.append(marker)
    daily_with_dk = calc_zhixing_duokong(daily_df)
    dk_dates = daily_with_dk['trade_date'].tolist()
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
.subtitle {{ text-align: center; color: #666; margin-bottom: 20px; font-size: 14px; }}
.summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 20px; }}
.card {{ background: white; border-radius: 8px; padding: 15px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
.card-label {{ color: #666; font-size: 12px; margin-bottom: 5px; }}
.card-value {{ font-size: 20px; font-weight: bold; color: #333; }}
.card-value.positive {{ color: #e74c3c; }}
.card-value.negative {{ color: #2ecc71; }}
.action-box {{ background: white; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); border-left: 5px solid {na['action_color']}; }}
.action-header {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }}
.action-label {{ font-size: 22px; font-weight: bold; color: {na['action_color']}; }}
.action-date {{ color: #999; font-size: 13px; }}
.action-detail {{ font-size: 14px; color: #555; line-height: 1.8; padding: 10px; background: #fafafa; border-radius: 4px; }}
.action-metrics {{ display: flex; flex-wrap: wrap; gap: 15px; margin-top: 12px; }}
.action-metric {{ padding: 6px 12px; background: #f0f2f5; border-radius: 4px; font-size: 13px; color: #333; }}
.chart {{ background: white; border-radius: 8px; padding: 15px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
.trade-table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
.trade-table th {{ background: #f5f7fa; padding: 8px; text-align: left; border-bottom: 2px solid #ddd; }}
.trade-table td {{ padding: 6px 8px; border-bottom: 1px solid #eee; }}
.trade-table tr:hover {{ background: #f9f9f9; }}
.buy-tag {{ background: #e74c3c; color: white; padding: 2px 6px; border-radius: 3px; font-size: 11px; }}
.sell-tag {{ background: #2ecc71; color: white; padding: 2px 6px; border-radius: 3px; font-size: 11px; }}
</style>
</head>
<body>
<div class="container">
<h1>周线KDJ定投策略回测报告</h1>
<div class="subtitle">{strategy.name} | 基础金额: {strategy.base_amount} | "
    f"J<={strategy.j_buy_threshold}定投 | J>={strategy.j_sell_half_threshold}卖1/3 | 收盘<多空线卖1/3 | 死叉全清</div>
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
    <th>日期</th><th>操作</th><th>价格</th><th>金额</th><th>份额</th>
    <th>原因</th><th>持仓</th><th>盈亏</th>
</tr>"""
    for t in strategy.trades:
        tag = '<span class="buy-tag">买入</span>' if t['action'] == 'BUY' else '<span class="sell-tag">卖出</span>'
        profit_str = f"{t.get('profit', ''):.2f}" if 'profit' in t and t.get('profit') is not None else '-'
        html += f"""
<tr>
    <td>{t['date']}</td>
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
var buyMarkers = {json.dumps(buy_markers)};
var sellMarkers = {json.dumps(sell_markers)};
var deadCross = {json.dumps(dead_cross_list)};
var dChart = echarts.init(document.getElementById('daily_chart'));
"""
    mark_points = []
    for t in strategy.trades:
        if t['action'] == 'BUY':
            mark_points.append(
                f"{{xAxis: '{t['date']}', yAxis: {t['price']:.4f}, "
                f"name: '买入', itemStyle: {{color: '#e74c3c'}}, "
                f"label: {{formatter: '买', color: '#fff', fontSize: 10}}}}"
            )
        else:
            mark_points.append(
                f"{{xAxis: '{t['date']}', yAxis: {t['price']:.4f}, "
                f"name: '卖出', itemStyle: {{color: '#2ecc71'}}, "
                f"label: {{formatter: '卖', color: '#fff', fontSize: 10}}}}"
            )
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
                 custom_name: str = None):
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
            strategy = WeeklyDCAStrategy(
                name=cfg['name'],
                base_amount=base_amount,
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
                'ts_code': action_info.get('ts_code', ''),
                'report_file': os.path.basename(report_file),
                'action_label': action_info.get('action_label', '未知'),
                'action_color': action_info.get('action_color', '#95a5a6'),
                'action': action_info.get('action', 'unknown'),
                'action_detail': action_info.get('action_detail', ''),
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
