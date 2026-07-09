# xueqiu.com 逆向接口说明

- 侦查页面：https://xueqiu.com/S/SH600519
- 侦查时间：2026-07-02T21:56:31
- 端点数量：25

> 由 discover_api 自动产出。fetch_api 读同目录 .json 复用；本文件供人工查阅/参考。

## 1. content

- URL：`https://open.xueqiu.com/mpaas/config/content?keys=stock_detail_AI_switch&uid=&appkey=92f09797f899bdba4fbf01a2829a16d2`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile`
- 字段样例：data, error_code, error_description

## 2. trade

- URL：`https://stock.xueqiu.com/v5/stock/history/trade.json?symbol=SH600519&count=10`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile`
- 字段样例：data, error_code, error_description

## 3. list

- URL：`https://xueqiu.com/statuses/ai_disclose/list.json?md5__1038=214d4f07715-yZ29jTST_CXhHfuDWsTNgeXVT0P8hTImLs8HsGAXCL%2FfvYwJqTynJI_exkTCMn72TUT62XIcTiqeTX9iFT%2F2Pg9TioXLTuZTpTyoTnTvQ%2FTAL9PtTt9ikvfjZT0TvoJTffChTifjhlv5aoTMTYyL1Yf1CfhqTDyOXThZCPq5XTTwQ07Z%2FikZ%2FlTT`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile, x-requested-with`
- 字段样例：display

## 4. zj_card

- URL：`https://xueqiu.com/recommend-proxy/card/zj_card.json?feed_id=1008&count=1&md5__1038=214d4f07715-i9Z9xfSTAfRTjyuXeclZXdXyZo9Wf6W9f%2FwHK57wP6OX%2F0K13qfXSk0snsM2ThabmTPOTpTj69Tv%3DZTjTuGTwT1ofT_f%2FhTQfPcTIfXhTJhkxdT7fqhT8FvXVTq9i1GfvvXnTTGXRwkN4gT_Yy_1DhSmf9LXXyIyTqqu9vm%2FX7qTX88iA7OjXkO1lTT`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile, x-requested-with`
- 字段样例：code, data

## 5. myandanalyst

- URL：`https://stock.xueqiu.com/v5/stock/myandanalyst.json?symbol=SH600519`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile`
- 字段样例：data, error_code, error_description

## 6. entry

- URL：`https://stock.xueqiu.com/v5/stock/event/entry.json?symbol=SH600519`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile`
- 字段样例：data, error_code, error_description

## 7. search

- URL：`https://xueqiu.com/statuses/interview/search.json?symbol=SH600519&count=3&md5__1038=214d4f07715-yZCxCZToqyqoi6FfseTbkT80X8TnmT1D9InBCFjxawqiy8kKxW1TPXp1Bj8fXw73oT%2FoXFT1nXGTv0ZTjTuGTwT1RTT_f%2FhTQfPcTIfXhTJJbxdT7fqhT8FvXVTq9i1GfvvXnTTGXRCkvdUTCo8CSw1tqYi6S2awxTdePwvqXYG1lfTyp9atqxHtqDfT`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile, x-requested-with`
- 字段样例：count, page, maxPage, interviews

## 8. recommend_user

- URL：`https://xueqiu.com/recommend-proxy/recommend_user.json?symbol=SH600519&category=1001&md5__1038=214d4f07715-vZ%3DfYfjTITRTAsqYt%3DfjVhTnTAxT0xifWpYPct9iZvZOF7OwGT%2F6W%3DEj6TiYYBTTUT62XIyyTXi_TToX%3DTPvXUyTXOTpfCyTkf%2FOT6fqcsOjf%2FWTlfqpxT19T8TAVqXXT_ZTCTNw_xnl9idsyY2ni9O%3DPTh3vYyTiYZ3i92O%3DPT1NfTwlA21itkqmlTT`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile, x-requested-with`
- 字段样例：data, message, code

## 9. pofriends

- URL：`https://xueqiu.com/recommend/pofriends.json?type=1&code=SH600519&start=0&count=14&md5__1038=214d4f07715-vZ0fAfRX92LNQ_2PoTovSFVX8NR6w%3DWTViTmxYXTx12qZmcOT%2F6GpW1fiUg5fXgTkfP8TZVTTxCfTOTFfXWTR8vTgTk9i8T62P1T%2F9iuAh69PtTt9ik2fjZT0TvBiTffChTifjhl2HDoX4XJweKlADiXZ2XWZTCxv2ClAZS%2FXaqTTItSQc%3DqpcPuT`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile, x-requested-with`
- 字段样例：totalcount, friends

## 10. relevant

- URL：`https://stock.xueqiu.com/v5/stock/quote/relevant.json?symbol=SH600519`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile`
- 字段样例：data, error_code, error_description

## 11. related_fund

- URL：`https://xueqiu.com/query/v1/search/related_fund.json?symbol=SH600519&q=%E8%B4%B5%E5%B7%9E%E8%8C%85%E5%8F%B0&count=5&page=1&md5__1038=214d4f07715-qZ68jZ%2Fyq62wfmDfsTNff_TfL6TjmfvIJZRaLsZfXy4oxpFY4OTP15JokUufXkq3VT%2FoXFT1Z9T2F9T1T%3D2TDTK7fTzT6ZX0T%2FGXSTPZXCZdPoXQTmZX6c2PhTmfqKAT22iVTX2PVD745OTzJw9CSwe08Q9%3Df28Zf92XCZdPPxG2f2TyVGo2s9d_0YyGT`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile, x-requested-with`
- 字段样例：code, data, message, meta, success

## 12. quote

- URL：`https://stock.xueqiu.com/v5/stock/quote.json?symbol=SH600519&extend=detail`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile`
- 字段样例：data, error_code, error_description

## 13. stockList

- URL：`https://xueqiu.com/stock/industry/stockList.json?code=SH600519&type=1&size=100&md5__1038=214d4f07715-1IThv2UO77XdihJXyDTOkVGD3YqTVCKZTAsI1j2%2FZT%2FXYeJ83TiI_O2POXFTO9P%2FqTqm7TPfCcTyfjKXTC9PeTc9XFTw9TVTA%3D7Ts6f%2FWTlfqp4T19T8TApoXXT_ZTCTNkI40x9CBo%3DefZamxAf1fX1bDQ22SiaKCTkiPm9fPyc%2Fsm7KX%3D7%3DuT`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile`
- 字段样例：stockname, platename, industrystocks, exchange, code, industryname

## 14. stock_question

- URL：`https://xueqiu.com/rainbow/ai/question/generate/stock_question.json?symbol=SH600519&md5__1038=214d4f07715-196fAfITYfxf_j%2Fwt%3Dfj9uY9m9qZZTOT0q2KlSGehXJuAfz%2FLhTATTDOytBjXGT_zTv9TUT62XIeTiCVTX9iFT%2F2PgcTioXLTuZTpTyoTnTvF89Tf6vX_TmLJTKfXyTsgviiTe9TuT4pzJSgoTMyDhjCIq91IZIiY9v6shsXT9g6fTwW2dXT2aXXlTT`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile`
- 字段样例：code, data, msg

## 15. bind_query

- URL：`https://xueqiu.com/uc_passport/enterprise/symbol/bind_query.json?symbol=SH600519&md5__1038=214d4f07715-19wfnTST_TAfYhTLyclZX5qqZLTq92i%2FJvJVgXaZ3cTPCL%3D1lhTX9JV4_HZxnGT__Ty2TUT62XI1v2XiHTToX%3DTPvXU1TXOTpfCyTkf%2FOT6fqcIyT2%2FWTlfqpYT19T8TAUqXXT_ZTCTNwFolo4T_IynXYOLjsTHXPy_Ci2HuY2vvAZqTX84jDhL_FhecT`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile`
- 字段样例：message, data, result_code

## 16. bind_query_2

- URL：`https://xueqiu.com/uc_passport/enterprise/symbol/bind_query.json?symbol=SH600519&md5__1038=214d4f07715-19wfnTST_TAfYhTLyclZX5qqZLTq92i%2FJvJVgXatZ4TjyCSglh_tTTD4mHZx0GT__Ty2TUT62XI1v2XiRTToX%3DTPvXU1TXOTpfCyTkf%2FOT6fqcIyT2%2FWTlfqpYT19T8TAUqXXT_ZTCTNwFolo4T_IyLXYOHjsTRXPy_Ci2R_A2vvAZqTX84iXZH_taHClTT`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile`
- 字段样例：message, data, result_code

## 17. bind_query_3

- URL：`https://xueqiu.com/uc_passport/enterprise/symbol/bind_query.json?symbol=SH600519&md5__1038=214d4f07715-19wfnTST_TAfYhTLyclZX5qqZLTq92i%2FJvJVgXaZ3vqq9Zae9TjiTTD4mHZsPGT__Ty2TUT62XI1v2XiRTToX%3DTPvXU1TXOTpfCyTkf%2FOT6fqcIyT2%2FWTlfqpYT19T8TAUqXXT_ZTCTNwFolo4T_YyLXYO_jsTRXXyICi2RiY2vvAZqTX84iXZ_iDa_ClTT`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile`
- 字段样例：message, data, result_code

## 18. content_2

- URL：`https://open.xueqiu.com/mpaas/config/content?keys=web_jianlian_v3_qrcode_login,web_login_modal_config&appkey=92f09797f899bdba4fbf01a2829a16d2`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile`
- 字段样例：data, error_code, error_description

## 19. content_3

- URL：`https://open.xueqiu.com/mpaas/config/content?keys=footer&appkey=92f09797f899bdba4fbf01a2829a16d2&last_update=1755681684000`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile`
- 字段样例：data, error_code, error_description

## 20. content_4

- URL：`https://open.xueqiu.com/mpaas/config/content?keys=seekball_pc_entry&uid=&appkey=92f09797f899bdba4fbf01a2829a16d2`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile`
- 字段样例：data, error_code, error_description

## 21. content_5

- URL：`https://open.xueqiu.com/mpaas/config/content?keys=web_unsign_download&appkey=92f09797f899bdba4fbf01a2829a16d2&last_update=1744791262000`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile`
- 字段样例：data, error_code, error_description

## 22. content_6

- URL：`https://open.xueqiu.com/mpaas/config/content?keys=trade_home_tabs&appkey=92f09797f899bdba4fbf01a2829a16d2&last_update=1744791262000`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile`
- 字段样例：data, error_code, error_description

## 23. content_7

- URL：`https://open.xueqiu.com/mpaas/config/content?keys=ai_search_enter&appkey=92f09797f899bdba4fbf01a2829a16d2`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile`
- 字段样例：data, error_code, error_description

## 24. query

- URL：`https://xueqiu.com/snowpard/launch_strategy/query.json?channel=3&page=11&model=9&md5__1038=214d4f07715-19CfAJi%2FTOP%2FXhlafsTNf%3Dfq9qoMcA%2Fx%2FI9_etYTTZX%2FkW6eokTw8GoIKQCn2THkEPTPOTpTj6ZWTq_cTPfCcTyfjKZTC9PeTc9XFTw9TVTA%3D6x3Tk2ieT00qTRTiZXjy2qqTHfTcT5FLCNK9X4mxwV08fhZ%2FGOm8Z24XYFZlwvex9TyDYee8xGjexyGT`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile`
- 字段样例：data, result_code, message, detail

## 25. minute

- URL：`https://stock.xueqiu.com/v5/stock/chart/minute.json?symbol=SH600519&period=1d`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile`
- 字段样例：data, error_code, error_description
