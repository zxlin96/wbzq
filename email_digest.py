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
    """Read archived data using the currently enabled ETF configuration."""
    root = Path(root)
    summary = {**(summary or {}), 'etf_list': [dict(e) for e in (summary or {}).get('etf_list', [])]}
    try:
        config = json.loads((root/'etf_config.json').read_text())
        enabled = {e['name']: e for e in config['etf_list'] if e.get('enabled', True)}
        versions = {name: e.get('strategy', 'v6') for name, e in enabled.items()}
        summary['etf_list'] = [e for e in summary['etf_list'] if e.get('name') in enabled]
        for etf in summary['etf_list']:
            entry = enabled[etf['name']]
            version = entry.get('strategy', 'v6')
            if version == 'trim':
                etf['portfolio_rules'] = entry.get('trim_params') or {}
            else:
                params = entry.get(f'{version}_params') or {}
                etf['scheduled_investment_amount'] = params.get('base_amount', config.get('base_amount', 1000))
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
        if date_key(value.get('requested_date') or value.get('data_date')) == day:
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
            if 'recent_trades' not in etf:
                etf['recent_trades'] = saved.get('recent_trades', saved.get('trades', []))
            if 'budget_remaining' not in etf:
                state = saved.get('state', {})
                if state.get('in_round'):
                    etf['budget_remaining'] = state.get('parts_left',0)*state.get('part_amt',0)
                elif state.get('wallet') is not None:
                    etf['budget_remaining'] = state['wallet']
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return summary


def collect_events(summary, sentiment, valuation, latest_date, as_of=None, *, include_week_history=False):
    """Read reminder records, optionally including daily strategies' week history."""
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
    for etf in (summary or {}).get('etf_list',[]):
        for trade in etf.get('recent_trades',[]):
            # Weekly purchase reminders keep their Friday/Sunday schedule.
            # Sales also depend on daily prices and must reach the daily email.
            if (stamp.weekday() in (4,6)
                    or (trade.get('action') == 'SELL'
                        and (include_week_history or date_key(trade.get('date')) == cutoff))):
                add('周线定投', etf, trade, 'week')
    state = sentiment or {}
    state_date = date_key(state.get('last_trade_date'))
    if include_week_history or state_date == cutoff:
        for trade in state.get('trades',[]):
            trade_date = date_key(trade.get('date'))
            if (include_week_history and trade_date <= state_date) or trade_date == cutoff == state_date:
                add('情绪反弹',dict(name='中证2000',ts_code='563300.SH'),trade,'latest')
    for etf in (valuation or {}).get('pool',[]):
        source_date = date_key(etf.get('last_date'))
        trades = list(etf.get('recent_trades') or []) if include_week_history else []
        today_action = etf.get('today_action')
        # today_action is a projection of the last full trade (without shares).
        # Prefer the full record so the projection cannot create a second signal.
        if today_action and not any(
                date_key(t.get('date')) == date_key(today_action.get('date'))
                and t.get('action') == today_action.get('action')
                and number(t.get('amount')) == number(today_action.get('amount'))
                and t.get('reason', '') == today_action.get('reason', '') for t in trades):
            trades.append(today_action)
        for trade in trades:
            trade_date = date_key(trade.get('date'))
            if (include_week_history and trade_date <= source_date) or trade_date == cutoff == source_date:
                add('估值定投',etf,trade,'latest')
    # Include amounts/shares/round/reason: same-day partial sales are distinct events.
    unique = {json.dumps(e,sort_keys=True,ensure_ascii=False):e for e in events}
    order = {'情绪反弹':0,'周线定投':1,'估值定投':2}
    return sorted(unique.values(), key=lambda e:(order[e['strategy']],e['date'],e['name'],str(e['round'])))


def collect_portfolio_actions(summary, latest_date, as_of=None):
    """Keep unfilled portfolio recommendations separate from simulated trades."""
    stamp=report_time(as_of)
    today=stamp.strftime('%Y%m%d')
    cutoff=min(date_key(latest_date),today)
    start=(stamp-timedelta(days=stamp.weekday()+7)).strftime('%Y%m%d')
    actions=[]
    for etf in (summary or {}).get('etf_list',[]):
        signal=date_key(etf.get('signal_week_date'))
        if date_key(etf.get('last_date'))!=cutoff or not start<=signal<=cutoff:
            continue
        if cutoff==today and stamp.hour<15:
            continue
        for position in etf.get('positions',[]):
            if _round_progress(position)['kind']!='portfolio':
                continue
            context=_portfolio_context(etf,position)
            if context['action'] not in ('trim','refill') or context['target'] is None:
                continue
            actions.append(dict(strategy='周线调仓',name=etf.get('name',''),ts_code=etf.get('ts_code',''),
                date=signal,action='SELL' if context['action']=='trim' else 'BUY',amount=None,shares=None,
                ratio=None,round=None,reason=position.get('action_detail') or etf.get('action_detail',''),
                scope='pending',pending=True,target_ratio=context['target'],current_ratio=context['current']))
            break
    return actions


def table(headers, rows, column_widths=None, *, compact=False):
    alignments = ['right' if h in ('金额／份额', '持有市值', '入选数量') else 'left' for h in headers]
    head_padding='8px 6px' if compact else '10px 6px'
    cell_padding='8px 6px' if compact else '12px 6px'
    head = ''.join(
        f'<th scope="col" style="padding:{head_padding};text-align:{align};vertical-align:top;'
        f'background:#edf3fb;color:#60708a;font-size:12px;font-weight:600;'
        f'border-bottom:1px solid #dfe7f2;overflow-wrap:anywhere">{escape(h)}</th>'
        for h, align in zip(headers, alignments))
    body = ''.join('<tr>'+''.join(
        f'<td style="padding:{cell_padding};text-align:{align};vertical-align:top;'
        f'background:{"#ffffff" if i % 2 == 0 else "#fafbfd"};'
        f'border-bottom:1px solid #e9edf4;overflow-wrap:anywhere;'
        f'font-variant-numeric:tabular-nums">{c}</td>'
        for c, align in zip(row, alignments))+'</tr>' for i, row in enumerate(rows))
    columns = ('<colgroup>'+''.join(f'<col style="width:{width}%">' for width in column_widths)
               +'</colgroup>') if column_widths else ''
    return (f'<table style="border-collapse:collapse;width:100%;table-layout:fixed;'
            f'font-size:14px;line-height:1.65;margin:10px 0 4px">'
            f'{columns}<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>')


def small(text):
    return f'<div style="color:#718096;font-size:12px;line-height:1.65;margin-top:4px">{escape(str(text))}</div>'


def _badge(text, tone='neutral'):
    colors = {'buy': ('#eaf1fd', '#456faf'), 'sell': ('#fceeea', '#a04e50'),
              'neutral': ('#eff2f7', '#63728a')}
    background, color = colors[tone]
    return (f'<span style="display:inline-block;max-width:100%;box-sizing:border-box;'
            f'padding:3px 6px;border-radius:4px;background:{background};color:{color};'
            f'font-size:12px;line-height:1.6;font-weight:600">{escape(str(text))}</span>')


def etf_label(etf):
    return '<strong style="color:#304665">'+escape(str(etf.get('name') or ''))+'</strong>'+small(etf.get('ts_code') or '代码暂无')


def render_actions(events, note='', empty_text='本次无新增操作'):
    if not events:
        return small(note)+f'<p>{escape(empty_text)}</p>'
    body = small(note)
    for action, label, color, background in (
            ('SELL', '卖出', '#a04e50', '#fff5f1'),
            ('BUY', '买入', '#456faf', '#f5f8ff')):
        group = [e for e in events if e['action'] == action]
        body += (f'<h3 style="font-size:18px;color:{color};margin:18px 0 10px">'
                 f'{label}提醒（{len(group)}项）</h3>')
        if not group:
            body += small(f'本次无{label}信号')
        for e in group:
            round_label = ''
            if e['round'] is not None:
                round_label = (f"第{e['round']}轮（R{e['round']}）" if e['strategy'] == '周线定投'
                               else f"第{e['round']}级")
            quantities = []
            if e.get('pending'):
                target=f"目标仓位 {e['target_ratio']:.2%}"
                if e.get('current_ratio') is not None:
                    target=f"模拟仓位 {e['current_ratio']:.2%} → "+target
                quantities.append(target)
            if action == 'SELL' and e['shares'] is not None:
                quantities.append(f"卖出 {fmt(e['shares'], 2)} 份")
            if e['ratio'] is not None:
                quantities.append(f"{'卖出' if action == 'SELL' else '买入'}比例 {e['ratio']:.0%}")
            if e['amount'] is not None:
                amount_label = '参考金额' if action == 'SELL' else '买入金额'
                quantities.append(f"{amount_label} ¥{fmt(e['amount'])}")
            if action == 'BUY' and e['shares'] is not None:
                quantities.append(f"参考份额 {fmt(e['shares'], 2)} 份")
            reason = small('触发原因：'+str(e.get('reason') or '未记录'))
            record_note=(f"{e.get('ts_code') or '代码暂无'} · {e['strategy']} · "
                         +('信号周 ' if e.get('pending') else '记录日 ')+e['date'])
            pending_note=(_badge('策略待执行建议')+small('下一交易日开盘；已操作请勿重复。')) if e.get('pending') else ''
            body += (f'<div data-trade-action="{action}" style="margin:10px 0;padding:14px;'
                     f'border:1px solid {"#efdcd5" if action == "SELL" else "#dce5f5"};border-left:3px solid {color};'
                     f'border-radius:10px;background:{background};overflow-wrap:anywhere">'
                     f'<div style="font-size:16px;font-weight:bold;color:{color}">'
                     f'{label} · {escape(e["name"])} {escape(round_label)}</div>'
                     +small(record_note)+pending_note
                     +f'<div style="font-size:16px;font-weight:bold;color:{color};margin-top:8px">'
                     +'<br>'.join(escape(q) for q in quantities)+'</div>'+reason+'</div>')
    return body


def render_market(context):
    if not context:
        return '<p>暂无市场情绪数据</p>'
    c = context
    rows = [
        ['J13股票数量',fmt(c.get('j13_count'),0),delta(c.get('j13_count'),c.get('previous_count'),'只')],
        ['数量日变化率',fmt(c.get('change_pct'))+'%' if c.get('change_pct') is not None else '暂无对比','对比日 '+str(c.get('previous_date') or '—')],
        ['前60日90%参考阈值',fmt(c.get('reference_p90'),1),delta(c.get('reference_p90'),c.get('previous_reference_p90'),'只')],
        ['策略本次触发阈值',fmt(c.get('strategy_threshold'),1),escape(c['signal_status']) if c.get('signal_status') else '已触发' if c.get('strategy_trigger') is True else '未触发' if c.get('strategy_trigger') is False else '暂无判断'],
    ]
    return small('数据截至 '+str(c.get('data_date','—')))+table(['指标','数值','变化／状态'],rows)+small('参考阈值仅使用此前60个交易日；策略本次阈值保留原有计算口径。')


def _round_progress(position):
    """Describe simulated round progress without claiming broker execution."""
    action = position.get('action') or ''
    stage = number(position.get('sell_stage')) or 0
    target = number(position.get('round_budget'))
    invested = number(position.get('total_invested'))
    remaining = number(position.get('budget_remaining'))
    if remaining is None and target is not None and target > 0 and invested is not None:
        remaining = max(target-invested, 0)
    if action in ('hold_long_term', 'v6h_stage_3'):
        kind, label, detail = 'retained', '', ''
    elif position.get('portfolio_value') is not None or action in ('trim', 'refill'):
        kind, label, detail = 'portfolio', '', ''
    elif stage > 0 or action.startswith(('sell_', 'wait_price_')) or action in ('wait_golden', 'v6h_stage_1', 'v6h_stage_2'):
        kind, label = 'selling', '减仓未完成'
        if action == 'v6h_stage_1':
            detail = '已减1/6 · 等下一信号'
        elif action == 'v6h_stage_2':
            detail = '已减1/3 · 等下一信号'
        elif action == 'wait_golden':
            detail = '等金叉及后续卖出信号'
        else:
            detail = '等下一卖出信号'
    elif remaining is not None and remaining > 0:
        kind, label = 'funding', '定投未完成'
        detail = '剩余预算 ¥'+fmt(remaining,0)
    elif remaining is not None and remaining <= 0:
        kind, label, detail = 'holding', '持有待卖', '预算已投完'
    else:
        kind, label, detail = 'unknown', '进度待核对', '预算数据不足'
    return dict(kind=kind, label=label, detail=detail)


def _portfolio_context(etf, position):
    action=position.get('action') or etf.get('action')
    current=number(position.get('position_ratio'))
    total=number(position.get('portfolio_value'))
    shares,price=number(position.get('shares')),number(etf.get('last_price'))
    if current is None and total is not None and total>0 and shares is not None and price is not None:
        current=shares*price/total
    target=number(position.get('target_ratio'))
    if target is None and action=='trim':
        target=number((etf.get('portfolio_rules') or {}).get('keep_ratio'))
    elif target is None and action=='refill':
        target=1.0
    if target is not None and not 0<=target<=1:
        target=None
    return dict(action=action,current=current,target=target)


def _render_portfolio(etf, position):
    action=position.get('action') or etf.get('action')
    label=position.get('action_label') or etf.get('action_label') or '状态待核对'
    tone='sell' if action=='trim' else 'buy' if action=='refill' else 'neutral'
    body=(etf_label(etf)+'<div style="margin-top:8px">'+_badge(label,tone)+'</div>')
    context=_portfolio_context(etf,position)
    if context['current'] is not None:
        allocation=f"模拟仓位 {context['current']:.2%}"
        if context['target'] is not None:
            allocation+=f" → 目标 {context['target']:.2%}"
        body+=small(allocation)
    if number(etf.get('weekly_j')) is not None:
        body+=small('确认周J '+fmt(etf['weekly_j']))
    rules=etf.get('portfolio_rules') or {}
    trim_j,refill_j,keep=(number(rules.get(key)) for key in ('trim_j','refill_j','keep_ratio'))
    if trim_j is not None and refill_j is not None and keep is not None:
        body+=small(f'满仓打底 · 周J>{trim_j:g}减至{keep:.0%} · 周J<{refill_j:g}补满')
    return ('<div data-portfolio-strategy="true" style="padding:14px;margin:12px 0;'
            'background:#fafbfd;border:1px solid #e4eaf4;border-radius:10px">'+body+'</div>')


def render_holdings(summary, latest_date=''):
    rows=[]
    etfs=(summary or {}).get('etf_list',[])
    dates={str(e.get('last_date') or '—') for e in etfs}
    weeks={str(e.get('signal_week_date') or '—') for e in etfs}
    shared_dates=len(dates)==1 and len(weeks)==1
    portfolios=[]
    for e in etfs:
        portfolio=next((p for p in e.get('positions',[]) if _round_progress(p)['kind']=='portfolio'),None)
        if portfolio is not None or 'portfolio_rules' in e:
            portfolios.append(_render_portfolio(e,portfolio or {}))
        for p in e.get('positions',[]):
            if (number(p.get('shares')) or 0)<=0:
                continue
            progress=_round_progress(p)
            if progress['kind'] in ('retained','portfolio'):
                continue
            rid = p.get('round_id')
            round_label=f'R{rid}' if rid is not None else '待核对'
            name=etf_label(e)
            if number(e.get('scheduled_investment_amount')) is not None:
                name+=small('常规定投 ¥'+fmt(e['scheduled_investment_amount'],0)+'/周')
            if not shared_dates:
                name+=small('截至 '+str(e.get('last_date') or '—'))
                if latest_date and date_key(e.get('last_date'))<date_key(latest_date):
                    name+=_badge('数据滞后')
            status=_badge(progress['label'])+small(progress['detail'])
            rows.append([name,'<strong>'+escape(round_label)+'</strong>',status])
    caption=small(f'截至 {next(iter(dates))} · 确认周线 {next(iter(weeks))}') if shared_dates else ''
    if shared_dates and latest_date and date_key(next(iter(dates)))<date_key(latest_date):
        caption+=_badge('数据滞后：主报告截至 '+str(latest_date), 'sell')
    body=(f'<p style="margin:0 0 8px">待完成 {len(rows)} 轮</p>'
          +table(['ETF','轮次','进度'],rows,[38,16,46])) if rows else '<p>暂无待完成轮次</p>'
    return caption+''.join(portfolios)+body+small('按策略模拟进度；实际成交请自行核对。')


def _valuation_change(current, previous, label, kind, unit='', *, compact=False):
    a,b=number(current),number(previous)
    if a is None or b is None:
        return small(label+' 暂无对比')
    change=round(a-b,2)
    if change==0:
        change=0.0
    arrow,tone=('▲','sell') if change>0 else ('▼','buy') if change<0 else ('→','neutral')
    suffix='%' if kind=='percentile' else ''
    description=f'{label} {arrow} {change:+.2f}{unit}（前次{fmt(b)}{suffix}→本次{fmt(a)}{suffix}）'
    return (f'<span data-valuation-change="{kind}" aria-label="{escape(description)}" '
            'style="font-size:12px;color:#718096">'
            +('' if compact else escape(label)+' ')
            +_badge(f'{arrow} {change:+.2f}'+('' if compact else unit),tone)+'</span>')


def _percentile_meter(value, threshold, period):
    """Render one 0–100% bar with email-compatible presentation tables."""
    pct=number(value)
    if pct is None:
        return ''
    if not 0<=pct<=100:
        return small('分位超出0–100%，请核对')
    gate=number(threshold)
    if gate is not None and not 0<=gate<=100:
        gate=None
    points=sorted({0.,100.,pct}|({gate} if gate is not None else set()))
    fill='#b8c4d7' if period=='previous' else '#81a4dd'
    height=5 if period=='previous' else 9
    cells=[]
    for start,end in zip(points,points[1:]):
        color=fill if end<=pct else '#edf1f7'
        marker=''
        if gate==end:
            marker='border-right:2px solid #7385a5;'
        elif gate==start==0:
            marker='border-left:2px solid #7385a5;'
        cells.append(f'<td width="{end-start:.6f}%" style="padding:0;height:{height}px;'
                     f'line-height:{height}px;font-size:1px;background:{color};{marker}">&nbsp;</td>')
    description=('前次' if period=='previous' else '本次')+f'五年分位 {pct:.2f}%'
    if gate is not None:
        description+=f'，估值门槛 {gate:g}%'
    return (f'<div role="img" aria-label="{escape(description)}" data-percentile-meter="{period}" '
            f'data-percentile="{pct:g}" style="margin:6px 0">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'style="width:100%;table-layout:fixed;border-collapse:collapse"><tr>'
            +''.join(cells)+'</tr></table></div>')


def render_valuation(summary, latest_date):
    pool=(summary or {}).get('pool',[])
    if not pool:
        return '<p>暂无估值定投数据</p>'
    rows=[]
    date_keys=('last_date','pe_date','previous_pe_date')
    shared_dates=len({tuple(e.get(key) for key in date_keys) for e in pool})==1
    threshold=number((summary or {}).get('gate_threshold'))
    def dates(etf):
        pe_date=etf.get('pe_date')
        label=(f'行情/PE {pe_date}' if pe_date and pe_date==etf.get('last_date')
               else f"行情 {etf.get('last_date') or '—'} · PE {pe_date or '—'}")
        if etf.get('previous_pe_date'):
            label+=' · 对比 '+str(etf['previous_pe_date'])
        if date_key(etf.get('last_date'))<date_key(latest_date):label+=' · 数据滞后'
        if pe_date and date_key(pe_date)<date_key(etf.get('last_date')):label+=' · PE较行情滞后'
        return label
    for e in pool:
        rank=fmt(e.get('pe_pct'))+'%' if number(e.get('pe_pct')) is not None else '暂无数据'
        pe=('<div>PE '+fmt(e.get('pe_ttm'))+' '
            +_valuation_change(e.get('pe_ttm'),e.get('previous_pe_ttm'),'PE变化','pe',compact=True)+'</div>')
        pe+='<div style="margin-top:4px"><strong>'+('五年分位 ' if rank=='暂无数据' else '')+rank+'</strong> '
        pe+=_valuation_change(e.get('pe_pct'),e.get('previous_pe_pct'),'分位变化','percentile','个百分点',compact=True)+'</div>'
        pe+=_percentile_meter(e.get('pe_pct'),threshold if e.get('gate_enabled',True) else None,'current')
        remaining=e.get('budget_remaining')
        progress=f"{e.get('parts_bought',0)}/{e.get('n_parts',0)}份"
        pct=number(e.get('pe_pct'))
        if not e.get('gate_enabled',True):gate='未设估值门'
        elif e.get('gate_open') is not None:gate='估值门开' if e['gate_open'] else '估值门关'
        else:gate=('估值门开' if pct<threshold else '估值门关') if pct is not None and threshold is not None else '估值门状态未知'
        status='<span style="font-size:12px">'+escape(progress)+'</span> '+_badge(gate)
        status+=small(e.get('next_desc') or e.get('action_label',''))
        if not shared_dates:
            status+=small(dates(e))
        name=etf_label(e)+small('余¥'+fmt(remaining,0) if remaining is not None else '预算暂无')
        rows.append([name,pe,status])
    caption=small(dates(pool[0])) if shared_dates else ''
    legend='0–100%刻度 · 分位变化单位：百分点'
    if threshold is not None and 0<=threshold<=100 and any(e.get('gate_enabled',True) for e in pool):
        legend+=f' · 竖线：{threshold:g}%门槛'
    return ('<div data-valuation-compact="true">'+caption+small(legend)
            +table(['ETF','PE／五年分位','进度／下一步'],rows,[22,42,36],compact=True)+'</div>')
