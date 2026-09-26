#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
定投决策账本 (dca_decision_ledger.py)

解决"邮件操作建议每天漂移"的持久化层：每个ETF、每根已收盘周线对应一条
不可变决策记录。一旦某天邮件发出过基于某周收盘周线的建议，后续任何一天的
重放都不得撤销或改写它——只允许在新的一周收盘后追加新记录。

账本文件: html/dca/decision_ledger.json
结构:
{
  "version": 1,
  "etfs": {
    "<etf_name>": {
      "current_week": "20260918",          // 当前生效决策所属的收盘周
      "weeks": {
        "20260918": {
          "signal_week": "20260918",
          "action": "hold_fully_invested",
          "action_label": "R13 预算已投完·持有等卖",
          "weekly_j": 19.81,
          "recorded_at": "2026-09-21 20:00:03",
          "source": "live"
        }
      }
    }
  }
}

使用：
    from dca_decision_ledger import sync_ledger
    summary_data = sync_ledger(summary_data)   # 在 _generate_dca_summary 写盘前调用
"""
import json
import logging
import os
from datetime import datetime

DEFAULT_LEDGER_PATH = os.path.join('html', 'dca', 'decision_ledger.json')


def load_ledger(path: str = DEFAULT_LEDGER_PATH) -> dict:
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict) and 'etfs' in data:
                return data
        except (json.JSONDecodeError, OSError) as e:
            logging.warning(f"[decision_ledger] 账本文件损坏，重建: {e}")
    return {'version': 1, 'etfs': {}}


def save_ledger(ledger: dict, path: str = DEFAULT_LEDGER_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)


def sync_ledger(summary_data: dict, path: str = DEFAULT_LEDGER_PATH) -> dict:
    """将本次回放产生的汇总建议与账本对齐。

    规则：
      1. summary 中每个 ETF 取其 signal_week（决策所依据的已收盘周线日期）。
      2. 账本中已有该周的记录 → 用账本记录覆盖 summary 的 action/label/detail，
         保证"发出去的建议不可撤销"；若回放结果与账本不一致，记警告日志。
      3. 账本中没有 → 追加新记录（source='live'），即本周新确认的决策。
      4. 无 signal_week 的 ETF 跳过（例如数据不足）。

    Returns:
        传入的 summary_data（就地修改并返回，便于链式调用）。
    """
    ledger = load_ledger(path)
    etfs = ledger.setdefault('etfs', {})
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    for item in summary_data.get('etf_list', []):
        name = item.get('name')
        signal_week = item.get('signal_week_date')
        if not name or not signal_week:
            continue
        entry_live = {
            'signal_week': signal_week,
            'action': item.get('action', 'unknown'),
            'action_label': item.get('action_label', ''),
            'weekly_j': item.get('weekly_j', 0),
            'recorded_at': now_str,
            'source': 'live',
        }
        etf_book = etfs.setdefault(name, {'current_week': None, 'weeks': {}})
        weeks = etf_book.setdefault('weeks', {})
        existing = weeks.get(signal_week)
        if existing is None:
            weeks[signal_week] = entry_live
            etf_book['current_week'] = signal_week
            logging.info(f"[decision_ledger] {name} 新决策入账: 周{signal_week} "
                         f"{entry_live['action_label']} (J={entry_live['weekly_j']})")
        else:
            # 已有不可变记录：以账本为准，回放结果只作对照
            if (existing.get('action') != entry_live['action']
                    or existing.get('action_label') != entry_live['action_label']):
                logging.warning(
                    f"[decision_ledger] {name} 周{signal_week} 回放结果与账本不一致: "
                    f"账本='{existing.get('action_label')}' vs 回放='{entry_live['action_label']}'。"
                    f"以账本为准（已发出的建议不可撤销）。")
            item['action'] = existing.get('action', item['action'])
            item['action_label'] = existing.get('action_label', item['action_label'])
            detail = item.get('action_detail', '')
            mark = f"依据{signal_week}收盘周线"
            if mark not in detail:
                item['action_detail'] = f"{detail} ｜ {mark}（已确认）"
            item['decision_source'] = 'ledger'
            etf_book['current_week'] = signal_week

    save_ledger(ledger, path)
    return summary_data


def get_current_decision(etf_name: str, path: str = DEFAULT_LEDGER_PATH) -> dict:
    """读取某 ETF 当前生效的账本决策（供邮件/Widget 使用）。"""
    ledger = load_ledger(path)
    etf_book = ledger.get('etfs', {}).get(etf_name, {})
    week = etf_book.get('current_week')
    if week:
        return etf_book.get('weeks', {}).get(week, {})
    return {}


if __name__ == '__main__':
    # 简单自检：打印当前账本内容
    book = load_ledger()
    for name, etf_book in book.get('etfs', {}).items():
        cur = etf_book.get('current_week')
        entry = etf_book.get('weeks', {}).get(cur, {}) if cur else {}
        print(f"{name}: 当前决策 周{cur} -> {entry.get('action_label', '无')} "
              f"(J={entry.get('weekly_j', '-')}, 记录于{entry.get('recorded_at', '-')})")
