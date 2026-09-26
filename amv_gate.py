#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
活跃市值(0AMV) "两票共振" 市场级闸门信号

从 amv_timing_backtest.py 的状态定义抽取为可复用模块, 供定投策略 V7 做市场级开关:
  票1 amv_state_open : AMV close > MA10        (st_AMV>MA10, 10日滚动均额, 含当日)
  票2 resonance_open : 多头共振                (st_多头共振: AMV>MA10 且 MA10>MA30
                                                且 当日成交额>20日均额, 均含当日)
  gate_open          : 两票取或 (>=1 票即开)

口径与 amv_timing_backtest.py 完全一致:
  - 均线/均额均为含当日的右对齐滚动窗口 (pandas rolling 默认)
  - 比较为严格大于 (>)
  - 预热期(前 9/29 个交易日)指标为 NaN, 对应信号为 False, 与回测口径一致
数据源: data/0AMV_day.csv (date,open,high,low,close,volume,amount, 1993 ~ 今)
"""
import logging

import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

MA_STATE = 10    # 票1: AMV close 均线窗口
MA_TREND = 30    # 票2: 趋势确认均线窗口 (MA10>MA30)
MA_AMOUNT = 20   # 票2: 成交额均额窗口


def load_amv_gate(csv_path: str = 'data/0AMV_day.csv') -> pd.DataFrame:
    """加载 0AMV 日线并计算市场级闸门信号。

    返回 DataFrame, 列:
      trade_date      : YYYYMMDD 字符串 (与定投回测 daily 数据格式一致)
      amv_state_open  : 票1, AMV close > MA10
      resonance_open  : 票2, 多头共振 (AMV>MA10 且 MA10>MA30 且 amount>MA20均额)
      gate_open       : 两票取或, >=1 票即开
    所有指标仅使用当日及之前数据。
    """
    df = pd.read_csv(csv_path, encoding='utf-8-sig', parse_dates=['date'])
    df = df.rename(columns={'date': 'trade_date', 'close': 'amv', 'amount': 'mkt_amount'})
    df = df[['trade_date', 'amv', 'mkt_amount']].sort_values('trade_date').reset_index(drop=True)

    # 与 amv_timing_backtest.py 口径一致: 含当日的滚动均线
    ma10 = df['amv'].rolling(MA_STATE).mean()
    ma30 = df['amv'].rolling(MA_TREND).mean()
    ma_amt20 = df['mkt_amount'].rolling(MA_AMOUNT).mean()

    state = df['amv'] > ma10                                        # 票1: AMV>MA10
    resonance = (df['amv'] > ma10) & (ma10 > ma30) \
        & (df['mkt_amount'] > ma_amt20)                             # 票2: 多头共振
    # 预热期 NaN 比较结果为 False, 与回测口径一致
    state = state.fillna(False).astype(bool)
    resonance = resonance.fillna(False).astype(bool)

    out = pd.DataFrame({
        'trade_date': df['trade_date'].dt.strftime('%Y%m%d'),
        'amv_state_open': state,
        'resonance_open': resonance,
    })
    out['gate_open'] = out['amv_state_open'] | out['resonance_open']
    logger.info('AMV 闸门信号加载完成: %s (%s ~ %s), 共 %d 个交易日',
                csv_path, out['trade_date'].iloc[0], out['trade_date'].iloc[-1], len(out))
    return out


if __name__ == '__main__':
    gate = load_amv_gate()

    # 1) 2020 年以来 gate_open 开/关天数占比
    g2020 = gate[gate['trade_date'] >= '20200101']
    open_pct = g2020['gate_open'].mean()
    print(f'\n2020 以来共 {len(g2020)} 个交易日: gate_open 开 {g2020["gate_open"].sum()} 天'
          f' ({open_pct:.1%}) / 关 {(~g2020["gate_open"]).sum()} 天 ({1 - open_pct:.1%})')
    print(f'  票1(AMV>MA10) 开: {g2020["amv_state_open"].mean():.1%} | '
          f'票2(多头共振) 开: {g2020["resonance_open"].mean():.1%}')

    # 2) 最近 20 个交易日状态
    print('\n最近 20 个交易日:')
    print(gate.tail(20).to_string(index=False))

    # 3) 抽查历史时点
    print('\n历史时点抽查:')
    for d in ('20240924', '20220426', '20210218'):
        row = gate[gate['trade_date'] == d]
        if len(row):
            r = row.iloc[0]
            print(f'  {d}: state={r["amv_state_open"]} resonance={r["resonance_open"]} '
                  f'gate_open={r["gate_open"]}')
        else:
            print(f'  {d}: 非交易日, 无数据')
