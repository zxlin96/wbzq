#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
4%定投法策略模块（雷牛牛4%定投 + 估值门，经回测验证的参数）

策略纪律（来源：backtest_4pct_dca.py / backtest_4pct_valuation.py 回测结论）：
  1. 每轮预算平分 n_parts 份（默认10，创业板指按回测用5），第1份建仓
  2. 以上次买入价为台阶锚点，每再跌4%补1份（只买跌，不买涨）
  3. 涨超平均成本15%进入止盈观察期（停止补仓），自观察期最高价回撤4%一次性全卖
  4. 估值门：指数PE_TTM五年滚动分位 <50% 才允许开启新一轮（防"高锚点开局"，
     回测平均最优；恒生指数无PE数据不设门，需人工关注估值）
  5. 止盈后资金滚动进入下一轮

与回测引擎的关系：本模块不做第二次实现，直接复用 backtest_4pct_dca.py 的
确定性状态机（run_fourpct），每日用"从策略起始日到今天的全量重放"推进状态——
策略是确定性的，重放即状态推进，不存在状态漂移。

标的池（验证结论）：沪深300 / 上证50 / 中证1000（均为10份，PE分位门控）

用法：
  python dca_4pct_strategy.py                # 推进所有标的，生成报告与邮件摘要
  python dca_4pct_strategy.py --refresh      # 强制重拉行情
  python dca_4pct_strategy.py --start 20261012   # 首次运行指定起始日（默认当天）
  python dca_4pct_strategy.py --budget 20000

输出：
  - dca_state_4pct_{name}.json          每标的状态（重放结果持久化）
  - html/dca_4pct/strategy.html         策略报告（估值分位+阶段+下一台阶）
  - html/dca_4pct/strategy_summary.json 邮件摘要（generate_email_report.py 读取）
"""

from static_site import write_report

import os
import json
import logging
import argparse
from datetime import datetime, timedelta

import pandas as pd

from backtest_4pct_dca import load_etf_daily, run_fourpct
from backtest_4pct_valuation import load_index_pe, pe_percentile, PE_PCT_WINDOW

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s | %(message)s')
logger = logging.getLogger(__name__)

REPORT_DIR = os.path.join('html', 'dca_4pct')
STATE_PREFIX = 'dca_state_4pct'

# 策略标的池：A股宽基，全部带估值门（PE五年分位<50%开轮）
# （恒生指数、创业板指已按需求移出：前者无PE数据无法设门，后者回撤超出台阶覆盖）
POOL_4PCT = [
    {'ts_code': '510300.SH', 'name': '沪深300', 'index_code': '000300.SH', 'n_parts': 10},
    {'ts_code': '510050.SH', 'name': '上证50', 'index_code': '000016.SH', 'n_parts': 10},
    {'ts_code': '512100.SH', 'name': '中证1000', 'index_code': '000852.SH', 'n_parts': 10},
]

GATE_THRESHOLD = 0.50   # PE五年滚动分位低于该值才允许开新一轮
DROP_PCT = 4.0
DETACH_PCT = 15.0
EXIT_DROP_PCT = 4.0


# ============================ 状态持久化 ============================

def state_path(name: str) -> str:
    return f'{STATE_PREFIX}_{name}.json'


def load_state(name: str):
    p = state_path(name)
    if os.path.exists(p):
        with open(p, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None


def save_state(name: str, state: dict):
    with open(state_path(name), 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=1, default=str)


# ============================ 单标的状态推进 ============================

def advance(etf: dict, start_date: str, end_date: str, budget: float,
            refresh: bool = False) -> dict:
    """重放 [start_date, end_date] 得到当前状态与最新估值分位"""
    start_ts = pd.to_datetime(start_date)
    end_ts = pd.to_datetime(end_date)
    # 一次取数：回放窗口(>=start_date) + 图表窗口(近五年≈1220交易日) 都从中切片
    wide_start = min((end_ts - pd.Timedelta(days=1900)).strftime('%Y%m%d'),
                     (start_ts - timedelta(days=30)).strftime('%Y%m%d'))
    df_all = load_etf_daily(etf['ts_code'], wide_start, end_date, refresh=refresh)
    df = df_all[df_all['trade_date'] >= start_date].reset_index(drop=True)
    if df.empty:
        # 起始日尚无收盘数据（假日/盘中首跑）：以最近一根K线作为开轮基准
        df = df_all.tail(1).reset_index(drop=True)
    if len(df) < 1:
        raise ValueError(f"{etf['name']} 无行情数据")

    # 估值分位序列与门
    pe_df = pe_pct_now = pe_now = pe_date = None
    gate = None
    if etf['index_code']:
        pe_raw = load_index_pe(etf['index_code'], refresh=refresh)
        pe_df = pe_percentile(pe_raw)
        m = df[['trade_date']].merge(pe_df, on='trade_date', how='left')
        gate = (m['pe_pct'] < GATE_THRESHOLD).fillna(True).values
        last_valid = m.dropna(subset=['pe_pct'])
        if len(last_valid):
            pe_pct_now = float(last_valid['pe_pct'].iloc[-1]) * 100
            pe_now = float(last_valid['pe_ttm'].iloc[-1])
            pe_date = last_valid['trade_date'].iloc[-1]
        else:
            pe_pct_now = pe_now = pe_date = None

    r = run_fourpct(df, gate=gate,
                    initial_capital=budget, round_budget=budget,
                    n_parts=etf['n_parts'], drop_pct=DROP_PCT,
                    detach_pct=DETACH_PCT, exit_drop_pct=EXIT_DROP_PCT)

    st = r['state']
    last_px = float(df['close_qfq'].iloc[-1])
    last_date = df['trade_date'].iloc[-1]

    # ---- 阶段与今日动作 ----
    buys = [t for t in r['trades'] if t['action'] == 'BUY']
    sells = [t for t in r['trades'] if t['action'] == 'SELL']
    last_trade = r['trades'][-1] if r['trades'] else None
    today_action = last_trade if (last_trade and last_trade['date'] == last_date) else None

    parts_bought = etf['n_parts'] - st['parts_left'] if st['in_round'] else 0

    if not st['in_round']:
        if pe_pct_now is not None and pe_pct_now >= GATE_THRESHOLD * 100:
            phase = 'WAIT_GATE'
            action_label = '等待估值门'
            action_color = '#95a5a6'
        else:
            phase = 'READY_START'
            action_label = '门开·待建仓'
            action_color = '#e67e22'
        detail = f"空仓，钱包{st['wallet']:.0f}元"
    else:
        if st['phase'] == 'WATCH':
            phase = 'WATCH'
            trig = st['watch_max'] * (1 - EXIT_DROP_PCT / 100)
            action_label = f'止盈观察期(已买{parts_bought}份)'
            action_color = '#e74c3c'
        else:
            phase = 'ACCUM' if st['parts_left'] > 0 else 'FULL'
            trig = st['last_low'] * (1 - DROP_PCT / 100)
            action_label = f"持有第{parts_bought}/{etf['n_parts']}份"
            action_color = '#2980b9'

    # 下一触发位（补仓台阶 或 止盈触发价）
    if st['in_round']:
        next_trigger = round(float(trig), 4)
        next_trigger_pct = round((next_trigger / last_px - 1) * 100, 2)
        if phase == 'WATCH':
            next_desc = f'回撤{EXIT_DROP_PCT}%触发止盈@{next_trigger}'
        else:
            next_desc = (f"再跌{DROP_PCT}%补1份@{next_trigger}"
                         if st['parts_left'] > 0 else '份额已投满')
    else:
        next_trigger = next_trigger_pct = None
        next_desc = '门开后投第1份' if phase == 'WAIT_GATE' else '下一交易日收盘可投第1份'

    if st['in_round'] and st['avg_cost']:
        vs_cost_pct = round((last_px / st['avg_cost'] - 1) * 100, 2)
    else:
        vs_cost_pct = None

    holding_value = st['shares'] * last_px
    total_assets = st['wallet'] + holding_value

    # ---- 图表数据：近五年价格 × PE分位（对齐到同一组日期）+ 策略买卖点 ----
    chart_df = df_all.tail(1220)
    chart = chart_df[['trade_date', 'close_qfq']].copy()
    chart.columns = ['date', 'close']
    if pe_df is not None:
        chart = chart.merge(pe_df[['trade_date', 'pe_pct']], left_on='date',
                            right_on='trade_date', how='left')
        chart['pe_pct'] = (chart['pe_pct'] * 100).round(1)
    else:
        chart['pe_pct'] = None
    chart['close'] = chart['close'].round(4)
    chart_win_start = chart['date'].iloc[0]
    chart_record = {
        'dates': chart['date'].tolist(),
        'close': chart['close'].tolist(),
        'pe_pct': [None if pd.isna(v) else float(v) for v in chart['pe_pct']],
        'buys': [[t['date'], t['price']] for t in r['trades']
                 if t['action'] == 'BUY' and t['date'] >= chart_win_start],
        'sells': [[t['date'], t['price']] for t in r['trades']
                  if t['action'] == 'SELL' and t['date'] >= chart_win_start],
    }

    return {
        'name': etf['name'],
        'ts_code': etf['ts_code'],
        'last_date': last_date,
        'last_price': round(last_px, 4),
        'phase': phase,
        'action_label': action_label,
        'action_color': action_color,
        'today_action': ({'date': today_action['date'], 'action': today_action['action'],
                          'amount': today_action['amount'],
                          'reason': today_action['reason']} if today_action else None),
        'parts_bought': parts_bought,
        'n_parts': etf['n_parts'],
        'part_amount': round(budget / etf['n_parts'], 0),
        'avg_cost': st['avg_cost'],
        'vs_cost_pct': vs_cost_pct,
        'next_trigger': next_trigger,
        'next_trigger_pct': next_trigger_pct,
        'next_desc': next_desc,
        'pe_ttm': pe_now,
        'pe_pct': pe_pct_now,
        'pe_date': pe_date if pe_pct_now is not None else None,
        'gate_enabled': etf['index_code'] is not None,
        'gate_open': bool(gate[-1]) if gate is not None else None,
        'wallet': st['wallet'],
        'holding_value': round(holding_value, 2),
        'total_assets': round(total_assets, 2),
        'total_profit': round(total_assets - budget, 2),
        'n_trades': r['n_trades'],
        'n_rounds': r['n_rounds'],
        'trades': r['trades'][-12:],
        'rounds_detail': r['rounds_detail'][-6:],
        'state': st,
        'chart': chart_record,
    }


# ============================ 主流程 ============================

def main():
    ap = argparse.ArgumentParser(description='4%定投法策略（估值门版）每日推进')
    ap.add_argument('--start', default=None, help='策略起始日 YYYYMMDD（默认：首次运行取今天）')
    ap.add_argument('--budget', type=float, default=20000.0, help='每轮预算')
    ap.add_argument('--refresh', action='store_true', help='强制重拉行情/PE')
    ap.add_argument('--skip-html', action='store_true')
    args = ap.parse_args()

    results = []
    for etf in POOL_4PCT:
        name = etf['name']
        st = load_state(name)
        start_date = args.start or (st or {}).get('start_date') \
            or datetime.now().strftime('%Y%m%d')
        try:
            r = advance(etf, start_date, datetime.now().strftime('%Y%m%d'),
                        args.budget, refresh=args.refresh)
        except Exception as e:
            logger.error(f'{name} 推进失败: {e}')
            continue
        r['start_date'] = start_date
        r['budget'] = args.budget
        save_state(name, r)
        results.append(r)
        ta = r['today_action']
        ta_str = f" | 今日{ta['action']} {ta['amount']:.0f}元" if ta else ''
        pe_str = f"PE分位{r['pe_pct']:.0f}%" if r['pe_pct'] is not None else '无PE门'
        logger.info(f"{name}: {r['action_label']} | {pe_str} | {r['next_desc']}{ta_str}")

    os.makedirs(REPORT_DIR, exist_ok=True)

    # ---- 邮件摘要 ----
    summary = {
        'last_update': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'gate_threshold': GATE_THRESHOLD * 100,
        'budget': args.budget,
        'pool': [{k: r[k] for k in ('name', 'ts_code', 'last_date', 'last_price',
                                    'action_label', 'action_color', 'today_action',
                                    'parts_bought', 'n_parts', 'avg_cost', 'vs_cost_pct',
                                    'next_desc', 'pe_ttm', 'pe_pct', 'gate_enabled',
                                    'wallet', 'holding_value', 'total_profit',
                                    'phase', 'start_date')}
                 for r in results],
    }
    with open(os.path.join(REPORT_DIR, 'strategy_summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=1, default=str)

    if not args.skip_html:
        html_path = os.path.join(REPORT_DIR, 'strategy.html')
        generate_strategy_html(results, summary, html_path, args)
        logger.info(f'策略报告已生成 {html_path}')


# ============================ HTML 报告 ============================

def _build_overlay_option(r):
    """单只ETF的叠加图配置：左轴收盘价 + 右轴PE分位 + 门限线 + 策略买卖点"""
    c = r['chart']
    has_pe = any(v is not None for v in c['pe_pct'])
    series = [
        {'name': '收盘价', 'type': 'line', 'data': c['close'],
         'showSymbol': False, 'lineStyle': {'color': '#e67e22', 'width': 1.6}},
    ]
    if has_pe:
        series.append({
            'name': 'PE分位%', 'type': 'line', 'yAxisIndex': 1, 'data': c['pe_pct'],
            'showSymbol': False, 'lineStyle': {'color': '#8e44ad', 'width': 1.2},
            'markLine': {'symbol': 'none', 'data': [{'yAxis': GATE_THRESHOLD * 100}],
                         'lineStyle': {'color': '#27ae60', 'type': 'dashed'},
                         'label': {'formatter': f'门限{GATE_THRESHOLD * 100:.0f}%'}},
        })
    if c['buys']:
        series.append({'name': '买入', 'type': 'scatter', 'data': c['buys'],
                       'symbolSize': 9, 'itemStyle': {'color': '#c0392b'}})
    if c['sells']:
        series.append({'name': '卖出', 'type': 'scatter', 'data': c['sells'],
                       'symbolSize': 9, 'itemStyle': {'color': '#27ae60'}})
    title_txt = f"{r['name']} 收盘价 × PE五年分位（近五年）"
    if not has_pe:
        title_txt += '｜无PE数据'
    y_axis = [{'type': 'value', 'name': '价格', 'scale': True}]
    if has_pe:
        y_axis.append({'type': 'value', 'name': 'PE分位%', 'max': 100, 'position': 'right',
                       'splitLine': {'show': False}})
    legend = ['收盘价'] + (['PE分位%'] if has_pe else []) + \
        (['买入'] if c['buys'] else []) + (['卖出'] if c['sells'] else [])
    return {
        'title': {'text': title_txt, 'left': 'center', 'textStyle': {'fontSize': 13}},
        'tooltip': {'trigger': 'axis'},
        'legend': {'data': legend, 'top': 26},
        'toolbox': {'right': 10, 'top': 4,
                    'feature': {'saveAsImage': {'title': '保存图片'}}},
        'grid': {'left': 60, 'right': 60, 'top': 60, 'bottom': 78},
        'xAxis': {'type': 'category', 'data': c['dates']},
        'yAxis': y_axis,
        'dataZoom': [
            {'type': 'inside', 'start': 0, 'end': 100},
            {'type': 'slider', 'height': 20, 'bottom': 10},
        ],
        'series': series,
    }


def generate_strategy_html(results, summary, out_path, args):
    n_parts_txt = '、'.join(str(r['n_parts']) for r in results)

    rows = ''
    for r in results:
        pe_txt = f"{r['pe_ttm']:.1f}（{r['pe_pct']:.0f}%分位）" if r['pe_pct'] is not None \
            else '无PE数据'
        gate_txt = ('<span style="color:#27ae60">开</span>'
                    if r['gate_open'] else '<span style="color:#e74c3c">关</span>') \
            if r['gate_enabled'] else '—'
        vs_cost = f"{r['vs_cost_pct']:+.1f}%" if r['vs_cost_pct'] is not None else '—'
        vc = '#27ae60' if (r['vs_cost_pct'] or 0) >= 0 else '#e74c3c'
        today = '—'
        if r['today_action']:
            t = r['today_action']
            today = f"<b style='color:{r['action_color']}'>{t['action']} {t['amount']:.0f}元</b>"
        rows += f"""<tr>
<td class="etf"><b>{r['name']}</b><br><small>{r['ts_code']}<br>{r['start_date']}起</small></td>
<td style="color:{r['action_color']};font-weight:bold">{r['action_label']}</td>
<td>{today}</td>
<td>{r['parts_bought']}/{r['n_parts']}份<br><small>{r['part_amount']:.0f}元/份</small></td>
<td>{r['avg_cost'] if r['avg_cost'] else '—'}<br>
    <small style="color:{vc}">现价{vs_cost}</small></td>
<td>{r['next_desc']}</td>
<td>{pe_txt}<br><small>门: {gate_txt}</small></td>
<td>{r['total_assets']:,.0f}<br><small class="{"pos" if r["total_profit"] >= 0 else "neg"}">{r['total_profit']:+,.0f}元</small></td>
</tr>"""

    pe_opts = json.dumps([
        _build_overlay_option(r)
        for r in results
    ], ensure_ascii=False)
    pe_names = [r['name'] for r in results]

    trades_html = ''
    for r in results:
        if not r['trades']:
            continue
        trs = ''.join(
            f"<tr><td>{t['date']}</td><td style='color:{'#c0392b' if t['action'] == 'BUY' else '#27ae60'}'>"
            f"{t['action']}</td><td>{t['price']}</td><td>{t['amount']:,.0f}</td><td style='text-align:left'>{t['reason']}</td></tr>"
            for t in reversed(r['trades']))
        trades_html += (f"<h3>{r['name']}（{r['ts_code']}）最近交易</h3>"
                        f"<table><tr><th>日期</th><th>方向</th><th>价格</th><th>金额</th><th>依据</th></tr>{trs}</table>")

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>4%定投法策略报告（估值门版）</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>
body {{ font-family: "Microsoft YaHei", sans-serif; margin: 24px auto; max-width: 1250px;
       background: #f7f8fa; color: #2c3e50; }}
h1 {{ font-size: 22px; }} h2 {{ font-size: 18px; margin-top: 32px;
     border-left: 4px solid #e74c3c; padding-left: 10px; }}
h3 {{ font-size: 15px; margin: 20px 0 6px; }}
table {{ border-collapse: collapse; width: 100%; background: #fff; font-size: 13px; }}
th, td {{ border: 1px solid #e0e4e8; padding: 6px 10px; text-align: center; }}
th {{ background: #34495e; color: #fff; font-weight: normal; }}
td.etf {{ font-weight: bold; background: #fbfbfc; }}
td.pos {{ color: #c0392b; }} td.neg {{ color: #27ae60; }}
.desc {{ background: #fff; border: 1px solid #e0e4e8; padding: 14px 18px;
         font-size: 13px; line-height: 1.9; }}
.chart {{ width: 100%; height: 340px; }}
.card {{ background: #fff; border: 1px solid #e0e4e8; margin-top: 16px; padding: 8px; }}
small {{ color: #7f8c8d; }}
</style>
</head>
<body>
<h1>4%定投法策略报告（估值门版）</h1>
<div class="desc">
<b>纪律</b>：每轮预算平分{n_parts_txt}份建仓 → 每较上次买入价再跌{DROP_PCT:.0f}%补1份（只买跌）→
涨超成本{DETACH_PCT:.0f}%进入止盈观察期 → 自高点回撤{EXIT_DROP_PCT:.0f}%一次性止盈 → 滚动开新轮。<br>
<b>估值门</b>：指数PE_TTM {PE_PCT_WINDOW}日分位 < {GATE_THRESHOLD * 100:.0f}% 才允许开新一轮（回测验证的平均最优纪律，
池内全部标的均带门控）。<br>
<b>参数</b>：每轮预算 {args.budget:.0f}元 ｜ 数据截至 {summary['pool'][0]['last_date'] if summary['pool'] else '—'}
｜ 最近更新 {summary['last_update']} ｜ <small>数据推演，非投资建议</small>
</div>

<h2>一、策略状态总览</h2>
<table>
<tr><th>ETF</th><th>阶段</th><th>今日动作</th><th>份额</th><th>持仓成本</th>
<th>下一触发</th><th>估值分位</th><th>总资产</th></tr>
{rows}
</table>

<h2>二、价格 × 估值分位叠加（近五年，含策略买卖点，滚轮缩放/底部滑块）</h2>
<div id="pe_charts"></div>

<h2>三、最近交易</h2>
{trades_html}

<script>
var peOpts = {pe_opts};
var peNames = {json.dumps(pe_names, ensure_ascii=False)};
var holder = document.getElementById('pe_charts');
peOpts.forEach(function(opt, i) {{
    var div = document.createElement('div');
    div.className = 'card';
    div.innerHTML = '<div class="chart" id="pe_' + i + '"></div>';
    holder.appendChild(div);
    echarts.init(div.firstChild).setOption(opt);
}});
window.addEventListener('resize', function() {{
    document.querySelectorAll('.chart').forEach(function(el) {{
        var inst = echarts.getInstanceByDom(el);
        if (inst) inst.resize();
    }});
}});
</script>
</body>
</html>"""
    write_report(out_path, html)


if __name__ == '__main__':
    main()
