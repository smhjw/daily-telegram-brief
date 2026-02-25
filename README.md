# Daily Brief (GitHub Actions)

每天自动推送以下信息到 Telegram（可选同时转发到个人微信）：
- 天气温度
- A 股行情
- 黄金价格与持仓盈亏
- BTC / ETH 价格

## 1. 准备 Telegram Bot

1. 在 Telegram 里找 `@BotFather`，发送 `/newbot` 创建机器人。
2. 记录 Bot Token（形如 `123456:ABC...`）。
3. 给机器人发一条消息。
4. 打开：
   - `https://api.telegram.org/bot<你的Token>/getUpdates`
5. 在返回 JSON 中找到 `message.chat.id`，这就是 `TELEGRAM_CHAT_ID`。

## 2. 配置 GitHub 仓库

把这个目录代码推到你的 GitHub 仓库后，配置：

### Secrets（必须）
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

### Secrets（可选，用于个人微信）
- `WECHAT_SENDKEY`：Server酱 SendKey（到 [sct.ftqq.com](https://sct.ftqq.com/) 获取）
  - 配置后会把同一条消息额外推送到微信
  - 不配置则仅推送 Telegram

### Variables（可选）
- `CITY_NAME`：城市名，默认 `Shanghai`
- `WEATHER_LATITUDE`：纬度（可选，填了可跳过城市地理解析）
- `WEATHER_LONGITUDE`：经度（可选）
- `A_STOCK_CODES`：股票列表，默认 `600519,002605,sh000001`
  - 支持格式：`600519` 或 `sh600519` / `sz000001`
  - 示例中对应：贵州茅台、姚记科技、上证指数（`sh000001`）
- `GOLD_HOLDING_GRAMS`：持仓克数（可选）
- `GOLD_TOTAL_COST_CNY`：黄金总成本（人民币，可选，优先使用）
- `GOLD_COST_PER_GRAM_CNY`：黄金成本单价（人民币/克，可选）
- `TIMEZONE`：默认 `Asia/Shanghai`
- `DRY_RUN`：`true` 时只打印结果，不发送 Telegram/微信（默认 `false`）

## 3. 定时执行

工作流文件：`.github/workflows/daily-telegram-brief.yml`

默认 cron：
- `35 1 * * *`（UTC）= 北京时间每天 `09:35`
- `0 13 * * *`（UTC）= 北京时间每天 `21:00`

你也可以在 GitHub Actions 页面手动点击 `Run workflow` 立即测试。

## 4. 本地调试（可选）

```bash
pip install -r requirements.txt
set TELEGRAM_BOT_TOKEN=xxx
set TELEGRAM_CHAT_ID=xxx
set WECHAT_SENDKEY=xxx
set GOLD_HOLDING_GRAMS=20
set GOLD_TOTAL_COST_CNY=10800
set DRY_RUN=true
python main_telegram.py
```

## 5. 数据来源

- 天气：Open-Meteo
- A 股：东方财富公开行情接口
- 黄金：Gate.io XAUT/PAXG（失败时回退 Stooq XAUUSD）+ 汇率接口折算为 CNY/克
- 加密货币（BTC/ETH）：CoinGecko（失败时自动回退 Binance / Gate.io）
- 微信转发：Server酱
