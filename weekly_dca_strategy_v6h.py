#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
周线KDJ定投策略 V6H —— V6 的买入端 + V1h 的卖出哲学。

为什么需要这个变体
------------------
V6 的卖出是"清仓式"：J≥93 卖1/3 → 收盘<中期线再卖 → 收盘<多空线全清，一轮走完必然空仓。
V1h 的卖出是"留仓式"：三段各卖 1/6（合计 50%），剩余 1/2 永久持有不再卖。

红利低波 512890 实测（2019-2026）：留半仓的 V1h 利润是清仓式 V6 的 6.4 倍；
但 V1h 的买入端继承 V1 的 ×2/×4 倍投，对低波品种是隐患。V6H = 取两者之长。

规则（hold_half=True，默认）
--------------------------
买入：完全等同 V6（J≤13 定投、每轮 budget 5000、最多 5 期、无倍投）
卖出：三段递减，每段卖"基准仓位"的 1/6，累计最多 50%，之后永久持有
    第一段（stage 0→1）：以下任一触发
        · V4 止损：收盘<多空线 且 中期<多空线 且 收盘<本轮最低价（需 J 门控解锁）
        · J 峰值回撤 ≥20（峰值≥50）
        · 周线 J ≥ 93
    第二段（stage 1→2）：golden_confirmed 且 收盘<中期线
    第三段（stage 2→3）：price_below_dk_sold 且 收盘<多空线
    stage 3：永久持有，不再任何卖出

开关
----
hold_half=False  → 卖出段退化为 V6 原逻辑，用于"等价性断言"（必须逐笔一致）
stop_full=True   → V4 止损条件仍全额清仓（覆盖 hold_half 的减仓语义），用于消融

实现说明
--------
V4/V6 的卖出状态机是内联在 backtest() 里的（没有可覆写的方法），
因此本类 fork 了 V4.backtest 的完整实现，仅替换"遍历每个活跃仓位"的卖出段。
买入段、周线决策口径、状态清理、汇总打印均与 V4/V6 保持一致。

状态文件：dca_state_v6h_{name}.json
"""

import logging

import pandas as pd

from weekly_dca_strategy import calc_kdj, calc_zhixing_duokong
from weekly_dca_strategy_v5 import NoMultiplierPosition
from weekly_dca_strategy_v6 import WeeklyDCAStrategyV6


class WeeklyDCAStrategyV6H(WeeklyDCAStrategyV6):
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
                 hold_half: bool = True,
                 stop_full: bool = False,
                 state_file: str = None):
        self.hold_half = hold_half
        self.stop_full = stop_full
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
            state_file=state_file or f'dca_state_v6h_{name}.json',
        )

    def _new_position(self) -> NoMultiplierPosition:
        """V6 的无倍投仓位（复用 V5），额外挂 base_shares 记录本轮基准份额。"""
        pos = super()._new_position()
        pos.base_shares = 0.0
        return pos

    # ---------------------------------------------------------------- 卖出
    @staticmethod
    def _ensure_base(pos) -> float:
        """基准份额：优先用已记录的；状态恢复后缺失时从成交流水反推。

        生产每次全量重放不会触发，但断点续跑（从 state_file 恢复的 Position
        没有 base_shares 字段）必须自洽，否则卖出的 1/6 会按剩余份额算，越卖越小。
        """
        base = getattr(pos, 'base_shares', 0.0) or 0.0
        if base <= 0 and getattr(pos, 'trades', None):
            base = sum(t.get('shares', 0.0) for t in pos.trades
                       if t.get('action') == 'BUY')
        if getattr(pos, 'shares', 0.0) > base:
            base = pos.shares
        pos.base_shares = base
        return base

    def _sell_tranche(self, pos, trade_date, price, reason):
        """卖基准仓位的 1/6，累计不超过 50%。返回是否成交。"""
        base = self._ensure_base(pos)
        if base <= 0 or pos.shares <= 0:
            return False
        tranche = base / 6.0
        sold = base - pos.shares
        allowed = base / 2.0 - sold
        qty = min(tranche, allowed, pos.shares)
        if qty <= 0:
            return False
        pos.sell(trade_date, price, qty / pos.shares, reason)
        return True

    def backtest(self, daily_df: pd.DataFrame, weekly_df: pd.DataFrame,
                 backtest_start: str = None):
        mode = 'V6H 留半仓' if self.hold_half else 'V6 原版(等价性对照)'
        logging.info(f"\n{'='*70}")
        logging.info(f"  回测策略: {self.name} ({mode})")
        logging.info(f"  买入端: J<={self.j_buy_threshold} 定投, 每轮预算 {self.round_budget} "
                     f"分 {self.round_periods} 期, 无倍投")
        if self.hold_half:
            logging.info(f"  卖出端: 三段各卖基准1/6 → 累计50% → 剩余永久持有"
                         f"{'（止损仍全清）' if self.stop_full else '（止损亦减为1/6）'}")
        else:
            logging.info(f"  卖出端: V6 原逻辑（1/3 → 1/2 → 全清）")
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
        # 决策只消费"已收盘周线"，跳过本周半成品 bar（沿用 V4 口径）
        max_daily_date = all_dates[-1] if all_dates else None
        decision_weekly_dates = [wd for wd in sorted_weekly_dates
                                 if max_daily_date and wd < max_daily_date]
        for d in all_dates:
            best_wd = None
            for wd in decision_weekly_dates:
                if wd <= d:
                    best_wd = wd
                else:
                    break
            if best_wd:
                daily_to_weekly[d] = best_wd

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

            # ===== 买入逻辑（与 V6 完全一致）=====
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
                        dca_pos.dca_exit_date = trade_date
                        dca_pos.dca_exit_amount = remaining
                        logging.info(f"[{self.name}] R{dca_pos.round_id} {trade_date} "
                                     f"J回升投入剩余预算: 价格={price:.4f}, 金额={remaining:.0f}")
                    dca_pos.dca_exited = True

            # ===== 遍历活跃仓位，独立判断卖出 =====
            for pos in self._active_positions():
                # 基准份额：优先用已记录的；状态恢复后缺失时从成交流水反推
                self._ensure_base(pos)

                # 本轮买入周期内最低收盘价（V4 止损锚点）
                if pos.shares > 0 and not pos.dca_exited:
                    cycle_low = getattr(pos, 'cycle_low', None)
                    if cycle_low is None or price < cycle_low:
                        pos.cycle_low = price

                # V4 J门控：买入后周线J曾>j_operation_gate 才允许卖出
                if pos.shares > 0 and pos.dca_exited:
                    if not getattr(pos, 'j_high_done', False) and weekly_j > self.j_operation_gate:
                        pos.j_high_done = True
                        logging.info(f"[{self.name}] R{pos.round_id} {trade_date} "
                                     f"周线J={weekly_j:.2f}>{self.j_operation_gate}，解锁卖出操作")

                if pos.shares > 0 and pos.sell_stage == 0:
                    if weekly_j > pos.j_peak:
                        pos.j_peak = weekly_j

                if pos.waiting_golden and trade_date in golden_cross_dates:
                    pos.waiting_golden = False
                    pos.golden_confirmed = True
                    pos.price_below_dk_sold = False
                    logging.info(f"[{self.name}] R{pos.round_id} {trade_date} "
                                 f"日线知行多空金叉，监控收盘<中期线或等收盘<多空线")

                # ---------- 卖出：V6 原逻辑（等价性对照） ----------
                if not self.hold_half:
                    cycle_low = getattr(pos, 'cycle_low', None)
                    stop_ref = cycle_low * (1 - self.stop_loss_buf) if cycle_low is not None else None
                    if (pos.shares > 0 and pos.dca_exited
                            and getattr(pos, 'j_high_done', False)
                            and pd.notna(dk_val_today) and price < dk_val_today
                            and pd.notna(mid_val_today) and mid_val_today < dk_val_today
                            and stop_ref is not None and price < stop_ref):
                        pos.sell(trade_date, price, 1.0,
                                 f"R{pos.round_id} V4: 收盘({price:.4f})<黄多空线({dk_val_today:.4f}) "
                                 f"且中期线({mid_val_today:.4f})<多空线({dk_val_today:.4f}) "
                                 f"且收盘<本轮最低价止损线({stop_ref:.4f})全清")
                        self.total_sell_amount += pos.total_sell_amount
                        continue

                    if pos.sell_stage == 0 and getattr(pos, 'j_high_done', False):
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
                            else:
                                pos.golden_confirmed = False
                            self.total_sell_amount += pos.total_sell_amount
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
                            else:
                                pos.golden_confirmed = False

                    if (pos.shares > 0 and getattr(pos, 'j_high_done', False)
                            and pos.golden_confirmed
                            and not pos.price_below_dk_sold
                            and pd.notna(mid_val_today) and price < mid_val_today):
                        mid_below_dk = (pd.notna(mid_val_today) and pd.notna(dk_val_today)
                                        and mid_val_today < dk_val_today)
                        if mid_below_dk:
                            pos.sell(trade_date, price, 1.0,
                                     f"R{pos.round_id} 双空全清: 收盘({price:.4f})<中期线({mid_val_today:.4f}) "
                                     f"且中期({mid_val_today:.4f})<多空线({dk_val_today:.4f})")
                        else:
                            pos.sell(trade_date, price, 0.5,
                                     f"R{pos.round_id} 多头回调: 收盘({price:.4f})<中期线({mid_val_today:.4f}), "
                                     f"中期({mid_val_today:.4f})>多空线({dk_val_today:.4f})")
                            pos.sell_stage = 2
                            pos.price_below_dk_sold = True

                    if (pos.shares > 0 and getattr(pos, 'j_high_done', False)
                            and pos.golden_confirmed
                            and pos.price_below_dk_sold
                            and pd.notna(dk_val_today) and price < dk_val_today):
                        pos.sell(trade_date, price, 1.0,
                                 f"R{pos.round_id} 收盘({price:.4f})<多空线({dk_val_today:.4f})全清")
                    continue

                # ---------- 卖出：V6H 三段递减，留 1/2 永持 ----------
                if pos.sell_stage >= 3 or pos.shares <= 0:
                    continue  # 已卖满 50%，永久持有

                cycle_low = getattr(pos, 'cycle_low', None)
                stop_ref = cycle_low * (1 - self.stop_loss_buf) if cycle_low is not None else None
                stop_hit = (pos.dca_exited and getattr(pos, 'j_high_done', False)
                            and pd.notna(dk_val_today) and price < dk_val_today
                            and pd.notna(mid_val_today) and mid_val_today < dk_val_today
                            and stop_ref is not None and price < stop_ref)

                # 第一段：止损 / J峰值回撤 / J≥93
                if pos.sell_stage == 0 and getattr(pos, 'j_high_done', False):
                    if stop_hit and self.stop_full:
                        pos.sell(trade_date, price, 1.0,
                                 f"R{pos.round_id} V4止损全清: 收盘({price:.4f})<多空线({dk_val_today:.4f}) "
                                 f"且中期<多空 且跌破本轮最低价({stop_ref:.4f})")
                        pos.sell_stage = 3
                        self.total_sell_amount += pos.total_sell_amount
                        continue

                    if stop_hit:
                        if self._sell_tranche(pos, trade_date, price,
                                              f"R{pos.round_id} 止损减仓1/6: 收盘跌破多空线且创新低"):
                            pos.sell_stage = 1
                            logging.info(f"[{self.name}] R{pos.round_id} {trade_date} 止损减仓1/6")
                    elif (not pos.pullback_sold and pos.j_peak >= self.j_peak_min
                          and weekly_j <= pos.j_peak - self.j_pullback):
                        if self._sell_tranche(pos, trade_date, price,
                                              f"R{pos.round_id} J峰值回撤(J_peak={pos.j_peak:.2f}, "
                                              f"J={weekly_j:.2f})减仓1/6"):
                            pos.sell_stage = 1
                            pos.pullback_sold = True
                            pos.waiting_golden = True
                            mid_val = daily_row.get('zhixing_mid')
                            dk_val = daily_row.get('zhixing_duokong')
                            if pd.notna(mid_val) and pd.notna(dk_val) and mid_val > dk_val:
                                pos.golden_confirmed = True
                                pos.waiting_golden = False
                            else:
                                pos.golden_confirmed = False
                            self.total_sell_amount += pos.total_sell_amount
                    elif weekly_j >= self.j_sell_half_threshold:
                        if self._sell_tranche(pos, trade_date, price,
                                              f"R{pos.round_id} 周线J={weekly_j:.2f}"
                                              f">={self.j_sell_half_threshold} 减仓1/6"):
                            pos.sell_stage = 1
                            pos.waiting_golden = True
                            mid_val = daily_row.get('zhixing_mid')
                            dk_val = daily_row.get('zhixing_duokong')
                            if pd.notna(mid_val) and pd.notna(dk_val) and mid_val > dk_val:
                                pos.golden_confirmed = True
                                pos.waiting_golden = False
                            else:
                                pos.golden_confirmed = False

                # 第二段：收盘<中期线
                elif (pos.sell_stage == 1 and getattr(pos, 'j_high_done', False)
                      and pos.golden_confirmed and not pos.price_below_dk_sold
                      and pd.notna(mid_val_today) and price < mid_val_today):
                    if self._sell_tranche(pos, trade_date, price,
                                          f"R{pos.round_id} 收盘({price:.4f})<中期线"
                                          f"({mid_val_today:.4f}) 减仓1/6"):
                        pos.sell_stage = 2
                        pos.price_below_dk_sold = True
                        logging.info(f"[{self.name}] R{pos.round_id} {trade_date} 第二段减仓1/6")

                # 第三段：收盘<多空线
                elif (pos.sell_stage == 2 and getattr(pos, 'j_high_done', False)
                      and pos.price_below_dk_sold
                      and pd.notna(dk_val_today) and price < dk_val_today):
                    if self._sell_tranche(pos, trade_date, price,
                                          f"R{pos.round_id} 收盘({price:.4f})<多空线"
                                          f"({dk_val_today:.4f}) 减仓1/6"):
                        pos.sell_stage = 3
                        logging.info(f"[{self.name}] R{pos.round_id} {trade_date} "
                                     f"第三段减仓1/6，剩余永久持有")

            total_sell = 0
            all_trades = []
            for p in self.positions:
                total_sell += p.total_sell_amount
                all_trades.extend(p.trades)
            self.total_sell_amount = total_sell
            self.trades = sorted(all_trades, key=lambda t: (t['date'], t.get('round', 0)))
            self._cleanup_cleared()

        self._print_summary(daily_dict)
