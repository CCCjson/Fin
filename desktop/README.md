# Fin 桌面应用（本机 Mac）

把现有网页端套一个原生窗口壳。**2026-07 起 app 端和 web 端的开发流程解耦**：

- **web 端**（`restart.sh`）：前后端全热更新，日常开发都在这边做，:8010 后端 + :5174 前端，连的是本地空测试库（`backend/data_dev/`），不碰生产数据。
- **app 端**（这个目录 + Tauri 壳）：前后端**都不热更新**，加载的是真正 `vite build` 出来的静态产物，后端是独立的稳定实例(:8000，不带 `--reload`)，连生产库（T9 上的 `market.db`）。日常真实用（模拟盘、自动化扫描）都在这边，不受 web 端开发中的代码改动影响。

两边跑在同一台机器上互不干扰：端口不同(8000 vs 8010)、数据库不同(生产 vs 本地测试库)、前端交付方式不同(静态构建 vs vite dev server)。

## 组成

- `desktop/start-services.sh` — 幂等启动：逐个探端口，已在跑就跳过。拉起后端(:8000，**不带 `--reload`**) + 2 个 C++ 服务(:8001/8002) + 可选 SSH 隧道(:11434)。不再启动 vite dev server —— app 前端是打包进去的静态文件，不需要。
- `desktop/stop-services.sh` — 手动彻底停 app 端服务（**关窗不会调用它**，服务设计为常驻）。精确匹配 `--port 8000`，不会误杀 web 端跑在 8010 的开发后端。
- `desktop/sync-to-app.sh` — **"web 验证过 → 推到 app"的 promote 脚本**，见下方「日常使用」。
- `frontend/src-tauri/` — Tauri v2 外壳：
  - `src/lib.rs` — 开 app 时 spawn `start-services.sh`（幂等）。
  - `tauri.conf.json` — 窗口配置；`frontendDist: "../dist"` + `beforeBuildCommand: "npm run build"`，`tauri build` 时自动把 `frontend/dist`（真正的 vite 生产构建产物）打包进 app，不再有中间的加载/跳转页。
  - `Info.plist` — ATS 放行本机 http（否则 WKWebView 拦截 :8000）。

## 行为

- **开 app**：自动拉起服务（`:8000` 没在跑才启动），直接加载打包好的静态界面（不是 dev server，没有跳转等待）。
- **关窗**：只关窗口，**后端/C++ 服务继续常驻**，重开 app 秒进。
- **改代码不会影响正在跑的 app**：前端没有 HMR，后端没有 `--reload`——这正是这次解耦要的效果。要让 app 看到新代码，必须显式 promote（见下）。

## 日常使用

开发流程：**web 端先开发验证 → 确认没问题 → `sync-to-app.sh` 推到 app**。

```bash
# 日常开发，全程热更新，走本地测试库，master 分支上随便改
bash restart.sh

# 确认 web 端（localhost:5174）没问题、代码已 commit 到 master 之后：
bash desktop/sync-to-app.sh
```

`sync-to-app.sh` 做的事：检查当前在 `master` 且 working tree 干净 → `npm run build` → `npm run app:build`（`tauri build`）→ 停掉旧的 app 后端/App 窗口 → `ditto` 覆盖安装到 `/Applications/Fin.app` → 把 `app` 分支指针快进到这次 promote 的 commit → 重新打开 App。

首次打开 `Fin.app` 若被 Gatekeeper 拦（"无法验证开发者"），右键「打开」确认一次即可（本机自用，不需要签名/公证；如果以后想分发给别人用，需要另外弄 Apple 开发者账号签名+公证，是完全独立的一块工作）。

也可以只想重新打包看看效果、不走完整 promote 流程时手动跑：

```bash
cd frontend
npm run app:dev      # 开发联调 Rust 外壳本身用（会先跑 npm run build 出静态产物，不是 dev server）
npm run app:build    # 打包出 Fin.app + .dmg，产物在 frontend/src-tauri/target/release/bundle/
```

## Git 分支约定

- `master`：唯一的开发分支，所有代码改动都在这上面进行（不管最终是给 web 还是给 app 用）。
- `app`：纯粹的"当前 App 在跑哪个 commit"标记指针，**只能被 `sync-to-app.sh` 移动，不要手动 commit 到这条分支上**。想知道还有哪些改动没推到 app，跑 `git log app..master --oneline`。

## 配置

- 仓库根路径写死在 `src/lib.rs` 的 `repo_root()`，默认 `/Users/cccjson/Desktop/Fin`，可用环境变量 `FIN_REPO` 覆盖。
- Python 解释器在 `start-services.sh` 的 `PYTHON`，默认 conda `quant` 环境，可用 `FIN_PYTHON` 覆盖。
- 自定义应用图标：把一张 1024×1024 png 丢进去跑 `npm run tauri icon <png>`，会生成全套 `icons/`。

## 已知边界

- 不可移植：依赖本机 conda `quant` 环境、T9 上 14G+ 的 `market.db`、`backend/.env` 密钥、Clash 代理(:7897)、已编译的 C++ 二进制。换机器需重建环境。要分发给别人是另一个数量级的工程（代码签名 + 公证）。
- app 和 web 各跑一份独立后端进程。SQLite 层面安全（WAL + busy_timeout，且 web 端现在连的是完全独立的本地测试库，不会碰生产数据），但纯内存状态（会话锁、业务事件总线）两边天然不同步——这是预期行为，web 端的模拟盘/会话状态不代表真实持仓，别拿它当真实数据源用。
