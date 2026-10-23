"""Exercise every stock report writer using synthetic, offline market data."""
from contextlib import contextmanager
import json
import os
import subprocess
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from generate_stock_html import (generate_stock_selection_html, generate_c154_html,
    generate_c432_html, generate_macd_html, generate_multi_indicator_html)


@contextmanager
def chdir(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class ReportGeneratorTests(unittest.TestCase):
    def test_all_stock_generators_write_lazy_public_reports_and_empty_states(self):
        rows = []
        for code in ['600000.SH', '000001.SZ']:
            for i, date in enumerate(['20261020','20261021','20261022']):
                rows.append(dict(ts_code=code, name='测试股票', industry_name='测试行业', trade_date=date,
                    open_qfq=10., close_qfq=11., high_qfq=12., low_qfq=9., pre_close=10.,
                    kdj_qfq=[-15.,40.,115.][i], kdj_k_qfq=30., kdj_d_qfq=40.,
                    amount=100000., ma_qfq_60=9., macd_dif_qfq=.2, macd_dea_qfq=.1,
                    macd_qfq=.2, pct_chg=1., total_mv=1000000., ma60_upward=True,
                    is_amount_top30=True, bottom_violent_k=False,
                    zhixing_duokong=9., zhixing_mid_duokong=9.1))
        history = pd.DataFrame(rows)
        selected = history[history.trade_date == '20261022']
        generators = [
            ('stock_selection', lambda result: generate_stock_selection_html(result, history, '20261022', pd.Series({'测试行业':2}))),
            ('c154_selection', lambda result: generate_c154_html(result, history, '20261022', {}, {'测试行业':2})),
            ('c432_selection', lambda result: generate_c432_html(result, history, '20261022', {}, {'测试行业':2})),
            ('macd_selection', lambda result: generate_macd_html(result, history, '20261022', {}, {'测试行业':2})),
            ('multi_indicator_selection', lambda result: generate_multi_indicator_html(result, history, '20261022', {}, {'测试行业':2}, [])),
        ]
        with TemporaryDirectory() as directory, chdir(directory):
            Path('index.html').write_text('<html><head></head><body>Home</body></html>')
            for stem, generate in generators:
                with self.subTest(generator=stem):
                    generate(selected)
                    path = Path(f'html/20261022/{stem}_20261022.html')
                    content = path.read_text()
                    self.assertIn('await SiteReports.loadStock(', content)
                    self.assertNotIn('cdn.', content)
                    manifest = json.loads(path.with_suffix('.data').joinpath('manifest.json').read_text())
                    self.assertEqual(set(manifest), {'600000.SH','000001.SZ'})
                    self.assertNotIn('v.amount', content)
                    self.assertNotIn('min: 0, max: 100', content)
                    subprocess.run(['node', '-e', '''
                        const fs=require('fs'),vm=require('vm');
                        const html=fs.readFileSync(process.argv[1],'utf8');
                        for(const m of html.matchAll(/<script\\b([^>]*)>([\\s\\S]*?)<\\/script>/gi)) {
                            if(!m[1].includes('src=')) new vm.Script(m[2]);
                        }
                    ''', str(path)], check=True, capture_output=True)
                    generate(selected.iloc[:0])
                    empty = path.read_text()
                    self.assertNotIn('data-chart-base=', empty)
                    self.assertTrue('无符合' in empty or '无入选' in empty)


if __name__ == '__main__':
    unittest.main()
