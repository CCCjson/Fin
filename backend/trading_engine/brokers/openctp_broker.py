"""
OpenCTP 券商接口实现
"""
import time
from datetime import datetime
from typing import List, Optional, Dict
from loguru import logger
import threading

try:
    from openctp_ctp import mdapi, tdapi
except ImportError:
    logger.warning("openctp-ctp 未安装，请运行: pip install openctp-ctp")
    mdapi = None
    tdapi = None

from .base import BaseBroker, BrokerOrder, BrokerPosition, OrderStatus


class CtpMdSpi(mdapi.CThostFtdcMdSpi):
    """CTP 行情回调"""

    def __init__(self, broker):
        super().__init__()
        self.broker = broker

    def OnFrontConnected(self):
        """行情前置连接成功"""
        logger.success("行情服务器连接成功")
        # 登录
        req = mdapi.CThostFtdcReqUserLoginField()
        req.BrokerID = self.broker.broker_id
        req.UserID = self.broker.user_id
        req.Password = self.broker.password
        self.broker.md_api.ReqUserLogin(req, 0)

    def OnRspUserLogin(self, pRspUserLogin, pRspInfo, nRequestID, bIsLast):
        """登录响应"""
        if pRspInfo and pRspInfo.ErrorID != 0:
            logger.error(f"行情登录失败: {pRspInfo.ErrorMsg}")
            self.broker.md_connected = False
        else:
            logger.success("行情登录成功")
            self.broker.md_connected = True

    def OnRtnDepthMarketData(self, pDepthMarketData):
        """行情推送"""
        if pDepthMarketData:
            symbol = pDepthMarketData.InstrumentID
            self.broker.market_data[symbol] = {
                "symbol": symbol,
                "last_price": pDepthMarketData.LastPrice,
                "bid_price": pDepthMarketData.BidPrice1,
                "ask_price": pDepthMarketData.AskPrice1,
                "volume": pDepthMarketData.Volume,
                "turnover": pDepthMarketData.Turnover,
                "update_time": pDepthMarketData.UpdateTime,
            }

    def OnFrontDisconnected(self, nReason):
        """断开连接"""
        logger.warning(f"行情服务器断开: {nReason}")
        self.broker.md_connected = False


class CtpTdSpi(tdapi.CThostFtdcTraderSpi):
    """CTP 交易回调"""

    def __init__(self, broker):
        super().__init__()
        self.broker = broker

    def OnFrontConnected(self):
        """交易前置连接成功"""
        logger.success("交易服务器连接成功")
        # 认证
        req = tdapi.CThostFtdcReqAuthenticateField()
        req.BrokerID = self.broker.broker_id
        req.UserID = self.broker.user_id
        req.AppID = self.broker.app_id
        req.AuthCode = self.broker.auth_code
        self.broker.td_api.ReqAuthenticate(req, 0)

    def OnRspAuthenticate(self, pRspAuthenticateField, pRspInfo, nRequestID, bIsLast):
        """认证响应"""
        if pRspInfo and pRspInfo.ErrorID != 0:
            logger.error(f"认证失败: {pRspInfo.ErrorMsg}")
            self.broker.td_connected = False
        else:
            logger.success("认证成功，开始登录...")
            # 登录
            req = tdapi.CThostFtdcReqUserLoginField()
            req.BrokerID = self.broker.broker_id
            req.UserID = self.broker.user_id
            req.Password = self.broker.password
            self.broker.td_api.ReqUserLogin(req, 0)

    def OnRspUserLogin(self, pRspUserLogin, pRspInfo, nRequestID, bIsLast):
        """登录响应"""
        if pRspInfo and pRspInfo.ErrorID != 0:
            logger.error(f"交易登录失败: {pRspInfo.ErrorMsg}")
            self.broker.td_connected = False
        else:
            logger.success("交易登录成功")
            self.broker.td_connected = True
            self.broker.front_id = pRspUserLogin.FrontID
            self.broker.session_id = pRspUserLogin.SessionID

            # 查询账户
            self.broker.query_account()
            # 查询持仓
            time.sleep(1)
            self.broker.query_positions()

    def OnRspQryTradingAccount(self, pTradingAccount, pRspInfo, nRequestID, bIsLast):
        """账户查询响应"""
        if pTradingAccount:
            self.broker.account_info = {
                "balance": pTradingAccount.Balance,
                "available": pTradingAccount.Available,
                "commission": pTradingAccount.Commission,
                "margin": pTradingAccount.CurrMargin,
                "frozen_margin": pTradingAccount.FrozenMargin,
                "frozen_cash": pTradingAccount.FrozenCash,
                "close_profit": pTradingAccount.CloseProfit,
                "position_profit": pTradingAccount.PositionProfit,
            }
            logger.info(f"账户资金: {pTradingAccount.Balance:.2f}, 可用: {pTradingAccount.Available:.2f}")

    def OnRspQryInvestorPosition(self, pInvestorPosition, pRspInfo, nRequestID, bIsLast):
        """持仓查询响应"""
        if pInvestorPosition:
            symbol = pInvestorPosition.InstrumentID
            position = BrokerPosition(
                symbol=symbol,
                quantity=pInvestorPosition.Position,
                avg_cost=pInvestorPosition.OpenCost / pInvestorPosition.Position if pInvestorPosition.Position > 0 else 0,
                current_price=0.0,  # 需要从行情获取
                market_value=0.0,
                unrealized_pnl=pInvestorPosition.PositionProfit,
                available=pInvestorPosition.Position - pInvestorPosition.TodayPosition  # 昨仓可用
            )
            self.broker.positions[symbol] = position

        if bIsLast:
            logger.info(f"持仓查询完成，共 {len(self.broker.positions)} 个持仓")

    def OnRspOrderInsert(self, pInputOrder, pRspInfo, nRequestID, bIsLast):
        """报单录入响应"""
        if pRspInfo and pRspInfo.ErrorID != 0:
            logger.error(f"报单失败: {pRspInfo.ErrorMsg}")
            # 更新订单状态
            order_ref = str(pInputOrder.OrderRef)
            if order_ref in self.broker.orders:
                self.broker.orders[order_ref].status = OrderStatus.REJECTED
                self.broker.orders[order_ref].error_msg = pRspInfo.ErrorMsg

    def OnRtnOrder(self, pOrder):
        """报单回报"""
        if pOrder:
            order_ref = str(pOrder.OrderRef)
            if order_ref in self.broker.orders:
                order = self.broker.orders[order_ref]

                # 更新订单状态
                if pOrder.OrderStatus == '0':  # 全部成交
                    order.status = OrderStatus.FILLED
                    order.filled_quantity = pOrder.VolumeTraded
                    order.filled_time = datetime.now()
                elif pOrder.OrderStatus == '1':  # 部分成交
                    order.status = OrderStatus.PARTIAL_FILLED
                    order.filled_quantity = pOrder.VolumeTraded
                elif pOrder.OrderStatus == '5':  # 撤单
                    order.status = OrderStatus.CANCELLED
                elif pOrder.OrderStatus == 'a':  # 未知
                    order.status = OrderStatus.PENDING
                elif pOrder.OrderStatus == 'c':  # 已提交
                    order.status = OrderStatus.SUBMITTED

                logger.info(f"订单回报: {order.symbol} {order.action} {order.quantity} 状态={order.status.value}")

    def OnRtnTrade(self, pTrade):
        """成交回报"""
        if pTrade:
            logger.success(
                f"成交通知: {pTrade.InstrumentID} "
                f"{'买入' if pTrade.Direction == '0' else '卖出'} "
                f"{pTrade.Volume}手 @ {pTrade.Price}"
            )

    def OnFrontDisconnected(self, nReason):
        """断开连接"""
        logger.warning(f"交易服务器断开: {nReason}")
        self.broker.td_connected = False


class OpenCtpBroker(BaseBroker):
    """OpenCTP 券商接口"""

    def __init__(
        self,
        broker_id: str = "9999",
        user_id: str = "16982",
        password: str = "123456",
        app_id: str = "simnow_client_test",
        auth_code: str = "0000000000000000",
        md_address: str = "tcp://180.168.146.187:10131",
        td_address: str = "tcp://180.168.146.187:10130"
    ):
        """
        初始化 OpenCTP 接口

        Args:
            broker_id: 经纪商代码，默认 9999 (SimNow)
            user_id: 用户ID
            password: 密码
            app_id: 应用ID
            auth_code: 授权码
            md_address: 行情服务器地址
            td_address: 交易服务器地址
        """
        super().__init__(name="OpenCTP")

        self.broker_id = broker_id
        self.user_id = user_id
        self.password = password
        self.app_id = app_id
        self.auth_code = auth_code
        self.md_address = md_address
        self.td_address = td_address

        # 连接状态
        self.md_connected = False
        self.td_connected = False

        # 会话信息
        self.front_id = 0
        self.session_id = 0
        self.order_ref = 0

        # 数据存储
        self.account_info: Dict = {}
        self.positions: Dict[str, BrokerPosition] = {}
        self.orders: Dict[str, BrokerOrder] = {}
        self.market_data: Dict[str, dict] = {}

        # API 对象
        self.md_api = None
        self.td_api = None
        self.md_spi = None
        self.td_spi = None

        logger.info(f"OpenCTP 初始化完成: 用户={user_id}, 经纪商={broker_id}")

    def connect(self):
        """连接行情和交易服务器"""
        if mdapi is None or tdapi is None:
            logger.error("openctp-ctp 未安装")
            return False

        try:
            # 创建行情 API
            self.md_api = mdapi.CThostFtdcMdApi.CreateFtdcMdApi("./md_flow/")
            self.md_spi = CtpMdSpi(self)
            self.md_api.RegisterSpi(self.md_spi)
            self.md_api.RegisterFront(self.md_address)
            self.md_api.Init()
            logger.info(f"行情服务器: {self.md_address}")

            # 创建交易 API
            self.td_api = tdapi.CThostFtdcTraderApi.CreateFtdcTraderApi("./td_flow/")
            self.td_spi = CtpTdSpi(self)
            self.td_api.RegisterSpi(self.td_spi)
            self.td_api.SubscribePublicTopic(tdapi.THOST_TERT_QUICK)
            self.td_api.SubscribePrivateTopic(tdapi.THOST_TERT_QUICK)
            self.td_api.RegisterFront(self.td_address)
            self.td_api.Init()
            logger.info(f"交易服务器: {self.td_address}")

            # 等待连接
            logger.info("等待连接...")
            for i in range(30):
                if self.md_connected and self.td_connected:
                    logger.success("OpenCTP 连接成功！")
                    return True
                time.sleep(1)

            logger.error("连接超时")
            return False

        except Exception as e:
            logger.error(f"连接失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def disconnect(self):
        """断开连接"""
        if self.md_api:
            self.md_api.Release()
        if self.td_api:
            self.td_api.Release()
        logger.info("OpenCTP 已断开")

    def subscribe_market_data(self, symbols: List[str]):
        """订阅行情"""
        if not self.md_connected:
            logger.warning("行情未连接")
            return False

        self.md_api.SubscribeMarketData([s.encode() for s in symbols], len(symbols))
        logger.info(f"订阅行情: {symbols}")
        return True

    def get_account_info(self) -> dict:
        """获取账户信息"""
        return {
            "cash": self.account_info.get("available", 0),
            "total_value": self.account_info.get("balance", 0),
            "margin": self.account_info.get("margin", 0),
            "commission": self.account_info.get("commission", 0),
        }

    def query_account(self):
        """查询账户"""
        if not self.td_connected:
            return

        req = tdapi.CThostFtdcQryTradingAccountField()
        req.BrokerID = self.broker_id
        req.InvestorID = self.user_id
        self.td_api.ReqQryTradingAccount(req, 0)

    def query_positions(self):
        """查询持仓"""
        if not self.td_connected:
            return

        self.positions.clear()
        req = tdapi.CThostFtdcQryInvestorPositionField()
        req.BrokerID = self.broker_id
        req.InvestorID = self.user_id
        self.td_api.ReqQryInvestorPosition(req, 0)

    def get_positions(self) -> List[BrokerPosition]:
        """获取持仓列表"""
        return list(self.positions.values())

    def get_position(self, symbol: str) -> Optional[BrokerPosition]:
        """获取指定持仓"""
        return self.positions.get(symbol)

    def submit_order(
        self,
        symbol: str,
        action: str,
        quantity: int,
        price: Optional[float] = None
    ) -> BrokerOrder:
        """
        提交订单

        Args:
            symbol: 合约代码
            action: BUY 或 SELL
            quantity: 数量（手）
            price: 价格（None=市价单）
        """
        if not self.td_connected:
            logger.error("交易未连接")
            return None

        # 生成订单引用
        self.order_ref += 1
        order_ref = str(self.order_ref)

        # 创建订单
        order = BrokerOrder(
            order_id=order_ref,
            symbol=symbol,
            action=action,
            quantity=quantity,
            price=price,
            status=OrderStatus.PENDING,
            submit_time=datetime.now()
        )
        self.orders[order_ref] = order

        # 构造 CTP 报单
        req = tdapi.CThostFtdcInputOrderField()
        req.BrokerID = self.broker_id
        req.InvestorID = self.user_id
        req.InstrumentID = symbol
        req.OrderRef = order_ref

        # 买卖方向
        req.Direction = '0' if action == "BUY" else '1'

        # 组合开平标志（开仓）
        req.CombOffsetFlag = '0'

        # 组合投机套保标志（投机）
        req.CombHedgeFlag = '1'

        # 数量
        req.VolumeTotalOriginal = quantity

        # 价格类型和价格
        if price is None:
            req.OrderPriceType = tdapi.THOST_FTDC_OPT_AnyPrice  # 市价
            req.LimitPrice = 0
            req.TimeCondition = tdapi.THOST_FTDC_TC_IOC  # 立即成交
        else:
            req.OrderPriceType = tdapi.THOST_FTDC_OPT_LimitPrice  # 限价
            req.LimitPrice = price
            req.TimeCondition = tdapi.THOST_FTDC_TC_GFD  # 当日有效

        # 成交量类型
        req.VolumeCondition = tdapi.THOST_FTDC_VC_AV  # 任意数量

        # 最小成交量
        req.MinVolume = 1

        # 触发条件
        req.ContingentCondition = tdapi.THOST_FTDC_CC_Immediately

        # 强平原因
        req.ForceCloseReason = tdapi.THOST_FTDC_FCC_NotForceClose

        # 自动挂起标志
        req.IsAutoSuspend = 0

        # 用户强评标志
        req.UserForceClose = 0

        # 提交报单
        ret = self.td_api.ReqOrderInsert(req, 0)
        if ret == 0:
            logger.info(f"订单提交: {symbol} {action} {quantity}手 @ {price or '市价'}")
            order.status = OrderStatus.SUBMITTED
        else:
            logger.error(f"订单提交失败: {ret}")
            order.status = OrderStatus.FAILED

        return order

    def cancel_order(self, order_id: str) -> bool:
        """撤销订单"""
        if order_id not in self.orders:
            logger.warning(f"订单不存在: {order_id}")
            return False

        order = self.orders[order_id]

        req = tdapi.CThostFtdcInputOrderActionField()
        req.BrokerID = self.broker_id
        req.InvestorID = self.user_id
        req.OrderRef = order_id
        req.FrontID = self.front_id
        req.SessionID = self.session_id
        req.ActionFlag = tdapi.THOST_FTDC_AF_Delete

        ret = self.td_api.ReqOrderAction(req, 0)
        if ret == 0:
            logger.info(f"撤单请求已发送: {order_id}")
            return True
        else:
            logger.error(f"撤单失败: {ret}")
            return False

    def get_order(self, order_id: str) -> Optional[BrokerOrder]:
        """查询订单"""
        return self.orders.get(order_id)

    def get_orders(self, symbol: Optional[str] = None) -> List[BrokerOrder]:
        """获取订单列表"""
        if symbol:
            return [o for o in self.orders.values() if o.symbol == symbol]
        return list(self.orders.values())

    def get_current_price(self, symbol: str) -> float:
        """获取当前价格"""
        if symbol in self.market_data:
            return self.market_data[symbol].get("last_price", 0.0)
        return 0.0
