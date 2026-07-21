"""需求3 阶段5：路由注册冒烟（端点存在 + app 能挂载；业务逻辑已在 service/pending 层测透）。"""


def test_router_exposes_expected_paths():
    from api.routes.crypto_strategy import router
    paths = {r.path for r in router.routes}
    for p in ("/crypto-strategy/strategies", "/crypto-strategy/strategies/{strategy_id}/arm",
              "/crypto-strategy/strategies/{strategy_id}/backtest", "/crypto-strategy/engine/status",
              "/crypto-strategy/engine/kill", "/crypto-strategy/pending",
              "/crypto-strategy/pending/{order_ref}/confirm",
              "/crypto-strategy/pending/{order_ref}/reject"):
        assert p in paths, f"缺端点 {p}"


def test_router_mounts_in_app():
    # 导入主 app 不炸（含新路由注册 + startup 引擎接线的 import 正确性）
    from api.main import app
    all_paths = {r.path for r in app.routes}
    assert "/crypto-strategy/strategies" in all_paths
