"""Notification policy and amounts must be independent of strategy mutation."""
import copy
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import generate_email_report as mail
from email_digest import (collect_events, collect_portfolio_actions, market_context, render_actions, render_holdings, render_valuation,
                          report_time, weekly_comparison, enrich_archived_summary,
                          enrich_valuation_summary, load_market_context)


def trade(date='20261030', action='BUY', amount=1000, **kw):
    return dict(date=date,action=action,amount=amount,round=1,reason='trigger',**kw)


def weekly(records=None):
    return {'etf_list':[dict(name='测试ETF',ts_code='000001.SH',last_date='20261030',
        signal_week_date='20261030',weekly_j=11,previous_weekly_j=-4,last_price=10,
        recent_trades=records if records is not None else [trade(),trade('20261027','SELL',500)],
        positions=[dict(shares=100,avg_cost=10,budget_remaining=0,action='buy',action_label='建议定投(0x)')])]}


def portfolio(action='trim',ratio=.5147):
    target={'trim':.5,'refill':1.,'hold':None}[action]
    label={'trim':'建议减仓至50%','refill':'建议补满仓','hold':'持有（仓位50%）'}[action]
    return {'etf_list':[dict(name='红利低波',ts_code='512890.SH',last_date='20261120',
        signal_week_date='20261120',weekly_j=-1 if action=='refill' else 100.37,
        last_price=1.,portfolio_rules=dict(trim_j=100,refill_j=0,keep_ratio=.5),recent_trades=[],
        positions=[dict(round_id=1,shares=ratio*10000,portfolio_value=10000,
                        position_ratio=ratio,target_ratio=target,action=action,action_label=label,
                        action_detail='下一交易日开盘调整至目标仓位')])]}


@pytest.mark.parametrize('action,direction,target',[('trim','SELL',.5),('refill','BUY',1.)])
def test_portfolio_pending_advice_is_visible_without_fabricating_a_fill(action,direction,target):
    source=portfolio(action);before=copy.deepcopy(source)
    events=collect_portfolio_actions(source,'20261120','2026-11-20 21:00')
    assert len(events)==1
    event=events[0]
    assert event['action']==direction and event['target_ratio']==target
    assert event['pending'] is True and event['scope']=='pending'
    assert event['amount'] is None and event['shares'] is None and event['round'] is None
    html=render_actions(events)
    for text in ['红利低波','策略待执行建议','模拟仓位 51.47%',f'目标仓位 {target:.2%}',
                 '下一交易日开盘','信号周 20261120']:
        assert text in html
    assert '买入金额' not in html and '参考金额' not in html and source==before


def test_portfolio_at_half_with_no_new_advice_has_no_pending_sell():
    source=portfolio('hold',.5)
    assert not collect_portfolio_actions(source,'20261120','2026-11-20 21:00')
    assert '模拟仓位 50.00%' in render_holdings(source)
    source=portfolio()
    del source['etf_list'][0]['positions'][0]['target_ratio']
    del source['etf_list'][0]['portfolio_rules']
    assert not collect_portfolio_actions(source,'20261120','2026-11-20 21:00')


@pytest.mark.parametrize('when,latest,count',[
    ('2026-11-20 14:00','20261120',0),('2026-11-19 21:00','20261120',0),
    ('2026-11-23 21:00','20261123',0),('2026-11-23 09:00','20261120',1),
    ('2026-12-07 21:00','20261120',0),
])
def test_pending_portfolio_reminders_use_confirmed_current_data_and_survive_the_weekend(when,latest,count):
    assert len(collect_portfolio_actions(portfolio(),latest,when))==count


def test_dividend_pending_advice_reaches_the_top_of_the_email_and_ratio_can_be_derived(monkeypatch,tmp_path):
    monkeypatch.chdir(tmp_path)
    source=portfolio()
    del source['etf_list'][0]['positions'][0]['position_ratio']
    monkeypatch.setattr(mail,'load_reports_json',lambda:dict(latestDate='20261120'))
    monkeypatch.setattr(mail,'load_dca_summary',lambda:source)
    monkeypatch.setattr(mail,'load_strategy_state',lambda:{})
    monkeypatch.setattr(mail,'load_4pct_summary',lambda:{})
    monkeypatch.setattr(mail,'load_multi_indicator_hints',lambda:[])
    html=mail.generate_email_report(as_of='2026-11-20 21:00')
    current=re.findall(r'<section[^>]*>(.*?)</section>',html,re.S)[0]
    assert '卖出提醒（1项）' in current and '红利低波' in current
    assert '模拟仓位 51.47% → 目标仓位 50.00%' in current and '策略待执行建议' in current
    assert source['etf_list'][0]['recent_trades']==[]


@pytest.mark.parametrize('j,ratio,action,target',[
    (100.37,.5147,'trim',.5),(100.37,.5,'hold',None),(-1.,.5,'refill',1.),(50.,1.,'hold',None),
])
def test_portfolio_signal_exports_current_and_target_allocation_without_execution(monkeypatch,j,ratio,action,target):
    from portfolio_trim_strategy import PortfolioTrimStrategy
    daily=pd.DataFrame([dict(trade_date='20261120',close_qfq=1.)])
    weekly_frame=pd.DataFrame([dict(trade_date='20261120',week_completed=True,J=j,K=50.,D=50.)])
    monkeypatch.setattr(PortfolioTrimStrategy,'_prepare',staticmethod(lambda *args:(daily,weekly_frame)))
    strategy=PortfolioTrimStrategy('红利低波')
    strategy.shares=ratio*10000
    strategy.cash=(1-ratio)*10000
    strategy.last_price=1.
    result=strategy.get_next_action(daily,weekly_frame)['positions'][0]
    assert result['action']==action and result['position_ratio']==pytest.approx(ratio)
    assert result['target_ratio']==target and not strategy.trades


@pytest.mark.parametrize('when,count',[
    ('2026-10-26 20:00',0),('2026-10-27 20:00',1),('2026-10-28 20:00',0),
    ('2026-10-29 20:00',0),('2026-10-30 20:00',2),('2026-10-31 20:00',0),
    ('2026-11-01 18:00',2),('2026-10-30T16:00:00Z',0),
])
def test_weekly_sales_daily_and_purchases_friday_sunday(when,count):
    assert len(collect_events(weekly(),{}, {},'20261030',when))==count


@pytest.mark.parametrize('day',['20261026','20261027','20261028','20261029','20261030'])
def test_weekly_sale_reaches_daily_mail_without_repeating_old_sale(day):
    source=weekly([trade(day,'SELL',500,shares=50),trade('20261023','SELL',600),
                   trade(day,amount=1000)])
    source['etf_list'][0]['last_date']=day
    before=copy.deepcopy(source)
    stamp=datetime.strptime(day,'%Y%m%d').replace(hour=20)
    events=collect_events(source,{}, {},day,stamp)
    sales=[e for e in events if e['action']=='SELL']
    assert [(e['date'],e['shares']) for e in sales]==[(day,50)]
    assert source==before
    if stamp.weekday()!=4:
        assert all(e['action']=='SELL' for e in events)
    assert not collect_events(source,{}, {},day,stamp.replace(hour=14))


def test_weekday_sale_is_visible_in_full_email(monkeypatch,tmp_path):
    monkeypatch.chdir(tmp_path)
    source=weekly([trade('20261027','SELL',500,shares=50),trade('20261026','SELL',200)])
    source['etf_list'][0]['last_date']='20261027'
    monkeypatch.setattr(mail,'load_reports_json',lambda:dict(latestDate='20261027'))
    monkeypatch.setattr(mail,'load_dca_summary',lambda:source)
    monkeypatch.setattr(mail,'load_strategy_state',lambda:{})
    monkeypatch.setattr(mail,'load_4pct_summary',lambda:{})
    monkeypatch.setattr(mail,'load_multi_indicator_hints',lambda:[])
    html=mail.generate_email_report(as_of='2026-10-27 20:00')
    current=re.findall(r'<section[^>]*>(.*?)</section>',html,re.S)[0]
    assert '卖出提醒（1项）' in current and '50.00 份' in current
    assert '测试ETF' in current and '第1轮（R1）' in current
    assert '20261026' not in current
    assert '周线卖出每日提醒' in current


def test_sunday_replays_exact_week_without_generating_second_purchase():
    source=weekly();before=copy.deepcopy(source)
    friday=collect_events(source,{}, {},'20261030','2026-10-30 20:00')
    sunday=collect_events(source,{}, {},'20261030','2026-11-01 18:00')
    assert friday==sunday and source==before
    assert [e['date'] for e in sunday]==['20261027','20261030']


@pytest.mark.parametrize('when,latest,dates',[
    ('2027-01-01 20:00','20261231',['20261228','20261231']),
    ('2026-10-09 20:00','20260930',[]),
])
def test_calendar_week_not_latest_quote_controls_holidays_and_years(when,latest,dates):
    records=[trade('20261228'),trade('20261231'),trade('20260930')]
    assert [e['date'] for e in collect_events(weekly(records),{}, {},latest,when)]==dates


def test_daily_strategies_are_independent_and_old_valuation_action_is_not_new():
    state=dict(last_trade_date='20261028',trades=[trade('20261028',amount=2000)])
    val={'pool':[dict(name='估值ETF',last_date='20261028',today_action=trade('20261028'))]}
    events=collect_events(weekly(),state,val,'20261028','2026-10-28 20:00')
    assert [e['strategy'] for e in events]==['情绪反弹','估值定投']
    assert not collect_events({},state,val,'20261030','2026-10-30 20:00')


def test_week_summary_includes_daily_history_without_changing_reminder_window():
    state=dict(last_trade_date='20261030',trades=[trade('20261026',amount=2000),
               trade('20261028','SELL',500),trade(amount=4000),trade('20261102')])
    records=[trade('20261027','SELL',600),trade('20261029'),trade(shares=100)]
    today_action={k:records[-1][k] for k in ('date','action','amount','reason')}
    val={'pool':[dict(name='估值ETF',last_date='20261030',recent_trades=records,
                      today_action=today_action)]}
    source=weekly();before=copy.deepcopy((source,state,val))
    regular=collect_events(source,state,val,'20261030','2026-11-01 18:00')
    history=collect_events(source,state,val,'20261030','2026-11-01 18:00',include_week_history=True)
    assert len(regular)==4 and len(history)==8
    assert {e['date'] for e in history}=={'20261026','20261027','20261028','20261029','20261030'}
    assert len([e for e in history if e['strategy']=='估值定投' and e['date']=='20261030'])==1
    assert (source,state,val)==before
    assert not collect_events(source,state,val,'20261030','2026-11-06 20:00',include_week_history=True)


def test_zero_remaining_budget_does_not_hide_final_buy_or_create_zero_order():
    source=weekly([trade(amount=1000),trade(amount=0)])
    events=collect_events(source,{}, {},'20261030','2026-10-30 20:00')
    assert [e['amount'] for e in events]==[1000]
    assert '预算已投完' in render_holdings(source)
    assert '0x' not in render_holdings(source)


def test_separate_strategies_rounds_and_partial_sales_are_not_merged():
    a=trade(action='SELL',amount=500,shares=50)
    b={**a,'amount':300,'shares':30}
    c={**a,'round':2}
    state=dict(last_trade_date='20261030',trades=[trade(amount=2000)])
    events=collect_events(weekly([a,a,b,c,trade()]),state,{},'20261030','2026-10-30 20:00')
    assert len(events)==5
    assert len([e for e in events if e['action']=='SELL'])==3


def test_intraday_or_future_records_are_not_announced():
    assert not collect_events(weekly([trade()]),{}, {},'20261030','2026-10-30 14:00')
    assert not collect_events(weekly([trade('20261102')]),{}, {},'20261030','2026-10-30 20:00')


def test_market_reference_excludes_current_day_and_keeps_actual_rule_separate():
    history=[dict(trade_date=d,count=i) for i,d in enumerate(pd.bdate_range('2026-01-01',periods=62).strftime('%Y%m%d'))]
    history[-1]['count']=1000000
    result=market_context(history,dict(percentile_90=88,is_above_threshold=False))
    assert result['reference_p90']==pytest.approx(54.1)
    assert result['previous_reference_p90']==pytest.approx(53.1)
    assert result['strategy_threshold']==88 and result['strategy_trigger'] is False
    assert result['change']==1000000-60
    history[-2]['count']=0
    assert market_context(history)['change_pct'] is None
    assert market_context(history[-2:])['reference_p90'] is None


def test_weekly_j_changes_are_points_including_negative_values():
    c=weekly_comparison(pd.DataFrame([dict(trade_date='20261023',J=-5),dict(trade_date='20261030',J=12)]))
    assert c['weekly_j_change']==17 and c['previous_week_date']=='20261023'


def test_pending_rounds_hide_retained_and_cleared_positions_and_omit_market_value():
    source=weekly([])
    source['etf_list'][0]['positions']=[
        dict(round_id=7,shares=0,action='sell_all'),
        dict(round_id=8,shares=80,avg_cost=9,action='v6h_stage_3'),
        dict(round_id=9,shares=90,avg_cost=10,action='hold_long_term'),
        dict(round_id=12,shares=120,avg_cost=11,action='v6h_stage_1',sell_stage=1),
        dict(round_id=13,shares=130,avg_cost=12,action='buy',round_budget=5000,budget_remaining=1000),
    ]
    source['etf_list'].append(dict(name='空仓ETF',last_date='20261030',signal_week_date='20261030',positions=[]))
    before=copy.deepcopy(source)
    html=render_holdings(source,'20261102')
    for text in ['R12','R13','已减1/6','定投未完成','待完成 2 轮',
                 '剩余预算 ¥1,000','策略模拟进度','数据滞后','20261030']:
        assert text in html
    for text in ['R7','R8','R9','持有市值','长期保留','长期持有','成本','120.00份','前周','空仓ETF']:
        assert text not in html
    assert source==before


def test_cash_only_portfolio_does_not_invent_an_unfinished_round_or_show_value():
    source=weekly([])
    source['etf_list'][0]['positions']=[dict(round_id=1,shares=0,portfolio_value=10000)]
    html=render_holdings(source)
    assert '暂无待完成轮次' in html
    assert '¥10,000' not in html and 'R1' not in html


@pytest.mark.parametrize('changes,label,detail',[
    ({'total_invested':2000,'budget_remaining':3000}, '定投未完成', '剩余预算 ¥3,000'),
    ({'total_invested':5000,'budget_remaining':0}, '持有待卖', '预算已投完'),
    ({'action':'v6h_stage_1','sell_stage':1,'total_invested':5000,'budget_remaining':0},
     '减仓未完成', '已减1/6'),
    ({'action':'v6h_stage_2','sell_stage':2,'total_invested':5000,'budget_remaining':0},
     '减仓未完成', '已减1/3'),
])
def test_pending_rounds_keep_funding_and_selling_progress_without_claiming_execution(changes,label,detail):
    source=weekly([])
    position=dict(round_id=12,shares=100,avg_cost=10,action='buy',round_budget=5000)
    position.update(changes)
    source['etf_list'][0]['positions']=[position]
    before=copy.deepcopy(source)
    html=render_holdings(source)
    assert label in html and detail in html and 'R12' in html
    assert '策略模拟进度' in html and '实际成交请自行核对' in html
    assert source==before


def test_pending_list_excludes_retained_cleared_and_portfolio_rounds():
    source=weekly([])
    source['etf_list'][0]['positions']=[
        dict(round_id=1,shares=10,round_budget=5000,budget_remaining=1000,action='buy'),
        dict(round_id=2,shares=20,round_budget=5000,budget_remaining=0,action='buy'),
        dict(round_id=3,shares=30,round_budget=5000,budget_remaining=1000,sell_stage=1,action='v6h_stage_1'),
        dict(round_id=4,shares=40,sell_stage=3,action='v6h_stage_3'),
        dict(round_id=5,shares=0,action='sell_all'),
        dict(round_id=6,shares=60,portfolio_value=600,action='hold'),
    ]
    html=render_holdings(source)
    for text in ['待完成 3 轮','定投未完成','持有待卖','减仓未完成','R1','R2','R3','data-portfolio-strategy="true"']:
        assert text in html
    for text in ['R4','R5','R6','长期保留','组合总值','持有市值']:
        assert text not in html


@pytest.mark.parametrize('action,label,shares',[
    ('trim','建议减仓至50%',100),('refill','建议补满仓',0),('hold','持有（仓位50%）',50),
])
def test_dividend_strategy_rules_and_current_signal_remain_visible_without_round_or_value(action,label,shares):
    source={'etf_list':[dict(name='红利低波',ts_code='512890.SH',last_date='20261120',
        signal_week_date='20261120',weekly_j=100.37,
        portfolio_rules=dict(trim_j=100,refill_j=0,keep_ratio=.5),
        positions=[dict(round_id=1,shares=shares,portfolio_value=100000,
                        action=action,action_label=label)])]}
    before=copy.deepcopy(source)
    html=render_holdings(source)
    for text in ['红利低波','512890.SH',label,'周J&gt;100减至50%','周J&lt;0补满',
                 '确认周J 100.37','data-portfolio-strategy="true"','暂无待完成轮次']:
        assert text in html
    for text in ['R1','持有市值','组合总值','100,000','成本','长期保留']:
        assert text not in html
    assert source==before


def test_final_sale_still_reminds_even_when_the_round_is_removed_from_tracking():
    source=weekly([trade(action='SELL',amount=500,shares=50)])
    source['etf_list'][0]['positions']=[dict(round_id=1,shares=50,action='v6h_stage_3',sell_stage=3)]
    html=render_holdings(source)
    assert '暂无待完成轮次' in html and 'R1' not in html and '测试ETF' not in html
    events=collect_events(source,{}, {},'20261030','2026-10-30 20:00')
    assert len(events)==1 and events[0]['action']=='SELL'
    assert '卖出 50.00 份' in render_actions(events)


def test_missing_round_budget_is_unknown_and_available_budget_progress_can_be_derived():
    source=weekly([])
    source['etf_list'][0]['positions']=[dict(round_id=1,shares=10,action='buy')]
    html=render_holdings(source)
    assert '进度待核对' in html and '预算数据不足' in html
    assert '持有待卖' not in html
    source['etf_list'][0]['positions'][0].update(round_budget=5000,total_invested=2000)
    html=render_holdings(source)
    assert '定投未完成' in html and '剩余预算 ¥3,000' in html


def test_buy_sell_groups_show_direction_amount_round_and_escaped_reason():
    source=weekly([trade(),trade(action='SELL',amount=500,shares=50,ratio=.5)])
    events=collect_events(source,{}, {},'20261030','2026-10-30 20:00')
    events[0]['reason']='<script>unsafe</script>'
    before=copy.deepcopy(events)
    html=render_actions(events)
    assert html.index('卖出提醒（1项）')<html.index('买入提醒（1项）')
    for text in ['data-trade-action="SELL"','data-trade-action="BUY"','卖出 50.00 份',
                 '卖出比例 50%','参考金额 ¥500.00','买入金额 ¥1,000.00',
                 '第1轮（R1）','触发原因：','记录日 20261030','000001.SH']:
        assert text in html
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert events==before


def test_valuation_shows_dates_gate_and_missing_comparison():
    source={'gate_threshold':50,'pool':[dict(name='估值ETF',last_date='20261028',pe_date='20261027',
        pe_ttm=12,pe_pct=61.68,gate_enabled=True,gate_open=False,parts_bought=0,n_parts=10,
        budget_remaining=20000,next_desc='门开后投第1份')]}
    html=render_valuation(source,'20261030')
    for value in ['61.68%','12.00','估值门关','0/10份','20,000','20261027','滞后','暂无对比']:
        assert value in html
    current=copy.deepcopy(source)
    current['pool'][0]['last_date']='20261030'
    assert 'PE较行情滞后' in render_valuation(current,'20261030')
    del current['pool'][0]['pe_date']
    del current['pool'][0]['pe_pct']
    missing=render_valuation(current,'20261030')
    assert 'PE —' in missing and '五年分位 暂无数据' in missing


@pytest.mark.parametrize('pe,previous_pe,pct,previous_pct,pe_change,rank_change',[
    (13.531,13.4824,75.84,75.12,'▲ +0.05','▲ +0.72个百分点'),
    (36.1526,36.4059,47.12,49.6,'▼ -0.25','▼ -2.48个百分点'),
    (12.,12.,50.,50.,'→ +0.00','→ +0.00个百分点'),
])
def test_valuation_graph_preserves_exact_values_direction_units_and_comparison_dates(
        pe,previous_pe,pct,previous_pct,pe_change,rank_change):
    source={'gate_threshold':50,'pool':[dict(name='估值ETF',last_date='20261118',pe_date='20261118',
        pe_ttm=pe,previous_pe_ttm=previous_pe,pe_pct=pct,previous_pe_pct=previous_pct,
        previous_pe_date='20261117',gate_enabled=True,parts_bought=1,n_parts=10,budget_remaining=18000)]}
    before=copy.deepcopy(source)
    html=render_valuation(source,'20261120')
    assert pe_change in html and rank_change in html
    assert f'data-percentile-meter="current" data-percentile="{pct:g}"' in html
    assert 'data-percentile-meter="previous"' not in html
    assert f'前次{previous_pct:.2f}%→本次{pct:.2f}%' in html
    assert '竖线：50%门槛' in html and '对比 20261117' in html and '数据滞后' in html
    assert '1/10份' in html and '18,000' in html and source==before


@pytest.mark.parametrize('pct,gate',[(0,'估值门开'),(50,'估值门关'),(100,'估值门关')])
def test_valuation_scale_endpoints_and_gate_equality(pct,gate):
    source={'gate_threshold':50,'pool':[dict(name='估值ETF',pe_pct=pct,last_date='20261118')]}
    html=render_valuation(source,'20261118')
    assert f'data-percentile="{pct:g}"' in html
    assert 'data-percentile-meter="previous"' not in html
    assert gate in html and '暂无对比' in html


def test_disabled_or_missing_gate_never_invents_a_threshold_on_the_chart():
    source={'gate_threshold':50,'pool':[dict(name='估值ETF',pe_pct=47.12,gate_enabled=False)]}
    html=render_valuation(source,'20261118')
    assert '未设估值门' in html and 'data-percentile-meter="current"' in html
    assert '竖线：' not in html and 'border-right:2px' not in html
    del source['gate_threshold']
    source['pool'][0]['gate_enabled']=True
    html=render_valuation(source,'20261118')
    assert '估值门状态未知' in html and '竖线：' not in html


@pytest.mark.parametrize('pct',[-1,101])
def test_out_of_range_percentiles_are_reported_instead_of_drawn_as_valid(pct):
    source={'gate_threshold':50,'pool':[dict(name='估值ETF',pe_pct=pct)]}
    html=render_valuation(source,'20261118')
    assert '分位超出0–100%，请核对' in html and 'data-percentile-meter=' not in html


def test_compact_valuation_has_one_meter_per_etf_and_shared_dates_only_once():
    source={'gate_threshold':50,'pool':[dict(name=name,ts_code=code,last_date='20261118',
        pe_date='20261118',previous_pe_date='20261117',pe_ttm=13.53,previous_pe_ttm=13.48,
        pe_pct=pct,previous_pe_pct=pct-1,parts_bought=1,n_parts=10,budget_remaining=18000,
        next_desc='等待下一触发') for name,code,pct in (
            ('沪深300','510300.SH',75.84),('上证50','510050.SH',96.48),('中证1000','512100.SH',47.12))]}
    before=copy.deepcopy(source)
    html=render_valuation(source,'20261120')
    assert html.count('data-percentile-meter="current"')==3
    assert html.count('行情/PE 20261118')==1 and html.count('对比 20261117')==1
    assert 'data-percentile-meter="previous"' not in html and '阶段／下一触发' not in html
    for e in source['pool']:
        assert e['name'] in html and e['ts_code'] in html
    assert source==before


def test_old_summary_is_enriched_read_only_from_matching_state(tmp_path):
    (tmp_path/'etf_config.json').write_text(json.dumps({'etf_list':[dict(name='旧ETF',strategy='v6')]}))
    path=tmp_path/'dca_state_v6_旧ETF.json';path.write_text(json.dumps({'trades':[trade()]}))
    before=path.read_bytes()
    source={'etf_list':[dict(name='旧ETF')]}
    assert len(enrich_archived_summary(source,tmp_path)['etf_list'][0]['recent_trades'])==1
    assert path.read_bytes()==before
    path=tmp_path/'dca_state_4pct_旧ETF.json'
    path.write_text(json.dumps(dict(last_date='20261028',pe_date='20261027',trades=[trade('20261027')],
                                    state=dict(in_round=False,wallet=21000))))
    before=path.read_bytes()
    value=enrich_valuation_summary({'pool':[dict(name='旧ETF',last_date='20261028')]},tmp_path)['pool'][0]
    assert value['budget_remaining']==21000 and value['pe_date']=='20261027'
    assert value['recent_trades']==[trade('20261027')] and path.read_bytes()==before


def test_archived_weekly_summary_follows_enabled_configuration_without_rewriting_history(tmp_path):
    config={'base_amount':1000,'etf_list':[
        dict(name='纳指100',strategy='v6h',v6h_params=dict(base_amount=3000,round_budget=15000)),
        dict(name='标普500',strategy='v6h'),
        dict(name='已禁用ETF',enabled=False),
    ]}
    (tmp_path/'etf_config.json').write_text(json.dumps(config))
    source={'etf_list':[dict(name=name,recent_trades=[trade()],previous_weekly_j=5)
                        for name in ('纳指100','标普500','已移除ETF','已禁用ETF')]}
    before=copy.deepcopy(source)
    result=enrich_archived_summary(source,tmp_path)
    assert [e['name'] for e in result['etf_list']]==['纳指100','标普500']
    assert [e['scheduled_investment_amount'] for e in result['etf_list']]==[3000,1000]
    assert result['etf_list'][0]['recent_trades'][0]['amount']==1000
    assert source==before


def test_removed_weekly_etf_is_absent_from_email_while_separate_daily_strategy_remains(monkeypatch,tmp_path):
    monkeypatch.chdir(tmp_path)
    config={'etf_list':[dict(name='测试ETF',strategy='v6h',v6h_params=dict(base_amount=3000))]}
    (tmp_path/'etf_config.json').write_text(json.dumps(config))
    source=weekly()
    removed=copy.deepcopy(source['etf_list'][0])
    removed['name']='不再跟踪的ETF'
    source['etf_list'].append(removed)
    monkeypatch.setattr(mail,'load_reports_json',lambda:dict(latestDate='20261030'))
    monkeypatch.setattr(mail,'load_dca_summary',lambda:source)
    monkeypatch.setattr(mail,'load_strategy_state',lambda:dict(last_trade_date='20261030',trades=[trade(amount=2000)]))
    monkeypatch.setattr(mail,'load_4pct_summary',lambda:{})
    monkeypatch.setattr(mail,'load_multi_indicator_hints',lambda:[])
    html=mail.generate_email_report(as_of='2026-10-30 20:00')
    assert '不再跟踪的ETF' not in html
    assert '待完成 1 轮' in html and '常规定投 ¥3,000/周' in html
    assert '中证2000' in html and '情绪反弹' in html


@pytest.mark.parametrize('when,current_count,week_count',[
    ('2026-10-30 20:00',2,5),('2026-11-01 18:00',2,5),('2026-11-02 20:00',0,0),
    ('2026-10-30T16:00:00Z',1,0),('2026-11-08 18:00',0,0),
])
def test_current_signals_are_separate_from_week_review(monkeypatch,tmp_path,when,current_count,week_count):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mail,'load_reports_json',lambda:dict(latestDate='20261030'))
    monkeypatch.setattr(mail,'load_strategy_state',lambda:dict(last_trade_date='20261030',
                        trades=[trade('20261028','SELL',500),trade(amount=2000)]))
    monkeypatch.setattr(mail,'load_dca_summary',lambda:weekly())
    monkeypatch.setattr(mail,'load_4pct_summary',lambda:{'pool':[dict(name='估值ETF',last_date='20261030',
                         recent_trades=[trade('20261029')],today_action=None)]})
    monkeypatch.setattr(mail,'load_multi_indicator_hints',lambda:[])
    html=mail.generate_email_report(as_of=when)
    sections=re.findall(r'<section[^>]*>(.*?)</section>',html,re.S)
    current,history=sections[0],sections[-1]
    assert '本次操作' in current
    assert '本周操作汇总' in history
    assert current.count('data-trade-action=')==current_count
    assert history.count('data-trade-action=')==week_count
    if not current_count:
        assert '本次无新增操作' in current
    if when=='2026-11-01 18:00':
        assert '周五已确认信号（2026-10-30），供下周操作准备' in current
        assert '20261030' in current and '20261028' not in current and '20261029' not in current
    if when=='2026-11-08 18:00':
        assert '尚无本周新数据' in current and '供下周操作准备' not in current
    assert html.index('选股与行业提示') < html.index('本周操作汇总')
    if week_count:
        for text in ('2026-10-26 至 2026-11-01','20261027','20261028','20261029','20261030','含本次已列记录'):
            assert text in history
    elif report_time(when).weekday() not in (4,6):
        assert '周五、周日提供本周操作汇总' in history
    else:
        assert '本周暂无操作记录' in history


def test_full_digest_has_one_reminder_and_compact_holdings(monkeypatch,tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mail,'load_reports_json',lambda:dict(latestDate='20261030',totalStocks=3))
    monkeypatch.setattr(mail,'load_strategy_state',lambda:dict(last_trade_date='20261030',position=9,trades=[trade(amount=2000)]))
    monkeypatch.setattr(mail,'load_dca_summary',lambda:weekly())
    monkeypatch.setattr(mail,'load_4pct_summary',lambda:{'pool':[dict(name='估值ETF',ts_code='510300.SH',last_date='20261030',n_parts=10,parts_bought=0)]})
    monkeypatch.setattr(mail,'load_multi_indicator_hints',lambda:[])
    html=mail.generate_email_report(as_of='2026-11-01 18:00',repo='<repo>')
    assert html.count('同一信号仅执行一次')==1
    for text in ['本周信号复核','情绪反弹','估值定投','周线定投','2,000','1,000','记录日','&lt;repo&gt;']:
        assert text in html
    sections=re.findall(r'<section[^>]*>(.*?)</section>',html,re.S)
    for action_section in (sections[0],sections[-1]):
        assert '000001.SH' in action_section and '563300.SH' in action_section
    for title,code in [('市场情绪与持仓','563300.SH'),('周线定投','000001.SH'),('估值定投 · 独立检查','510300.SH')]:
        assert code in next(s for s in sections if re.search(r'<h2[^>]*>'+re.escape(title)+r'</h2>',s))
    assert '0x' not in html and '建议买入0元' not in html
    monday=mail.generate_email_report(as_of='2026-11-02 20:00')
    assert '本次无新增操作' in monday


def test_market_archive_parser_never_uses_newer_reference_day(tmp_path):
    path=tmp_path/'html/20261030';path.mkdir(parents=True)
    (path/'first_j13_step_daily_count.html').write_text('xAxis:{data:["2026-10-29","2026-10-30","2026-11-02"]},series:[{data:[10,20,999]}]')
    (path/'sentiment_rebound_strategy.html').write_text('90%分位数</div><div>15</div>')
    c=load_market_context(tmp_path,'20261030')
    assert c['j13_count']==20 and c['change']==10 and c['strategy_trigger'] is True


@pytest.mark.parametrize('export_fails',[False,True])
def test_sentiment_entry_exports_context_without_interrupting_strategy(monkeypatch,tmp_path,capsys,caplog,export_fails):
    import importlib
    from types import SimpleNamespace
    from unittest.mock import Mock, patch
    import numpy as np
    with patch('config.APIConfig.get_token',return_value='offline-test'), \
            patch('tushare.set_token'), patch('tushare.pro_api'):
        runner=importlib.import_module('main_par2')
    monkeypatch.chdir(tmp_path)
    strategy=Mock()
    strategy.etf_code='563300.SH'
    strategy.calculate_j13_stats.return_value=dict(current=1,percentile_90=.9,mean=1,
                                                    is_above_threshold=np.bool_(True))
    strategy.generate_current_signal.return_value=[]
    strategy.get_position_summary.return_value=dict(investment_level=0,position=1,red_bar_count=0,total_trades=0)
    monkeypatch.setattr(runner,'SentimentReboundStrategy',lambda **kw:strategy)
    quotes=pd.DataFrame(dict(trade_date=['20261028','20261029','20261030'],
                             open=1.,close=1.,high=1.,low=1.))
    monkeypatch.setattr(runner,'pro',SimpleNamespace(fund_daily=lambda **kw:quotes))
    monkeypatch.setattr(runner,'calculate_zhixing_brick_indicator',lambda frame:frame.assign(
                        zhixing_brick=1.,zhixing_brick_rising=True,zhixing_brick_xg=False))
    report=Mock()
    monkeypatch.setattr(runner,'generate_strategy_report',report)
    monkeypatch.setattr('sentiment_rebound_strategy.generate_etf_chart_data',lambda *a,**kw:None)
    if export_fails:
        def unavailable(*args,**kwargs):
            raise OSError('context file unavailable')
        monkeypatch.setattr(runner,'open',unavailable,raising=False)
    data=pd.DataFrame(dict(trade_date=['20261028','20261029','20261030'],kdj_qfq=[1,20,1]))
    runner.run_sentiment_rebound_strategy(data,'20261030',None,as_of='2026-10-30 20:00')
    strategy.generate_current_signal.assert_called_once()
    strategy.save_state.assert_called_once()
    report.assert_called_once()
    assert '情绪反弹策略执行失败' not in capsys.readouterr().out
    if export_fails:
        assert '继续执行策略' in caplog.text
    else:
        context=json.loads((tmp_path/'html/sentiment_context.json').read_text())
        assert context['data_date']=='20261030' and context['previous_count']==0
        assert context['change_pct'] is None and context['strategy_threshold']==.9
        assert context['strategy_trigger'] is True


def test_valuation_producer_exports_exact_previous_observation(monkeypatch):
    import dca_4pct_strategy as strategy
    d=pd.DataFrame(dict(trade_date=['20261028','20261029','20261030'],close_qfq=[100.,100.,99.]))
    pe=pd.DataFrame(dict(trade_date=['20261028','20261029','20261030','20261102'],
                         pe_ttm=[13.,13.2,13.5,999.],pe_pct=[.31,.32,.33,.99]))
    monkeypatch.setattr(strategy,'load_etf_daily',lambda *a,**kw:d)
    monkeypatch.setattr(strategy,'load_index_pe',lambda *a,**kw:pe)
    monkeypatch.setattr(strategy,'pe_percentile',lambda frame:frame)
    result=strategy.advance(dict(ts_code='510300.SH',name='测试',index_code='000300.SH',n_parts=10),
                            '20261028','20261030',20000)
    assert result['pe_date']=='20261030'
    assert result['previous_pe_date']=='20261029'
    assert result['previous_pe_ttm']==13.2 and result['previous_pe_pct']==32
    assert result['budget_remaining']==18000
    assert [(t['date'],t['action'],t['amount']) for t in result['recent_trades']]==[('20261028','BUY',2000)]


def test_weekly_summary_exports_events_and_confirmed_comparison(monkeypatch,tmp_path):
    from types import SimpleNamespace
    import weekly_dca_strategy as strategy
    monkeypatch.chdir(tmp_path)
    out=tmp_path/'output';out.mkdir()
    dates=pd.bdate_range('2026-07-01','2026-10-30').strftime('%Y%m%d')
    d=pd.DataFrame(dict(trade_date=dates,open_qfq=10.,close_qfq=10.,high_qfq=11.,low_qfq=9.))
    w=strategy.resample_to_weekly(d,as_of='2026-10-30 20:00')
    records=[trade('20260901'),trade('20261027'),trade('20261030')]
    s=SimpleNamespace(name='测试ETF',total_invested=3000,total_sell_amount=0,shares=300,trades=records,
                      _daily_df=d,_weekly_df=w,
                      _nav_curve=pd.DataFrame([dict(trade_date='20261030',cum_invested=3000,total_value=3000)]),
                      get_next_action=lambda *a:dict(last_date='20261030',signal_week_date='20261030',weekly_j=50))
    result=strategy._generate_dca_summary({'测试ETF':s},str(out))['etf_list'][0]
    assert [t['date'] for t in result['recent_trades']]==['20261027','20261030']
    assert result['previous_week_date']=='20261023'
    assert result['previous_weekly_j'] is not None
    assert s.trades==records
