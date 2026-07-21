# Fin 桌面应用（本机 Mac）

**2026-07-21 起项目只服务桌面 App，web 端已整体退役**（原来那套 :8010 开发后端 + vite :5174 + `backend/data_dev/` 测试库 + `master`→`app` promote 仪式，全部删除）。所有代码只有一个交付目标：`/Applications/Fin.app`。

## 唯一入口

```bash
bash restart.sh              # 全量：重建前端 → 重装 App → 重启后端 → 打开 App（约 1-2 分钟）
bash restart.sh --backend    # 快档：只重启后端 + C++ 服务（改 Python 时用，约 40s）
```

## 🔴 App 端没有热更新

前端静态产物被 `tauri build` **编译进 Rust 二进制**（`tauri.conf.json` 的 `frontendDist: "../dist"`），后端也不带 `--reload`。**改了代码不重跑 `restart.sh`，正在跑的 App 永远看不见。**

这不是配置没开，是这套架构的固有代价 —— 换来的是「App 里跑的东西不会被开发中的半成品代码影响」。双轨时期靠手动 promote，结果 `app` 分支一度落后 master 133 个 commit，App 里跑了几个月旧界面没人发现，这是收敛掉双轨的直接原因。

## 组成

- `restart.sh`（仓库根）— **编排层**，本身不实现任何服务启动逻辑，只按顺序调下面两个脚本。
- `desktop/build-app.sh` — **构建安装的单一真源**：`npm run build` → `npm run app:build`（`tauri build`）→ `ditto` 覆盖安装到 `/Applications/Fin.app`。不碰后端、不打开 App（避免和 restart.sh 抢着起服务）。
- `desktop/start-services.sh` — 幂等启动：逐个探端口，已在跑就跳过。拉起后端(:8000，`--host 127.0.0.1`，**不带 `--reload`**) + C++ 订单簿(:8001) + C++ 回测(:8002) + 可选 SSH 隧道(:11434，远程 GPU 本地模型用)。
- `desktop/stop-services.sh` — 彻底停掉全部后台服务（**关 App 窗口不会调用它**，服务设计为常驻）。
- `frontend/src-tauri/` — Tauri v2 外壳：
  - `src/lib.rs` — 开 App 时 spawn `start-services.sh`（幂等，服务已在跑则逐个跳过）。
  - `tauri.conf.json` — 窗口配置 + `frontendDist` / `beforeBuildCommand`。
  - `Info.plist` — ATS 放行本机 http（否则 WKWebView 拦截访问 :8000）。

## 行为

- **开 App**：自动拉起服务（端口没在监听才启动），直接加载打包好的静态界面。后端冷启动约 30-40s（pytdx 预热），这段由前端 `App.tsx` 的 `useBackendReady` 轮询 `/health` 挡着，显示「正在启动后端服务…」。
- **关窗**：只关窗口，**后端 / C++ 服务继续常驻**，重开 App 秒进。定时任务（每日数据链、新闻、crypto）也跟着常驻 —— 这正是要的效果。
- **想彻底停**：`bash desktop/stop-services.sh`。

## 数据与网络

- 后端连的是 **T9 上的生产库**（`backend/data/market.db` 软链），没有测试沙箱。`restart.sh` 开头硬检查 `/Volumes/T9` 挂载，没插盘直接拒绝启动。
- 需要临时起一个隔离实例调试时，自己传环境变量：`DATABASE_URL=sqlite:///... KNOWLEDGE_DB_PATH=... FIN_DISABLE_SCHEDULERS=true`（机制都还在，只是不再有脚本默认这么干）。
- 只绑 `127.0.0.1`，CORS 只放行本机与 `tauri://localhost`。局域网手机访问的能力随 web 端一起去掉了。

## 配置

- 仓库根路径写死在 `src-tauri/src/lib.rs` 的 `repo_root()`，默认 `/Users/cccjson/Desktop/Fin`，可用环境变量 `FIN_REPO` 覆盖。
- Python 解释器在 `start-services.sh` 的 `PYTHON`，默认 conda `quant` 环境，可用 `FIN_PYTHON` 覆盖。
- 应用图标：把一张 1024×1024 png 丢进去跑 `npm run tauri icon <png>`，会生成全套 `icons/`。

## 已知边界

- 不可移植：依赖本机 conda `quant` 环境、T9 上 15GB 的 `market.db`、`backend/.env` 密钥、快代理、已编译的 C++ 二进制。换机器需重建环境。
- 不签名不公证（本机自用）。首次打开被 Gatekeeper 拦「无法验证开发者」时，右键「打开」确认一次即可。要分发给别人是另一个数量级的工程。
