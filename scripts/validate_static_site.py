#!/usr/bin/env python3
"""Validate public links, indexes, assets and every lazy chart payload offline."""
import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from generate_reports_json import REPORT_TYPES, CSV_TYPES
from static_site import public_pages


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.charts = None
        self.stock_count = None
        self.external_runtime = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('a', 'link') and attrs.get('href'):
            self.links.append(attrs['href'])
        if tag == 'script' and attrs.get('src'):
            self.links.append(attrs['src'])
            if attrs['src'].startswith(('http:', 'https:', '//')):
                self.external_runtime.append(attrs['src'])
        if tag == 'body':
            self.charts = attrs.get('data-chart-base')
            if attrs.get('data-echarts-src'):
                self.links.append(attrs['data-echarts-src'])
        if tag == 'meta' and attrs.get('name') == 'report-stock-count':
            self.stock_count = int(attrs['content'])


def strict_json(path):
    def reject(value):
        raise ValueError(f'Non-JSON number {value}')
    return json.loads(path.read_text(encoding='utf-8'), parse_constant=reject)


def validate_site(root):
    root = Path(root).resolve()
    errors, payload_count = [], 0
    pages = public_pages(root)
    for path in pages:
        try:
            content = path.read_text(encoding='utf-8')
            page = Page(); page.feed(content)
            for link in page.links:
                url = urlsplit(link)
                if url.scheme or url.netloc or not url.path:
                    continue
                target = (path.parent / unquote(url.path)).resolve()
                if not target.is_relative_to(root) or not target.is_file():
                    errors.append(f'{path.relative_to(root)}: missing or non-public link {link}')
            if page.external_runtime:
                errors.append(f'{path.relative_to(root)}: external runtime dependencies')
            if 'fonts.googleapis.com' in content or 'cdn.tailwindcss.com' in content:
                errors.append(f'{path.relative_to(root)}: external font or runtime CSS')
            if re.search(r'const (?:stockCharts|chartsData)\s*=\s*\{[^}]', content):
                errors.append(f'{path.relative_to(root)}: inline stock history remains')
            if page.charts:
                directory = (path.parent / page.charts).resolve()
                if not directory.is_relative_to(root):
                    raise ValueError('Chart path escapes public directory')
                manifest = strict_json(directory / 'manifest.json')
                if len(set(manifest)) != len(manifest) or page.stock_count != len(manifest):
                    raise ValueError('Chart manifest count differs from report count')
                for code in manifest:
                    if not re.fullmatch(r'[A-Za-z0-9_.-]+', code):
                        raise ValueError('Unsafe stock code')
                    data = strict_json(directory / f'{code}.json')
                    n = len(data['dates'])
                    if len(data['candlestick']) != n or len(data['volume']) != n:
                        raise ValueError(f'{code}: inconsistent primary series lengths')
                    for key in ('kdj_k','kdj_d','kdj_j','ma60','price_change','macd_dif','macd_dea','macd_bar',
                                'zhixing_duokong','zhixing_mid_duokong'):
                        if data.get(key) and len(data[key]) != n:
                            raise ValueError(f'{code}: inconsistent {key} length')
                    if any(len(c) != 5 for c in data['candlestick']):
                        raise ValueError(f'{code}: malformed candle')
                    if any('value' not in value for value in data['volume']):
                        raise ValueError(f'{code}: missing volume value')
                    payload_count += 1
        except (ValueError, OSError, KeyError, TypeError) as error:
            errors.append(f'{path.relative_to(root)}: {error}')
    try:
        index = strict_json(root / 'reports.json')
        if index.get('schemaVersion') != 2:
            errors.append('reports.json: unsupported schema version')
        reports = index['reports']
        if index['totalReports'] != len(reports):
            errors.append('reports.json: invalid totalReports')
        if [r['date'] for r in reports] != sorted({r['date'] for r in reports}, reverse=True):
            errors.append('reports.json: dates are not unique and descending')
        for report in reports:
            for key in (*REPORT_TYPES, *CSV_TYPES):
                if key not in report:
                    errors.append(f"reports.json: {report['date']} missing {key}")
                value = report.get(key)
                if value and not (root / value).is_file():
                    errors.append(f'reports.json: missing output {value}')
        if reports and index['latestDate'] != reports[0]['date']:
            errors.append('reports.json: latestDate mismatch')
        for key in ('totalStocks','c154Stocks','c432Stocks','macdStocks','multiIndicatorStocks'):
            value = index[key]
            if value is not None and (type(value) is not int or value < 0):
                errors.append(f'reports.json: invalid {key}')
        summary_path = root / 'html/dca/dca_summary.json'
        if summary_path.exists():
            for etf in strict_json(summary_path)['etf_list']:
                report = etf.get('report_file', '')
                target = (summary_path.parent / report).resolve()
                if not report or not target.is_relative_to(root) or not target.is_file():
                    errors.append(f'dca_summary.json: missing ETF report {report}')
    except (ValueError, OSError, KeyError, TypeError) as error:
        errors.append(f'index: {error}')
    return {'pages': len(pages), 'stock_payloads': payload_count, 'errors': errors}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='.')
    args = parser.parse_args()
    result = validate_site(args.root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(bool(result['errors']))
