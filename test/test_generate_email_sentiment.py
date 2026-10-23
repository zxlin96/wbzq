"""回归验证邮件给出的下一交易日操作与当次信号一致。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from generate_email_report import generate_sentiment_section


class SentimentEmailActionTests(unittest.TestCase):
    def test_next_day_executes_signal_amount_before_level_upgrade(self):
        state = {
            "position": 7,
            "investment_level_idx": 3,
            "trades": [{
                "date": "20261019", "action": "BUY", "amount": 8000,
                "level": 3, "reason": "J13数量超过阈值",
            }],
        }

        body = generate_sentiment_section(state, "20261019")

        self.assertIn("下一交易日操作：买入 ¥8,000（第 3 级）", body)
        self.assertIn("执行 20261019 的本次信号", body)
        self.assertIn("已执行则无需重复操作", body)
        self.assertLess(body.index("下一交易日操作："), body.index("当前持仓:"))
        self.assertNotIn("第 4 级", body)
        self.assertNotIn("¥16,000", body)
        self.assertNotIn("明日请继续买入", body)

    def test_new_first_level_signal_after_interruption(self):
        state = {
            "position": 8,
            "investment_level_idx": 1,
            "trades": [
                {"date": "20261019", "action": "BUY", "amount": 8000, "level": 3},
                {"date": "20261022", "action": "BUY", "amount": 2000, "level": 1},
            ],
        }

        body = generate_sentiment_section(state, "20261022")

        self.assertIn("下一交易日操作：买入 ¥2,000（第 1 级）", body)
        self.assertNotIn("¥4,000", body)

    def test_no_new_signal_does_not_repeat_old_buy(self):
        state = {
            "position": 7,
            "investment_level_idx": 0,
            "trades": [{"date": "20261019", "action": "BUY", "amount": 8000, "level": 3}],
        }

        body = generate_sentiment_section(state, "20261020")

        self.assertIn("下一交易日操作：继续持有，等待新信号。", body)
        self.assertNotIn("下一交易日操作：买入", body)

    def test_next_day_sell_instructions_use_signal_ratio(self):
        for ratio, position, expected in [
            (0.5, 1, "卖出当前持仓的 50%。"),
            (1, 0, "全部卖出，清仓。"),
        ]:
            with self.subTest(ratio=ratio):
                state = {
                    "position": position,
                    "investment_level_idx": 0,
                    "trades": [{"date": "20260929", "action": "SELL", "ratio": ratio}],
                }

                body = generate_sentiment_section(state, "20260929")

                self.assertIn("下一交易日操作：" + expected, body)
                self.assertIn("执行 20260929 的本次信号", body)

    def test_no_new_signal_while_flat_keeps_waiting(self):
        state = {
            "position": 0,
            "investment_level_idx": 0,
            "trades": [{"date": "20260929", "action": "SELL", "ratio": 1}],
        }

        body = generate_sentiment_section(state, "20260930")

        self.assertIn("下一交易日操作：保持空仓，等待新信号。", body)
        self.assertNotIn("下一交易日操作：全部卖出", body)

    def test_incomplete_buy_never_invents_next_level_order(self):
        for missing_field in ("amount", "level"):
            with self.subTest(missing_field=missing_field):
                trade = {"date": "20261019", "action": "BUY", "amount": 8000, "level": 3}
                del trade[missing_field]
                state = {"position": 7, "investment_level_idx": 3, "trades": [trade]}

                body = generate_sentiment_section(state, "20261019")

                self.assertIn("本次买入信号缺少金额或级别", body)
                self.assertNotIn("¥16,000", body)


if __name__ == "__main__":
    unittest.main()
