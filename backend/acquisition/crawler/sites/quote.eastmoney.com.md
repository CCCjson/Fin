# quote.eastmoney.com 逆向接口说明

- 侦查页面：https://quote.eastmoney.com/unify/r/118.AU9999
- 侦查时间：2026-07-24T14:57:26
- 端点数量：6

> 由 scrape 侦查时自动产出；scrape 读同目录 .json 复用取数；本文件供人工查阅/参考。

## 1. get

- URL：`https://push2.eastmoney.com/api/qt/ulist/get?fltt=1&invt=2&fields=f14%2Cf12%2Cf13%2Cf1%2Cf2%2Cf4%2Cf3%2Cf152&secids=1.000001%2C0.399001%2C100.N225%2C100.HSI%2C100.DJIA%2C100.NDX%2C100.SPX%2C100.FTSE%2C100.GDAXI%2C100.FCHI%2C100.SSMI&ut=fa5fd1943c7b386f172d6893dbfba10b&pn=1&np=1&pz=20&dect=1&wbp2u=%7C0%7C0%7C0%7Cweb`
- 方法：GET　状态：200　JSON：True
- Gate 头：`origin, referer, sec-ch-ua, sec-ch-ua-mobile, sec-ch-ua-platform`

## 2. get_2

- URL：`https://push2.eastmoney.com/api/qt/ulist/get?fltt=1&invt=2&fields=f14%2Cf12%2Cf13%2Cf1%2Cf2%2Cf4%2Cf3%2Cf152&secids=100.UDI%2C120.USDCNYC%2C133.USDCNH%2C119.EURUSD%2C119.USDJPY%2C119.GBPUSD%2C119.AUDUSD%2C119.USDHKD%2C119.USDCHF%2C119.NZDUSD&ut=fa5fd1943c7b386f172d6893dbfba10b&pn=1&np=1&pz=20&dect=1&wbp2u=%7C0%7C0%7C0%7Cweb`
- 方法：GET　状态：200　JSON：True
- Gate 头：`origin, referer, sec-ch-ua, sec-ch-ua-mobile, sec-ch-ua-platform`

## 3. gethotgubalist

- URL：`https://quote.eastmoney.com/newapi/gethotgubalist`
- 方法：GET　状态：200　JSON：True
- Gate 头：`referer, x-requested-with, sec-ch-ua, sec-ch-ua-mobile, sec-ch-ua-platform`
- 字段样例：name, guba_code, quote_code

## 4. get_3

- URL：`https://push2.eastmoney.com/api/qt/ulist/get?fltt=1&invt=2&fields=f14%2Cf12%2Cf13%2Cf1%2Cf2%2Cf4%2Cf3%2Cf152&secids=0.002156%2C0.001258%2C0.002185%2C1.601606%2C0.000938%2C1.601179%2C1.600584%2C0.000021%2C1.600396%2C0.001309%2C1.600664%2C1.601678%2C0.002498%2C0.000815%2C0.301583&ut=fa5fd1943c7b386f172d6893dbfba10b&pn=1&np=1&pz=20&dect=1&wbp2u=%7C0%7C0%7C0%7Cweb`
- 方法：GET　状态：200　JSON：True
- Gate 头：`origin, referer, sec-ch-ua, sec-ch-ua-mobile, sec-ch-ua-platform`

## 5. webreport

- URL：`https://anonflow2.eastmoney.com/backend/api/webreport`
- 方法：POST　状态：405　JSON：True
- Gate 头：`origin, referer, sec-ch-ua, sec-ch-ua-mobile, sec-ch-ua-platform`
- 字段样例：timestamp, status, error, path

## 6. getcontextid

- URL：`https://i.eastmoney.com/websitecaptcha/api/getcontextid`
- 方法：POST　状态：200　JSON：True
- Gate 头：`origin, referer, x-requested-with, sec-ch-ua, sec-ch-ua-mobile, sec-ch-ua-platform`
