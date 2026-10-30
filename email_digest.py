"""Read-only email presentation. No strategy execution, state writes or ledger changes."""

from datetime import datetime, timedelta
from html import escape
import json
import math
from pathlib import Path
import re
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Shanghai')


def report_time(value=None):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    stamp = stamp or datetime.now(TZ)
    return stamp.replace(tzinfo=TZ) if stamp.tzinfo is None else stamp.astimezone(TZ)


def date_key(value):
    return str(value or '').replace('-', '')[:8]


def number(value):
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def fmt(value, precision=2):
    v = number(value)
    return f'{v:,.{precision}f}' if v is not None else '—'


def delta(current, previous, unit=''):
    a, b = number(current), number(previous)
    return f'{a-b:+,.2f}{unit}' if a is not None and b is not None else '暂无对比'


def quantile(values, q=.9):
    a = sorted(values)
    i = (len(a)-1)*q
    lo = int(i)
    return a[lo] + (a[min(lo+1, len(a)-1)]-a[lo])*(i-lo)


def market_context(history, strategy_stats=None):
    """Reference threshold uses 60 PRIOR sessions; actual strategy stats are separate."""
    rows = sorted({date_key(r['trade_date']): float(r['count']) for r in history}.items())
    if not rows:
        return {}
    day, count = rows[-1]
    prior = [v for _, v in rows[:-1]]
    reference = quantile(prior[-60:]) if len(prior) >= 60 else None
    previous_reference = quantile(prior[-61:-1]) if len(prior) >= 61 else None
    previous = prior[-1] if prior else None
    stats = strategy_stats or {}
    return dict(data_date=day, j13_count=count, previous_count=previous,
                previous_date=rows[-2][0] if len(rows)>1 else None,
                change=count-previous if previous is not None else None,
                change_pct=(count/previous-1)*100 if previous else None,
                reference_p90=reference, previous_reference_p90=previous_reference,
                reference_window=60, strategy_threshold=number(stats.get('percentile_90')),
                strategy_trigger=bool(stats['is_above_threshold']) if 'is_above_threshold' in stats else None)


def weekly_comparison(completed):
    rows = completed.sort_values('trade_date')
    if rows.empty:
        return {}
    current = number(rows.iloc[-1].get('J'))
    previous = number(rows.iloc[-2].get('J')) if len(rows)>1 else None
    return dict(previous_week_date=str(rows.iloc[-2].trade_date) if len(rows)>1 else None,
                previous_weekly_j=previous,
                weekly_j_change=current-previous if current is not None and previous is not None else None)


def enrich_archived_summary(summary, root=Path('.')):
    """Compatibility for summaries produced before recent_trades/comparison fields."""
    root = Path(root)
    try:
        config = json.loads((root/'etf_config.json').read_text())
        versions = {e['name']: e.get('strategy', 'v6') for e in config['etf_list']}
    except (OSError, ValueError, KeyError):
        versions = {}
    for etf in (summary or {}).get('etf_list', []):
        if 'recent_trades' not in etf:
            version = versions.get(etf.get('name'), 'v6')
            prefix = 'dca_state' if version == 'v1' else f'dca_state_{version}'
            path = root/f"{prefix}_{etf.get('name')}.json"
            try:
                state = json.loads(path.read_text())
                etf['recent_trades'] = state.get('trades', [])
            except (OSError, ValueError):
                etf['recent_trades'] = []
        if 'previous_weekly_j' not in etf:
            try:
                html = (root/'html/dca'/etf['report_file']).read_text()
                dates = json.loads(re.search(r'var weeklyDates = (.*?);', html)[1])
                values = json.loads(re.search(r'var weeklyJ = (.*?);', html)[1])
                done = [(date_key(d), number(v)) for d,v in zip(dates, values)
                        if date_key(d) <= date_key(etf.get('signal_week_date'))]
                etf['previous_weekly_j'] = done[-2][1] if len(done)>1 else None
                etf['previous_week_date'] = done[-2][0] if len(done)>1 else None
            except (OSError, ValueError, KeyError, TypeError, IndexError):
                etf['previous_weekly_j'] = None
    return summary


def load_market_context(root, day):
    root = Path(root)
    try:
        value = json.loads((root/'html/sentiment_context.json').read_text())
        if date_key(value.get('data_date')) == day:
            return value
    except (OSError, ValueError):
        pass
    try:
        trend = (root/f'html/{day}/first_j13_step_daily_count.html').read_text()
        dates = json.loads(re.search(r'xAxis\s*:.*?data\s*:\s*(\[.*?\])', trend, re.S)[1])
        counts = json.loads(re.search(r'series\s*:\s*\[\s*\{.*?data\s*:\s*(\[.*?\])', trend, re.S)[1])
        html = (root/f'html/{day}/sentiment_rebound_strategy.html').read_text()
        threshold = float(re.search(r'90%分位数</div>\s*<div[^>]*>([\d,.]+)', html)[1].replace(',', ''))
        history = [dict(trade_date=d,count=c) for d,c in zip(dates,counts) if date_key(d)<=day]
        stats = dict(percentile_90=threshold, is_above_threshold=history[-1]['count']>=threshold)
        return market_context(history, stats)
    except (OSError, ValueError, TypeError, IndexError):
        return {}


def enrich_valuation_summary(summary, root=Path('.')):
    for etf in (summary or {}).get('pool', []):
        try:
            saved = json.loads((Path(root)/f"dca_state_4pct_{etf['name']}.json").read_text())
            if date_key(saved.get('last_date')) != date_key(etf.get('last_date')):
                continue
            for key in ('pe_date','gate_open','previous_pe_ttm','previous_pe_pct','previous_pe_date','budget_remaining'):
                if key not in etf and key in saved:
                    etf[key] = saved[key]
            if 'budget_remaining' not in etf:
                state = saved.get('state', {})
                if state.get('in_round'):
                    etf['budget_remaining'] = state.get('parts_left',0)*state.get('part_amt',0)
                elif state.get('wallet') is not None:
                    etf['budget_remaining'] = state['wallet']
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return summary


def collect_events(summary, sentiment, valuation, latest_date, as_of=None):
    stamp = report_time(as_of)
    today = stamp.strftime('%Y%m%d')
    start = (stamp-timedelta(days=stamp.weekday())).strftime('%Y%m%d')
    cutoff = min(date_key(latest_date), today)
    events = []
    def add(strategy, etf, trade, scope):
        date = date_key(trade.get('date'))
        if not start <= date <= cutoff or trade.get('action') not in ('BUY','SELL'):
            return
        if date == today and stamp.hour < 15:
            return
        amount, shares, ratio = (number(trade.get(k)) for k in ('amount','shares','ratio'))
        if trade['action']=='BUY' and (amount is None or amount<=0):
            return
        if trade['action']=='SELL' and not any(v is not None and v>0 for v in (amount,shares,ratio)):
            return
        events.append(dict(strategy=strategy,name=etf.get('name',''),ts_code=etf.get('ts_code',''),
                           date=date,action=trade['action'],amount=amount,shares=shares,ratio=ratio,
                           round=trade.get('round',trade.get('level')),reason=trade.get('reason',''),scope=scope))
    if stamp.weekday() in (4,6):
        for etf in (summary or {}).get('etf_list',[]):
            for trade in etf.get('recent_trades',[]):
                add('周线定投', etf, trade, 'week')
    state = sentiment or {}
    if date_key(state.get('last_trade_date')) == cutoff:
        for trade in state.get('trades',[]):
            if date_key(trade.get('date'))==cutoff:
                add('情绪反弹',dict(name='中证2000',ts_code='563300.SH'),trade,'latest')
    for etf in (valuation or {}).get('pool',[]):
        trade = etf.get('today_action')
        if trade and date_key(etf.get('last_date'))==cutoff and date_key(trade.get('date'))==cutoff:
            add('估值定投',etf,trade,'latest')
    # Include amounts/shares/round/reason: same-day partial sales are distinct events.
    unique = {json.dumps(e,sort_keys=True,ensure_ascii=False):e for e in events}
    order = {'情绪反弹':0,'周线定投':1,'估值定投':2}
    return sorted(unique.values(), key=lambda e:(order[e['strategy']],e['date'],e['name'],str(e['round'])))


def table(headers, rows):
    head = ''.join(f'<th style="padding:8px 6px;text-align:left;border-bottom:1px solid #dce4ee">{escape(h)}</th>' for h in headers)
    body = ''.join('<tr>'+''.join(f'<td style="padding:8px 6px;vertical-align:top;border-bottom:1px solid #edf1f6;overflow-wrap:anywhere">{c}</td>' for c in row)+'</tr>' for row in rows)
    return f'<table style="border-collapse:collapse;width:100%;table-layout:fixed;font-size:13px"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def small(text):
    return f'<div style="color:#64748b;font-size:11px;margin-top:3px">{escape(str(text))}</div>'


def render_actions(events, as_of):
    rows=[]
    for e in events:
        amount = '¥'+fmt(e['amount'],0) if e['amount'] is not None else '—'
        if e['shares'] is not None:
            amount += small(fmt(e['shares'],2)+'份')
        if e['ratio'] is not None:
            amount += small(f"仓位比例 {e['ratio']:.0%}")
        name = escape(e['name'])+small(e['strategy']+(f" · 轮次/级别 {e['round']}" if e['round'] is not None else ''))
        rows.append([name,'买入' if e['action']=='BUY' else '卖出',amount,escape(e['date'])])
    note = '周线：本周信号复核。' if as_of.weekday()==6 else '周线：汇总本周记录。'
    if as_of.weekday() not in (4,6):
        note = '周线买卖于周五、周日汇总；情绪反弹及估值定投独立提醒。'
    body = table(['标的／策略','动作','金额／份额','记录日'], rows) if rows else '<p>本次无新增操作</p>'
    return small(note)+body


def render_market(context):
    if not context:
        return '<p>暂无市场情绪数据</p>'
    c = context
    rows = [
        ['J13股票数量',fmt(c.get('j13_count'),0),delta(c.get('j13_count'),c.get('previous_count'),'只')],
        ['数量日变化率',fmt(c.get('change_pct'))+'%' if c.get('change_pct') is not None else '暂无对比','对比日 '+str(c.get('previous_date') or '—')],
        ['前60日90%参考阈值',fmt(c.get('reference_p90'),1),delta(c.get('reference_p90'),c.get('previous_reference_p90'),'只')],
        ['策略本次触发阈值',fmt(c.get('strategy_threshold'),1),'已触发' if c.get('strategy_trigger') is True else '未触发' if c.get('strategy_trigger') is False else '暂无判断'],
    ]
    return small('数据截至 '+str(c.get('data_date','—')))+table(['指标','数值','变化／状态'],rows)+small('参考阈值仅使用此前60个交易日；策略本次阈值保留原有计算口径。')


def render_holdings(summary):
    rows=[]
    etfs=(summary or {}).get('etf_list',[])
    dates={str(e.get('last_date') or '—') for e in etfs}
    weeks={str(e.get('signal_week_date') or '—') for e in etfs}
    shared_dates=len(dates)==1 and len(weeks)==1
    for e in etfs:
        positions=e.get('positions',[])
        if not positions:
            status='空仓等待'
        elif all(p.get('action') in ('hold_long_term','v6h_stage_3') for p in positions):
            status=f'长期持有 · {len(positions)}轮'
        else:
            active=[p for p in positions if p.get('action') not in ('hold_long_term','v6h_stage_3')]
            budgets=[number(p.get('budget_remaining')) for p in active]
            status='预算已用完 · 持有' if budgets and all(b is not None and b<=0 for b in budgets) else f'持有 · {len(positions)}轮'
        value=sum((number(p.get('portfolio_value')) if p.get('portfolio_value') is not None else (number(p.get('shares')) or 0)*(number(e.get('last_price')) or 0)) or 0 for p in positions)
        j=fmt(e.get('weekly_j'))+small('前周 '+fmt(e.get('previous_weekly_j')))+small(delta(e.get('weekly_j'),e.get('previous_weekly_j'),'点'))
        name=escape(e.get('name',''))
        if not shared_dates:
            name+=small('行情 '+str(e.get('last_date') or '—'))
            j+=small('周线 '+str(e.get('signal_week_date') or '—'))
        rows.append([name,escape(status),j,'¥'+fmt(value,0)])
    caption=small(f'行情截至 {next(iter(dates))} · 已确认周线 {next(iter(weeks))}') if shared_dates else ''
    return caption+table(['ETF','持仓状态','确认周J／周变化','持有市值'],rows) if rows else '<p>暂无ETF持仓数据</p>'


def render_valuation(summary, latest_date):
    rows=[]
    for e in (summary or {}).get('pool',[]):
        pe_date=e.get('pe_date')
        rank=fmt(e.get('pe_pct'))+'%' if number(e.get('pe_pct')) is not None else '暂无数据'
        pe = 'PE '+fmt(e.get('pe_ttm'))+small('五年分位 '+rank)
        pe += small('PE变化 '+delta(e.get('pe_ttm'),e.get('previous_pe_ttm')))
        pe += small('分位变化 '+delta(e.get('pe_pct'),e.get('previous_pe_pct'),'个百分点'))
        if e.get('previous_pe_date'):
            pe += small('对比 '+str(e['previous_pe_date']))
        remaining=e.get('budget_remaining')
        progress=f"{e.get('parts_bought',0)}/{e.get('n_parts',0)}份"
        progress+=small('剩余预算 ¥'+fmt(remaining,0) if remaining is not None else '剩余预算暂无数据')
        pct=number(e.get('pe_pct'));threshold=number((summary or {}).get('gate_threshold'))
        if not e.get('gate_enabled',True):gate='未设估值门'
        elif e.get('gate_open') is not None:gate='估值门开' if e['gate_open'] else '估值门关'
        else:gate=('估值门开' if pct<threshold else '估值门关') if pct is not None and threshold is not None else '估值门状态未知'
        status=escape(e.get('action_label',''))+small(gate)+small(e.get('next_desc',''))
        dates=f"行情 {e.get('last_date') or '—'} · PE {pe_date or '—'}"
        if date_key(e.get('last_date'))<date_key(latest_date):dates+=' · 数据较报告日期滞后'
        if pe_date and date_key(pe_date)<date_key(e.get('last_date')):dates+=' · PE较行情滞后'
        rows.append([escape(e.get('name',''))+small(dates),pe,progress,status])
    return table(['估值定投','PE与变化','投入进度','阶段／下一触发'],rows) if rows else '<p>暂无估值定投数据</p>'
