import datetime as dt
import unittest

import main


def make_report(*, weather_error: bool = False, stock_error: bool = False) -> main.ReportData:
    weather_items = ["Shanghai: 晴，当前 16.5°C，体感 16.3°C，最高/最低 19.0/12.1°C，风速 8.2 km/h"]
    weather_errors: list[str] = []
    if weather_error:
        weather_items = ["天气获取失败: timeout"]
        weather_errors = ["天气获取失败: timeout"]

    stock_items = ["- 贵州茅台 (SH600519): 1504.77 CNY (+2.59%, +37.97)"]
    stock_errors: list[str] = []
    if stock_error:
        stock_items = ["- 600519: 获取失败 (bad response)"]
        stock_errors = ["600519: 获取失败 (bad response)"]

    return main.ReportData(
        title="每日资讯推送",
        generated_at=dt.datetime(2026, 3, 9, 9, 36),
        timezone="Asia/Shanghai",
        sections=[
            main.ReportSection(
                key=main.SECTION_WEATHER,
                title="天气",
                items=weather_items,
                errors=weather_errors,
                status=main.SECTION_STATUS_ERROR if weather_errors else main.SECTION_STATUS_OK,
            ),
            main.ReportSection(
                key=main.SECTION_STOCK,
                title="A股",
                items=stock_items,
                errors=stock_errors,
                status=main.SECTION_STATUS_PARTIAL if stock_errors else main.SECTION_STATUS_OK,
            ),
            main.ReportSection(
                key=main.SECTION_GOLD,
                title="黄金",
                items=[
                    "金价: $2,900.00/oz | CNY 672.15/g (+0.50% / 24h) (Gate.io XAUT)",
                    "持仓: 20.0000 g",
                    "当前总价: CNY 13,443.00",
                    "总成本: CNY 10,800.00",
                    "盈亏: +CNY 2,643.00 (+24.47%)",
                ],
                errors=[],
                status=main.SECTION_STATUS_OK,
            ),
            main.ReportSection(
                key=main.SECTION_CRYPTO,
                title="加密货币",
                items=["BTC: $92,000.00 | CNY 664,000.00 (+1.23% / 24h)"],
                errors=[],
                status=main.SECTION_STATUS_OK,
            ),
        ],
    )


class ReportPipelineTests(unittest.TestCase):
    def test_report_data_partial_errors_are_aggregated(self) -> None:
        report = make_report(weather_error=True, stock_error=True)

        self.assertTrue(report.has_partial_errors())
        self.assertEqual(
            report.partial_errors(),
            [
                "天气获取失败: timeout",
                "A股: 600519: 获取失败 (bad response)",
            ],
        )
        self.assertEqual(report.sections[0].status, main.SECTION_STATUS_ERROR)
        self.assertEqual(report.sections[1].status, main.SECTION_STATUS_PARTIAL)

    def test_build_report_section_sets_status_from_collection(self) -> None:
        error_section = main.build_report_section(
            main.SECTION_WEATHER,
            main.SectionCollection(
                items=["天气获取失败: timeout"],
                errors=["天气获取失败: timeout"],
                successful_items=0,
            ),
        )
        partial_section = main.build_report_section(
            main.SECTION_STOCK,
            main.SectionCollection(
                items=["- 贵州茅台 (SH600519): 1504.77 CNY (+2.59%, +37.97)", "- 600519: 获取失败 (bad response)"],
                errors=["600519: 获取失败 (bad response)"],
                successful_items=1,
            ),
        )
        ok_section = main.build_report_section(
            main.SECTION_CRYPTO,
            main.SectionCollection(
                items=["BTC: $92,000.00 | CNY 664,000.00 (+1.23% / 24h)"],
                errors=[],
                successful_items=1,
            ),
        )

        self.assertEqual(error_section.status, main.SECTION_STATUS_ERROR)
        self.assertEqual(partial_section.status, main.SECTION_STATUS_PARTIAL)
        self.assertEqual(ok_section.status, main.SECTION_STATUS_OK)

    def test_render_telegram_report_from_structured_data(self) -> None:
        report = make_report()

        rendered = main.render_telegram_report(report)

        self.assertIn("🗞️ 每日资讯推送", rendered)
        self.assertIn("🌤️ 天气", rendered)
        self.assertIn("📈 A股", rendered)
        self.assertIn("• 贵州茅台: 1504.77 CNY | +2.59%", rendered)
        self.assertIn("• 金价: CNY 672.15/g | 24h +0.50% | Gate.io XAUT", rendered)
        self.assertIn("• BTC: $92,000.00 | 24h +1.23%", rendered)

    def test_build_wechat_markdown_uses_structured_sections(self) -> None:
        report = make_report()

        rendered = main.build_wechat_markdown(report)

        self.assertIn("## 每日资讯推送", rendered)
        self.assertIn("### 🌤️ 天气", rendered)
        self.assertIn("- 上海", rendered.replace("Shanghai", "上海"))  # loose assertion for content shape
        self.assertIn("### 📈 A股", rendered)
        self.assertIn("- 贵州茅台: 1504.77 CNY | +2.59%", rendered)
        self.assertIn("### 🥇 黄金", rendered)
        self.assertIn("- 金价: CNY 672.15/g | 24h +0.50% | Gate.io XAUT", rendered)

    def test_build_dingtalk_markdown_formats_clean_items(self) -> None:
        report = make_report()

        title, rendered = main.build_dingtalk_markdown(report)

        self.assertEqual(title, "每日资讯推送")
        self.assertIn("## 每日资讯推送", rendered)
        self.assertIn("时间：2026-03-09 09:36 (Asia/Shanghai)", rendered)
        self.assertIn("### A股", rendered)
        self.assertIn("- 贵州茅台：1504.77 CNY | +2.59%", rendered)
        self.assertIn("- 金价：CNY 672.15/g | 24h +0.50% | Gate.io XAUT", rendered)
        self.assertIn("- BTC：$92,000.00 | 24h +1.23%", rendered)


if __name__ == "__main__":
    unittest.main()
