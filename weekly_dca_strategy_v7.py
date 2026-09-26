#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
周线KDJ定投策略 V7 - 行业定投优化版（特性开关式，全部关闭时行为等价 V6）

在 V6（V5无倍投买入 + V4卖出止损）基础上叠加四个可独立开关的优化特性，
依据《定投策略优化TODO计划.md》：行业定投的核心风险是价值陷阱、熊市满仓、
止盈回吐，优化目标是少亏钱而非提高年化。

- T1 趋势资格闸门（use_trend_gate）：行业级"价值陷阱"防御，只管新钱进场、不碰卖出
  · 'freeze'：长期趋势走坏（周线收盘 < N周均线 或 N周均线下行）时冻结新轮次
  · 'half'  ：趋势走坏时新轮次照开，但轮次预算减半（gate_factor=0.5）
  · 已有轮次不受影响，照常买入/卖出/止损——与已否决的"趋势清仓过滤"本质不同
- T3 J阈值分位化（use_j_percentile）：J 买入/过热卖出阈值改为滚动分位数
  · 买入阈值 = 决策周之前 j_pct_lookback 周J序列的 j_buy_pct 分位（默认156周/10分位）
  · 过热卖出阈值 = 同窗口 j_sell_pct 分位（默认95分位）
  · 历史 < j_pct_lookback 周时回退固定 13/93（次新ETF自动回退）
- T4 回升投放节奏（ramp_mode）：修正"J回升一次打完剩余预算"的熊市反弹陷阱
  · 'v6'    ：J>13 一次性打完剩余预算（原行为）
  · 'split2'：J>13 恢复投放，剩余预算分 2 期（2个新周）投完；期间 J 跌回≤13 恢复按周定投
  · 'j20'   ：J >= ramp_j_threshold（默认20）才允许一次打完；13<J<20 时轮次挂起等方向
- T2 AMV市场闸门（use_amv_gate）：市场级风险开关（amv_gate.load_amv_gate 的两票共振）
  · 关闸期间每期投入上限 = base_amount，且每周至多投入一次；轮次照常、只是投得慢
  · 与 T1 分层：T1 是行业级资格（冻不冻新轮），T2 是市场级仓位（投多快）

注意：本策略仅用于回测与研究验证；生产灰度切换见 run_backtest_from_config 的 v7 分支。
数据源：Tushare fund_daily（与 V4 一致）
"""
import bisect
import json
import logging
import os

import numpy as np
import pandas as pd

from weekly_dca_strategy_v4 import WeeklyDCAStrategyV4 as _V4
from weekly_dca_strategy_v5 import NoMultiplierPosition
from weekly_dca_strategy_v6 import WeeklyDCAStrategyV6 as _V6


class V7Position(NoMultiplierPosition):
    """V7 仓位：NoMultiplierPosition + 回升分批进度(ramp_stage) + 开轮闸门系数(gate_factor)"""

    def __init__(self, round_id: int, base_amount: float,
                 loss_threshold_1: float, loss_threshold_2: float,
                 j_buy_threshold: float, j_sell_half_threshold: float,
                 j_peak_min: float, j_pullback: float,
                 round_budget: float = 5000, round_periods: int = 5,
                 gate_factor: float = 1.0):
        super().__init__(
            round_id=round_id, base_amount=base_amount,
            loss_threshold_1=loss_threshold_1, loss_threshold_2=loss_threshold_2,
            j_buy_threshold=j_buy_threshold, j_sell_half_threshold=j_sell_half_threshold,
            j_peak_min=j_peak_min, j_pullback=j_pullback,
            round_budget=round_budget, round_periods=round_periods,
        )
        self.gate_factor = gate_factor  # 开轮时趋势闸门系数（'half'模式=0.5），决定本轮预算
        self.ramp_stage = 0             # T4 split2：J回升分批投放进度（0/1/2）

    def to_dict(self) -> dict:
        d = super().to_dict()
        d['gate_factor'] = self.gate_factor
        d['ramp_stage'] = self.ramp_stage
        return d

    @classmethod
    def from_dict(cls, data: dict, base_amount: float,
                  loss_threshold_1: float, loss_threshold_2: float,
                  j_buy_threshold: float, j_sell_half_threshold: float,
                  j_peak_min: float, j_pullback: float,
                  round_budget: float = 5000, round_periods: int = 5):
        p = super().from_dict(
            data, base_amount, loss_threshold_1, loss_threshold_2,
            j_buy_threshold, j_sell_half_threshold, j_peak_min, j_pullback,
            round_budget, round_periods,
        )
        p.gate_factor = data.get('gate_factor', 1.0)
        p.ramp_stage = data.get('ramp_stage', 0)
        return p


class WeeklyDCAStrategyV7(_V6):
    _position_class = V7Position

    def __init__(self,
                 name: str,
                 base_amount: float = 1000,
                 loss_threshold_1: float = 0.05,
                 loss_threshold_2: float = 0.10,
                 j_buy_threshold: float = 13,
                 j_sell_half_threshold: float = 93,
                 j_peak_min: float = 50,
                 j_pullback: float = 20,
                 j_exit_threshold: float = 50,
                 j_operation_gate: float = 30,
                 stop_loss_buf: float = 0.0,
                 round_budget: float = 5000,
                 round_periods: int = 5,
                 # ---- T1 趋势资格闸门 ----
                 use_trend_gate: str = 'off',   # 'off' | 'freeze' | 'half'
                 trend_ma_weeks: int = 100,
                 trend_decline_lb: int = 4,
                 # ---- T3 J阈值分位化 ----
                 use_j_percentile: bool = False,
                 j_pct_lookback: int = 156,
                 j_buy_pct: float = 0.10,
                 j_sell_pct: float = 0.95,
                 # ---- T4 回升投放节奏 ----
                 ramp_mode: str = 'v6',         # 'v6' | 'split2' | 'j20'
                 ramp_j_threshold: float = 20.0,
                 # ---- T2 AMV市场闸门 ----
                 use_amv_gate: bool = False,
                 amv_gate_df: pd.DataFrame = None,   # amv_gate.load_amv_gate() 的返回
                 state_file: str = None):
        if use_trend_gate not in ('off', 'freeze', 'half'):
            raise ValueError(f"use_trend_gate 非法: {use_trend_gate}")
        if ramp_mode not in ('v6', 'split2', 'j20'):
            raise ValueError(f"ramp_mode 非法: {ramp_mode}")
        self.use_trend_gate = use_trend_gate
        self.trend_ma_weeks = trend_ma_weeks
        self.trend_decline_lb = trend_decline_lb
        self.use_j_percentile = use_j_percentile
        self.j_pct_lookback = j_pct_lookback
        self.j_buy_pct = j_buy_pct
        self.j_sell_pct = j_sell_pct
        self.ramp_mode = ramp_mode
        self.ramp_j_threshold = ramp_j_threshold
        self.use_amv_gate = use_amv_gate
        self._amv_gate_df = amv_gate_df
        super().__init__(
            name=name,
            base_amount=base_amount,
            loss_threshold_1=loss_threshold_1,
            loss_threshold_2=loss_threshold_2,
            j_buy_threshold=j_buy_threshold,
            j_sell_half_threshold=j_sell_half_threshold,
            j_peak_min=j_peak_min,
            j_pullback=j_pullback,
            j_exit_threshold=j_exit_threshold,
            j_operation_gate=j_operation_gate,
            stop_loss_buf=stop_loss_buf,
            round_budget=round_budget,
            round_periods=round_periods,
            state_file=state_file or f'dca_state_v7_{name}.json',
        )

    # ---- 状态持久化：恢复为 V7Position（保留 gate_factor/ramp_stage） ----
    def load_state(self):
        if not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file, 'r', encoding='utf-8') as f:
                state = json.load(f)
            self.next_round_id = state.get('next_round_id', 1)
            self.total_sell_amount = state.get('total_sell_amount', 0)
            self.trades = state.get('trades', [])
            self.positions = []
            for pd_data in state.get('positions', []):
                p = V7Position.from_dict(
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

    def _new_position(self, gate_factor: float = 1.0) -> V7Position:
        pos = V7Position(
            round_id=self.next_round_id,
            base_amount=self.base_amount,
            loss_threshold_1=self.loss_threshold_1,
            loss_threshold_2=self.loss_threshold_2,
            j_buy_threshold=self.j_buy_threshold,
            j_sell_half_threshold=self.j_sell_half_threshold,
            j_peak_min=self.j_peak_min,
            j_pullback=self.j_pullback,
            round_budget=self.round_budget * gate_factor,
            round_periods=self.round_periods,
            gate_factor=gate_factor,
        )
        self.next_round_id += 1
        self.positions.append(pos)
        return pos

    # ---- V7 预计算 ----
    def _build_j_percentile_thresholds(self, weekly_kdj: pd.DataFrame):
        """T3: 决策周 -> (买入阈值, 过热卖出阈值)。窗口只用该周之前的J历史，无前视。"""
        buy_thr, sell_thr = {}, {}
        j = weekly_kdj[['trade_date', 'J']].dropna().reset_index(drop=True)
        j_vals = j['J'].to_numpy(dtype=float)
        wdates = j['trade_date'].tolist()
        lb = self.j_pct_lookback
        for i in range(len(j_vals)):
            if i >= lb:
                window = j_vals[i - lb:i]
                buy_thr[wdates[i]] = float(np.quantile(window, self.j_buy_pct))
                sell_thr[wdates[i]] = float(np.quantile(window, self.j_sell_pct))
            else:
                buy_thr[wdates[i]] = self.j_buy_threshold
                sell_thr[wdates[i]] = self.j_sell_half_threshold
        return buy_thr, sell_thr

    def _build_trend_gate(self, weekly_df: pd.DataFrame) -> dict:
        """T1: 决策周 -> 是否趋势走坏（关闸）。均线/下行窗口预热不足时视为开闸。"""
        closed = {}
        wc = weekly_df.sort_values('trade_date').reset_index(drop=True)
        ma = wc['close_qfq'].rolling(self.trend_ma_weeks,
                                     min_periods=self.trend_ma_weeks).mean()
        ma_lb = ma.shift(self.trend_decline_lb)
        for td, c, m, m_lb in zip(wc['trade_date'], wc['close_qfq'], ma, ma_lb):
            if pd.isna(m):
                closed[td] = False
            else:
                declining = pd.notna(m_lb) and m < m_lb
                closed[td] = bool(c < m or declining)
        return closed

    def _build_amv_gate_lookup(self):
        """T2: 返回 trade_date(YYYYMMDD str) -> gate_open(bool) 的 asof 查询函数。
        数据缺口回看最近一个交易日；数据起点之前视为开闸。"""
        if self._amv_gate_df is None:
            from amv_gate import load_amv_gate
            self._amv_gate_df = load_amv_gate()
        amv = self._amv_gate_df.sort_values('trade_date').reset_index(drop=True)
        dates = amv['trade_date'].astype(str).tolist()
        opens = amv['gate_open'].astype(bool).tolist()

        def amv_gate_at(trade_date: str) -> bool:
            i = bisect.bisect_right(dates, str(trade_date)) - 1
            if i < 0:
                return True
            return opens[i]

        return amv_gate_at

    def backtest(self, daily_df: pd.DataFrame, weekly_df: pd.DataFrame,
                 backtest_start: str = None):
        logging.info(f"\n{'='*70}")
        logging.info(f"  回测策略: {self.name} (V7)")
        logging.info(f"  基础定投金额: {self.base_amount}")
        logging.info(f"  周线 J<=买阈值 定投, J>=过热阈值 卖1/3, J峰值回撤>={self.j_pullback}(peak>={self.j_peak_min}) 卖1/3")
        logging.info(f"  [V7特性] 趋势闸门={self.use_trend_gate}(MA{self.trend_ma_weeks}/下行{self.trend_decline_lb}周), "
                     f"J分位化={self.use_j_percentile}({self.j_pct_lookback}周/{self.j_buy_pct:.2f}/{self.j_sell_pct:.2f}), "
                     f"回升节奏={self.ramp_mode}" +
                     (f"(J>={self.ramp_j_threshold})" if self.ramp_mode == 'j20' else '') +
                     f", AMV闸门={self.use_amv_gate}")
        logging.info(f"  多仓位独立管理: 每轮定投/卖出独立跟踪")
        if backtest_start:
            logging.info(f"  预热后回测起始: {backtest_start}")
        logging.info(f"{'='*70}")

        daily_with_duokong = self._calc_zhixing_duokong(daily_df)
        daily_dict = {}
        for _, row in daily_with_duokong.iterrows():
            daily_dict[row['trade_date']] = row.to_dict()
        weekly_kdj = self._calc_kdj(weekly_df)
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
        # 决策只消费"已收盘周线"（与 V4 相同）：跳过本周半成品 bar
        max_daily_date = all_dates[-1] if all_dates else None
        decision_weekly_dates = [wd for wd in sorted_weekly_dates
                                 if max_daily_date and wd < max_daily_date]
        if len(decision_weekly_dates) < len(sorted_weekly_dates):
            logging.info(f"[{self.name}] 本周周线尚未收盘，决策沿用 "
                         f"{decision_weekly_dates[-1] if decision_weekly_dates else '无'} 收盘周线"
                         f"（跳过半成品bar: {sorted_weekly_dates[-1]}）")
        for d in all_dates:
            best_wd = None
            for wd in decision_weekly_dates:
                if wd <= d:
                    best_wd = wd
                else:
                    break
            if best_wd:
                daily_to_weekly[d] = best_wd

        # ---- V7 特性预计算 ----
        if self.use_j_percentile:
            buy_thr_by_week, sell_thr_by_week = self._build_j_percentile_thresholds(weekly_kdj)
        else:
            buy_thr_by_week, sell_thr_by_week = {}, {}
        trend_closed_by_week = (self._build_trend_gate(weekly_df)
                                if self.use_trend_gate != 'off' else {})
        amv_gate_at = self._build_amv_gate_lookup() if self.use_amv_gate else (lambda d: True)

        # 重置所有状态（与 V4 相同）
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
            # [V7] 自适应阈值（T3）+ 行业趋势资格闸门（T1）+ 市场AMV闸门（T2）
            if self.use_j_percentile:
                buy_thr = buy_thr_by_week.get(week_key, self.j_buy_threshold)
                sell_thr = sell_thr_by_week.get(week_key, self.j_sell_half_threshold)
            else:
                buy_thr = self.j_buy_threshold
                sell_thr = self.j_sell_half_threshold
            gate_closed = (trend_closed_by_week.get(week_key, False)
                           if self.use_trend_gate != 'off' else False)
            amv_open = amv_gate_at(trade_date)
            dca_pos = self._dca_position()
            if weekly_j <= buy_thr:
                if dca_pos is None:
                    # [V7-T1] 趋势闸门只拦新轮次：freeze=不开轮，half=轮次预算减半
                    if self.use_trend_gate == 'freeze' and gate_closed:
                        dca_pos = None
                    else:
                        gf = 0.5 if (self.use_trend_gate == 'half' and gate_closed) else 1.0
                        dca_pos = self._new_position(gate_factor=gf)
                        dca_pos.dca_active = True
                        tag = "，趋势闸门中预算减半" if gf < 1 else ""
                        logging.info(f"[{self.name}] R{dca_pos.round_id} {trade_date} "
                                     f"周线J={weekly_j:.2f}<={buy_thr:.2f}{tag}, 启动定投")
                if dca_pos is not None and is_new_week and trade_date not in dca_pos.week_invested:
                    invest_amount = dca_pos.get_invest_amount(price)
                    # [V7-T2] AMV 关闸：单期投入上限=基础金额
                    if not amv_open:
                        invest_amount = min(invest_amount, self.base_amount)
                    if invest_amount > 0:
                        dca_pos.buy(trade_date, price, invest_amount,
                                    f"周线J={weekly_j:.2f}, 金额={invest_amount:.0f}"
                                    + ("" if amv_open else "，AMV关闸限基础额"))
                        dca_pos.week_invested.add(trade_date)
                        dca_pos.ramp_stage = 0  # [V7-T4] 恢复正常定投，重置分批进度
                        logging.info(f"[{self.name}] R{dca_pos.round_id} {trade_date} 买入: "
                                     f"价格={price:.4f}, 金额={invest_amount:.0f}")
            else:
                if dca_pos is not None and not dca_pos.dca_exited:
                    remaining = dca_pos.get_budget_remaining()
                    if remaining > 0:
                        # [V7-T4] J回升后的投放节奏
                        if self.ramp_mode == 'split2':
                            # 剩余预算≤本轮2%时视为尘埃，直接投完，避免反复对半产生碎单
                            if (getattr(dca_pos, 'ramp_stage', 0) >= 1
                                    or remaining <= dca_pos.round_budget * 0.02):
                                amount = remaining
                            else:
                                amount = remaining / 2.0
                            if not is_new_week:
                                amount = 0.0
                        elif self.ramp_mode == 'j20':
                            amount = remaining if weekly_j >= self.ramp_j_threshold else 0.0
                        else:  # 'v6' 原行为
                            amount = remaining
                        # [V7-T2] AMV 关闸：单期上限=基础金额，且每周至多投一次
                        if not amv_open and amount > 0:
                            amount = min(amount, self.base_amount)
                            if trade_date in dca_pos.week_invested:
                                amount = 0.0
                        if amount > 0:
                            if self.ramp_mode == 'split2':
                                reason = (f"J回升={weekly_j:.2f}, 分批投入"
                                          f"{getattr(dca_pos, 'ramp_stage', 0) + 1}/2, "
                                          f"金额={amount:.0f}")
                            elif self.ramp_mode == 'j20':
                                reason = (f"J回升>={self.ramp_j_threshold}, "
                                          f"剩余预算投入={amount:.0f}")
                            else:
                                reason = f"J回升={weekly_j:.2f}, 剩余预算投入={amount:.0f}"
                            if not amv_open:
                                reason += "，AMV关闸限基础额"
                            dca_pos.buy(trade_date, price, amount, reason)
                            dca_pos.week_invested.add(trade_date)
                            if self.ramp_mode == 'split2':
                                dca_pos.ramp_stage = getattr(dca_pos, 'ramp_stage', 0) + 1
                            dca_pos.dca_exit_date = trade_date
                            dca_pos.dca_exit_amount = amount
                            logging.info(f"[{self.name}] R{dca_pos.round_id} {trade_date} "
                                         f"J回升投入: 价格={price:.4f}, 金额={amount:.0f}")
                    # [V7] 与 V4 语义对齐：J>13 即结束买入期（无论剩余预算是否为0，
                    # 否则预算被周投恰好耗尽的轮次永远不退出、J门控永远不解锁）；
                    # 差异仅在关闸/分批模式下预算未打完时保留轮次继续投放
                    budget_done = dca_pos.get_budget_remaining() <= 1e-9
                    if (self.ramp_mode == 'v6' and amv_open) or budget_done:
                        dca_pos.dca_exited = True

            # ===== 遍历每个活跃仓位，独立判断卖出（与 V4 一致，仅卖出阈值可分位化） =====
            for pos in self._active_positions():
                # 追踪本轮买入周期内最低收盘价（V4）：买入周期内更新，停止买入后冻结
                if pos.shares > 0 and not pos.dca_exited:
                    if pos.cycle_low is None or price < pos.cycle_low:
                        pos.cycle_low = price

                # V4 J门控：买入后，周线J曾>j_operation_gate(默认30)才允许后续卖出操作
                if pos.shares > 0 and pos.dca_exited:
                    if not pos.j_high_done and weekly_j > self.j_operation_gate:
                        pos.j_high_done = True
                        logging.info(f"[{self.name}] R{pos.round_id} {trade_date} "
                                     f"周线J={weekly_j:.2f}>{self.j_operation_gate}，解锁卖出操作")

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
                                 f"日线知行多空金叉，监控收盘<中期线或等收盘<多空线")

                # ===== V4止损：收盘<黄多空线 且 中期<多空 且 收盘<本轮最低价 -> 全清 =====
                stop_ref = (pos.cycle_low * (1 - self.stop_loss_buf)
                            if pos.cycle_low is not None else None)
                if (pos.shares > 0 and pos.dca_exited
                        and pos.j_high_done
                        and pd.notna(dk_val_today) and price < dk_val_today
                        and pd.notna(mid_val_today) and mid_val_today < dk_val_today
                        and stop_ref is not None and price < stop_ref):
                    pos.sell(trade_date, price, 1.0,
                             f"R{pos.round_id} V4: "
                             f"收盘({price:.4f})<黄多空线({dk_val_today:.4f}) "
                             f"且中期线({mid_val_today:.4f})<多空线({dk_val_today:.4f}) "
                             f"且收盘<本轮最低价止损线({stop_ref:.4f})全清")
                    logging.info(f"[{self.name}] R{pos.round_id} {trade_date} "
                                 f"V4跌破多空线且空头且创新低全清")
                    self.total_sell_amount += pos.total_sell_amount
                    continue

                # 后续卖出均需先满足 J门控
                if pos.sell_stage == 0 and pos.j_high_done:
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
                                         f"回撤止盈卖1/3，多头区间，监控收盘<中期线")
                        else:
                            pos.golden_confirmed = False
                            logging.info(f"[{self.name}] R{pos.round_id} {trade_date} "
                                         f"回撤止盈卖1/3，空头区间，等金叉")
                        self.total_sell_amount += pos.total_sell_amount

                    # J≥过热阈值 卖1/3 [V7-T3: 阈值可分位化]
                    elif (pos.shares > 0 and weekly_j >= sell_thr
                            and pos.sell_stage == 0):
                        pos.sell(trade_date, price, 1.0 / 3,
                                 f"R{pos.round_id} 周线J={weekly_j:.2f}"
                                 f">={sell_thr:.2f}"
                                 + ("（分位化阈值）" if self.use_j_percentile else ""))
                        pos.sell_stage = 1
                        pos.waiting_golden = True
                        mid_val = daily_row.get('zhixing_mid')
                        dk_val = daily_row.get('zhixing_duokong')
                        if pd.notna(mid_val) and pd.notna(dk_val) and mid_val > dk_val:
                            pos.golden_confirmed = True
                            pos.waiting_golden = False
                            logging.info(f"[{self.name}] R{pos.round_id} {trade_date} "
                                         f"卖1/3，多头区间，监控收盘<中期线")
                        else:
                            pos.golden_confirmed = False
                            logging.info(f"[{self.name}] R{pos.round_id} {trade_date} "
                                         f"卖1/3，空头区间，等金叉")

                # 收盘<中期线：双空全清 vs 多头回调（需J门控解锁）
                if (pos.shares > 0 and pos.j_high_done
                        and pos.golden_confirmed
                        and not pos.price_below_dk_sold
                        and pd.notna(mid_val_today) and price < mid_val_today):
                    mid_below_dk = (pd.notna(mid_val_today) and pd.notna(dk_val_today)
                                    and mid_val_today < dk_val_today)
                    if mid_below_dk:
                        pos.sell(trade_date, price, 1.0,
                                 f"R{pos.round_id} 双空全清: "
                                 f"收盘({price:.4f})<中期线({mid_val_today:.4f}) "
                                 f"且中期({mid_val_today:.4f})<多空线({dk_val_today:.4f})")
                        logging.info(f"[{self.name}] R{pos.round_id} {trade_date} 双空全清")
                    else:
                        pos.sell(trade_date, price, 0.5,
                                 f"R{pos.round_id} 多头回调: "
                                 f"收盘({price:.4f})<中期线({mid_val_today:.4f}), "
                                 f"中期({mid_val_today:.4f})>多空线({dk_val_today:.4f})")
                        pos.sell_stage = 2
                        pos.price_below_dk_sold = True
                        logging.info(f"[{self.name}] R{pos.round_id} {trade_date} "
                                     f"多头回调卖1/3，等收盘<多空线全清")

                # 收盘<多空线全清（需J门控解锁）
                if (pos.shares > 0 and pos.j_high_done
                        and pos.golden_confirmed
                        and pos.price_below_dk_sold
                        and pd.notna(dk_val_today) and price < dk_val_today):
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

    # 让 V7 可复用 V2 的指标计算（保留虚线以便单测替换）
    def _calc_zhixing_duokong(self, df):
        from weekly_dca_strategy_v2 import calc_zhixing_duokong
        return calc_zhixing_duokong(df)

    def _calc_kdj(self, df):
        from weekly_dca_strategy_v2 import calc_kdj
        return calc_kdj(df)


__all__ = ['WeeklyDCAStrategyV7', 'V7Position']
