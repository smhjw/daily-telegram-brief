#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import base64
import hashlib
import hmac
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = "daily-telegram-brief/1.0"
TROY_OUNCE_TO_GRAM = 31.1034768
HTTP_RETRY_TOTAL = 3
HTTP_BACKOFF_FACTOR = 0.8
RETRY_STATUS_CODES = (429, 500, 502, 503, 504)
REPORT_TITLE = "每日资讯推送"
SECTION_WEATHER = "weather"
SECTION_STOCK = "stock"
SECTION_GOLD = "gold"
SECTION_CRYPTO = "crypto"
SECTION_STATUS_OK = "ok"
SECTION_STATUS_PARTIAL = "partial"
SECTION_STATUS_ERROR = "error"
SECTION_STATUS_SUFFIX = {
    SECTION_STATUS_OK: "",
    SECTION_STATUS_PARTIAL: "（部分失败）",
    SECTION_STATUS_ERROR: "（获取失败）",
}
SECTION_TITLES = {
    SECTION_WEATHER: "天气",
    SECTION_STOCK: "A股",
    SECTION_GOLD: "黄金",
    SECTION_CRYPTO: "加密货币",
}
SECTION_ICONS = {
    SECTION_WEATHER: "🌤️",
    SECTION_STOCK: "📈",
    SECTION_GOLD: "🥇",
    SECTION_CRYPTO: "🪙",
}
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


def create_http_session() -> requests.Session:
    retry = Retry(
        total=HTTP_RETRY_TOTAL,
        connect=HTTP_RETRY_TOTAL,
        read=HTTP_RETRY_TOTAL,
        backoff_factor=HTTP_BACKOFF_FACTOR,
        status_forcelist=RETRY_STATUS_CODES,
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


HTTP_SESSION = create_http_session()


@dataclass(frozen=True)
class ReportSection:
    key: str
    title: str
    items: list[str]
    errors: list[str]
    status: str


@dataclass(frozen=True)
class SectionCollection:
    items: list[str]
    errors: list[str]
    successful_items: int


@dataclass(frozen=True)
class ReportData:
    title: str
    generated_at: dt.datetime
    timezone: str
    sections: list[ReportSection]

    def partial_errors(self) -> list[str]:
        messages: list[str] = []
        for section in self.sections:
            for error in section.errors:
                if error.startswith(section.title):
                    messages.append(error)
                else:
                    messages.append(f"{section.title}: {error}")
        return messages

    def has_partial_errors(self) -> bool:
        return any(section.errors for section in self.sections)


@dataclass(frozen=True)
class AppConfig:
    dry_run: bool
    fail_on_partial_error: bool
    city_name: str
    timezone: str
    stock_codes: str
    latitude: Optional[float]
    longitude: Optional[float]
    gold_holding_grams: Optional[float]
    gold_total_cost_cny: Optional[float]
    gold_cost_per_gram_cny: Optional[float]
    telegram_bot_token: str
    telegram_chat_id: str
    wechat_sendkey: str
    dingtalk_webhook: str
    dingtalk_secret: str

    @property
    def has_telegram_channel(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def has_wechat_channel(self) -> bool:
        return bool(self.wechat_sendkey)

    @property
    def has_dingtalk_channel(self) -> bool:
        return bool(self.dingtalk_webhook)

    @property
    def has_any_channel(self) -> bool:
        return self.has_telegram_channel or self.has_wechat_channel or self.has_dingtalk_channel


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


def configure_console_encoding() -> None:
    # Prevent UnicodeEncodeError on some Windows terminals when output contains emoji.
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass


def to_bulleted_lines(raw_lines: list[str]) -> list[str]:
    formatted: list[str] = []
    for raw in raw_lines:
        text = raw.strip()
        if text.startswith("- "):
            text = text[2:]
        formatted.append(f"• {text}")
    return formatted


def status_from_collection(result: SectionCollection) -> str:
    if result.errors and result.successful_items == 0:
        return SECTION_STATUS_ERROR
    if result.errors:
        return SECTION_STATUS_PARTIAL
    return SECTION_STATUS_OK


def build_report_section(key: str, collection: SectionCollection) -> ReportSection:
    return ReportSection(
        key=key,
        title=SECTION_TITLES[key],
        items=collection.items,
        errors=collection.errors,
        status=status_from_collection(collection),
    )


def collect_single_source_section(fetcher, error_prefix: str) -> SectionCollection:
    try:
        result = fetcher()
    except Exception as exc:  # noqa: BLE001
        message = f"{error_prefix}: {exc}"
        return SectionCollection(items=[message], errors=[message], successful_items=0)

    items = result if isinstance(result, list) else [result]
    return SectionCollection(items=items, errors=[], successful_items=len(items))


def request_json(url: str, *, params: Optional[dict] = None, timeout: int = 20) -> dict:
    response = HTTP_SESSION.get(
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
    return fetch_a_share_block_with_errors(raw_codes).items


def fetch_a_share_block_with_errors(raw_codes: str) -> SectionCollection:
    codes = [code for code in re.split(r"[,\s]+", raw_codes.strip()) if code]
    if not codes:
        return SectionCollection(items=["- 未配置股票代码"], errors=[], successful_items=0)

    lines: list[str] = []
    errors: list[str] = []
    successful_items = 0
    for code in codes:
        try:
            lines.append(fetch_a_share_line(code))
            successful_items += 1
        except Exception as exc:  # noqa: BLE001
            message = f"{code}: 获取失败 ({exc})"
            lines.append(f"- {message}")
            errors.append(message)
    return SectionCollection(items=lines, errors=errors, successful_items=successful_items)


def format_change_text(change: Optional[float]) -> str:
    if change is None:
        return ""
    sign = "+" if change > 0 else ""
    return f" ({sign}{change:.2f}% / 24h)"


def fetch_crypto_line(name: str, coingecko_id: str, binance_symbol: str, gateio_pair: str) -> str:
    try:
        payload = request_json(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": coingecko_id,
                "vs_currencies": "usd,cny",
                "include_24hr_change": "true",
            },
            timeout=10,
        )
        coin = payload.get(coingecko_id) or {}

        usd = to_float(coin.get("usd"))
        cny = to_float(coin.get("cny"))
        change = to_float(coin.get("usd_24h_change"))
        if usd is None:
            raise RuntimeError("CoinGecko 返回空价格")

        if cny is not None:
            return f"{name}: ${usd:,.2f} | CNY {cny:,.2f}{format_change_text(change)}"
        return f"{name}: ${usd:,.2f}{format_change_text(change)}"
    except Exception as first_exc:  # noqa: BLE001
        try:
            payload = request_json(
                "https://api.binance.com/api/v3/ticker/24hr",
                params={"symbol": binance_symbol},
                timeout=8,
            )
            usd = to_float(payload.get("lastPrice"))
            change = to_float(payload.get("priceChangePercent"))
            if usd is None:
                raise RuntimeError("Binance 返回空价格")
            return f"{name}: ${usd:,.2f}{format_change_text(change)} (Binance)"
        except Exception as second_exc:  # noqa: BLE001
            try:
                payload = request_json(
                    "https://api.gateio.ws/api/v4/spot/tickers",
                    params={"currency_pair": gateio_pair},
                    timeout=8,
                )
                if not isinstance(payload, list) or not payload:
                    raise RuntimeError("Gate.io 返回空数据")
                ticker = payload[0]
                usd = to_float(ticker.get("last"))
                change = to_float(ticker.get("change_percentage"))
                if usd is None:
                    raise RuntimeError("Gate.io 返回空价格")
                return f"{name}: ${usd:,.2f}{format_change_text(change)} (Gate.io)"
            except Exception as third_exc:  # noqa: BLE001
                raise RuntimeError(
                    f"CoinGecko失败: {first_exc}; Binance失败: {second_exc}; Gate.io失败: {third_exc}"
                ) from third_exc


def fetch_crypto_block() -> list[str]:
    return fetch_crypto_block_with_errors().items


def fetch_crypto_block_with_errors() -> SectionCollection:
    lines: list[str] = []
    errors: list[str] = []
    successful_items = 0
    crypto_targets = [
        ("BTC", "bitcoin", "BTCUSDT", "BTC_USDT"),
        ("ETH", "ethereum", "ETHUSDT", "ETH_USDT"),
    ]
    for name, coingecko_id, binance_symbol, gateio_pair in crypto_targets:
        try:
            lines.append(fetch_crypto_line(name, coingecko_id, binance_symbol, gateio_pair))
            successful_items += 1
        except Exception as exc:  # noqa: BLE001
            message = f"{name} 获取失败: {exc}"
            lines.append(message)
            errors.append(message)
    return SectionCollection(items=lines, errors=errors, successful_items=successful_items)


def fetch_usd_cny_rate() -> float:
    payload = request_json(
        "https://open.er-api.com/v6/latest/USD",
        timeout=12,
    )
    if payload.get("result") != "success":
        raise RuntimeError(f"汇率接口异常: {payload.get('error-type', 'unknown')}")
    rate = to_float((payload.get("rates") or {}).get("CNY"))
    if rate is None:
        raise RuntimeError("汇率接口未返回 CNY")
    return rate


def fetch_gold_usd_per_oz() -> tuple[float, Optional[float], str]:
    # Preferred source: tokenized gold spot proxies from Gate.io
    gate_pairs = [
        ("XAUT_USDT", "Gate.io XAUT"),
        ("PAXG_USDT", "Gate.io PAXG"),
    ]
    gate_errors: list[str] = []
    for pair, source_name in gate_pairs:
        try:
            payload = request_json(
                "https://api.gateio.ws/api/v4/spot/tickers",
                params={"currency_pair": pair},
                timeout=10,
            )
            if not isinstance(payload, list) or not payload:
                raise RuntimeError("返回空数据")
            ticker = payload[0]
            usd = to_float(ticker.get("last"))
            change = to_float(ticker.get("change_percentage"))
            if usd is None:
                raise RuntimeError("返回空价格")
            return usd, change, source_name
        except Exception as exc:  # noqa: BLE001
            gate_errors.append(f"{pair}: {exc}")

    # Fallback source: stooq XAUUSD csv quote
    try:
        response = HTTP_SESSION.get(
            "https://stooq.com/q/l/?s=xauusd&i=d",
            timeout=10,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        parts = response.text.strip().split(",")
        if len(parts) < 7:
            raise RuntimeError("CSV 字段不足")
        close_price = to_float(parts[6])
        if close_price is None:
            raise RuntimeError("解析收盘价失败")
        return close_price, None, "Stooq XAUUSD"
    except Exception as stooq_exc:  # noqa: BLE001
        raise RuntimeError(
            f"Gate.io失败: {'; '.join(gate_errors)}; Stooq失败: {stooq_exc}"
        ) from stooq_exc


def build_gold_block(holding_grams: Optional[float], total_cost_cny: Optional[float], cost_per_gram_cny: Optional[float]) -> list[str]:
    usd_per_oz, change_pct, source = fetch_gold_usd_per_oz()
    usd_cny = fetch_usd_cny_rate()
    cny_per_gram = usd_per_oz * usd_cny / TROY_OUNCE_TO_GRAM

    lines = [f"金价: ${usd_per_oz:,.2f}/oz | CNY {cny_per_gram:,.2f}/g{format_change_text(change_pct)} ({source})"]

    if holding_grams is None or holding_grams <= 0:
        return lines

    lines.append(f"持仓: {holding_grams:,.4f} g")
    current_value = holding_grams * cny_per_gram
    lines.append(f"当前总价: CNY {current_value:,.2f}")

    effective_total_cost = total_cost_cny
    if effective_total_cost is None and cost_per_gram_cny is not None:
        effective_total_cost = holding_grams * cost_per_gram_cny

    if effective_total_cost is not None and effective_total_cost > 0:
        pnl = current_value - effective_total_cost
        pnl_pct = pnl / effective_total_cost * 100
        sign = "+" if pnl > 0 else ""
        lines.append(f"总成本: CNY {effective_total_cost:,.2f}")
        lines.append(f"盈亏: {sign}CNY {pnl:,.2f} ({sign}{pnl_pct:.2f}%)")
    elif cost_per_gram_cny is not None and cost_per_gram_cny > 0:
        lines.append(f"成本单价: CNY {cost_per_gram_cny:,.2f}/g")

    return lines


def collect_report_data(
    city_name: str,
    timezone: str,
    latitude: Optional[float],
    longitude: Optional[float],
    stock_codes: str,
    gold_holding_grams: Optional[float],
    gold_total_cost_cny: Optional[float],
    gold_cost_per_gram_cny: Optional[float],
) -> ReportData:
    try:
        now = dt.datetime.now(ZoneInfo(timezone))
    except Exception:  # noqa: BLE001
        timezone = "UTC"
        now = dt.datetime.now(dt.timezone.utc)

    sections = [
        build_report_section(
            SECTION_WEATHER,
            collect_single_source_section(
                lambda: fetch_weather(city_name, timezone, latitude, longitude),
                "天气获取失败",
            ),
        ),
        build_report_section(SECTION_STOCK, fetch_a_share_block_with_errors(stock_codes)),
        build_report_section(
            SECTION_GOLD,
            collect_single_source_section(
                lambda: build_gold_block(gold_holding_grams, gold_total_cost_cny, gold_cost_per_gram_cny),
                "黄金获取失败",
            ),
        ),
        build_report_section(SECTION_CRYPTO, fetch_crypto_block_with_errors()),
    ]

    return ReportData(
        title=REPORT_TITLE,
        generated_at=now,
        timezone=timezone,
        sections=sections,
    )


def render_telegram_report(report: ReportData) -> str:
    lines = [
        f"🗞️ {report.title}",
        f"🕒 {report.generated_at:%Y-%m-%d %H:%M} · {report.timezone}",
    ]

    for section in report.sections:
        lines.extend(
            [
                "",
                f"{SECTION_ICONS.get(section.key, '•')} {section.title}{SECTION_STATUS_SUFFIX.get(section.status, '')}",
                "────────────────",
            ]
        )
        items = get_clean_section_items(section)
        if items:
            lines.extend(f"• {item}" for item in items)
        else:
            lines.append("• 暂无数据")

    return "\n".join(lines)


def clean_stock_line(raw: str) -> str:
    text = raw.strip()
    if text.startswith("- "):
        text = text[2:]

    m = re.match(r"^(.*?)\s*\([A-Za-z]{2}\d{6}\):\s*([0-9,.]+\s*CNY)\s*\(([+-]?[0-9.]+%)", text)
    if m:
        name, price, pct = m.groups()
        return f"• {name}: {price} | {pct}"

    text = re.sub(r"\s*\([A-Za-z]{2}\d{6}\)", "", text)
    text = re.sub(r"\(([+-]?[0-9.]+%)\s*,\s*[^)]*\)", r"(\1)", text)
    text = re.sub(r"\(([+-]?[0-9.]+%)\)", r"| \1", text)
    return f"• {text}"


def clean_gold_lines(raw_lines: list[str]) -> list[str]:
    if not raw_lines:
        return []

    lines = [line.strip() for line in raw_lines if line.strip()]
    result: list[str] = []

    first = lines[0]
    match = re.search(
        r"CNY\s*([0-9,]+(?:\.[0-9]+)?)/g(?:\s*\(([+-]?[0-9.]+%)\s*/\s*24h\))?(?:\s*\(([^()]+)\))?",
        first,
    )
    if match:
        price_cny, change_pct, source = match.groups()
        parts = [f"金价: CNY {price_cny}/g"]
        if change_pct:
            parts.append(f"24h {change_pct}")
        if source:
            parts.append(source)
        result.append(f"• {' | '.join(parts)}")
    else:
        result.append(f"• {first}")

    for line in lines[1:]:
        normalized = line.replace("当前总价", "当前市值").replace("盈亏", "浮动盈亏")
        result.append(f"• {normalized}")
    return result


def clean_crypto_line(raw: str, *, include_cny: bool = False) -> str:
    text = raw.strip()
    if text.startswith("- "):
        text = text[2:]
    if not include_cny:
        text = re.sub(r"\s*\|\s*CNY\s*[0-9,]+(?:\.[0-9]+)?", "", text)
    text = re.sub(r"\(\s*([+-]?[0-9.]+%)\s*/\s*24h\s*\)", r"| 24h \1", text)
    return f"• {text}"


def clean_weather_line(raw: str) -> str:
    text = raw.strip()
    match = re.match(
        r"^(.*?):\s*(.*?)，当前\s*([0-9.]+°C)，体感\s*([0-9.]+°C)，最高/最低\s*([0-9.]+)/([0-9.]+)°C(?:，风速\s*([0-9.]+)\s*km/h)?$",
        text,
    )
    if match:
        city, weather, current, feels_like, high, low, wind = match.groups()
        parts = [
            f"{city}: {weather}",
            f"当前 {current}",
            f"体感 {feels_like}",
            f"高/低 {high}/{low}°C",
        ]
        if wind:
            parts.append(f"风速 {wind} km/h")
        return f"• {' | '.join(parts)}"
    return f"• {text}"


def strip_bullet_prefix(text: str) -> str:
    if text.startswith("• "):
        return text[2:].strip()
    return text.strip()


def get_clean_section_items(section: ReportSection) -> list[str]:
    if section.key == SECTION_WEATHER:
        return [strip_bullet_prefix(clean_weather_line(item)) for item in section.items]
    if section.key == SECTION_GOLD:
        return [strip_bullet_prefix(item) for item in clean_gold_lines(section.items)]
    if section.key == SECTION_CRYPTO:
        return [strip_bullet_prefix(clean_crypto_line(item)) for item in section.items]
    if section.key == SECTION_STOCK:
        return [strip_bullet_prefix(clean_stock_line(item)) for item in section.items]
    return [item.strip() for item in section.items if item.strip()]


def send_wechat_serverchan(sendkey: str, text: str, title: str = REPORT_TITLE) -> None:
    url = f"https://sctapi.ftqq.com/{sendkey}.send"
    response = HTTP_SESSION.post(
        url,
        data={
            "title": title,
            "desp": text,
        },
        timeout=20,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()

    payload = response.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"Server酱 API error: {payload.get('message', 'unknown error')}")


def build_wechat_markdown(report: ReportData) -> str:
    out: list[str] = [f"## {report.title}", f"> {report.generated_at:%Y-%m-%d %H:%M} ({report.timezone})"]

    for section in report.sections:
        out.append("")
        out.append(f"### {SECTION_ICONS.get(section.key, '•')} {section.title}{SECTION_STATUS_SUFFIX.get(section.status, '')}")
        items = get_clean_section_items(section)
        if not items:
            out.append("- 暂无数据")
            continue
        for item in items:
            out.append(f"- {item}")

    return "\n".join(out)


def build_dingtalk_markdown(report: ReportData) -> tuple[str, str]:
    def fmt_item(section_key: str, item: str) -> str:
        text = item.strip()
        text = text.replace(":", "：", 1)
        text = re.sub(r"：\s*", "：", text, count=1)

        if section_key == SECTION_WEATHER:
            match = re.match(
                r"^(.*?)：\s*(.*?)\s*\|\s*当前\s*([0-9.]+°C)\s*\|\s*体感\s*([0-9.]+°C)\s*\|\s*高/低\s*([0-9.]+)/([0-9.]+)°C(?:\s*\|\s*风速\s*([0-9.]+\s*km/h))?$",
                text,
            )
            if match:
                city, weather, temp, feels, high, low, wind = match.groups()
                parts = [f"- **{city}**：{weather}", f"当前/体感：**{temp} / {feels}**", f"高/低：**{high}/{low}°C**"]
                if wind:
                    parts.append(f"风速：**{wind}**")
                return "｜".join(parts)
            return f"- {text}"

        if section_key == SECTION_GOLD:
            if text.startswith("金价："):
                value = text.split("：", 1)[1].strip().replace(" | ", "｜")
                return f"- 金价：**{value}**"
            if text.startswith("持仓："):
                return f"- 持仓：**{text.split('：', 1)[1].strip()}**"
            if text.startswith("当前市值："):
                return f"- 当前市值：**{text.split('：', 1)[1].strip()}**"
            if text.startswith("总成本："):
                return f"- 总成本：{text.split('：', 1)[1].strip()}"
            if text.startswith("浮动盈亏："):
                match = re.match(r"^浮动盈亏：\s*([+-][^（(]+)\s*[（(]([+-]?[0-9.]+%)[）)]$", text)
                if match:
                    pnl_value, pnl_pct = match.groups()
                    icon = "🟢" if pnl_value.startswith("+") else "🔴"
                    return f"- {icon} 浮动盈亏：**{pnl_value.strip()}**（{pnl_pct}）"
            return f"- {text}"

        if section_key == SECTION_CRYPTO:
            text = re.sub(r"\(\s*([+-]?[0-9.]+%)\s*/\s*24h\s*\)", r"（24h \1）", text)
            text = re.sub(r"\(([^()]+)\)", r"（\1）", text)
            text = re.sub(r"\s+（", "（", text)
            text = text.replace(" | ", "｜")
            return f"- {text}"

        if section_key == SECTION_STOCK:
            text = re.sub(r"\(([+-]?[0-9.]+%)\)", r"（\1）", text)
            text = re.sub(r"\s+（", "（", text)
            text = text.replace(" | ", "｜")
            return f"- {text}"

        return f"- {text}"

    title = report.title
    out: list[str] = [f"## 🗞️ {title}", f"> ⏰ {report.generated_at:%Y-%m-%d %H:%M} ({report.timezone})"]

    for section in report.sections:
        out.append("")
        out.append(f"### {SECTION_ICONS.get(section.key, '•')} {section.title}{SECTION_STATUS_SUFFIX.get(section.status, '')}")
        items = get_clean_section_items(section)
        if not items:
            out.append("- 暂无数据")
            continue
        for item in items:
            out.append(fmt_item(section.key, item))

    return title, "\n".join(out)


def sign_dingtalk_url(webhook: str, secret: str) -> str:
    timestamp = str(int(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    sign = quote_plus(base64.b64encode(hmac_code))
    sep = "&" if "?" in webhook else "?"
    return f"{webhook}{sep}timestamp={timestamp}&sign={sign}"


def send_dingtalk_robot(webhook: str, markdown_text: str, title: str, secret: str = "") -> None:
    url = sign_dingtalk_url(webhook, secret) if secret else webhook
    response = HTTP_SESSION.post(
        url,
        json={
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": markdown_text,
            },
        },
        timeout=20,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()

    payload = response.json()
    if payload.get("errcode") != 0:
        raise RuntimeError(f"DingTalk API error: {payload.get('errmsg', 'unknown error')}")


def build_report(report: ReportData) -> str:
    lines = [
        f"🗞️ {report.title}",
        f"🕒 {report.generated_at:%Y-%m-%d %H:%M} ({report.timezone})",
    ]

    for section in report.sections:
        lines.extend(
            [
                "━━━━━━━━━━━━",
                f"{SECTION_ICONS.get(section.key, '•')} {section.title}",
            ]
        )
        for item in get_clean_section_items(section):
            lines.append(f"• {item}")

    return "\n".join(lines)


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    response = HTTP_SESSION.post(
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


def load_config_from_env() -> AppConfig:
    return AppConfig(
        dry_run=read_bool_env("DRY_RUN", default=False),
        fail_on_partial_error=read_bool_env("FAIL_ON_PARTIAL_ERROR", default=True),
        city_name=read_env("CITY_NAME", default="Shanghai"),
        timezone=read_env("TIMEZONE", default="Asia/Shanghai"),
        stock_codes=read_env("A_STOCK_CODES", default="600519,002605,sh000001"),
        latitude=parse_optional_float(read_env("WEATHER_LATITUDE", default=""), "WEATHER_LATITUDE"),
        longitude=parse_optional_float(read_env("WEATHER_LONGITUDE", default=""), "WEATHER_LONGITUDE"),
        gold_holding_grams=parse_optional_float(read_env("GOLD_HOLDING_GRAMS", default=""), "GOLD_HOLDING_GRAMS"),
        gold_total_cost_cny=parse_optional_float(read_env("GOLD_TOTAL_COST_CNY", default=""), "GOLD_TOTAL_COST_CNY"),
        gold_cost_per_gram_cny=parse_optional_float(read_env("GOLD_COST_PER_GRAM_CNY", default=""), "GOLD_COST_PER_GRAM_CNY"),
        telegram_bot_token=read_env("TELEGRAM_BOT_TOKEN", default=""),
        telegram_chat_id=read_env("TELEGRAM_CHAT_ID", default=""),
        wechat_sendkey=read_env("WECHAT_SENDKEY", default=""),
        dingtalk_webhook=read_env("DINGTALK_WEBHOOK", default=""),
        dingtalk_secret=read_env("DINGTALK_SECRET", default=""),
    )


def validate_config(config: AppConfig) -> None:
    if config.dry_run:
        return

    if not config.has_any_channel:
        raise ValueError(
            "No push channel configured. Configure Telegram (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID), "
            "WECHAT_SENDKEY, or DINGTALK_WEBHOOK."
        )


def send_report_to_channels(config: AppConfig, report: ReportData, rendered_report: str) -> list[str]:
    errors: list[str] = []
    succeeded_channels = 0

    if config.has_telegram_channel:
        try:
            send_telegram_message(config.telegram_bot_token, config.telegram_chat_id, rendered_report)
            succeeded_channels += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Telegram send failed: {exc}")
    elif config.telegram_bot_token or config.telegram_chat_id:
        errors.append("Telegram config is incomplete. Both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required.")

    if config.has_wechat_channel:
        try:
            wechat_markdown = build_wechat_markdown(report)
            send_wechat_serverchan(config.wechat_sendkey, wechat_markdown)
            succeeded_channels += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"WeChat send failed: {exc}")

    if config.has_dingtalk_channel:
        try:
            ding_title, ding_markdown = build_dingtalk_markdown(report)
            send_dingtalk_robot(config.dingtalk_webhook, ding_markdown, ding_title, config.dingtalk_secret)
            succeeded_channels += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"DingTalk send failed: {exc}")

    if errors:
        error_message = "; ".join(errors)
        if config.fail_on_partial_error or succeeded_channels == 0:
            raise RuntimeError(error_message)
        print(f"Partial channel failures: {error_message}", file=sys.stderr)

    return errors


def main() -> int:
    try:
        configure_console_encoding()
        config = load_config_from_env()
        validate_config(config)

        report = collect_report_data(
            config.city_name,
            config.timezone,
            config.latitude,
            config.longitude,
            config.stock_codes,
            config.gold_holding_grams,
            config.gold_total_cost_cny,
            config.gold_cost_per_gram_cny,
        )
        rendered_report = build_report(report)
        data_errors = report.partial_errors()

        print(rendered_report)
        if data_errors and config.fail_on_partial_error:
            raise RuntimeError("; ".join(data_errors))

        if not config.dry_run:
            send_report_to_channels(config, report, rendered_report)
        else:
            print("DRY_RUN=true, skipped Telegram/WeChat/DingTalk send.")

        if data_errors:
            print(f"Partial data failures: {'; '.join(data_errors)}", file=sys.stderr)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"执行失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
