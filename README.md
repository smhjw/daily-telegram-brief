# Telegram Daily Brief (GitHub Actions)

每天自动推送以下信息到 Telegram：
- 天气温度
- A 股行情
- 比特币价格

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

### Variables（可选）
- `CITY_NAME`：城市名，默认 `Shanghai`
- `WEATHER_LATITUDE`：纬度（可选，填了可跳过城市地理解析）
- `WEATHER_LONGITUDE`：经度（可选）
- `A_STOCK_CODES`：股票列表，默认 `600519,000001,300750`
  - 支持格式：`600519` 或 `sh600519` / `sz000001`
- `TIMEZONE`：默认 `Asia/Shanghai`
- `DRY_RUN`：`true` 时只打印结果，不发送 Telegram（默认 `false`）

## 3. 定时执行

工作流文件：`.github/workflows/daily-telegram-brief.yml`

默认 cron：
- `0 23 * * *`（UTC）
- 对应北京时间每天 `07:00`

你也可以在 GitHub Actions 页面手动点击 `Run workflow` 立即测试。

## 4. 本地调试（可选）

```bash
pip install -r requirements.txt
set TELEGRAM_BOT_TOKEN=xxx
set TELEGRAM_CHAT_ID=xxx
set DRY_RUN=true
python main.py
```

## 5. 数据来源

- 天气：Open-Meteo
- A 股：东方财富公开行情接口
- BTC：CoinGecko（失败时自动回退 Binance / Gate.io）
