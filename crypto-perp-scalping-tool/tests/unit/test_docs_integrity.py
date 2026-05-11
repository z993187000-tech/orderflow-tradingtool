import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class DocsIntegrityTests(unittest.TestCase):
    def test_technical_spec_is_readable_chinese_markdown(self):
        text = (PROJECT_ROOT / "docs" / "crypto-perp-scalping-technical-spec.md").read_text(encoding="utf-8")

        self.assertIn("加密永续剥头皮全自动交易工具技术规范", text)
        self.assertIn("Auction Market Theory", text)
        self.assertIn("POC / HVN / LVN / VAH / VAL", text)
        self.assertIn("Risk Engine", text)
        self.assertNotIn("鏃", text)
        self.assertNotIn("鎴", text)
        self.assertNotIn("甯", text)

    def test_usage_documents_perp_trade_as_dashboard_last_price(self):
        text = (PROJECT_ROOT / "docs" / "usage.md").read_text(encoding="utf-8")

        self.assertIn("Perp Last / 永续最新成交价", text)
        self.assertIn("优先显示 Binance USDⓈ-M Futures aggTrade 最新成交价", text)
        self.assertNotIn("Spot/Index Last / 现货/指数最新价", text)

    def test_usage_documents_simulation_command(self):
        text = (PROJECT_ROOT / "docs" / "usage.md").read_text(encoding="utf-8")

        self.assertIn("运行故障仿真", text)
        self.assertIn("python -m crypto_perp_tool.cli simulation run", text)
        self.assertIn("websocket_disconnect", text)
        self.assertIn("stop_submission_failure", text)


if __name__ == "__main__":
    unittest.main()
