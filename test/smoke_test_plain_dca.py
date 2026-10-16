# -*- coding: utf-8 -*-
"""冒烟测试：直接每周定投（海外宽基）— 用临时配置跑通完整管道"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OUT_DIR = 'test/tmp_plain_out'
os.makedirs(OUT_DIR, exist_ok=True)

test_config = {
    "etf_list": [
        {"ts_code": "513100.SH", "name": "纳指100", "target": "nasdaq_plain",
         "strategy": "plain", "enabled": True},
        {"ts_code": "513500.SH", "name": "标普500", "target": "sp500",
         "strategy": "plain", "enabled": True},
        {"ts_code": "513520.SH", "name": "日经225", "target": "nikkei225",
         "strategy": "plain", "enabled": True},
        {"ts_code": "513030.SH", "name": "德国DAX", "target": "german_dax",
         "strategy": "plain", "enabled": True},
    ],
    "base_amount": 1000,
    "years": 5,
}
cfg_path = os.path.join('test', 'plain_config.json')
with open(cfg_path, 'w', encoding='utf-8') as f:
    json.dump(test_config, f, ensure_ascii=False, indent=2)

import weekly_dca_strategy as wds

results = wds.run_backtest_from_config(
    config_path=cfg_path, output_dir=OUT_DIR)

print("\n" + "=" * 70)
print("冒烟测试结果")
print("=" * 70)
for name, s in results.items():
    na = s.get_next_action(s._daily_df, s._weekly_df)
    print(f"[{name}] 投入={s.total_invested:.0f} 份额={s.shares:.2f} "
          f"成本={s.get_avg_cost():.4f} 交易数={len(s.trades)} "
          f"首笔={s.trades[0]['date'] if s.trades else '-'} "
          f"末笔={s.trades[-1]['date'] if s.trades else '-'}")
    print(f"    建议: {na['action_label']} | {na['action_detail'][:80]}")
    # 校验每周一笔：交易数应约等于周数
    wk = set()
    for t in s.trades:
        wk.add(s._week_key(t['date']))
    assert len(wk) == len(s.trades), f"{name} 同周多笔买入!"
    # 校验 state 文件
    sf = f'dca_state_{name}.json'
    assert os.path.exists(sf), f"{name} 状态文件未生成"

summary_path = os.path.join(OUT_DIR, 'dca_summary.json')
with open(summary_path, 'r', encoding='utf-8') as f:
    summary = json.load(f)
print(f"\n汇总条目数: {len(summary['etf_list'])}")
for item in summary['etf_list']:
    assert item['ts_code'], f"{item['name']} 缺 ts_code 映射"
    print(f"  - {item['name']}({item['ts_code']}) "
          f"投入={item['total_invested']} 收益率={item['return_pct']}% "
          f"建议={item['action_label']} 报告={item['report_file']}")
    assert '依据' not in item['action_detail'], "plain 不应有周线决策标注"
print("\n冒烟测试全部通过 ✔")
