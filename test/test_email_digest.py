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
from email_digest import (collect_events, market_context, render_holdings, render_valuation,
                          report_time, weekly_comparison, enrich_archived_summary,
                          enrich_valuation_summary, load_market_context)


def trade(date='20261030', action='BUY', amount=1000, **kw):
    return dict(date=date,action=action,amount=amount,round=1,reason='trigger',**kw)


def weekly(records=None):
    return {'etf_list':[dict(name='测试ETF',ts_code='000001.SH',last_date='20261030',
        signal_week_date='20261030',weekly_j=11,previous_weekly_j=-4,last_price=10,
        recent_trades=records if records is not None else [trade(),trade('20261027','SELL',500)],
        positions=[dict(shares=100,avg_cost=10,budget_remaining=0,action='buy',action_label='建议定投(0x)')])]}


@pytest.mark.parametrize('when,count',[
    ('2026-10-26 20:00',0),('2026-10-27 20:00',0),('2026-10-28 20:00',0),
    ('2026-10-29 20:00',0),('2026-10-30 20:00',2),('2026-10-31 20:00',0),
    ('2026-11-01 18:00',2),('2026-10-30T16:00:00Z',0),
])
def test_weekly_notifications_only_friday_sunday(when,count):
    assert len(collect_events(weekly(),{}, {},'20261030',when))==count


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
    assert '预算已用完' in render_holdings(source)
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
    html=render_holdings(weekly())
    assert '前周 -4.00' in html and '+15.00点' in html


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
    assert current.count('<tr>')==(current_count+1 if current_count else 0)
    assert history.count('<tr>')==(week_count+1 if week_count else 0)
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
    for text in ['本周信号复核','情绪反弹','估值定投','ETF持仓简表','2,000','1,000','记录日','&lt;repo&gt;']:
        assert text in html
    sections=re.findall(r'<section[^>]*>(.*?)</section>',html,re.S)
    for action_section in (sections[0],sections[-1]):
        assert '000001.SH' in action_section and '563300.SH' in action_section
    for title,code in [('市场情绪与持仓','563300.SH'),('ETF持仓简表','000001.SH'),('估值定投 · 独立检查','510300.SH')]:
        assert code in next(s for s in sections if title in s)
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
    monkeypatch.setattr(runner,'pro',SimpleNamespace(fund_daily=lambda **kw:None))
    report=Mock()
    monkeypatch.setattr(runner,'generate_strategy_report',report)
    monkeypatch.setattr('sentiment_rebound_strategy.generate_etf_chart_data',lambda *a,**kw:None)
    if export_fails:
        def unavailable(*args,**kwargs):
            raise OSError('context file unavailable')
        monkeypatch.setattr(runner,'open',unavailable,raising=False)
    data=pd.DataFrame(dict(trade_date=['20261028','20261029','20261030'],kdj_qfq=[1,20,1]))
    runner.run_sentiment_rebound_strategy(data,'20261030',None)
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
