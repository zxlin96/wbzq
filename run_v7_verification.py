#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V7 验证运行器 —— 《定投策略优化TODO计划.md》Phase 3 的可复现执行入口

阶段：
  cache        拉取 9 年日线(含复权)到 data/cache/，后续全部回测读缓存（保证同一数据快照）
  matrix       对比矩阵：V6 基线 + 6 个 V7 特性组合 + V7全关等价性检查，14 标的全量
  sensitivity  以完整组合(T2full)为基准的 ±20% 邻域参数扰动（每 ETF 一份 JSON）
  aggregate    合并 matrix/sensitivity 结果并按预注册采纳标准出判定

用法：
  python run_v7_verification.py cache
  python run_v7_verification.py matrix --etfs 通信,创新药
  python run_v7_verification.py sensitivity --etfs 通信,创新药
  python run_v7_verification.py aggregate

口径说明：
- 净值曲线沿用 calc_nav_curve（（市值+已实现利润）/累计投入，不含闲置资金），
  衡量"已部署资金"的信号质量；总利润 = 市值 + 总卖出 - 总投入（财富口径）。
- IS/OOS：按回测窗口 70%/30% 切分；OOS 净值以切分点归一。
- 预注册采纳标准（Phase 3 开始前写死）：
  候选在 OOS 上 Calmar 较 V6 改善 >=15%；或 OOS 最大回撤收窄 >=10% 且年化损失 <=2pp。
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from weekly_dca_strategy_v2 import WeeklyDCAStrategy as StrategyV2, _get_etf_data, resample_to_weekly
from weekly_dca_strategy_v6 import WeeklyDCAStrategyV6
from weekly_dca_strategy_v7 import WeeklyDCAStrategyV7

logging.basicConfig(level=logging.WARNING, format='[%(asctime)s] %(levelname)s | %(message)s')
logging.getLogger().setLevel(logging.WARNING)

CACHE_DIR = 'data/cache'
OUT_DIR = 'results/v7'
STATE_DIR = os.path.join(OUT_DIR, 'states')
FETCH_YEARS = 9          # 缓存拉取年数（T3 分位化需要 156 周≈3 年 J 历史）
YEARS = 5                # 回测窗口（与生产 etf_config.json years 一致）
WARMUP_WEEKS = 30
IS_RATIO = 0.7

# ---- 对比矩阵：V6 基线 + V7 特性组合（逐项消融，不打包上线） ----
MATRIX_CONFIGS = {
    'V7OFF':   {},  # 全关，必须与 V6 逐笔一致（等价性检查）
    'T4A':     {'ramp_mode': 'split2'},
    'T4B':     {'ramp_mode': 'j20'},
    'T1freeze': {'ramp_mode': 'split2', 'use_trend_gate': 'freeze'},
    'T1half':  {'ramp_mode': 'split2', 'use_trend_gate': 'half'},
    'T3':      {'ramp_mode': 'split2', 'use_trend_gate': 'freeze', 'use_j_percentile': True},
    'T2full':  {'ramp_mode': 'split2', 'use_trend_gate': 'freeze',
                'use_j_percentile': True, 'use_amv_gate': True},
}

# ---- 敏感性：以 T2full 为基准，单参数扰动（其余保持基准值） ----
# ramp_j_threshold 只在 j20 模式下生效，该组扰动时基准切换为 j20 模式
SENS_BASE = dict(MATRIX_CONFIGS['T2full'])
SENS_GRID = [
    ('trend_ma_weeks',   [80, 100, 120], {}),
    ('trend_decline_lb', [3, 4, 5], {}),
    ('j_pct_lookback',   [125, 156, 187], {}),
    ('j_buy_pct',        [0.08, 0.10, 0.12], {}),
    ('j_sell_pct',       [0.93, 0.95, 0.97], {}),
    ('ramp_j_threshold', [16, 20, 24], {'ramp_mode': 'j20'}),
]


def load_etf_list(config_path='etf_config.json'):
    with open(config_path, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    return [e for e in cfg['etf_list'] if e.get('enabled', True)]


# ---------------------------------------------------------------- data cache
def stage_cache(config_path='etf_config.json'):
    os.makedirs(CACHE_DIR, exist_ok=True)
    etfs = load_etf_list(config_path)
    end = datetime.now()
    start = end - timedelta(days=FETCH_YEARS * 365)
    for etf in etfs:
        path = os.path.join(CACHE_DIR, f"{etf['ts_code'].replace('.', '_')}_daily.csv")
        if os.path.exists(path):
            print(f"[cache] 已存在，跳过: {path}")
            continue
        print(f"[cache] 拉取 {etf['name']} ({etf['ts_code']}) {start:%Y%m%d}~{end:%Y%m%d} ...")
        df = _get_etf_data(etf['ts_code'], start, end)
        df.to_csv(path, index=False, encoding='utf-8')
        print(f"[cache] {etf['name']}: {len(df)} 条, "
              f"{df['trade_date'].iloc[0]}~{df['trade_date'].iloc[-1]}")


def load_cached(ts_code: str) -> pd.DataFrame:
    path = os.path.join(CACHE_DIR, f"{ts_code.replace('.', '_')}_daily.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(f"缓存不存在: {path}，先运行 cache 阶段")
    df = pd.read_csv(path, dtype={'trade_date': str})
    return df


# ---------------------------------------------------------------- metrics
def _curve_metrics(nav: pd.DataFrame) -> dict:
    """对净值曲线（列为 trade_date, nav）计算年化/最大回撤/Calmar。"""
    if nav is None or len(nav) < 2:
        return {'ann_ret': None, 'max_dd': None, 'calmar': None, 'end_nav': None, 'n_days': 0}
    vals = nav['nav'].to_numpy(dtype=float)
    n_days = len(vals)
    end_nav = float(vals[-1])
    ann_ret = end_nav ** (252.0 / n_days) - 1.0 if end_nav > 0 else -1.0
    run_max = np.maximum.accumulate(vals)
    dd = vals / run_max - 1.0
    max_dd = float(dd.min())
    calmar = (ann_ret / abs(max_dd)) if max_dd < -1e-9 else None
    return {'ann_ret': round(ann_ret, 4), 'max_dd': round(max_dd, 4),
            'calmar': None if calmar is None else round(calmar, 3),
            'end_nav': round(end_nav, 4), 'n_days': n_days}


def _sound_nav(strategy, daily_df: pd.DataFrame) -> pd.DataFrame:
    """自算净值曲线：AUM = 市值 + 累计卖出金额（卖出现金闲置不计息），
    nav = AUM / 累计投入。

    注意：不使用 calc_nav_curve——其公式为（市值+已实现利润）/累计投入，
    漏加卖出返还的本金，全清后 nav=利润/投入 而非 1+收益率，做回撤/Calmar
    排序会系统性失真（曾出现 maxDD<-100% 的越界值）。本函数与其交易重放逻辑
    相同，仅把 cum_sell_profit 换成 cum_sell_amount。"""
    trade_map = {}
    for t in strategy.trades:
        d = t['date']
        if d not in trade_map:
            trade_map[d] = {'buy_amount': 0, 'sell_amount': 0}
        if t['action'] == 'BUY':
            trade_map[d]['buy_amount'] += t.get('amount', 0)
        else:
            trade_map[d]['sell_amount'] += t.get('amount', 0)
    rows = []
    cum_invested = 0.0
    cum_sell = 0.0
    shares = 0.0
    avg_cost = 0.0
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
                cum_sell += tm['sell_amount']
                if shares > 0:
                    avg_cost = (shares * avg_cost - cost_removed) / shares
                else:
                    avg_cost = 0
        aum = shares * price + cum_sell
        nav = aum / cum_invested if cum_invested > 0 else 1.0
        rows.append({'trade_date': d, 'nav': nav})
    return pd.DataFrame(rows)


def variant_metrics(strategy, daily_df: pd.DataFrame, backtest_start: str) -> dict:
    """单变体指标：全窗口 + IS/OOS 切分。"""
    nav_all = _sound_nav(strategy, daily_df)
    nav = nav_all[nav_all['trade_date'] >= backtest_start].reset_index(drop=True)
    m = _curve_metrics(nav)
    market_value = strategy.shares * daily_df['close_qfq'].iloc[-1]
    total_profit = market_value + strategy.total_sell_amount - strategy.total_invested
    buys = [t for t in strategy.trades if t['action'] == 'BUY']
    sells = [t for t in strategy.trades if t['action'] == 'SELL']
    out = {
        'ann_ret': m['ann_ret'], 'max_dd': m['max_dd'], 'calmar': m['calmar'],
        'end_nav': m['end_nav'], 'n_days': m['n_days'],
        'total_invested': round(strategy.total_invested, 2),
        'total_profit': round(total_profit, 2),
        'buy_count': len(buys), 'sell_count': len(sells),
        'rounds_opened': len({t['round'] for t in strategy.trades}),
    }
    # IS/OOS 切分（各变体数据窗口一致，对比公平）
    split_idx = int(len(nav) * IS_RATIO)
    if 30 < split_idx < len(nav) - 5:
        is_nav = nav.iloc[:split_idx + 1].copy()
        oos_nav = nav.iloc[split_idx:].copy().reset_index(drop=True)
        oos_nav['nav'] = oos_nav['nav'] / oos_nav['nav'].iloc[0] if oos_nav['nav'].iloc[0] > 0 else oos_nav['nav']
        out['is'] = _curve_metrics(is_nav)
        out['oos'] = _curve_metrics(oos_nav)
        out['oos_start'] = str(nav.iloc[split_idx]['trade_date'])
    else:
        out['is'] = out['oos'] = None
        out['oos_start'] = None
    return out


# ---------------------------------------------------------------- backtest
def run_variant(cfg_id: str, params, name: str, ts_code: str,
                daily_warmup: pd.DataFrame, weekly: pd.DataFrame,
                effective_start: str, amv_gate_df=None):
    """跑单个配置，返回 (strategy, metrics)。V6 基线用 V6 类，其余用 V7。"""
    state_file = os.path.join(STATE_DIR, f"{cfg_id}_{ts_code.replace('.', '_')}.json")
    if cfg_id == 'V6':
        s = WeeklyDCAStrategyV6(name=f"{name}-{cfg_id}", base_amount=1000.0,
                                state_file=state_file)
    else:
        s = WeeklyDCAStrategyV7(name=f"{name}-{cfg_id}", base_amount=1000.0,
                                amv_gate_df=amv_gate_df, state_file=state_file,
                                **params)
    s.backtest(daily_warmup, weekly, backtest_start=effective_start)
    m = variant_metrics(s, daily_warmup, effective_start)
    return s, m


def stage_matrix(config_path='etf_config.json', etf_names=None):
    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(os.path.join(OUT_DIR, 'matrix'), exist_ok=True)
    from amv_gate import load_amv_gate
    amv_gate_df = load_amv_gate()
    etfs = load_etf_list(config_path)
    if etf_names:
        etfs = [e for e in etfs if e['name'] in etf_names]
    backtest_start = (datetime.now() - timedelta(days=YEARS * 365)).strftime('%Y%m%d')

    for etf in etfs:
        name, ts_code = etf['name'], etf['ts_code']
        out_path = os.path.join(OUT_DIR, 'matrix', f"{name}.json")
        if os.path.exists(out_path):
            print(f"[matrix] 已存在，跳过: {out_path}")
            continue
        try:
            daily_warmup = load_cached(ts_code)
        except FileNotFoundError as e:
            print(f"[matrix] {name}: {e}")
            continue
        weekly = resample_to_weekly(daily_warmup)
        # 与生产 run_backtest_from_config 相同的预热逻辑
        effective_start = backtest_start
        if len(weekly) > WARMUP_WEEKS:
            warmup_end = str(weekly.iloc[WARMUP_WEEKS]['trade_date'])
            if warmup_end > backtest_start:
                effective_start = warmup_end

        result = {'etf': name, 'ts_code': ts_code,
                  'backtest_start': effective_start,
                  'data_start': str(daily_warmup['trade_date'].iloc[0]),
                  'data_end': str(daily_warmup['trade_date'].iloc[-1]),
                  'weekly_bars': len(weekly),
                  'variants': {}}

        # V6 基线
        s_v6, m = run_variant('V6', None, name, ts_code, daily_warmup, weekly,
                              effective_start, amv_gate_df)
        result['variants']['V6'] = m

        # V7 全关等价性检查：交易序列必须与 V6 逐笔一致
        s_off, m_off = run_variant('V7OFF', MATRIX_CONFIGS['V7OFF'], name, ts_code,
                                   daily_warmup, weekly, effective_start, amv_gate_df)
        result['variants']['V7OFF'] = m_off

        # 其余配置
        for cfg_id, params in MATRIX_CONFIGS.items():
            if cfg_id == 'V7OFF':
                continue
            _, m = run_variant(cfg_id, params, name, ts_code, daily_warmup, weekly,
                               effective_start, amv_gate_df)
            result['variants'][cfg_id] = m

        # 等价性断言：V7OFF vs V6 交易序列逐笔对比
        t_v6 = [(t['date'], t['action'], t['round'], round(t['amount'], 2), t['price'])
                for t in s_v6.trades]
        t_off = [(t['date'], t['action'], t['round'], round(t['amount'], 2), t['price'])
                 for t in s_off.trades]
        result['equivalence_v6_v7off'] = (t_v6 == t_off)
        if not result['equivalence_v6_v7off']:
            diff = [(a, b) for a, b in zip(t_v6, t_off) if a != b][:5]
            result['equivalence_diff_sample'] = [list(map(str, d)) for d in diff]
            logging.error(f"[matrix] {name}: V7OFF 与 V6 不等价! {diff}")

        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[matrix] {name}: 完成 -> {out_path} "
              f"(等价性={result['equivalence_v6_v7off']})")


def stage_sensitivity(config_path='etf_config.json', etf_names=None):
    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(os.path.join(OUT_DIR, 'sensitivity'), exist_ok=True)
    from amv_gate import load_amv_gate
    amv_gate_df = load_amv_gate()
    etfs = load_etf_list(config_path)
    if etf_names:
        etfs = [e for e in etfs if e['name'] in etf_names]
    backtest_start = (datetime.now() - timedelta(days=YEARS * 365)).strftime('%Y%m%d')

    for etf in etfs:
        name, ts_code = etf['name'], etf['ts_code']
        out_path = os.path.join(OUT_DIR, 'sensitivity', f"{name}.json")
        if os.path.exists(out_path):
            print(f"[sens] 已存在，跳过: {out_path}")
            continue
        try:
            daily_warmup = load_cached(ts_code)
        except FileNotFoundError as e:
            print(f"[sens] {name}: {e}")
            continue
        weekly = resample_to_weekly(daily_warmup)
        effective_start = backtest_start
        if len(weekly) > WARMUP_WEEKS:
            warmup_end = str(weekly.iloc[WARMUP_WEEKS]['trade_date'])
            if warmup_end > backtest_start:
                effective_start = warmup_end

        variants = {}
        # 基准（T2full）本身
        _, m = run_variant('SENS_BASE', SENS_BASE, name, ts_code, daily_warmup, weekly,
                           effective_start, amv_gate_df)
        variants['BASE'] = m
        for param, values, override in SENS_GRID:
            base_params = dict(SENS_BASE)
            base_params.update(override or {})
            # 该组扰动需要非默认模式时，先补跑该模式下的基准
            if override:
                oid = 'SENS_BASE_' + '_'.join(f"{k}{v}" for k, v in override.items())
                if oid not in variants:
                    _, m = run_variant(oid, base_params, name, ts_code, daily_warmup,
                                       weekly, effective_start, amv_gate_df)
                    variants[oid] = m
            for v in values:
                if v == base_params.get(param):
                    continue  # 与该组基准重合，跳过
                params = dict(base_params)
                params[param] = v
                cfg_id = f"SENS_{param}_{v}"
                _, m = run_variant(cfg_id, params, name, ts_code, daily_warmup, weekly,
                                   effective_start, amv_gate_df)
                variants[cfg_id] = m

        result = {'etf': name, 'ts_code': ts_code, 'base': 'T2full',
                  'backtest_start': effective_start, 'variants': variants}
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[sens] {name}: {len(variants)} 个变体 -> {out_path}")


def stage_aggregate():
    """合并结果 + 预注册采纳标准判定。"""
    mdir = os.path.join(OUT_DIR, 'matrix')
    if not os.path.isdir(mdir):
        print('无 matrix 结果')
        return
    rows = []
    for fn in sorted(os.listdir(mdir)):
        if not fn.endswith('.json'):
            continue
        with open(os.path.join(mdir, fn), 'r', encoding='utf-8') as f:
            r = json.load(f)
        v6 = r['variants']['V6']
        for cfg_id, m in r['variants'].items():
            if m is None:
                continue
            row = {'etf': r['etf'], 'cfg': cfg_id,
                   'equivalence_ok': r.get('equivalence_v6_v7off')}
            for seg in ['', 'is_', 'oos_']:
                src = m if not seg else m.get(seg.strip('_'))
                if src:
                    row[f'{seg}ann_ret'] = src.get('ann_ret')
                    row[f'{seg}max_dd'] = src.get('max_dd')
                    row[f'{seg}calmar'] = src.get('calmar')
            row['total_invested'] = m.get('total_invested')
            row['total_profit'] = m.get('total_profit')
            row['buy_count'] = m.get('buy_count')
            row['oos_start'] = m.get('oos_start')
            # 预注册采纳标准（相对 V6 的 OOS 指标）
            if cfg_id != 'V6' and v6.get('oos') and m.get('oos'):
                v6_cal, m_cal = v6['oos'].get('calmar'), m['oos'].get('calmar')
                v6_dd, m_dd = v6['oos'].get('max_dd'), m['oos'].get('max_dd')
                v6_ann, m_ann = v6['oos'].get('ann_ret'), m['oos'].get('ann_ret')
                ci = dn = al = None
                if v6_cal is not None and m_cal is not None and v6_cal > 0:
                    ci = round(m_cal / v6_cal - 1, 3)
                if v6_dd is not None and m_dd is not None and v6_dd < 0:
                    # 回撤收窄为正：候选回撤比 V6 更浅（更接近0）才算收窄
                    dn = round((m_dd - v6_dd) / abs(v6_dd), 3)
                if v6_ann is not None and m_ann is not None:
                    al = round((v6_ann - m_ann) * 100, 2)
                row['oos_calmar_impr'] = ci
                row['oos_dd_narrow'] = dn
                row['oos_ann_loss_pp'] = al
                c1 = ci is not None and ci >= 0.15
                c2 = (dn is not None and dn >= 0.10
                      and al is not None and al <= 2.0)
                row['adopt_rule1'] = bool(c1)
                row['adopt_rule2'] = bool(c2)
                row['adopt'] = bool(c1 or c2)
            rows.append(row)
    out = os.path.join(OUT_DIR, 'aggregate.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"[aggregate] {len(rows)} 行 -> {out}")

    # 控制台摘要
    etfs = sorted({r['etf'] for r in rows})
    print(f"\n{'ETF':<10}{'配置':<10}{'全期Calmar':>10}{'OOS Calmar':>10}{'OOS年化':>9}"
          f"{'OOS回撤':>9}{'采纳':>5}")
    for etf in etfs:
        for r in [x for x in rows if x['etf'] == etf]:
            print(f"{etf:<10}{r['cfg']:<10}"
                  f"{str(r.get('calmar')):>10}{str(r.get('oos_calmar')):>10}"
                  f"{str(r.get('oos_ann_ret')):>9}{str(r.get('oos_max_dd')):>9}"
                  f"{'√' if r.get('adopt') else '':>5}")


def main():
    p = argparse.ArgumentParser(description='V7 验证运行器')
    p.add_argument('stage', choices=['cache', 'matrix', 'sensitivity', 'aggregate'])
    p.add_argument('--etfs', type=str, default=None, help='逗号分隔的ETF名称子集')
    p.add_argument('--config', type=str, default='etf_config.json')
    args = p.parse_args()
    names = args.etfs.split(',') if args.etfs else None
    if args.stage == 'cache':
        stage_cache(args.config)
    elif args.stage == 'matrix':
        stage_matrix(args.config, names)
    elif args.stage == 'sensitivity':
        stage_sensitivity(args.config, names)
    elif args.stage == 'aggregate':
        stage_aggregate()


if __name__ == '__main__':
    main()
