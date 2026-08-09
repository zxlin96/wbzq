#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
周线KDJ定投策略 V5 - 去除亏损倍投 + 每轮最多买入5次

在 V2 基础上【简化买入侧】：
- 去掉亏损倍投：无论持仓亏损多少，都不再 ×2 / ×4 加码
- 每轮最多买入 round_periods(默认5) 次：达到次数后不再买入，等待J回升投入剩余预算或卖出

卖出逻辑与 V2 完全一致（收盘<中期线判断双空/回调，收盘<多空线全清）。

实现说明：
- V2 的 get_invest_amount 定义在 Position 类上，倍投(×2/×4)也在其中
- V5 定义 NoMultiplierPosition 继承 Position 并重写 get_invest_amount（去倍投+限次数）
- 并重写 _new_position 使其返回 NoMultiplierPosition
"""
import logging
from typing import List

from weekly_dca_strategy_v2 import (
    WeeklyDCAStrategy as _V2,
    Position as _Position,
)


class NoMultiplierPosition(_Position):
    """V5 仓位：无亏损倍投，每轮最多买入 round_periods 次"""

    def get_invest_amount(self, current_price: float) -> float:
        """V5: 无倍投；达到 round_periods 次后不再买入(返回0)"""
        # 每轮最多买入 round_periods 次
        if self.buy_count >= self.round_periods:
            return 0
        # 预算期内：动态均摊剩余预算
        remaining = max(self.round_budget - self.total_invested, 0)
        remaining_periods = max(self.round_periods - self.buy_count, 1)
        base = remaining / remaining_periods
        # 预算期内受剩余预算约束
        remaining = max(self.round_budget - self.total_invested, 0)
        base = min(base, remaining)
        return max(base, 0)


class WeeklyDCAStrategyV5(_V2):
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
            round_budget=round_budget,
            round_periods=round_periods,
            state_file=state_file or f'dca_state_v5_{name}.json',
        )

    def _new_position(self) -> NoMultiplierPosition:
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


__all__ = ['WeeklyDCAStrategyV5', 'NoMultiplierPosition']