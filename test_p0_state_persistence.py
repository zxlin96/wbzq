#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P0-3 回归测试：V4 卖出状态字段（cycle_low / j_high_done）的持久化与断点续跑一致性

背景：V4 起经 getattr(pos, 'cycle_low'/'j_high_done') 读取这两个字段，但历史上
未进 to_dict/from_dict——任何从 dca_state_*.json 恢复的路径（断点续跑、增量决策）
都会把 J 门控和创新低止损锚点重置为未解锁/无锚点。

验证：
1. v1.Position 与 v2.Position（V6 实际使用的仓位类）roundtrip 后字段不丢
2. 含该字段的 state 文件经 load_state 恢复后，V4 的卖出门控判断不重置
3. 旧版 state 文件（无这两个字段）加载后回退为安全默认值，不报错
"""
import json
import os
import tempfile

from weekly_dca_strategy import Position as PositionV1
from weekly_dca_strategy_v2 import Position as PositionV2
from weekly_dca_strategy_v6 import WeeklyDCAStrategyV6


def make_pos(cls, **kwargs):
    return cls(
        round_id=kwargs.get('round_id', 7),
        base_amount=1000.0,
        loss_threshold_1=0.05,
        loss_threshold_2=0.10,
        j_buy_threshold=13,
        j_sell_half_threshold=93,
        j_peak_min=50,
        j_pullback=20,
        round_budget=kwargs.get('round_budget', 5000),
        round_periods=5,
    )


def test_roundtrip(cls, label):
    p = make_pos(cls)
    p.shares = 123.4
    p.total_invested = 5000.0
    p.cycle_low = 0.9523
    p.j_high_done = True
    d = p.to_dict()
    assert 'cycle_low' in d and 'j_high_done' in d, f"{label}: to_dict 缺字段"
    q = cls.from_dict(
        d, 1000.0, 0.05, 0.10, 13, 93, 50, 20, 5000, 5)
    assert q.cycle_low == 0.9523, f"{label}: cycle_low 未恢复, got {q.cycle_low}"
    assert q.j_high_done is True, f"{label}: j_high_done 未恢复"
    print(f"  [PASS] {label}: roundtrip 保留 cycle_low/j_high_done")


def test_legacy_state_missing_fields():
    """旧 state（无这两个字段）加载不报错，回退默认值"""
    d = make_pos(PositionV2).to_dict()
    d.pop('cycle_low', None)
    d.pop('j_high_done', None)
    q = PositionV2.from_dict(d, 1000.0, 0.05, 0.10, 13, 93, 50, 20, 5000, 5)
    assert q.cycle_low is None and q.j_high_done is False
    print("  [PASS] 旧版 state（无新字段）加载回退默认值")


def test_load_state_via_strategy(tmpdir):
    """经 V6 策略 load_state 恢复后，j_high_done/cycle_low 保持——断点续跑门控不重置"""
    state_file = os.path.join(tmpdir, 'dca_state_v6_test.json')
    p = make_pos(PositionV2, round_budget=5000)
    p.dca_active = False
    p.shares = 100.0
    p.total_invested = 5000.0
    p.dca_exited = True  # 已停止买入，进入卖出观察期
    p.cycle_low = 1.0100
    p.j_high_done = True  # J 门控已解锁
    state = {
        'next_round_id': 8,
        'total_sell_amount': 0.0,
        'trades': [],
        'positions': [p.to_dict()],
    }
    with open(state_file, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False)

    s = WeeklyDCAStrategyV6(name='test', base_amount=1000.0,
                            state_file=state_file)
    assert len(s.positions) == 1
    pos = s.positions[0]
    # V4 的卖出逻辑以 getattr 读取这两个字段——恢复后必须等价于重启前
    assert getattr(pos, 'j_high_done', False) is True, "重启后 J 门控被重置（卖出被错误锁定）"
    assert getattr(pos, 'cycle_low', None) == 1.0100, "重启后止损锚点丢失"
    # 模拟 V4 的止损判断：门控解锁 + 破锚点应允许触发（这里只验证门控读数）
    print("  [PASS] V6 load_state 恢复 j_high_done/cycle_low（断点续跑门控不重置）")


if __name__ == '__main__':
    print("P0-3 状态持久化回归测试")
    test_roundtrip(PositionV1, "v1.Position")
    test_roundtrip(PositionV2, "v2.Position(V6在用)")
    test_legacy_state_missing_fields()
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        test_load_state_via_strategy(tmpdir)
    print("全部通过")
