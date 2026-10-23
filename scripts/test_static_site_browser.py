#!/usr/bin/env python3
"""Browser regression of the built site, served at a project subpath.

Requires Playwright and its Chromium browser. Only localhost is reachable from
the test pages. All archived stock dialogs are opened once; the detailed suite
also checks filters, mobile layout, retry, cancellation, cache and resizing.
"""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
from urllib.parse import quote

from playwright.sync_api import sync_playwright


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def translate_path(self, path):
        return super().translate_path(path.removeprefix('/project'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='dist')
    parser.add_argument('--output', default='/tmp/wbzq-site-validation')
    parser.add_argument('--browser')
    parser.add_argument('--skip-archive-charts', action='store_true',
                        help='Repeat only focused interactions after the full archive has passed')
    args = parser.parse_args()
    root, output = Path(args.root).resolve(), Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}/project/'
    results, errors, external = [], [], []
    with sync_playwright() as playwright:
        options = {'headless': True, 'args': ['--no-sandbox']}
        if args.browser:
            options['executable_path'] = args.browser
        browser = playwright.chromium.launch(**options)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000}, locale='zh-CN')
        def route_request(route):
            if route.request.url.startswith(base):
                route.continue_()
            else:
                external.append(route.request.url); route.abort()
        context.route('**/*', route_request)
        context.add_init_script("""
            window.__resizeAdds = 0;
            const originalAdd = window.addEventListener;
            window.addEventListener = function(type, ...args) {
                if (type === 'resize') window.__resizeAdds++;
                return originalAdd.call(this, type, ...args);
            };
        """)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        def check(name, callback):
            before = len(errors)
            try:
                callback()
                if len(errors) != before:
                    raise AssertionError('; '.join(errors[before:]))
                results.append({'test': name, 'passed': True})
            except Exception as error:
                results.append({'test': name, 'passed': False, 'error': str(error)[:1000]})
                page.screenshot(path=str(output / f'failure-{len(results)}.png'), full_page=True)

        def home():
            page.goto(base, wait_until='networkidle')
            assert page.locator('#reportList tr').count() == 20
            assert not page.locator('a[href="null"], a[href="undefined"]').count()
            assert page.locator('#c154Stocks').inner_text() in ('—', '0')
            page.locator('#nextPage').click()
            assert '第 2 /' in page.locator('#pageStatus').inner_text()
            page.locator('#reportSearch').fill('2026-10')
            assert all('2026-10' in text for text in page.locator('#reportList th').all_text_contents())
            page.locator('#reportType').select_option('c154Html')
            assert '没有匹配' in page.locator('#reportList').inner_text()
            page.locator('#reportSearch').fill('')
            assert page.locator('#reportList tr a').count() > 0
            assert all(row.locator('td').nth(1).locator('a').count() for row in page.locator('#reportList tr').all())
            page.locator('#reportType').select_option('')
            page.locator('#dateFrom').fill('2026-10-20'); page.locator('#dateTo').fill('2026-10-22')
            assert page.locator('#reportList tr').count() == 3
            page.reload(wait_until='networkidle')
            assert page.locator('#reportList tr').count() == 3
            page.goto(base, wait_until='networkidle')
            page.screenshot(path=str(output / 'home-desktop.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.locator('#mobileReports').is_visible()
            assert not page.locator('.site-desktop-reports').is_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(output / 'home-mobile.png'), full_page=True)
            page.set_viewport_size({'width': 1440, 'height': 1000})
        check('首页：分页、日期与类型筛选、URL恢复、手机布局', home)

        def home_retry():
            pattern = '**/reports.json'; failures = [True]
            def fail_once(route):
                if failures.pop() if failures else False:
                    route.fulfill(status=503, body='temporarily unavailable')
                else:
                    route.continue_()
            page.route(pattern, fail_once)
            page.goto(base, wait_until='networkidle')
            assert '加载失败' in page.locator('#lastUpdate').inner_text()
            page.locator('#reportList button').click()
            page.wait_for_function("document.querySelector('#reportList tr a') !== null")
            page.unroute(pattern, fail_once)
        check('首页：失败后重试', home_retry)

        def dca():
            page.goto(base + 'html/dca/index.html', wait_until='networkidle')
            data = json.loads((root / 'html/dca/dca_summary.json').read_text())['etf_list']
            names = page.locator('#etfGrid h3').all_text_contents()
            assert len(names) == len(data)
            if any(item['action'] == 'buy' for item in data):
                assert next(item for item in data if item['name'] == names[0])['action'] == 'buy'
        check('ETF：完整渲染与买入优先排序', dca)

        chart_pages = [p for p in [*sorted(root.glob('stock_selection_????????.html')), *sorted((root / 'html').rglob('*.html'))]
                       if 'data-chart-base="' in p.read_text()]
        for number, path in enumerate([] if args.skip_archive_charts else chart_pages, 1):
            def open_archived(path=path):
                requests = []
                listener = lambda request: requests.append(request.url) if '.data/' in request.url else None
                page.on('request', listener)
                try:
                    page.goto(base + quote(path.relative_to(root).as_posix()), wait_until='networkidle')
                    assert not requests, '历史数据在点击前已被下载'
                    manifest = json.loads(path.with_suffix('.data').joinpath('manifest.json').read_text())
                    code = manifest[0]
                    page.evaluate("code => (window.openChart || window.showChart)(code, '测试')", code)
                    page.wait_for_function("document.querySelector('#klineChart canvas') !== null")
                    details = page.evaluate("""() => {
                        const k = echarts.getInstanceByDom(document.getElementById('klineChart')).getOption();
                        const series = k.series.find(s => s.type === 'candlestick');
                        const candle = series.data[0].value || series.data[0];
                        const volume = echarts.getInstanceByDom(document.getElementById('volumeChart')).getOption();
                        const point = volume.series[0].data[0]; const value = point.value ?? point;
                        const tooltip = volume.tooltip[0].formatter;
                        if (tooltip) tooltip([{dataIndex:0,axisValue:'test',value,data:point}]);
                        return {candle, value, count:series.data.length};
                    }""")
                    assert details['candle'][2] <= details['candle'][3], 'K线低/高顺序错误'
                    assert isinstance(details['value'], (int,float)), '成交额为空'
                    assert details['count'] > 0
                    assert len(requests) == 1
                    page.keyboard.press('Escape')
                    assert not page.locator('#chartModal').is_visible()
                finally:
                    page.remove_listener('request', listener)
            check(f'历史股票图表：{path.relative_to(root)}', open_archived)
            if number % 25 == 0:
                print(json.dumps({'stock_pages_checked':number,'total':len(chart_pages),'failures':sum(not r['passed'] for r in results)}), flush=True)

        target = root / 'html/20261022/multi_indicator_selection_20261022.html'
        def modal_behaviour():
            page.goto(base + target.relative_to(root).as_posix(), wait_until='networkidle')
            manifest = json.loads(target.with_suffix('.data').joinpath('manifest.json').read_text())
            code = manifest[0]; requests = []
            listener = lambda request: requests.append(request.url) if '.data/' in request.url else None
            page.on('request', listener)
            try:
                for _ in range(4):
                    page.evaluate('code => openChart(code)', code)
                    page.wait_for_function("document.querySelector('#klineChart canvas') !== null && !document.querySelector('#chartStatus').hidden === false")
                    page.locator('#chartModal .close').click()
                assert len(requests) == 1, '重复打开没有使用缓存'
                assert page.evaluate('window.__resizeAdds') == 1, '重复注册窗口resize'
                page.evaluate('code => openChart(code)', code)
                page.wait_for_function("document.querySelector('#chartStatus').hidden")
                page.set_viewport_size({'width': 900, 'height': 800})
                page.wait_for_function("Math.abs(echarts.getInstanceByDom(document.getElementById('klineChart')).getWidth()-document.getElementById('klineChart').clientWidth)<2")
                page.screenshot(path=str(output / 'chart-desktop.png'))
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(output / 'chart-mobile.png'))
                page.keyboard.press('Escape')
                table = page.locator('table.site-wide-table').first
                table.scroll_into_view_if_needed()
                table.evaluate('(node) => { node.parentElement.scrollLeft = 0; }')
                assert table.locator('tbody tr').first.locator('td').nth(1).evaluate(
                    '(node) => getComputedStyle(node).whiteSpace === "nowrap" && node.offsetHeight < 50')
                page.screenshot(path=str(output / 'stock-table-mobile.png'))
                page.set_viewport_size({'width':1440,'height':1000})
            finally:
                page.remove_listener('request', listener)
        check('弹窗：缓存、重复开关、缩放、Esc、手机布局', modal_behaviour)

        def chart_retry():
            page.goto(base + target.relative_to(root).as_posix(), wait_until='networkidle')
            manifest = json.loads(target.with_suffix('.data').joinpath('manifest.json').read_text())
            code = manifest[0]; pattern = '**/*.data/' + code + '.json'; failed = [True]
            def fail_once(route):
                if failed.pop() if failed else False:
                    route.fulfill(status=503, body='unavailable')
                else:
                    route.continue_()
            page.route(pattern, fail_once)
            page.evaluate('code => openChart(code)', code)
            assert page.locator('#chartStatus button').is_visible()
            page.locator('#chartStatus button').click()
            page.wait_for_function("document.querySelector('#chartStatus').hidden")
            page.wait_for_function("document.querySelector('#klineChart canvas') !== null")
            page.unroute(pattern, fail_once)
            page.keyboard.press('Escape')
        check('图表：请求失败后重试', chart_retry)

        def cancellation():
            page.goto(base + target.relative_to(root).as_posix(), wait_until='networkidle')
            codes = json.loads(target.with_suffix('.data').joinpath('manifest.json').read_text())[:2]
            pattern = '**/*.data/' + codes[0] + '.json'
            pending = []
            def hold(route): pending.append(route)
            page.route(pattern, hold)
            page.evaluate('code => { void openChart(code); }', codes[0])
            page.wait_for_function("document.querySelector('#chartStatus') !== null")
            page.wait_for_timeout(50)
            assert pending, '没有捕获待完成的数据请求'
            page.evaluate('code => openChart(code)', codes[1])
            page.wait_for_function("document.querySelector('#klineChart canvas') !== null")
            pending.pop().continue_()
            page.wait_for_timeout(100)
            assert codes[1] in page.locator('#modalTitle').inner_text(), '较旧请求覆盖了新选择'
            page.unroute(pattern, hold); page.keyboard.press('Escape')

            page.goto(base + target.relative_to(root).as_posix(), wait_until='networkidle')
            page.route(pattern, hold)
            page.evaluate('code => { void openChart(code); }', codes[0])
            page.wait_for_function("document.querySelector('#chartStatus') !== null")
            page.wait_for_timeout(50)
            page.keyboard.press('Escape'); pending.pop().continue_()
            page.wait_for_timeout(100)
            assert not page.locator('#chartModal').is_visible(), '取消的请求重新打开了弹窗'
            page.unroute(pattern, hold)
        check('图表：切换股票与关闭弹窗时忽略过期请求', cancellation)

        def library_retry():
            page.goto(base + target.relative_to(root).as_posix(), wait_until='networkidle')
            pattern = '**/echarts-5.5.0.min.js'; failed = [True]
            def fail_once(route):
                if failed.pop() if failed else False:
                    route.fulfill(status=503, body='unavailable')
                else:
                    route.continue_()
            page.route(pattern, fail_once)
            code = json.loads(target.with_suffix('.data').joinpath('manifest.json').read_text())[0]
            page.evaluate('code => openChart(code)', code)
            assert page.locator('#chartStatus button').is_visible()
            page.locator('#chartStatus button').click()
            page.wait_for_function("document.querySelector('#chartStatus').hidden")
            page.wait_for_function("document.querySelector('#klineChart canvas') !== null")
            page.keyboard.press('Escape'); page.unroute(pattern, fail_once)
        check('图表：图表库加载失败后重试', library_retry)

        def keyboard_and_downloads():
            page.goto(base + 'html/20261021/stock_selection_20261021.html', wait_until='networkidle')
            header = page.locator('#stockTable th').first
            header.focus(); page.keyboard.press('Enter')
            assert header.get_attribute('aria-sort') in ('ascending','descending')
            row = page.locator('#stockTable tbody tr').first
            row.focus(); page.keyboard.press('Enter')
            page.wait_for_function("document.querySelector('#klineChart canvas') !== null")
            kdj = page.evaluate("echarts.getInstanceByDom(document.getElementById('kdjChart')).getOption().yAxis[0]")
            assert kdj.get('min') != 0 and kdj.get('max') != 100, 'KDJ仍被限制为0~100'
            page.keyboard.press('Escape')
            assert row.evaluate('(node) => document.activeElement === node'), '关闭后焦点未回到股票行'
            with page.expect_download() as download:
                page.locator('button[onclick="downloadCSV(\'stock_selection\')"]').click()
            csv = output / download.value.suggested_filename; download.value.save_as(str(csv))
            assert csv.read_bytes().startswith(b'\xef\xbb\xbf'), 'CSV缺少UTF-8 BOM'
            c154 = next(p for p in chart_pages if p.name.startswith('c154_selection'))
            page.goto(base + c154.relative_to(root).as_posix(), wait_until='networkidle')
            with page.expect_download() as download:
                page.locator('button[onclick="downloadSEL()"]') .click()
            sel = output / download.value.suggested_filename; download.value.save_as(str(sel))
            content = sel.read_bytes(); count = int.from_bytes(content[:2], 'little')
            assert len(content) == 2 + count * 8
        check('表格与导出：键盘排序、焦点、KDJ范围、CSV和SEL', keyboard_and_downloads)

        for relative in ['html/20261022/industry_total_amount_trend.html',
                         'html/20261022/first_j13_step_daily_count.html',
                         'html/20261022/sentiment_rebound_strategy.html',
                         'html/multi_indicator_trend/index.html',
                         'html/dca/dca_report_红利低波.html', 'html/dca_4pct/strategy.html',
                         'daily_first_j13_interactive.html', 'first_j13_step_daily_count.html',
                         'industry_total_amount_trend.html', 'stock_selection_20260227.html',
                         'stock_selection_20260304.html', 'stock_selection_20260306.html']:
            def static_chart(relative=relative):
                page.goto(base + quote(relative), wait_until='networkidle')
                if relative.startswith('stock_selection_'):
                    assert page.locator('table').count()
                    manifest_path = (root / relative).with_suffix('.data') / 'manifest.json'
                    if manifest_path.exists():
                        code = json.loads(manifest_path.read_text())[0]
                        page.evaluate("code => showChart(code, '历史报告')", code)
                        page.wait_for_function("document.querySelector('#klineChart canvas') !== null")
                        page.keyboard.press('Escape')
                else:
                    assert page.locator('canvas, svg polyline, .js-plotly-plot svg').count() > 0, 'No canvas/SVG graph rendered'
                home_links = page.get_by_role('link', name=re.compile('返回首页'))
                assert home_links.count() and home_links.first.get_attribute('href')
            check('其他图表：' + relative, static_chart)
        context.close(); browser.close()
    server.shutdown(); server.server_close()
    summary = {'checks':len(results),'passed':sum(r['passed'] for r in results),
               'failed':[r for r in results if not r['passed']], 'page_errors':errors,
               'external_requests':external, 'results':results}
    (output / 'browser-results.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in summary.items() if k != 'results'},ensure_ascii=False,indent=2), flush=True)
    return bool(summary['failed'] or errors or external)


if __name__ == '__main__':
    raise SystemExit(main())
