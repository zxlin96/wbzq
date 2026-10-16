# -*- coding: utf-8 -*-
"""海外宽基：周线J值择时 vs 每周无脑定投 — 5年回测对比

同一份数据分别跑四种策略（状态文件写临时目录，不污染正式文件）：
- plain: 每周首个交易日开盘无条件买入（基线）
- v1:    周线J<=13启动定投+亏损倍投+J>=93/回撤分批卖+死叉全清（NASDAQ100 在用）
- v1h:   v1卖出变体 — 每阶段只卖基准1/6共卖50%，剩余1/2长期持有
- v6:    V5无倍投限5次买入 + V4止损（A股生产主力）
"""
import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import weekly_dca_strategy as wds
from weekly_dca_strategy_plain import PlainWeeklyDCA
from weekly_dca_strategy_v6 import WeeklyDCAStrategyV6

ETFS = [
    ("纳指100", "513100.SH"),
    ("标普500", "513500.SH"),
    ("日经225", "513520.SH"),
    ("德国DAX", "513030.SH"),
]
YEARS = 5
BASE = 1000
TMP = 'test/tmp_overseas_cmp'
os.makedirs(TMP, exist_ok=True)

backtest_start = (datetime.now() - timedelta(days=YEARS * 365)).strftime('%Y%m%d')
WARMUP_WEEKS = 30


def metrics(s, daily):
    last_price = float(daily['close_qfq'].iloc[-1])
    mv = s.shares * last_price
    profit = mv + s.total_sell_amount - s.total_invested
    ret = profit / s.total_invested * 100 if s.total_invested > 0 else 0
    buys = sum(1 for t in s.trades if t['action'] == 'BUY')
    sells = sum(1 for t in s.trades if t['action'] == 'SELL')
    positions = getattr(s, 'positions', None)
    rounds = len(positions) if positions is not None else (1 if buys else 0)
    return {
        'invested': s.total_invested, 'sell_amount': s.total_sell_amount,
        'mv': mv, 'profit': profit, 'ret': ret,
        'buys': buys, 'sells': sells, 'rounds': rounds,
    }


all_rows = {}
for name, code in ETFS:
    daily = wds._get_etf_data(
        code, datetime.now() - timedelta(days=(YEARS + 1) * 365), datetime.now())
    weekly = wds.resample_to_weekly(daily)
    effective_start = backtest_start
    if len(weekly) > WARMUP_WEEKS:
        wend = weekly.iloc[WARMUP_WEEKS]['trade_date']
        if wend > backtest_start:
            effective_start = wend
    last_price = float(daily['close_qfq'].iloc[-1])
    first_price = float(
        daily[daily['trade_date'] >= effective_start]['close_qfq'].iloc[0])
    bh_ret = (last_price - first_price) / first_price * 100

    p = PlainWeeklyDCA(name=name, base_amount=BASE,
                       state_file=os.path.join(TMP, f'plain_{name}.json'))
    p.backtest(daily, weekly, backtest_start=effective_start)
    s1 = wds.WeeklyDCAStrategy(
        name=f'{name}_v1', base_amount=BASE, sell_version='v1',
        state_file=os.path.join(TMP, f'v1_{name}.json'))
    s1.backtest(daily, weekly, backtest_start=effective_start)
    sh = wds.WeeklyDCAStrategy(
        name=f'{name}_v1h', base_amount=BASE, sell_version='v1h',
        state_file=os.path.join(TMP, f'v1h_{name}.json'))
    sh.backtest(daily, weekly, backtest_start=effective_start)
    s6 = WeeklyDCAStrategyV6(
        name=f'{name}_v6', base_amount=BASE,
        state_file=os.path.join(TMP, f'v6_{name}.json'))
    s6.backtest(daily, weekly, backtest_start=effective_start)

    all_rows[name] = {
        '区间涨幅': bh_ret,
        'plain': metrics(p, daily),
        'v1': metrics(s1, daily),
        'v1h': metrics(sh, daily),
        'v6': metrics(s6, daily),
    }

print("\n" + "=" * 100)
print(f"海外宽基 5 年回测对比（{backtest_start} 起，每周基础金额 {BASE} 元，价格前复权）")
print("=" * 100)
for name, row in all_rows.items():
    print(f"\n【{name}】同期指数涨幅(期初买入持有): {row['区间涨幅']:+.2f}%")
    header = f"{'策略':<8}{'总投入':>12}{'总卖出':>12}{'期末市值':>12}{'净收益':>12}{'收益率':>10}{'买入':>6}{'卖出':>6}{'轮数':>6}"
    print(header)
    for strat in ['plain', 'v1', 'v1h', 'v6']:
        m = row[strat]
        label = {'plain': '每周定投', 'v1': 'J择时v1', 'v1h': 'J择时v1h', 'v6': 'J择时v6'}[strat]
        print(f"{label:<8}{m['invested']:>12,.0f}{m['sell_amount']:>12,.0f}"
              f"{m['mv']:>12,.0f}{m['profit']:>12,.0f}{m['ret']:>9.2f}%"
              f"{m['buys']:>6}{m['sells']:>6}{m['rounds']:>6}")

with open(os.path.join(TMP, 'comparison.json'), 'w', encoding='utf-8') as f:
    json.dump(all_rows, f, ensure_ascii=False, indent=2, default=float)
print(f"\n明细已保存: {TMP}/comparison.json")
