# Fin 量化交易系统 — 部署指南

> 支持 macOS / Windows / Linux 跨平台部署，一键搞定环境配置。

---

## 目录

- [快速开始](#快速开始)
- [环境要求](#环境要求)
- [一、一键部署（首次使用）](#一一键部署首次使用)
- [二、启动 / 停止服务](#二启动--停止服务)
- [三、配置说明](#三配置说明)
- [四、模型迁移](#四模型迁移)
- [五、Mac 完整模式（进阶）](#五mac-完整模式进阶)
- [六、常见问题](#六常见问题)
- [脚本一览](#脚本一览)

---

## 快速开始

三步上手，复制粘贴就行：

**macOS / Linux：**

```bash
# 1. 一键部署（安装所有依赖）
bash deploy.sh

# 2. 编辑配置文件，填入你的 API key
nano backend/.env

# 3. 启动服务
bash start.sh
```

**Windows：**

```cmd
:: 1. 一键部署
deploy.bat

:: 2. 编辑配置文件
notepad backend\.env

:: 3. 启动服务
start.bat
```

启动后访问：
- 前端界面：http://127.0.0.1:5174
- 后端 API 文档：http://127.0.0.1:8000/docs

---

## 环境要求

| 项目 | 要求 |
|------|------|
| 操作系统 | macOS 12+ / Windows 10+ / Ubuntu 20.04+ |
| 内存 | 8GB 以上（跑本地模型建议 16GB+） |
| 磁盘 | 至少 10GB 可用空间 |
| 网络 | 需要联网下载依赖 |

> deploy 脚本会自动安装 Miniconda、Python 3.12、Node.js，不需要你提前装好。

---

## 一、一键部署（首次使用）

### macOS / Linux

```bash
bash deploy.sh
```

### Windows

```cmd
deploy.bat
```

### 部署脚本做了什么？

```
[1/6] 检测系统环境
      → 自动识别 OS、CPU 架构、GPU 类型
      → macOS arm64 识别为 Apple Silicon (MPS)
      → 有 NVIDIA 显卡自动识别为 CUDA

[2/6] 安装 Miniconda（如果没有）
      → 根据平台下载对应安装包，静默安装
      → 已安装则跳过

[3/6] 创建 conda 环境 quant（Python 3.12）
      → 环境已存在则跳过

[4/6] 安装 Python 依赖
      → 自动安装 backend/requirements.txt 中的所有包
      → PyTorch 智能安装：
        · macOS          → 标准版（自带 MPS 加速）
        · NVIDIA GPU     → CUDA 12.1 版
        · 无 GPU         → CPU 版
      → macOS 额外安装 mlx-lm（Apple Silicon 专属加速框架）

[5/6] 安装 Node.js + 前端依赖
      → macOS 优先用 Homebrew，没有则下载安装包
      → Linux 用 NodeSource 安装 Node.js 20.x
      → Windows 下载 MSI 静默安装
      → 自动 npm install

[6/6] 初始化配置
      → 复制 .env.example → .env（如果不存在）
      → 创建 data/、logs/、finetune/ 等必要目录
```

> **deploy 只需跑一次。** 它会自动检测已安装的组件并跳过，重复运行也是安全的。

---

## 二、启动 / 停止服务

### 启动

```bash
# macOS / Linux
bash start.sh

# Windows
start.bat
```

start 脚本会启动两个核心服务：

| 服务 | 端口 | 说明 |
|------|------|------|
| Python 后端 (uvicorn) | 8000 | FastAPI 接口 |
| 前端 (vite) | 5174 | React 开发服务器 |

### 停止

```bash
# macOS / Linux
bash stop.sh

# Windows
stop.bat
```

### 查看日志

```bash
# 后端日志
tail -f /tmp/fin-backend.log

# 前端日志
tail -f /tmp/fin-frontend.log
```

Windows 日志在 `%TEMP%\fin-backend.log` 和 `%TEMP%\fin-frontend.log`。

---

## 三、配置说明

部署完成后，编辑 `backend/.env` 填入你的配置：

```bash
# ---- 必填 ----
AUTH_USERNAME=你的登录用户名
AUTH_PASSWORD=你的登录密码
JWT_SECRET=随便写一个长字符串

# ---- 数据源（按需填写）----
TUSHARE_TOKEN=xxx              # A股数据（tushare.pro 注册获取）

# ---- 可选 ----
LOG_LEVEL=INFO                 # 日志级别: DEBUG / INFO / WARNING
DATA_UPDATE_ENABLED=true       # 是否自动更新行情数据
DATA_UPDATE_TIME=15:30         # 自动更新时间（收盘后）
```

> `.env` 文件包含敏感信息，已被 `.gitignore` 排除，不会提交到 Git。

---

## 四、模型迁移

在其他机器上训练好模型后，把模型迁移回 Mac 使用。

### 导出模型（在训练机器上）

```bash
# macOS / Linux
bash export_model.sh

# Windows
export_model.bat
```

脚本会自动检测并打包：

| 内容 | 路径 | 说明 |
|------|------|------|
| LoRA 权重 | `backend/finetune/adapters/` | 核心产出 |
| 融合模型 | `backend/finetune/fused_model/` | 如果已融合 |
| 训练数据 | `backend/finetune/data/final/` | 可选，默认不包含 |
| 元信息 | `metadata.json` | 机器、GPU、训练迭代次数等 |

输出一个 zip 文件，如 `fin_model_export_20260302_143000.zip`。

### 导入模型（在 Mac 上）

```bash
# macOS / Linux
bash import_model.sh fin_model_export_20260302_143000.zip

# Windows
import_model.bat fin_model_export_20260302_143000.zip
```

脚本会：
1. 解压 zip 文件
2. 显示导出机器的元信息（日期、GPU、训练轮次等）
3. 自动备份现有模型（如果有）
4. 将 adapters 和 fused_model 放到正确位置

### 导入后使用模型

```bash
# 将 LoRA adapters 融合到基础模型
conda run -n quant python backend/finetune/deploy.py
```

### 迁移流程图

```
训练机器 (Windows/Linux + NVIDIA GPU)          Mac (Apple Silicon)
┌──────────────────────────────┐       ┌──────────────────────────────┐
│  1. bash deploy.sh           │       │                              │
│  2. 训练模型                  │       │                              │
│  3. bash export_model.sh     │──→──→─│  4. bash import_model.sh     │
│     生成 zip                  │ 传输  │  5. python deploy.py 融合     │
│                              │       │  6. bash start.sh 使用        │
└──────────────────────────────┘       └──────────────────────────────┘
```

---

## 五、Mac 完整模式（进阶）

Mac 上有额外的 C++ 加速服务和本地 MLX 模型服务，使用 `restart.sh` 启动完整模式：

```bash
bash restart.sh
```

完整模式包含 7 个服务：

| 服务 | 端口 | 说明 |
|------|------|------|
| MLX 本地模型 | 11434 | Alpha Lab 用的本地 AI 模型 |
| C++ 订单簿 | 8001 | 高性能订单簿模拟 |
| C++ 回测 | 8002 | 高性能回测引擎 |
| C++ 数据管道 | 8003 | 高性能数据处理 |
| Python 后端 | 8000 | FastAPI 接口 |
| 前端 | 5174 | React 开发服务器 |

> C++ 服务和 MLX 模型是 Mac 专属的可选增强模块。其他平台用 `start.sh` 启动核心服务即可。

---

## 六、常见问题

### Q: deploy.sh 报错 "conda: command not found"

Miniconda 刚安装，需要重新打开终端窗口让环境变量生效：

```bash
source ~/.bashrc   # 或 source ~/.zshrc
bash deploy.sh     # 重新运行
```

### Q: PyTorch 安装很慢

PyTorch 包比较大（约 2GB），耐心等待即可。如果网络不好，可以先手动安装：

```bash
# NVIDIA GPU
conda run -n quant pip install torch --index-url https://download.pytorch.org/whl/cu121

# 无 GPU
conda run -n quant pip install torch --index-url https://download.pytorch.org/whl/cpu
```

### Q: 前端启动后访问白屏

确认后端已启动并可以访问 http://127.0.0.1:8000/docs ，然后检查前端日志：

```bash
tail -f /tmp/fin-frontend.log
```

### Q: Windows 上 conda 命令找不到

需要使用 **Anaconda Prompt** 或 **Miniconda Prompt** 运行脚本，而不是普通的 cmd。

### Q: 导出的 zip 太大了

默认不包含训练数据（约 200MB+）。如果 zip 仍然很大，说明 `fused_model/` 里有完整模型权重（通常几 GB）。可以只迁移 adapters（几 MB），在目标机器上用 `deploy.py` 重新融合。

### Q: 端口被占用

```bash
# 查看谁占了 8000 端口
lsof -i :8000    # macOS/Linux
netstat -aon | findstr :8000    # Windows

# 用 stop 脚本清理
bash stop.sh
```

---

## 脚本一览

| 脚本 | macOS / Linux | Windows | 用途 |
|------|---------------|---------|------|
| 一键部署 | `bash deploy.sh` | `deploy.bat` | 首次使用，安装所有依赖 |
| 启动服务 | `bash start.sh` | `start.bat` | 日常启动后端 + 前端 |
| 停止服务 | `bash stop.sh` | `stop.bat` | 停止所有服务 |
| 完整模式 | `bash restart.sh` | — | Mac 专属，启动全部 7 个服务 |
| 导出模型 | `bash export_model.sh` | `export_model.bat` | 打包模型为 zip |
| 导入模型 | `bash import_model.sh <zip>` | `import_model.bat <zip>` | 从 zip 导入模型 |
