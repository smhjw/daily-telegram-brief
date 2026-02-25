#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import re
import sys
from typing import Optional
from zoneinfo import ZoneInfo

import main as core
import requests


def configure_console_encoding() -> None:
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def clean_stock_line(raw: str) -> str:
    text = raw.strip()
    if text.startswith("- "):
        text = text[2:]

    # Typical old format: 贵州茅台 (SH600519): 1504.77 CNY (+2.59%, +37.97)
    m = re.match(r"^(.*?)\s*\([A-Za-z]{2}\d{6}\):\s*([0-9,.]+\s*CNY)\s*\(([+-]?[0-9.]+%)", text)
    if m:
        name, price, pct = m.groups()
        return f"• {name}: {price} ({pct})"

    # Fallback: remove code part if present.
    text = re.sub(r"\s*\([A-Za-z]{2}\d{6}\)", "", text)
    # Keep only first percent field when there are multiple.
    text = re.sub(r"\(([+-]?[0-9.]+%)\s*,\s*[^)]*\)", r"(\1)", text)
    return f"• {text}"


def clean_gold_lines(raw_lines: list[str]) -> list[str]:
    if not raw_lines:
        return []

    lines = [line.strip() for line in raw_lines if line.strip()]
    result: list[str] = []

    # First line from core usually has both USD/oz and CNY/g; keep only CNY/g.
    first = lines[0]
    m = re.search(r"CNY\s*([0-9,]+(?:\.[0-9]+)?)/g", first)
    if m:
        result.append(f"• 金价: CNY {m.group(1)}/g")
    else:
        # Fallback if format changed unexpectedly.
        result.append(f"• {first}")

    # Keep position/cost/PnL lines as-is.
    for line in lines[1:]:
        result.append(f"• {line}")
    return result


def clean_crypto_line(raw: str) -> str:
    text = raw.strip()
    if text.startswith("- "):
        text = text[2:]
    # Remove CNY conversion part if present.
    text = re.sub(r"\s*\|\s*CNY\s*[0-9,]+(?:\.[0-9]+)?", "", text)
    return f"• {text}"


def clean_weather_line(raw: str) -> str:
    text = raw.strip()
    # core format example:
    # Shanghai: 阴，当前 16.5°C，体感 16.3°C，最高/最低 19.0/12.1°C，风速 8.2 km/h
    m = re.match(
        r"^(.*?):\s*(.*?)，当前\s*([0-9.]+°C)，体感\s*([0-9.]+°C)，最高/最低\s*([0-9.]+)/([0-9.]+)°C(?:，风速.*)?$",
        text,
    )
    if m:
        city, weather, current, feels_like, high, low = m.groups()
        return f"• {city}: {weather} {current} 体感{feels_like} 高/低 {high}/{low}°C"
    return f"• {text}"


def send_wechat_serverchan(sendkey: str, text: str, title: str = "每日资讯推送") -> None:
    url = f"https://sctapi.ftqq.com/{sendkey}.send"
    response = requests.post(
        url,
        data={
            "title": title,
            "desp": text,
        },
        timeout=20,
        headers={"User-Agent": core.USER_AGENT},
    )
    response.raise_for_status()

    payload = response.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"Server酱 API error: {payload.get('message', 'unknown error')}")


def build_wechat_markdown_from_telegram(telegram_text: str) -> str:
    lines = [line.strip() for line in telegram_text.splitlines() if line.strip()]
    timestamp = ""
    sections: dict[str, list[str]] = {
        "天气": [],
        "黄金": [],
        "加密货币": [],
        "A股": [],
    }
    current_section = ""

    for line in lines:
        if line.startswith("🕒 "):
            timestamp = line[2:].strip()
            continue
        if line.startswith("🌤️ "):
            current_section = "天气"
            continue
        if line.startswith("🥇 "):
            current_section = "黄金"
            continue
        if line.startswith("🪙 "):
            current_section = "加密货币"
            continue
        if line.startswith("📈 "):
            current_section = "A股"
            continue
        if line.startswith("🗞️ ") or line.startswith("━━━━━━━━"):
            continue
        if line.startswith("• ") and current_section:
            sections[current_section].append(line[2:].strip())

    out: list[str] = ["## 每日资讯推送"]
    if timestamp:
        out.append(f"> {timestamp}")

    for section_name in ["天气", "黄金", "加密货币", "A股"]:
        out.append("")
        out.append(f"### {section_name}")
        items = sections.get(section_name, [])
        if not items:
            out.append("- 暂无数据")
            continue
        for item in items:
            out.append(f"- {item}")

    return "\n".join(out)


def build_report(
    city_name: str,
    timezone: str,
    latitude: Optional[float],
    longitude: Optional[float],
    stock_codes: str,
    gold_holding_grams: Optional[float],
    gold_total_cost_cny: Optional[float],
    gold_cost_per_gram_cny: Optional[float],
) -> str:
    try:
        now = dt.datetime.now(ZoneInfo(timezone))
    except Exception:
        timezone = "UTC"
        now = dt.datetime.now(dt.timezone.utc)

    lines = [
        "🗞️ 每日资讯推送",
        f"🕒 {now:%Y-%m-%d %H:%M} ({timezone})",
        "━━━━━━━━━━━━",
        "🌤️ 天气",
    ]

    try:
        weather_line = core.fetch_weather(city_name, timezone, latitude, longitude)
        lines.append(clean_weather_line(weather_line))
    except Exception as exc:
        lines.append(f"• 天气获取失败: {exc}")

    lines.extend(["━━━━━━━━━━━━", "🥇 黄金"])
    try:
        gold_lines = core.build_gold_block(gold_holding_grams, gold_total_cost_cny, gold_cost_per_gram_cny)
        lines.extend(clean_gold_lines(gold_lines))
    except Exception as exc:
        lines.append(f"• 黄金获取失败: {exc}")

    lines.extend(["━━━━━━━━━━━━", "🪙 加密货币"])
    for crypto_line in core.fetch_crypto_block():
        lines.append(clean_crypto_line(crypto_line))

    lines.extend(["━━━━━━━━━━━━", "📈 A股"])
    for stock_line in core.fetch_a_share_block(stock_codes):
        lines.append(clean_stock_line(stock_line))

    return "\n".join(lines)


def main() -> int:
    try:
        configure_console_encoding()

        dry_run = core.read_bool_env("DRY_RUN", default=False)
        bot_token = core.read_env("TELEGRAM_BOT_TOKEN", default="", required=not dry_run)
        chat_id = core.read_env("TELEGRAM_CHAT_ID", default="", required=not dry_run)
        wechat_sendkey = core.read_env("WECHAT_SENDKEY", default="")

        city_name = core.read_env("CITY_NAME", default="Shanghai")
        timezone = core.read_env("TIMEZONE", default="Asia/Shanghai")
        stock_codes = core.read_env("A_STOCK_CODES", default="600519,002605,sh000001")

        latitude = core.parse_optional_float(core.read_env("WEATHER_LATITUDE", default=""), "WEATHER_LATITUDE")
        longitude = core.parse_optional_float(core.read_env("WEATHER_LONGITUDE", default=""), "WEATHER_LONGITUDE")
        gold_holding_grams = core.parse_optional_float(core.read_env("GOLD_HOLDING_GRAMS", default=""), "GOLD_HOLDING_GRAMS")
        gold_total_cost_cny = core.parse_optional_float(core.read_env("GOLD_TOTAL_COST_CNY", default=""), "GOLD_TOTAL_COST_CNY")
        gold_cost_per_gram_cny = core.parse_optional_float(core.read_env("GOLD_COST_PER_GRAM_CNY", default=""), "GOLD_COST_PER_GRAM_CNY")

        report = build_report(
            city_name,
            timezone,
            latitude,
            longitude,
            stock_codes,
            gold_holding_grams,
            gold_total_cost_cny,
            gold_cost_per_gram_cny,
        )

        print(report)
        if not dry_run:
            errors: list[str] = []
            try:
                core.send_telegram_message(bot_token, chat_id, report)
            except Exception as exc:
                errors.append(f"Telegram发送失败: {exc}")

            if wechat_sendkey:
                try:
                    wechat_markdown = build_wechat_markdown_from_telegram(report)
                    send_wechat_serverchan(wechat_sendkey, wechat_markdown)
                except Exception as exc:
                    errors.append(f"微信发送失败: {exc}")

            if errors:
                raise RuntimeError("；".join(errors))
        else:
            print("DRY_RUN=true, skipped Telegram/WeChat send.")
        return 0
    except Exception as exc:
        print(f"执行失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
