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
卖出：每周最后一个交易日收盘统一判断，允许跨档合并，下一交易日开盘执行。
    三档累计目标为本轮原始份额的 1/6、1/3、1/2，只卖目标与已卖份额的差额。
    第一段（stage 0→1）：以下任一触发
        · V4 止损：收盘<多空线 且 中期<多空线 且 收盘<本轮最低价（需 J 门控解锁）
        · J 峰值回撤 ≥20（峰值≥50）
        · 周线 J ≥ 93
    第二段（stage 1→2）：收盘<中期线，且满足以下任一
        · golden_confirmed（多头回调场景）
        · 中期<多空（空头场景，不再等金叉）
        · V4 止损条件成立
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
买入段保持 V6 口径。留半仓模式将卖出拆为周末确认与次交易日开盘成交，
待执行订单单独持久化，净值按实际成交份额回放。

状态文件：dca_state_v6h_{name}.json
"""

import json
import logging
import math
from pathlib import Path

import pandas as pd

from weekly_completion import confirmed_week_dates, refresh_action_summary

from weekly_dca_strategy import calc_kdj, calc_zhixing_duokong
from weekly_dca_strategy_v5 import NoMultiplierPosition
from weekly_dca_strategy_v6 import WeeklyDCAStrategyV6


SELL_POLICY = 'weekly_aggregate_next_open_v1'


class V6HPosition(NoMultiplierPosition):
    """Persist confirmed orders without treating them as executed sales."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.base_shares = 0.0
        self.pending_sell = None

    def to_dict(self):
        return dict(super().to_dict(), base_shares=self.base_shares,
                    pending_sell=self.pending_sell)

    @classmethod
    def from_dict(cls, data, *args, **kwargs):
        pos = super().from_dict(data, *args, **kwargs)
        pos.base_shares = data.get('base_shares', 0.0)
        pos.pending_sell = data.get('pending_sell')
        return pos


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

    def _new_position(self) -> V6HPosition:
        pos = V6HPosition(
            self.next_round_id, self.base_amount,
            self.loss_threshold_1, self.loss_threshold_2,
            self.j_buy_threshold, self.j_sell_half_threshold,
            self.j_peak_min, self.j_pullback, self.round_budget, self.round_periods)
        self.next_round_id += 1
        self.positions.append(pos)
        return pos

    def load_state(self):
        """Restore V6H fields and keep the no-multiplier position type."""
        if not Path(self.state_file).exists():
            return
        try:
            state = json.loads(Path(self.state_file).read_text(encoding='utf-8'))
            positions = [V6HPosition.from_dict(
                data, self.base_amount, self.loss_threshold_1, self.loss_threshold_2,
                self.j_buy_threshold, self.j_sell_half_threshold,
                self.j_peak_min, self.j_pullback, self.round_budget, self.round_periods)
                for data in state.get('positions', [])]
            self.positions = positions
            self.next_round_id = max(state.get('next_round_id', 1),
                                     max((p.round_id + 1 for p in positions), default=1))
            self.total_sell_amount = state.get('total_sell_amount', 0)
            self.trades = state.get('trades', [])
        except (OSError, ValueError, TypeError) as exc:
            logging.warning('[%s] 加载状态失败: %s', self.name, exc)

    def _dca_position(self):
        # A confirmed sale closes this round to further funding, even while its
        # fill is pending. The executed sell_stage changes only at the fill.
        return next((p for p in self.positions if p.dca_active and p.sell_stage == 0
                     and not getattr(p, 'pending_sell', None)), None)

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

    def _plan_weekly_sell(self, pos, signal_date, price, weekly_j, mid, dk,
                          execution_date=None):
        """Evaluate reachable stages together; leave executed state untouched."""
        if (pos.shares <= 0 or pos.sell_stage >= 3
                or not pos.j_high_done or getattr(pos, 'pending_sell', None)):
            return None
        stage = pos.sell_stage
        flags = {key: getattr(pos, key) for key in (
            'pullback_sold', 'waiting_golden', 'golden_confirmed', 'price_below_dk_sold')}
        stop_ref = (pos.cycle_low * (1 - self.stop_loss_buf)
                    if pos.cycle_low is not None else None)
        stop_hit = (pos.dca_exited and pd.notna(mid) and pd.notna(dk)
                    and price < dk and mid < dk
                    and stop_ref is not None and price < stop_ref)
        reasons = []
        full_exit = bool(stage == 0 and stop_hit and self.stop_full)
        if stage == 0:
            if stop_hit:
                reasons.append('V4止损：收盘跌破多空线且创新低')
            elif (not pos.pullback_sold and pos.j_peak >= self.j_peak_min
                  and weekly_j <= pos.j_peak - self.j_pullback):
                reasons.append(f'J峰值回撤（{pos.j_peak:.2f}→{weekly_j:.2f}）')
                flags['pullback_sold'] = True
            elif weekly_j >= self.j_sell_half_threshold:
                reasons.append(f'周线J={weekly_j:.2f}≥{self.j_sell_half_threshold}')
            else:
                return None
            stage = 1
            if not stop_hit:
                flags['golden_confirmed'] = bool(pd.notna(mid) and pd.notna(dk) and mid > dk)
                flags['waiting_golden'] = not flags['golden_confirmed']
        if (stage == 1 and not flags['price_below_dk_sold']
                and pd.notna(mid) and price < mid
                and (flags['golden_confirmed'] or (pd.notna(dk) and mid < dk) or stop_hit)):
            stage = 2
            flags['price_below_dk_sold'] = True
            flags['waiting_golden'] = False
            reasons.append(f'收盘({price:.4f})<中期线({mid:.4f})')
        if stage == 2 and flags['price_below_dk_sold'] and pd.notna(dk) and price < dk:
            stage = 3
            reasons.append(f'收盘({price:.4f})<多空线({dk:.4f})')
        if full_exit:
            stage = 3
        if stage <= pos.sell_stage:
            return None
        base = self._ensure_base(pos)
        target = 1.0 if full_exit else stage / 6.0
        qty = min(pos.shares, max(0.0, base * target - (base - pos.shares)))
        if qty <= 1e-9:
            return None
        return dict(signal_date=str(signal_date), execution_date=execution_date,
                    target_stage=stage, target_sold_ratio=target, shares=qty,
                    reason='；'.join(reasons), state_after_fill=flags,
                    execution='next_session_open')

    def _confirm_weekly_sell(self, pos, trade_date, price, weekly_j, mid, dk,
                             execution_date=None):
        order = self._plan_weekly_sell(pos, trade_date, price, weekly_j, mid, dk,
                                       execution_date=execution_date)
        if order:
            pos.pending_sell = order
            logging.info('[%s] R%s %s 确认累计卖出目标 %.2f%%，待下一交易日开盘卖出 %.4f份',
                         self.name, pos.round_id, trade_date,
                         order['target_sold_ratio'] * 100, order['shares'])

    def _execute_pending_sell(self, pos, trade_date, open_price):
        order = getattr(pos, 'pending_sell', None)
        if not order or str(trade_date) <= order['signal_date']:
            return False
        # With a calendar, a missing opening quote must not silently move the
        # fill to a later session (or fall back to the closing price).
        expected = order.get('execution_date')
        if expected and str(trade_date) != expected:
            return False
        if not expected:
            order['execution_date'] = str(trade_date)
        if pd.isna(open_price) or not math.isfinite(open_price) or open_price <= 0 or pos.shares <= 0:
            return False
        base = self._ensure_base(pos)
        allowed = max(0.0, base * order['target_sold_ratio'] - (base - pos.shares))
        qty = min(order['shares'], allowed, pos.shares)
        if qty <= 1e-9:
            return False
        reason = (f"R{pos.round_id} 周线统一减仓（信号{order['signal_date']}，"
                  f"累计卖出目标{order['target_sold_ratio']:.2%}）：{order['reason']}")
        pos.sell(trade_date, open_price, qty / pos.shares, reason)
        pos.sell_stage = order['target_stage']
        for key, value in order['state_after_fill'].items():
            setattr(pos, key, value)
        pos.trades[-1].update(signal_date=order['signal_date'], execution='next_session_open',
                             target_stage=order['target_stage'],
                             target_sold_ratio=order['target_sold_ratio'])
        pos.pending_sell = None
        return True

    def calc_nav_curve(self, daily_df):
        if not self.hold_half:
            return super().calc_nav_curve(daily_df)
        by_date = {}
        for trade in self.trades:
            by_date.setdefault(trade['date'], []).append(trade)
        rows = []
        shares = invested = proceeds = 0.0
        for row in daily_df.itertuples():
            for trade in by_date.get(row.trade_date, []):
                if trade['action'] == 'BUY':
                    shares += trade['shares']
                    invested += trade['amount']
                elif trade['action'] == 'SELL':
                    shares -= trade['shares']
                    proceeds += trade['amount']
            # Use executed shares, rather than dividing opening proceeds by the
            # day's closing price. Summary adds sale proceeds separately.
            market = max(0.0, shares) * row.close_qfq
            rows.append(dict(trade_date=row.trade_date,
                             nav=round((market + proceeds) / invested, 6) if invested else 1.,
                             cum_invested=round(invested, 2), market_value=round(market, 2),
                             total_value=round(market, 2)))
        return pd.DataFrame(rows)

    # ---------------------------------------------------------------- 展示层
    STAGE_LABELS = {
        1: ('已减1/6·等周末检查', '#a04e50'),
        2: ('已减1/3·等周末检查', '#a04e50'),
        3: ('已卖50%·永久持有', '#456faf'),
    }

    def get_next_action(self, daily_df, weekly_df):
        """在 V2/V4 的建议结构上，改写 hold_half 语义的阶段标签。

        V2 的 get_next_action 只认 sell_stage 0/1/2，stage=3 会落到 '未知'；
        且 1/2 段的文案是"已卖1/3/2/3"（V2 口径），对 V6H 是错的。
        这里按 round_id 回填正确文案，顶层汇总同步刷新。
        """
        res = super().get_next_action(daily_df, weekly_df)
        if not self.hold_half:
            return res
        res['sell_policy'] = SELL_POLICY
        by_round = {p.round_id: p for p in self._active_positions()}
        for info in res.get('positions', []):
            pos = by_round.get(info.get('round_id'))
            if pos is None:
                continue
            stage = pos.sell_stage
            order = getattr(pos, 'pending_sell', None)
            if order:
                info['pending_sell'] = dict(order)
                info['action'] = 'sell_weekly_pending'
                info['action_label'] = f"R{pos.round_id} 待执行·累计卖出目标{order['target_sold_ratio']:.2%}"
                info['action_color'] = '#a04e50'
                execution = order.get('execution_date') or '下一交易日'
                info['action_detail'] = (
                    f"R{pos.round_id} {order['signal_date']}收盘确认，{execution}开盘卖出"
                    f"{order['shares']:.2f}份，使累计卖出达到本轮原始份额的"
                    f"{order['target_sold_ratio']:.2%}；尚未计为成交")
            elif stage in self.STAGE_LABELS:
                label, color = self.STAGE_LABELS[stage]
                info['action'] = f'v6h_stage_{stage}'
                info['action_label'] = f"R{pos.round_id} {label}"
                info['action_color'] = color
                if stage == 3:
                    info['action_detail'] = (
                        f"R{pos.round_id} 已卖满50%，剩余{pos.shares:.0f}份永久持有，不再触发卖出")
                elif stage == 2:
                    info['action_detail'] = (
                        f"R{pos.round_id} 周末收盘<多空线，累计卖出目标1/2；下一交易日开盘执行")
                else:
                    info['action_detail'] = (
                        f"R{pos.round_id} 周末统一检查后续档位，可跨档合并，累计最多卖出50%")
            elif info.get('action', '').startswith('sell_'):
                # The parent proposes daily V2 sales. Only a confirmed V6H
                # order may be presented as actionable under this policy.
                info.update(action='hold', action_label=f'R{pos.round_id} 持有·等周末检查',
                            action_color='#456faf',
                            action_detail=f'R{pos.round_id} 等本周最后一个交易日收盘统一检查卖出条件')
        if res.get('positions'):
            first = res['positions'][0]
            res['action'] = first.get('action', res.get('action'))
            res['action_label'] = first.get('action_label', res.get('action_label'))
            res['action_color'] = first.get('action_color', res.get('action_color'))
            res['action_detail'] = ' | '.join(
                p.get('action_detail', '') for p in res['positions'])
        return refresh_action_summary(res)

    def backtest(self, daily_df: pd.DataFrame, weekly_df: pd.DataFrame,
                 backtest_start: str = None):
        mode = 'V6H 留半仓' if self.hold_half else 'V6 原版(等价性对照)'
        logging.info(f"\n{'='*70}")
        logging.info(f"  回测策略: {self.name} ({mode})")
        logging.info(f"  买入端: J<={self.j_buy_threshold} 定投, 每轮预算 {self.round_budget} "
                     f"分 {self.round_periods} 期, 无倍投")
        if self.hold_half:
            logging.info(f"  卖出端: 周末收盘统一判断、跨档合并 → 下一交易日开盘 → 累计最多50%"
                         f"{'（止损仍全清）' if self.stop_full else '（止损也遵守累计上限）'}")
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
        # 本周最后一个交易日收盘即确认，不等待下一条日线。
        decision_weekly_dates = confirmed_week_dates(weekly_kdj, daily_df)
        decision_dates = set(decision_weekly_dates)
        calendar = weekly_df.attrs.get('trading_calendar') or daily_df.attrs.get('trading_calendar', {})
        open_sessions = sorted(str(day) for day, is_open in calendar.items() if int(is_open) == 1)
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
            if self.hold_half:
                for pos in self._active_positions():
                    self._execute_pending_sell(pos, trade_date, daily_row.get('open_qfq', float('nan')))
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

            is_sell_decision = self.hold_half and trade_date in decision_dates
            execution_date = (next((d for d in open_sessions if d > str(trade_date)), None)
                              if is_sell_decision else None)
            # At a low-J close, confirm the exited funding round's first sale
            # before choosing the buy round. A pending sale closes that round
            # to funding; its fill and executed stage still wait for next open.
            if is_sell_decision and weekly_j <= self.j_buy_threshold:
                previous_round = self._dca_position()
                if previous_round is not None and previous_round.dca_exited:
                    self._confirm_weekly_sell(
                        previous_round, trade_date, price, weekly_j,
                        mid_val_today, dk_val_today, execution_date=execution_date)

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

                if (pos.waiting_golden and trade_date in golden_cross_dates
                        and (not self.hold_half or pos.sell_stage < 2)):
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

                # ---------- V6H：仅在本周最后一个交易日收盘形成订单 ----------
                if is_sell_decision:
                    self._confirm_weekly_sell(
                        pos, trade_date, price, weekly_j, mid_val_today, dk_val_today,
                        execution_date=execution_date)

            total_sell = 0
            all_trades = []
            for p in self.positions:
                total_sell += p.total_sell_amount
                all_trades.extend(p.trades)
            self.total_sell_amount = total_sell
            self.trades = sorted(all_trades, key=lambda t: (t['date'], t.get('round', 0)))
            self._cleanup_cleared()

        self._print_summary(daily_dict)
