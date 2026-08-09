#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
周线KDJ定投策略 V6 - V5(无倍投/限购买次数) + V4(止损策略)

组合 V5 的买入逻辑 与 V4 的卖出/止损逻辑：
- 买入侧（来自 V5）：去掉亏损倍投(×2/×4)，每轮最多买入 round_periods(默认5) 次
- 卖出侧（来自 V4）：
  · J门控：买入(停止买入dca_exited)后，须周线J曾>j_operation_gate(默认30)才解锁卖出操作
  · V4止损：收盘<黄多空线 且 中期线<多空线(空头) 且 收盘<本轮买入期最低价(可选缓冲) -> 全清
  · 其余卖出逻辑与 V2 一致（收盘<中期线判断双空/回调，收盘<多空线全清）

实现：继承 V4，仅重写 _new_position 返回无倍投仓位(NoMultiplierPosition，复用V5)。
"""
import logging
from typing import List

from weekly_dca_strategy_v4 import WeeklyDCAStrategyV4 as _V4
from weekly_dca_strategy_v5 import NoMultiplierPosition


class WeeklyDCAStrategyV6(_V4):
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
            state_file=state_file or f'dca_state_v6_{name}.json',
        )

    def _new_position(self) -> NoMultiplierPosition:
        """V6: 使用无倍投、限购买次数的仓位"""
        pos = NoMultiplierPosition(
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


__all__ = ['WeeklyDCAStrategyV6']