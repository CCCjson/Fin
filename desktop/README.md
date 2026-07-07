# Fin 桌面应用（本机 Mac）

把现有网页端套一个原生窗口壳，**完整保留热重载开发体验**：改 Python 走 uvicorn `--reload`、改前端走 Vite HMR，无需重启、无需每次改动重新打包。数据/AI/回测仍全走本机，跟 `restart.sh` 完全一致。

## 组成

- `desktop/start-services.sh` — 幂等启动：逐个探端口，已在跑就跳过。拉起后端(:8000 `--reload`) + 3 个 C++ 服务(:8001/8002/8003) + vite dev(:5174) + 可选 SSH 隧道(:11434)。
- `desktop/stop-services.sh` — 手动彻底停所有服务（**关窗不会调用它**，服务设计为常驻）。
- `frontend/src-tauri/` — Tauri v2 外壳：
  - `ui/index.html` — 启动加载页，轮询后端 `/docs` 与前端 `:5174`,就绪后跳转 `:5174`。
  - `src/lib.rs` — 开 app 时 spawn `start-services.sh`（幂等）。
  - `tauri.conf.json` — 窗口配置，`frontendDist: "ui"`（不打包前端产物，加载页跳转到活的 dev server）。
  - `Info.plist` — ATS 放行本机 http（否则 WKWebView 拦截 :5174/:8000）。

## 行为

- **开 app**：自动拉起服务（`:8000` 没在跑才启动），先显示加载页，就绪后进入界面。
- **关窗**：只关窗口，**后端/C++ 服务继续常驻**，重开 app 秒进。
- **改代码**：前端 HMR 即时刷新；后端 uvicorn 自动重载。C++ 改了需重编——重跑 `start-services.sh`（会重新 cmake/make）或手动重启对应服务。

## 日常使用

```bash
cd frontend
npm run app:dev      # 开发联调（可边改 Rust 外壳边看）
npm run app:build    # 打包出 Fin.app + .dmg
```

打包产物在 `frontend/src-tauri/target/release/bundle/`。首次打开 `Fin.app` 若被 Gatekeeper 拦，右键「打开」即可（本机自用无需签名/公证）。

## 配置

- 仓库根路径写死在 `src/lib.rs` 的 `repo_root()`，默认 `/Users/cccjson/Desktop/Fin`，可用环境变量 `FIN_REPO` 覆盖。
- Python 解释器在 `start-services.sh` 的 `PYTHON`，默认 conda `quant` 环境，可用 `FIN_PYTHON` 覆盖。
- 自定义应用图标：把一张 1024×1024 png 丢进去跑 `npm run tauri icon <png>`，会生成全套 `icons/`。

## 已知边界

不可移植：依赖本机 conda `quant` 环境、6.1G `market.db`、`backend/.env` 密钥、Clash 代理(:7897)、已编译的 C++ 二进制。换机器需重建环境。要分发给别人是另一个数量级的工程。
