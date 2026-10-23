#!/usr/bin/env python3
"""Generate the complete, shared schema consumed by the report homepage."""
import csv
from datetime import datetime
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

REPORT_TYPES = {
    'stockSelection': 'stock_selection_{date}.html',
    'c154Html': 'c154_selection_{date}.html',
    'c432Html': 'c432_selection_{date}.html',
    'macdHtml': 'macd_selection_{date}.html',
    'industryTrend': 'industry_total_amount_trend.html',
    'j13Trend': 'first_j13_step_daily_count.html',
    'sentimentRebound': 'sentiment_rebound_strategy.html',
    'multiIndicator': 'multi_indicator_selection_{date}.html',
}
CSV_TYPES = {'c154Result': 'c154', 'c432Result': 'c432', 'macdResult': 'macd'}


def _stock_count(html_path, csv_path=None):
    if html_path:
        text = Path(html_path).read_text(encoding='utf-8')
        match = re.search(r'<meta name="report-stock-count" content="(\d+)"', text)
        if not match:
            match = re.search(r'共选出\s*(\d+)\s*只|共\s*(\d+)\s*只', text)
        if match:
            return int(next(g for g in match.groups() if g is not None))
        if any(word in text for word in ['无符合', '未选出', '暂无股票', '没有符合', '无入选']):
            return 0
    if csv_path:
        with Path(csv_path).open(encoding='utf-8-sig', newline='') as stream:
            return sum(1 for row in csv.DictReader(stream) if any(row.values()))
    return None  # Missing output differs from a successfully generated empty report.


def generate_reports_json():
    base = Path('html')
    if not base.exists():
        print('⚠️ html/ 目录不存在，跳过生成索引')
        return
    dates = {p.name for p in base.iterdir()
             if p.is_dir() and re.fullmatch(r'\d{8}', p.name)}
    for prefix in CSV_TYPES.values():
        for path in Path('.').glob(f'{prefix}_result_*.csv'):
            match = re.fullmatch(prefix + r'_result_(\d{8})\.csv', path.name)
            if match:
                dates.add(match[1])
    reports = []
    for date in sorted(dates, reverse=True):
        report = {'date': date}
        for key, pattern in REPORT_TYPES.items():
            path = base / date / pattern.format(date=date)
            report[key] = path.as_posix() if path.is_file() else None
        for key, prefix in CSV_TYPES.items():
            path = Path(f'{prefix}_result_{date}.csv')
            report[key] = path.as_posix() if path.is_file() else None
        reports.append(report)
    latest = reports[0] if reports else {}
    counts = {
        'stockSelection': _stock_count(latest.get('stockSelection')),
        'c154Html': _stock_count(latest.get('c154Html'), latest.get('c154Result')),
        'c432Html': _stock_count(latest.get('c432Html'), latest.get('c432Result')),
        'macdHtml': _stock_count(latest.get('macdHtml'), latest.get('macdResult')),
        'multiIndicator': _stock_count(latest.get('multiIndicator')),
    }
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    data = {
        'schemaVersion': 2,
        'lastUpdate': now.strftime('%Y-%m-%d %H:%M:%S') + ' 北京时间',
        'generatedAt': now.isoformat(timespec='seconds'),
        'totalStocks': counts['stockSelection'],
        'c154Stocks': counts['c154Html'], 'c432Stocks': counts['c432Html'],
        'macdStocks': counts['macdHtml'], 'multiIndicatorStocks': counts['multiIndicator'],
        'latestCounts': counts, 'totalReports': len(reports),
        'latestDate': latest.get('date', '-'), 'reports': reports,
    }
    Path('reports.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"✅ 已生成索引: {len(reports)} 个日期，最新 {data['latestDate']}")
    return data


if __name__ == '__main__':
    generate_reports_json()
