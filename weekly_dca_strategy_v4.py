#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
周线KDJ定投策略 V4 - 叠加日线多空线止盈/止损 + 跌破本轮最低点版

在 V3 基础上【新增叠加】条件：
- V3 全清规则为：周线 J > 阈值(默认50) 且 日线收盘<黄多空线 且 中期线<多空线(空头)
- V4 修改为：去除周线J限制，仅当 日线收盘<黄多空线 且 中期线<多空线(空头) 且 收盘价<本轮买入周期内最低价 时才立即全清
- V4 另增【J门控】：买入(停止买入dca_exited)后，须周线J曾>j_operation_gate(默认30)，才解锁所有卖出操作
其余买入/卖出逻辑与 V3 完全一致。

「本轮买入周期内最低价」定义（V4）：
- 在买入周期内（未停止买入 dca_exited=False）动态跟踪出现过的最低收盘价
- 一旦停止买入（dca_exited=True，即J回升投入剩余预算后），该最低价被冻结作为止损锚点
- 之后只要收盘价跌破这个锚点（确认买入后下跌创新低）且处于空头排列，即全清止损

J门控（V4）：
- 买入周期结束后，若周线J一直未超过j_operation_gate(默认30)，说明标的阴跌无反弹，则只持有、不做任何卖出
- 只有当周线J曾突破该阈值（确认有反弹/多头动能）后，才允许后续止盈/止损操作

数据源：Tushare fund_daily
"""

import logging

import pandas as pd

from weekly_dca_strategy_v2 import (
    WeeklyDCAStrategy,
    calc_kdj,
    calc_zhixing_duokong,
)


class WeeklyDCAStrategyV4(WeeklyDCAStrategy):
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
                 state_file: str = None):
        self.j_operation_gate = j_operation_gate
        self.stop_loss_buf = stop_loss_buf
        self.j_exit_threshold = j_exit_threshold
        super().__init__(
            name=name,
            base_amount=base_amount,
            loss_threshold_1=loss_threshold_1,
            loss_threshold_2=loss_threshold_2,
            j_buy_threshold=j_buy_threshold,
            j_sell_half_threshold=j_sell_half_threshold,
            j_peak_min=j_peak_min,
            j_pullback=j_pullback,
            round_budget=round_budget,
            round_periods=round_periods,
            state_file=state_file or f'dca_state_v4_{name}.json',
        )

    def backtest(self, daily_df: pd.DataFrame, weekly_df: pd.DataFrame,
                 backtest_start: str = None):
        logging.info(f"\n{'='*70}")
        logging.info(f"  回测策略: {self.name} (V4)")
        logging.info(f"  基础定投金额: {self.base_amount}")
        logging.info(f"  亏损翻倍阈值: {self.loss_threshold_1*100}% / {self.loss_threshold_2*100}%")
        logging.info(f"  周线 J<={self.j_buy_threshold} 定投, J>={self.j_sell_half_threshold} 卖1/3")
        logging.info(f"  J峰值回撤>={self.j_pullback}(peak>={self.j_peak_min}) 卖1/3")
        logging.info(f"  [V2] 收盘<中期线: 中期>多空卖1/3, 中期<多空全清")
        logging.info(f"  [V2] 已卖2/3后: 收盘<多空线全清")
        logging.info(f"  [V3] 周线J>{self.j_exit_threshold} 且 收盘<黄多空线 且 中期<多空 -> 全清")
        logging.info(f"  [V4新增] 去除J限制: 收盘<黄多空线 且 中期<多空 且 收盘<本轮最低价(创新低) -> 全清")
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
                # 追踪本轮买入周期内最低收盘价（V4）
                # 仅在「买入周期内」(未停止买入) 更新；停止买入(dca_exited)后冻结，作为止损锚点
                if pos.shares > 0 and not pos.dca_exited:
                    cycle_low = getattr(pos, 'cycle_low', None)
                    if cycle_low is None or price < cycle_low:
                        pos.cycle_low = price

                # V4 J门控：买入后，周线J曾>j_operation_gate(默认30)才允许后续卖出操作
                if pos.shares > 0 and pos.dca_exited:
                    if not getattr(pos, 'j_high_done', False) and weekly_j > self.j_operation_gate:
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

                # ===== V4新增：收盘<黄多空线 且 中期<多空 且 收盘<本轮最低价(创新低) -> 全清（无J限制）=====
                # 需先满足 J门控：买入后周线J曾>j_operation_gate才允许卖出
                cycle_low = getattr(pos, 'cycle_low', None)
                stop_ref = cycle_low * (1 - self.stop_loss_buf) if cycle_low is not None else None
                if (pos.shares > 0 and pos.dca_exited
                        and getattr(pos, 'j_high_done', False)
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

                # 后续卖出均需先满足 J门控（买入后周线J曾>j_operation_gate）
                if pos.sell_stage == 0 and getattr(pos, 'j_high_done', False):
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
                                         f"卖1/3，多头区间，监控收盘<中期线")
                        else:
                            pos.golden_confirmed = False
                            logging.info(f"[{self.name}] R{pos.round_id} {trade_date} "
                                         f"卖1/3，空头区间，等金叉")

                # 收盘<中期线：双空全清 vs 多头回调（需J门控解锁）
                if (pos.shares > 0 and getattr(pos, 'j_high_done', False)
                        and pos.golden_confirmed
                        and not pos.price_below_dk_sold
                        and pd.notna(mid_val_today) and price < mid_val_today):
                    mid_below_dk = (pd.notna(mid_val_today) and pd.notna(dk_val_today) and mid_val_today < dk_val_today)
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

                # 收盘<多空线全清（替代原死叉全清，需J门控解锁）
                if (pos.shares > 0 and getattr(pos, 'j_high_done', False)
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
