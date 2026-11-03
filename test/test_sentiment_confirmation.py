"""Live runs may advance state only with matching, completed daily inputs."""
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sentiment_confirmation import confirmation_error
from sentiment_rebound_strategy import SentimentReboundStrategy
from email_digest import load_market_context, render_market


@pytest.mark.parametrize('when,market,quote,price,allowed',[
    ('2026-11-02 09:44','20261030','20261030',1.,False),
    ('2026-11-02 14:59:59','20261102','20261102',1.,False),
    ('2026-11-02 15:00:00','20261102','20261102',1.,True),
    ('2026-11-02T06:59:59Z','20261102','20261102',1.,False),
    ('2026-11-02T07:00:00Z','20261102','20261102',1.,True),
    ('2026-11-02 20:00','20261030','20261102',1.,False),
    ('2026-11-02 20:00','20261102','20261030',1.,False),
    ('2026-11-02 20:00','20261102',None,1.,False),
    ('2026-11-02 20:00','20261102','20261102',0.,False),
    ('2026-11-02 20:00','20261102','20261102',float('nan'),False),
    ('2026-11-01 20:00','20261102','20261102',1.,False),
    ('2026-11-03 09:00','20261102','20261102',1.,True),
])
def test_close_and_input_dates(when,market,quote,price,allowed):
    assert (confirmation_error('20261102',market,quote,price,as_of=when) is None)==allowed


@pytest.fixture
def live_entry(tmp_path,monkeypatch):
    with patch('config.APIConfig.get_token',return_value='offline-test'), \
            patch('tushare.set_token'), patch('tushare.pro_api'):
        runner=importlib.import_module('main_par2')
    monkeypatch.chdir(tmp_path)
    state=dict(position=9.,position_cost=2000.,investment_level_idx=1,
               red_bar_count=0,green_bar_count=2,last_trade_date='20261030',last_bar_date='20261030',
               trades=[dict(date='20261030',action='BUY',type='倍投',amount=2000,level=1,reason='prior')])
    path=tmp_path/'strategy_state.json'
    path.write_text(json.dumps(state))
    data=pd.DataFrame(dict(trade_date=['20261029']*2+['20261030']*3,kdj_qfq=1.))
    quote=pd.DataFrame(dict(trade_date=['20261030'],open=1.,close=1.,high=1.,low=1.))
    inputs={'quote':quote}
    monkeypatch.setattr(runner,'pro',SimpleNamespace(fund_daily=lambda **kw:inputs['quote']))
    monkeypatch.setattr(runner,'calculate_zhixing_brick_indicator',lambda frame:frame.assign(
                        zhixing_brick=1.,zhixing_brick_rising=frame['trade_date'].eq('20261102'),
                        zhixing_brick_xg=False))
    report=Mock()
    monkeypatch.setattr(runner,'generate_strategy_report',report)
    return runner,path,data,inputs,report


def fresh_market(data,count=1):
    today=pd.DataFrame(dict(trade_date=['20261102']*max(count,1),kdj_qfq=1. if count else 20.))
    return pd.concat([data,today],ignore_index=True)


def fresh_quote():
    return pd.DataFrame(dict(trade_date=['20261102'],open=1.,close=1.,high=1.,low=1.))


@pytest.mark.parametrize('case',['intraday','partial_today','stale_market','stale_price','missing_price'])
def test_unconfirmed_live_run_leaves_state_byte_identical(live_entry,case):
    runner,path,data,inputs,report=live_entry
    before=path.read_bytes()
    when='2026-11-02 20:00'
    if case in ('intraday','partial_today'):
        when='2026-11-02 09:44'
    if case!='intraday' and case!='stale_market':
        data=fresh_market(data,count=4)
    if case in ('partial_today','stale_market'):
        inputs['quote']=fresh_quote()
    if case=='missing_price':
        inputs['quote']=None
    runner.run_sentiment_rebound_strategy(data,'20261102',None,as_of=when)
    assert path.read_bytes()==before
    assert report.call_args.args[1]==[]
    context=json.loads(Path('html/sentiment_context.json').read_text())
    assert context['signal_status'] and context['strategy_trigger'] is None
    displayed=load_market_context(Path('.'),'20261102')
    assert displayed['data_date']==context['data_date']
    assert context['signal_status'] in render_market(displayed)
    assert '已触发' not in render_market(displayed)


def test_morning_cannot_block_evening_and_evening_repeat_is_idempotent(live_entry):
    runner,path,data,inputs,report=live_entry
    before=path.read_bytes()
    runner.run_sentiment_rebound_strategy(data,'20261102',None,as_of='2026-11-02 09:44')
    assert path.read_bytes()==before
    inputs['quote']=fresh_quote()
    runner.run_sentiment_rebound_strategy(fresh_market(data),'20261102',None,as_of='2026-11-02 20:00')
    state=json.loads(path.read_text())
    assert state['position']==9 and state['investment_level_idx']==0
    assert state['last_trade_date']==state['last_bar_date']=='20261102'
    assert state['red_bar_count']==1 and state['green_bar_count']==0
    assert len(state['trades'])==1
    runner.run_sentiment_rebound_strategy(fresh_market(data),'20261102',None,as_of='2026-11-02 21:00')
    repeated=json.loads(path.read_text())
    assert {k:v for k,v in repeated.items() if k!='saved_at'}=={k:v for k,v in state.items() if k!='saved_at'}


def test_confirmed_buy_is_preserved_and_not_duplicated(live_entry):
    runner,path,data,inputs,report=live_entry
    inputs['quote']=fresh_quote()
    today=fresh_market(data,count=4)
    for when in ('2026-11-02 20:00','2026-11-02 21:00'):
        runner.run_sentiment_rebound_strategy(today,'20261102',None,as_of=when)
    state=json.loads(path.read_text())
    assert state['position']==11 and state['investment_level_idx']==2
    assert [(t['date'],t['amount']) for t in state['trades']]==[('20261030',2000),('20261102',4000)]


def test_zero_count_session_does_not_reuse_old_trigger_or_change_threshold(live_entry):
    runner,path,data,inputs,report=live_entry
    inputs['quote']=fresh_quote()
    expected=data.groupby('trade_date').size().quantile(.9)
    runner.run_sentiment_rebound_strategy(fresh_market(data,count=0),'20261102',None,as_of='2026-11-02 20:00')
    stats=report.call_args.args[2]
    assert stats['current']==0 and stats['data_date']=='20261102'
    assert stats['percentile_90']==expected and not stats['is_above_threshold']
    state=json.loads(path.read_text())
    assert state['position']==9 and len(state['trades'])==1


def test_future_rows_are_excluded_before_confirmation_and_statistics(live_entry):
    runner,path,data,inputs,report=live_entry
    data=pd.concat([fresh_market(data),pd.DataFrame(dict(trade_date=['20261103']*100,kdj_qfq=1.))])
    inputs['quote']=pd.concat([fresh_quote(),fresh_quote().assign(trade_date='20261103',close=999.)])
    runner.run_sentiment_rebound_strategy(data,'20261102',None,as_of='2026-11-03 20:00')
    stats=report.call_args.args[2]
    assert stats['data_date']==stats['price_date']=='20261102' and stats['current']==1
    assert json.loads(path.read_text())['position']==9


@pytest.mark.parametrize('metadata',[
    dict(data_date='20261030'),dict(price_date='20261030'),dict(signal_status='等待收盘'),
])
def test_core_rejects_unconfirmed_metadata_before_any_mutation(live_entry,metadata):
    _,path,_,_,_=live_entry
    before=path.read_bytes()
    strategy=SentimentReboundStrategy(state_file=str(path))
    signals=strategy.generate_current_signal(dict(current=3036,percentile_90=2577,
                      is_above_threshold=True,**metadata),'20261102',None,1.)
    assert signals==[] and path.read_bytes()==before
    assert strategy.position==9 and strategy.investment_level_idx==1
