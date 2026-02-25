#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from typing import Optional
from zoneinfo import ZoneInfo

import requests

USER_AGENT = "daily-telegram-brief/1.0"
WEATHER_CODE_MAP = {
    0: "晴",
    1: "大部晴",
    2: "局部多云",
    3: "阴",
    45: "雾",
    48: "冻雾",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "强毛毛雨",
    56: "冻毛毛雨",
    57: "强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "冰粒",
    80: "阵雨",
    81: "较强阵雨",
    82: "强阵雨",
    85: "阵雪",
    86: "强阵雪",
    95: "雷雨",
    96: "雷雨伴小冰雹",
    99: "雷雨伴大冰雹",
}


def read_env(name: str, default: Optional[str] = None, required: bool = False) -> str:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        if required and default is None:
            raise ValueError(f"Missing required environment variable: {name}")
        return default or ""
    return value.strip()


def read_bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def request_json(url: str, *, params: Optional[dict] = None, timeout: int = 20) -> dict:
    response = requests.get(
        url,
        params=params,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"JSON parse failed for {url}: {exc}") from exc


def to_float(value: object, scale: float = 1.0) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, str) and value.strip() in {"", "-"}:
        return None
    try:
        return float(value) / scale
    except (TypeError, ValueError):
        return None


def resolve_city(city_name: str) -> tuple[float, float, str]:
    payload = request_json(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={
            "name": city_name,
            "count": 1,
            "language": "zh",
            "format": "json",
        },
    )
    results = payload.get("results") or []
    if not results:
        raise RuntimeError(f"城市未找到: {city_name}")
    city = results[0]
    return float(city["latitude"]), float(city["longitude"]), city.get("name", city_name)


def fetch_weather(city_name: str, timezone: str, latitude: Optional[float], longitude: Optional[float]) -> str:
    resolved_name = city_name
    lat = latitude
    lon = longitude
    if lat is None or lon is None:
        lat, lon, resolved_name = resolve_city(city_name)

    payload = request_json(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min",
            "forecast_days": 1,
            "timezone": timezone,
        },
    )

    current = payload.get("current") or {}
    daily = payload.get("daily") or {}

    weather_code = current.get("weather_code")
    weather_text = WEATHER_CODE_MAP.get(weather_code, f"天气码 {weather_code}")
    temp = to_float(current.get("temperature_2m"))
    feels_like = to_float(current.get("apparent_temperature"))
    wind = to_float(current.get("wind_speed_10m"))
    max_temp = to_float((daily.get("temperature_2m_max") or [None])[0])
    min_temp = to_float((daily.get("temperature_2m_min") or [None])[0])

    return (
        f"{resolved_name}: {weather_text}，当前 {temp:.1f}°C，体感 {feels_like:.1f}°C，"
        f"最高/最低 {max_temp:.1f}/{min_temp:.1f}°C，风速 {wind:.1f} km/h"
    )


def normalize_stock_code(raw_code: str) -> tuple[str, str]:
    code = raw_code.strip().lower()
    if not code:
        raise ValueError("空股票代码")

    if re.fullmatch(r"(sh|sz)\d{6}", code):
        market = code[:2]
        digits = code[2:]
    elif re.fullmatch(r"\d{6}", code):
        digits = code
        market = "sh" if digits.startswith(("5", "6", "9")) else "sz"
    else:
        raise ValueError(f"不支持的股票代码格式: {raw_code}")

    secid = f"{'1' if market == 'sh' else '0'}.{digits}"
    return f"{market}{digits}", secid


def fetch_a_share_line(stock_code: str) -> str:
    normalized, secid = normalize_stock_code(stock_code)
    payload = request_json(
        "https://push2.eastmoney.com/api/qt/stock/get",
        params={
            "secid": secid,
            "fields": "f43,f57,f58,f60,f169,f170",
        },
    )

    data = payload.get("data")
    if not data:
        raise RuntimeError("返回数据为空")

    name = data.get("f58") or normalized.upper()
    price = to_float(data.get("f43"), scale=100)
    change_amount = to_float(data.get("f169"), scale=100)
    change_pct = to_float(data.get("f170"), scale=100)
    prev_close = to_float(data.get("f60"), scale=100)

    if price is None and prev_close is not None:
        price = prev_close

    if price is None:
        return f"- {name} ({normalized.upper()}): 暂无价格"

    if change_pct is None:
        return f"- {name} ({normalized.upper()}): {price:.2f} CNY"

    sign = "+" if change_pct > 0 else ""
    amount_text = f"{sign}{change_amount:.2f}" if change_amount is not None else "N/A"
    return (
        f"- {name} ({normalized.upper()}): {price:.2f} CNY "
        f"({sign}{change_pct:.2f}%, {amount_text})"
    )


def fetch_a_share_block(raw_codes: str) -> list[str]:
    codes = [code for code in re.split(r"[,\s]+", raw_codes.strip()) if code]
    if not codes:
        return ["- 未配置股票代码"]

    lines: list[str] = []
    for code in codes:
        try:
            lines.append(fetch_a_share_line(code))
        except Exception as exc:  # noqa: BLE001
            lines.append(f"- {code}: 获取失败 ({exc})")
    return lines


def fetch_btc_line() -> str:
    try:
        payload = request_json(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": "bitcoin",
                "vs_currencies": "usd,cny",
                "include_24hr_change": "true",
            },
            timeout=10,
        )
        btc = payload.get("bitcoin") or {}

        usd = to_float(btc.get("usd"))
        cny = to_float(btc.get("cny"))
        change = to_float(btc.get("usd_24h_change"))

        if usd is None:
            raise RuntimeError("CoinGecko 返回空价格")

        change_text = ""
        if change is not None:
            sign = "+" if change > 0 else ""
            change_text = f" ({sign}{change:.2f}% / 24h)"

        if cny is not None:
            return f"BTC: ${usd:,.2f} | ¥{cny:,.2f}{change_text}"
        return f"BTC: ${usd:,.2f}{change_text}"
    except Exception as first_exc:  # noqa: BLE001
        try:
            payload = request_json(
                "https://api.binance.com/api/v3/ticker/24hr",
                params={"symbol": "BTCUSDT"},
                timeout=8,
            )
            usd = to_float(payload.get("lastPrice"))
            change = to_float(payload.get("priceChangePercent"))
            if usd is None:
                raise RuntimeError("Binance 返回空价格")

            change_text = ""
            if change is not None:
                sign = "+" if change > 0 else ""
                change_text = f" ({sign}{change:.2f}% / 24h)"
            return f"BTC: ${usd:,.2f}{change_text} (Binance)"
        except Exception as second_exc:  # noqa: BLE001
            try:
                payload = request_json(
                    "https://api.gateio.ws/api/v4/spot/tickers",
                    params={"currency_pair": "BTC_USDT"},
                    timeout=8,
                )
                if not isinstance(payload, list) or not payload:
                    raise RuntimeError("Gate.io 返回空数据")
                ticker = payload[0]
                usd = to_float(ticker.get("last"))
                change = to_float(ticker.get("change_percentage"))
                if usd is None:
                    raise RuntimeError("Gate.io 返回空价格")

                change_text = ""
                if change is not None:
                    sign = "+" if change > 0 else ""
                    change_text = f" ({sign}{change:.2f}% / 24h)"
                return f"BTC: ${usd:,.2f}{change_text} (Gate.io)"
            except Exception as third_exc:  # noqa: BLE001
                raise RuntimeError(
                    f"CoinGecko失败: {first_exc}; Binance失败: {second_exc}; Gate.io失败: {third_exc}"
                ) from third_exc


def build_report(
    city_name: str,
    timezone: str,
    latitude: Optional[float],
    longitude: Optional[float],
    stock_codes: str,
) -> str:
    try:
        now = dt.datetime.now(ZoneInfo(timezone))
    except Exception:  # noqa: BLE001
        timezone = "UTC"
        now = dt.datetime.now(dt.timezone.utc)

    lines = [
        "每日资讯推送",
        f"时间: {now:%Y-%m-%d %H:%M} ({timezone})",
        "",
        "[天气]",
    ]

    try:
        lines.append(fetch_weather(city_name, timezone, latitude, longitude))
    except Exception as exc:  # noqa: BLE001
        lines.append(f"天气获取失败: {exc}")

    lines.extend(["", "[A股]"])
    lines.extend(fetch_a_share_block(stock_codes))

    lines.extend(["", "[比特币]"])
    try:
        lines.append(fetch_btc_line())
    except Exception as exc:  # noqa: BLE001
        lines.append(f"BTC 获取失败: {exc}")

    return "\n".join(lines)


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    response = requests.post(
        url,
        json={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        },
        timeout=20,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()

    payload = response.json()
    if not payload.get("ok"):
        description = payload.get("description", "unknown error")
        raise RuntimeError(f"Telegram API error: {description}")


def parse_optional_float(value: str, name: str) -> Optional[float]:
    if value == "":
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"环境变量 {name} 不是有效数字: {value}") from exc


def main() -> int:
    try:
        dry_run = read_bool_env("DRY_RUN", default=False)
        bot_token = read_env("TELEGRAM_BOT_TOKEN", default="", required=not dry_run)
        chat_id = read_env("TELEGRAM_CHAT_ID", default="", required=not dry_run)

        city_name = read_env("CITY_NAME", default="Shanghai")
        timezone = read_env("TIMEZONE", default="Asia/Shanghai")
        stock_codes = read_env("A_STOCK_CODES", default="600519,000001,300750")

        latitude = parse_optional_float(read_env("WEATHER_LATITUDE", default=""), "WEATHER_LATITUDE")
        longitude = parse_optional_float(read_env("WEATHER_LONGITUDE", default=""), "WEATHER_LONGITUDE")

        report = build_report(city_name, timezone, latitude, longitude, stock_codes)
        print(report)
        if not dry_run:
            send_telegram_message(bot_token, chat_id, report)
        else:
            print("DRY_RUN=true, skipped Telegram send.")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"执行失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
