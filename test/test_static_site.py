"""Offline regression tests for static publishing (standard-library only)."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from generate_reports_json import generate_reports_json, REPORT_TYPES, CSV_TYPES
from static_site import optimize_html, write_report, build_site


@contextmanager
def chdir(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class StaticPublishingTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'index.html').write_text('<html><head></head><body>Home</body></html>')

    def tearDown(self):
        self.temp.cleanup()

    def test_lazy_data_and_legacy_script_are_migrated_together(self):
        data = {'600000.SH': {'name': '测试', 'dates': ['20261022'],
            'candlestick': [['20261022', 10, 11, 12, 9]],
            'volume': [{'value': 13.5, 'is_double': False}], 'ma60': [float('nan')]}}
        document = '''<html><head><script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script></head><body>
<script>const chartsData = DATA;
function openChart(tsCode) { const d = chartsData[tsCode]; if (!d) return;
setTimeout(() => { const cd = d.candlestick.map(c => ({value: [c[1],c[2],c[3],c[4]]}));
const vd = d.volume.map(v => ({ value: v.amount, itemStyle: { color: v.is_double ? '#a855f7' : '#3b82f6' } })));
window.addEventListener('resize', () => { kc.resize(); vc.resize(); }); }, 100); }
function closeModal() { document.getElementById('chartModal').style.display = 'none'; }
</script></body></html>'''.replace('DATA', json.dumps(data))
        path = self.root / 'html/20261022/report.html'
        with chdir(self.root):
            write_report(path, document)
        result = path.read_text()
        payload = json.loads((path.with_suffix('.data') / '600000.SH.json').read_text())
        self.assertEqual(payload['candlestick'], data['600000.SH']['candlestick'])
        self.assertEqual(payload['ma60'], [None])
        self.assertNotIn('const chartsData =', result)
        self.assertIn('await SiteReports.loadStock(tsCode)', result)
        self.assertIn('[c[1], c[2], c[4], c[3]]', result)
        self.assertIn('value: v.value', result)
        self.assertIn('SiteReports.isCurrent(d)', result)
        self.assertNotIn('addEventListener(\'resize\'', result)
        self.assertNotIn('cdn.', result)
        self.assertEqual(optimize_html(result, path, self.root), result)

    def test_broken_links_are_disabled_and_can_be_restored(self):
        path = self.root / 'html/20261022/report.html'
        path.parent.mkdir(parents=True)
        source = '<html><head></head><body><a href="index.html">← 返回首页</a><a href="next.html">查看选股</a></body></html>'
        result = optimize_html(source, path, self.root)
        self.assertIn('href="../../index.html"', result)
        self.assertIn('data-unavailable-href="next.html"', result)
        (path.parent / 'next.html').write_text('next')
        restored = optimize_html(result, path, self.root)
        self.assertIn('<a href="next.html">查看报告</a>', restored)

    def test_index_includes_every_report_type_and_missing_is_not_zero(self):
        directory = self.root / 'html/20261022'; directory.mkdir(parents=True)
        for key, pattern in REPORT_TYPES.items():
            (directory / pattern.format(date='20261022')).write_text(
                '<html><head><meta name="report-stock-count" content="3"></head></html>')
        for prefix in CSV_TYPES.values():
            (self.root / f'{prefix}_result_20261022.csv').write_text('code,name\n600000,测试\n')
        with chdir(self.root):
            data = generate_reports_json()
        self.assertEqual(data['c154Stocks'], 3)
        self.assertEqual(data['c432Stocks'], 3)
        self.assertEqual(data['totalStocks'], 3)
        self.assertIn('+08:00', data['generatedAt'])
        self.assertTrue(all(data['reports'][0][key] for key in (*REPORT_TYPES, *CSV_TYPES)))
        (self.root / 'html/20261023').mkdir()
        with chdir(self.root):
            data = generate_reports_json()
        self.assertIsNone(data['totalStocks'])
        self.assertIsNone(data['c154Stocks'])
        self.assertEqual(data['latestDate'], '20261023')

    def test_empty_reports_have_a_real_zero_count(self):
        directory = self.root / 'html/20261022'; directory.mkdir(parents=True)
        (directory / 'stock_selection_20261022.html').write_text('<html>共选出 0 只</html>')
        with chdir(self.root):
            data = generate_reports_json()
        self.assertEqual(data['totalStocks'], 0)

    def test_build_copies_only_public_files(self):
        (self.root / 'html/20261022').mkdir(parents=True)
        (self.root / 'assets').mkdir()
        (self.root / 'assets/site.js').write_text('')
        (self.root / 'stock_selection_20260304.html').write_text('<html>Legacy report</html>')
        (self.root / 'c154_result_20261022.csv').write_text('code\n600000\n')
        for name in ['.env', 'config.py', 'strategy_state.json', 'private.csv']:
            (self.root / name).write_text('private')
        with chdir(self.root):
            generate_reports_json()
        output = build_site(self.root, self.root / 'dist')
        self.assertTrue((output / 'c154_result_20261022.csv').is_file())
        self.assertTrue((output / 'stock_selection_20260304.html').is_file())
        for name in ['.env', 'config.py', 'strategy_state.json', 'private.csv']:
            self.assertFalse((output / name).exists())
        self.assertTrue((output / '.nojekyll').exists())
        self.assertEqual(build_site(self.root, output), output)
        with self.assertRaises(ValueError):
            build_site(self.root, self.root)
        unmanaged = self.root / 'unmanaged'; unmanaged.mkdir()
        with self.assertRaises(ValueError):
            build_site(self.root, unmanaged)

    def test_embedded_plotly_is_shared_without_changing_its_code(self):
        library = '/** plotly.js v3.1.0; MIT */\n' + (' ' * 1_000_000) + 'window.Plotly = {};'
        source = '<html><head></head><body><script>' + library + '</script></body></html>'
        result = optimize_html(source, self.root / 'first_j13_step_daily_count.html', self.root)
        bundles = list((self.root / 'assets/vendor').glob('plotly-*.min.js'))
        self.assertEqual(len(bundles), 1)
        self.assertEqual(bundles[0].read_text(), library)
        self.assertIn('src="assets/vendor/plotly-', result)
        self.assertNotIn(library, result)
        self.assertEqual(optimize_html(result, self.root / 'first_j13_step_daily_count.html', self.root), result)


if __name__ == '__main__':
    unittest.main()
