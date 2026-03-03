#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import subprocess
import pandas as pd
import re
from pathlib import Path
from datetime import datetime
import os
import sys
from collections import defaultdict

# ---------- 参数区 ----------
STRATEGY_FILE = "main_par2.py"
LIST_FILE     = "validate_list.csv"
LOG_DIR       = Path("logs")
SUMMARY_FILE  = "result.csv"
DETAIL_FILE   = "result_detail.csv"
REPORT_FILE   = "validation_report.txt"
KEYWORD_HIT   = "✅ 符合所有条件"
# ----------------------------

LOG_DIR.mkdir(exist_ok=True)

# 检查项列表（按顺序）
CHECK_ITEMS = [
    "基础技术指标",
    "K线形态",
    "振幅",
    "阶梯放量策略",
    "放量",
    "异动",
    "成交额排名",
    "底部暴力K",
    "派发信号",
    "知行多空线",
    "次新股",
]


def parse_debug_output(log_txt: str) -> dict:
    """
    解析debug输出，提取每个检查项的结果
    """
    result = {
        'all_passed': KEYWORD_HIT in log_txt,
        'checks': {},
        'failed_items': [],
        'passed_count': 0,
        'total_count': 0,
        'summary': {}
    }
    
    # 解析每个检查项
    check_patterns = [
        (r'📌 1\. 基础技术指标.*?通过.*?\| (.*?)(?:\n|$)', '基础技术指标'),
        (r'📌 2\. K线形态检查.*?\n.*?\n.*?\n  (✅|❌).*?\| K线形态可接受', 'K线形态'),
        (r'📌 3\. 振幅检查.*?\n  (✅|❌).*?\| 振幅符合要求', '振幅'),
        (r'📌 4\. 阶梯放量策略.*?\n.*?\n  (✅|❌).*?\| first_j13_step 标记', '阶梯放量策略'),
        (r'📌 5\. 放量检查.*?\n  (✅|❌).*?\| 周期内曾放量', '放量'),
        (r'📌 6\. 异动检查.*?\n  (✅|❌).*?\| 周期内曾异动', '异动'),
        (r'📌 7\. 成交额排名检查.*?\n  (✅|❌).*?\| 成交额在前60%', '成交额排名'),
        (r'📌 8\. 底部暴力K检查.*?\n.*?\n  (✅|❌).*?\| 周期内有底部暴力K', '底部暴力K'),
        (r'📌 9\. 派发信号检查.*?无派发信号.*?\n.*?\n.*?\n  (✅|❌).*?\| 无派发信号', '派发信号'),
        (r'📌 10\. 知行多空线检查.*?\n.*?\n.*?\n.*?\n  (✅|❌).*?\| 知行中期多空线', '知行多空线'),
        (r'📌 11\. 次新股检查.*?\n  (✅|❌).*?\| 非次新股', '次新股'),
    ]
    
    for pattern, name in check_patterns:
        match = re.search(pattern, log_txt, re.DOTALL)
        if match:
            status = '✅' in match.group(0)
            result['checks'][name] = status
            if status:
                result['passed_count'] += 1
            else:
                result['failed_items'].append(name)
            result['total_count'] += 1
    
    # 解析汇总信息
    summary_match = re.search(r'通过: (\d+)/(\d+) 项', log_txt)
    if summary_match:
        result['summary']['passed'] = int(summary_match.group(1))
        result['summary']['total'] = int(summary_match.group(2))
    
    # 提取未通过条件列表
    failed_section = re.search(r'未通过条件:(.*?)(?=\n\n|\Z)', log_txt, re.DOTALL)
    if failed_section:
        failed_lines = re.findall(r'❌ (.+)', failed_section.group(1))
        result['summary']['failed_list'] = failed_lines
    
    return result


def run_one(code: str, date_str: str, days: int):
    """
    运行单只股票验证
    """
    cmd = [sys.executable, STRATEGY_FILE,
           "--date", date_str,
           "--days", str(days),
           "--debug", code]

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    p = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        env=env
    )
    full_log = p.stdout
    
    # 解析详细结果
    parsed = parse_debug_output(full_log)
    hit = parsed['all_passed']
    
    return hit, full_log, parsed


def generate_report(results: list, strategy_stats: dict):
    """
    生成验证报告
    """
    lines = []
    lines.append("=" * 80)
    lines.append("股票策略验证报告")
    lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 80)
    lines.append("")
    
    # 总体统计
    total = len(results)
    passed = sum(1 for r in results if r['actual_hit'])
    failed = total - passed
    
    lines.append("【总体统计】")
    lines.append(f"  验证总数: {total}")
    lines.append(f"  通过: {passed} ({passed/total*100:.1f}%)")
    lines.append(f"  失败: {failed} ({failed/total*100:.1f}%)")
    lines.append("")
    
    # 按策略统计
    lines.append("【按策略统计】")
    for strategy, stats in sorted(strategy_stats.items()):
        s_total = stats['total']
        s_passed = stats['passed']
        s_failed = stats['failed']
        lines.append(f"  {strategy}:")
        lines.append(f"    总数: {s_total}, 通过: {s_passed} ({s_passed/s_total*100:.1f}%), 失败: {s_failed}")
    lines.append("")
    
    # 失败详情
    lines.append("【失败详情】")
    failed_items = [r for r in results if not r['actual_hit']]
    if failed_items:
        for item in failed_items:
            lines.append(f"  ❌ {item['name']} ({item['code']}) - {item['date']}")
            lines.append(f"     策略: {item['expect_strategy']}")
            if item.get('failed_items'):
                lines.append(f"     未通过项: {', '.join(item['failed_items'])}")
            lines.append("")
    else:
        lines.append("  无 - 所有验证均通过！")
    lines.append("")
    
    # 通过详情
    lines.append("【通过详情】")
    passed_items = [r for r in results if r['actual_hit']]
    for item in passed_items:
        lines.append(f"  ✅ {item['name']} ({item['code']}) - {item['date']} - {item['expect_strategy']}")
    
    lines.append("")
    lines.append("=" * 80)
    
    return '\n'.join(lines)


def main():
    try:
        df = pd.read_csv(LIST_FILE, dtype=str)
    except FileNotFoundError:
        print("[E] 找不到列表文件", LIST_FILE)
        return

    results = []
    strategy_stats = defaultdict(lambda: {'total': 0, 'passed': 0, 'failed': 0})
    
    print("\n" + "=" * 80)
    print("开始批量验证...")
    print("=" * 80 + "\n")
    
    for idx, row in df.iterrows():
        code, name, date_str = row.code, row.name, row.date_str
        days = int(row.days)
        expect_strategy = row.get('expect_strategy', '未知')
        
        print(f"[{idx+1}/{len(df)}] 验证 {name} ({code}) - {date_str}...", end=' ')
        
        hit, log_txt, parsed = run_one(code, date_str, days)

        # 保存日志
        log_file = LOG_DIR / f"{code}_{date_str}.log"
        log_file.write_text(log_txt, encoding="utf-8")

        # 记录结果
        result = {
            "code": code,
            "name": name,
            "date": date_str,
            "days": days,
            "expect_strategy": expect_strategy,
            "actual_hit": hit,
            "passed_count": parsed.get('summary', {}).get('passed', 0),
            "total_count": parsed.get('summary', {}).get('total', 0),
            "failed_items": parsed.get('failed_items', []),
        }
        results.append(result)
        
        # 更新策略统计
        strategy_stats[expect_strategy]['total'] += 1
        if hit:
            strategy_stats[expect_strategy]['passed'] += 1
        else:
            strategy_stats[expect_strategy]['failed'] += 1
        
        # 显示结果
        tag = "✅ PASS" if hit else "❌ FAIL"
        detail = f"({parsed.get('summary', {}).get('passed', 0)}/{parsed.get('summary', {}).get('total', 0)})"
        print(f"{tag} {detail}")
        
        if not hit and parsed.get('failed_items'):
            print(f"      未通过: {', '.join(parsed['failed_items'][:3])}")

    # 保存详细结果
    df_result = pd.DataFrame(results)
    df_result.to_csv(SUMMARY_FILE, index=False, encoding="utf-8")
    
    # 保存展开的检查项详情
    detail_rows = []
    for r in results:
        row = {
            'code': r['code'],
            'name': r['name'],
            'date': r['date'],
            'expect_strategy': r['expect_strategy'],
            'actual_hit': r['actual_hit'],
            'passed_count': r['passed_count'],
            'total_count': r['total_count'],
            'failed_items': '|'.join(r['failed_items']) if r['failed_items'] else ''
        }
        detail_rows.append(row)
    
    pd.DataFrame(detail_rows).to_csv(DETAIL_FILE, index=False, encoding="utf-8")
    
    # 生成报告
    report = generate_report(results, strategy_stats)
    Path(REPORT_FILE).write_text(report, encoding="utf-8")
    
    # 输出汇总
    print("\n" + "=" * 80)
    print("验证完成!")
    print("=" * 80)
    print(f"\n结果文件:")
    print(f"  - 汇总: {SUMMARY_FILE}")
    print(f"  - 详情: {DETAIL_FILE}")
    print(f"  - 报告: {REPORT_FILE}")
    print(f"  - 日志: {LOG_DIR}/")
    
    print(f"\n{report}")

if __name__ == "__main__":
    main()
