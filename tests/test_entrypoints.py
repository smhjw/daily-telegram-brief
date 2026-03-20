import contextlib
import io
import os
import unittest
from unittest import mock

import main

from tests.test_report_pipeline import make_report


class MainEntrypointTests(unittest.TestCase):
    def test_create_http_session_retries_get_only(self) -> None:
        session = main.create_http_session()

        adapter = session.get_adapter("https://example.com")

        self.assertEqual(adapter.max_retries.allowed_methods, frozenset({"GET"}))

    def test_load_config_from_env_reads_runtime_and_channel_settings(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "DRY_RUN": "true",
                "FAIL_ON_PARTIAL_ERROR": "false",
                "CITY_NAME": "Beijing",
                "TIMEZONE": "Asia/Shanghai",
                "A_STOCK_CODES": "600519,sh000001",
                "WEATHER_LATITUDE": "39.90",
                "WEATHER_LONGITUDE": "116.40",
                "GOLD_HOLDING_GRAMS": "20",
                "GOLD_TOTAL_COST_CNY": "10800",
                "GOLD_COST_PER_GRAM_CNY": "540",
                "TELEGRAM_BOT_TOKEN": "token",
                "TELEGRAM_CHAT_ID": "chat",
                "WECHAT_SENDKEY": "sendkey",
                "DINGTALK_WEBHOOK": "https://example.com/webhook",
                "DINGTALK_SECRET": "secret",
            },
            clear=True,
        ):
            config = main.load_config_from_env()

        self.assertTrue(config.dry_run)
        self.assertFalse(config.fail_on_partial_error)
        self.assertEqual(config.city_name, "Beijing")
        self.assertEqual(config.stock_codes, "600519,sh000001")
        self.assertEqual(config.latitude, 39.90)
        self.assertEqual(config.longitude, 116.40)
        self.assertTrue(config.has_telegram_channel)
        self.assertTrue(config.has_wechat_channel)
        self.assertTrue(config.has_dingtalk_channel)
        self.assertTrue(config.has_any_channel)

    def test_validate_config_requires_channel_when_not_dry_run(self) -> None:
        config = main.AppConfig(
            dry_run=False,
            fail_on_partial_error=True,
            city_name="Shanghai",
            timezone="Asia/Shanghai",
            stock_codes="600519",
            latitude=None,
            longitude=None,
            gold_holding_grams=None,
            gold_total_cost_cny=None,
            gold_cost_per_gram_cny=None,
            telegram_bot_token="",
            telegram_chat_id="",
            wechat_sendkey="",
            dingtalk_webhook="",
            dingtalk_secret="",
        )

        with self.assertRaisesRegex(ValueError, "No push channel configured"):
            main.validate_config(config)

    def test_main_returns_error_when_partial_data_failure_is_fatal(self) -> None:
        report = make_report(weather_error=True)
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            mock.patch.dict(os.environ, {"DRY_RUN": "true", "FAIL_ON_PARTIAL_ERROR": "true"}, clear=True),
            mock.patch("main.collect_report_data", return_value=report),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = main.main()

        self.assertEqual(exit_code, 1)
        self.assertIn("天气获取失败: timeout", stderr.getvalue())

    def test_main_allows_partial_data_failure_when_disabled(self) -> None:
        report = make_report(weather_error=True)
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            mock.patch.dict(os.environ, {"DRY_RUN": "true", "FAIL_ON_PARTIAL_ERROR": "false"}, clear=True),
            mock.patch("main.collect_report_data", return_value=report),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = main.main()

        self.assertEqual(exit_code, 0)
        self.assertIn("Partial data failures:", stderr.getvalue())

    def test_main_returns_error_when_partial_channel_or_data_failure_is_fatal(self) -> None:
        report = make_report(stock_error=True)
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            mock.patch.dict(os.environ, {"DRY_RUN": "true", "FAIL_ON_PARTIAL_ERROR": "true"}, clear=True),
            mock.patch("main.collect_report_data", return_value=report),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = main.main()

        self.assertEqual(exit_code, 1)
        self.assertIn("A股: 600519: 获取失败 (bad response)", stderr.getvalue())

    def test_main_allows_partial_channel_or_data_failure_when_disabled(self) -> None:
        report = make_report(stock_error=True)
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            mock.patch.dict(os.environ, {"DRY_RUN": "true", "FAIL_ON_PARTIAL_ERROR": "false"}, clear=True),
            mock.patch("main.collect_report_data", return_value=report),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = main.main()

        self.assertEqual(exit_code, 0)
        self.assertIn("Partial data failures:", stderr.getvalue())

    def test_main_does_not_send_when_partial_data_failure_is_fatal(self) -> None:
        report = make_report(weather_error=True)
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            mock.patch.dict(
                os.environ,
                {
                    "FAIL_ON_PARTIAL_ERROR": "true",
                    "TELEGRAM_BOT_TOKEN": "token",
                    "TELEGRAM_CHAT_ID": "chat",
                },
                clear=True,
            ),
            mock.patch("main.collect_report_data", return_value=report),
            mock.patch("main.send_report_to_channels") as mocked_send,
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = main.main()

        self.assertEqual(exit_code, 1)
        mocked_send.assert_not_called()
        self.assertIn("天气获取失败: timeout", stderr.getvalue())

    def test_main_sends_when_partial_data_failure_is_allowed(self) -> None:
        report = make_report(weather_error=True)
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            mock.patch.dict(
                os.environ,
                {
                    "FAIL_ON_PARTIAL_ERROR": "false",
                    "TELEGRAM_BOT_TOKEN": "token",
                    "TELEGRAM_CHAT_ID": "chat",
                },
                clear=True,
            ),
            mock.patch("main.collect_report_data", return_value=report),
            mock.patch("main.send_report_to_channels", return_value=[]) as mocked_send,
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = main.main()

        self.assertEqual(exit_code, 0)
        mocked_send.assert_called_once()
        self.assertIn("Partial data failures:", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
