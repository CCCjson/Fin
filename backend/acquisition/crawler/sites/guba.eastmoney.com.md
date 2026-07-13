# guba.eastmoney.com 逆向接口说明

- 侦查页面：https://guba.eastmoney.com/list,600519.html
- 侦查时间：2026-07-02T22:01:57
- 端点数量：3

> 由 discover_api 自动产出。fetch_api 读同目录 .json 复用；本文件供人工查阅/参考。

## 1. companyinfo

- URL：`https://eminterservice.securities.eastmoney.com/api/data/companyinfo?data=%7B%22appKey%22%3A%22%22%2C%22client%22%3A%22wap%22%2C%22method%22%3A%22qgqm%22%2C%22args%22%3A%7B%22fundId%22%3A%22%22%2C%22hkFundId%22%3A%22%22%2C%22uid%22%3A%22%22%2C%22customerId%22%3A%22%22%2C%22custid%22%3A%22%22%2C%22pageId%22%3A%22Stock_quotes_page%22%2C%22positions%22%3A%22Stock_quotes_page_gbxf_text%22%2C%22stockCodeWithoutMarket%22%3A%22600519%22%2C%22marketCode%22%3A1%7D%7D&t=0.7998558020505063`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile`

## 2. GetWebTape

- URL：`https://eminterservice.eastmoney.com/UserData/GetWebTape?code=sh600519`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile`

## 3. getcontextid

- URL：`https://i.eastmoney.com/websitecaptcha/api/getcontextid`
- 方法：POST　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile, x-requested-with`
