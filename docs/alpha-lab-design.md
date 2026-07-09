# Alpha Lab — AI 自动策略生成与迭代优化系统

> 设计文档 v1.0 | 2026-02-28

## 一、系统概述

Alpha Lab 是量化交易平台的"层次三"功能：让 AI 自动生成策略代码、执行回测、分析结果、迭代优化，形成闭环，持续寻找 alpha。

**核心理念**：用户只需设定目标（股票、优化指标、约束条件），系统自动完成"生成 → 回测 → 评估 → 改进"的迭代循环，最终输出经过验证的可用策略。

---

## 二、系统架构

### 2.1 目录结构

```
backend/alpha_lab/
├── __init__.py
├── engine.py              # AlphaLabEngine — 迭代主循环
├── session_manager.py     # 会话管理（多轮对话上下文）
├── code_generator.py      # AI 策略代码生成（Prompt 工程）
├── sandbox.py             # 安全沙箱（AST 白名单 + subprocess 隔离）
├── evaluator.py           # 回测结果评估 + 反过拟合检测
├── strategy_store.py      # 策略持久化（代码 + 结果 + 元数据）
└── prompts/
    ├── __init__.py
    ├── system.py          # System Prompt（教 AI 策略接口）
    ├── generate.py        # 首次生成 Prompt
    └── iterate.py         # 迭代优化 Prompt
```

### 2.2 架构图

```
┌─────────────────────────────────────────────────────────┐
│                    Frontend (AlphaLab.tsx)               │
│  ┌──────────┐ ┌────────────┐ ┌──────────┐ ┌─────────┐  │
│  │ 目标设定  │ │ 迭代进度面板│ │ 结果对比图│ │策略代码  │  │
│  └──────────┘ └────────────┘ └──────────┘ └─────────┘  │
└───────────────────────┬─────────────────────────────────┘
                        │ NDJSON Stream
┌───────────────────────▼─────────────────────────────────┐
│                 API Layer (routes/alpha_lab.py)          │
│  POST /start  POST /iterate  GET /sessions  GET /strategies │
└───────────────────────┬─────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────────┐
│                  AlphaLabEngine (engine.py)              │
│                                                         │
│  ┌─────────────┐    ┌──────────────┐    ┌───────────┐  │
│  │CodeGenerator │───▶│   Sandbox    │───▶│ Evaluator │  │
│  │ (OpenAI API) │    │(AST+Process) │    │(反过拟合)  │  │
│  └──────┬──────┘    └──────────────┘    └─────┬─────┘  │
│         │                                      │        │
│         └──────────── 迭代反馈 ◀───────────────┘        │
│                                                         │
│  ┌──────────────┐              ┌────────────────────┐   │
│  │SessionManager│              │  StrategyStore     │   │
│  │ (对话上下文)  │              │ (持久化代码+结果)   │   │
│  └──────────────┘              └────────────────────┘   │
└─────────────────────────────────────────────────────────┘
                        │
          ┌─────────────┼─────────────┐
          ▼             ▼             ▼
   ┌────────────┐ ┌──────────┐ ┌──────────────┐
   │ DataEngine │ │BacktestE.│ │TechIndicators│
   │(历史数据)   │ │(回测执行) │ │(指标计算)     │
   └────────────┘ └──────────┘ └──────────────┘
```

---

## 三、核心流程

### 3.1 完整迭代流程

```
用户设定目标 (股票/市场, 优化指标, 约束条件)
       │
       ▼
Phase 1: 探索期 (gpt-4o-mini, 前 3-5 轮)
  ├─ AI 生成策略代码（基于 BaseStrategy 接口）
  ├─ AST 安全检查 → subprocess 沙箱执行回测
  ├─ 评估: 训练集指标 + 验证集指标 + 过拟合分数
  └─ 反馈给 AI → 生成改进版本
       │
       ▼
Phase 2: 精炼期 (gpt-4o, 后 5-10 轮)
  ├─ 基于最佳策略继续优化
  ├─ Walk-forward 验证
  └─ 多股票交叉验证
       │
       ▼
Phase 3: 输出
  ├─ 最优策略代码 + 完整回测报告
  ├─ 过拟合风险评分
  └─ 可选: 一键部署到模拟交易
```

### 3.2 单轮迭代详细流程

```python
# 伪代码
async def single_iteration(session, iteration_num):
    # 1. 构建 prompt（包含历史最佳结果）
    prompt = build_prompt(session, iteration_num)

    # 2. 调用 AI 生成策略代码
    code = await code_generator.generate(prompt, session.messages)

    # 3. AST 安全检查
    is_safe, violations = sandbox.ast_check(code)
    if not is_safe:
        # 反馈违规信息给 AI，让它修正
        return retry_with_feedback(violations)

    # 4. 在沙箱中执行回测
    result = sandbox.execute_backtest(code, session.target_symbols, session.data_range)

    # 5. 评估结果
    evaluation = evaluator.evaluate(
        train_metrics=result.train_metrics,
        val_metrics=result.val_metrics,
        history=session.history
    )

    # 6. 持久化
    strategy_store.save(session.id, iteration_num, code, evaluation)

    # 7. 构建反馈给 AI
    return build_feedback(evaluation)
```

---

## 四、模块详细设计

### 4.1 AlphaLabEngine (engine.py)

迭代主循环，协调所有子模块。

```python
class AlphaLabEngine:
    """Alpha Lab 迭代引擎"""

    def __init__(self):
        self.session_manager = SessionManager()
        self.code_generator = CodeGenerator()
        self.sandbox = Sandbox()
        self.evaluator = Evaluator()
        self.strategy_store = StrategyStore()

    async def start_session(
        self,
        target_symbols: List[str],
        optimization_goal: str,        # "sharpe" | "return" | "win_rate" | "drawdown"
        data_start: str,
        data_end: str,
        max_iterations: int = 15,
        constraints: Optional[Dict] = None
    ) -> AsyncGenerator[str, None]:
        """
        启动新的 Alpha Lab 会话（流式 NDJSON）

        Yields:
            NDJSON 事件流：session_created / iteration_start / code_generated /
            backtest_running / evaluation_done / iteration_complete /
            phase_change / session_complete / error
        """
        # 创建会话
        session = self.session_manager.create(
            target_symbols=target_symbols,
            optimization_goal=optimization_goal,
            data_range=(data_start, data_end),
            max_iterations=max_iterations,
            constraints=constraints or {}
        )
        yield _ndjson({"event": "session_created", "session_id": session.id})

        # 准备数据（训练集 70% + 验证集 30%）
        yield _ndjson({"event": "preparing_data", "message": "正在准备数据..."})
        train_data, val_data = self._prepare_data(session)

        # 迭代循环
        for i in range(1, max_iterations + 1):
            model = "gpt-4o-mini" if i <= 5 else "gpt-4o"
            yield _ndjson({
                "event": "iteration_start",
                "iteration": i,
                "model": model,
                "phase": "explore" if i <= 5 else "refine"
            })

            try:
                async for event in self._run_iteration(session, i, model, train_data, val_data):
                    yield event
            except Exception as e:
                yield _ndjson({"event": "iteration_error", "iteration": i, "error": str(e)})
                continue

            # 检查是否满足终止条件
            if self._should_stop(session):
                yield _ndjson({"event": "early_stop", "reason": "连续3轮无改进"})
                break

        # 输出最终结果
        best = session.get_best_strategy()
        yield _ndjson({
            "event": "session_complete",
            "session_id": session.id,
            "best_iteration": best.iteration,
            "best_sharpe": best.val_metrics["sharpe_ratio"],
            "overfit_score": best.overfit_score,
            "total_cost_usd": session.total_cost
        })

    async def continue_session(
        self,
        session_id: str,
        additional_iterations: int = 5,
        user_feedback: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        """继续已有会话的迭代"""
        session = self.session_manager.get(session_id)
        if user_feedback:
            session.add_user_feedback(user_feedback)
        # ... 继续迭代循环

    def _prepare_data(self, session) -> Tuple[Dict[str, pd.DataFrame], Dict[str, pd.DataFrame]]:
        """准备训练/验证数据集（时序分割，非随机）"""
        data_engine = DataEngine()
        train_data = {}
        val_data = {}

        for symbol in session.target_symbols:
            df = data_engine.get_daily_data(
                symbol=symbol,
                start_date=session.data_range[0],
                end_date=session.data_range[1]
            )
            df = TechnicalIndicators.calculate_all_indicators(df)

            # 时序分割：前 70% 训练，后 30% 验证
            split_idx = int(len(df) * 0.7)
            train_data[symbol] = df.iloc[:split_idx]
            val_data[symbol] = df.iloc[split_idx:]

        return train_data, val_data

    def _should_stop(self, session) -> bool:
        """早停条件：连续 3 轮验证集 Sharpe 无改善"""
        history = session.get_evaluation_history()
        if len(history) < 4:
            return False
        best_sharpe = max(h.val_metrics.get("sharpe_ratio", -999) for h in history[:-3])
        recent_best = max(h.val_metrics.get("sharpe_ratio", -999) for h in history[-3:])
        return recent_best <= best_sharpe
```

### 4.2 SessionManager (session_manager.py)

```python
@dataclass
class AlphaLabSessionState:
    """会话运行时状态"""
    id: str
    target_symbols: List[str]
    optimization_goal: str              # "sharpe" | "return" | "win_rate" | "drawdown"
    data_range: Tuple[str, str]
    max_iterations: int
    constraints: Dict

    # 运行状态
    status: str = "running"             # running | paused | completed | failed
    current_iteration: int = 0
    phase: str = "explore"              # explore | refine

    # AI 对话上下文
    messages: List[Dict] = field(default_factory=list)  # OpenAI messages 格式

    # 迭代历史
    iterations: List[Dict] = field(default_factory=list)
    best_iteration: Optional[int] = None
    best_val_sharpe: float = -999.0

    # 成本追踪
    total_tokens: int = 0
    total_cost: float = 0.0

    created_at: float = field(default_factory=time.time)


class SessionManager:
    """会话管理器"""

    def __init__(self):
        self._sessions: Dict[str, AlphaLabSessionState] = {}
        self._lock = threading.Lock()

    def create(self, **kwargs) -> AlphaLabSessionState:
        session_id = f"alab_{uuid.uuid4().hex[:12]}"
        session = AlphaLabSessionState(id=session_id, **kwargs)
        with self._lock:
            self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> AlphaLabSessionState:
        session = self._sessions.get(session_id)
        if not session:
            raise ValueError(f"会话不存在: {session_id}")
        return session

    def update_best(self, session: AlphaLabSessionState, iteration: int, val_sharpe: float):
        if val_sharpe > session.best_val_sharpe:
            session.best_val_sharpe = val_sharpe
            session.best_iteration = iteration

    def add_cost(self, session: AlphaLabSessionState, tokens: int, model: str):
        session.total_tokens += tokens
        # gpt-4o-mini: $0.15/1M input + $0.60/1M output
        # gpt-4o: $2.50/1M input + $10.00/1M output
        rate = 0.00075 if "mini" in model else 0.00625  # 平均每 1K token
        session.total_cost += tokens * rate / 1000
```

### 4.3 CodeGenerator (code_generator.py)

```python
class CodeGenerator:
    """AI 策略代码生成器"""

    def __init__(self):
        self.client = None
        self._init_client()

    def _init_client(self):
        api_key = os.getenv("OPENAI_API_KEY", "")
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        if api_key:
            self.client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=HttpxTimeout(connect=15.0, read=120.0, write=30.0, pool=30.0),
                max_retries=2
            )

    def generate(
        self,
        session: AlphaLabSessionState,
        model: str = "gpt-4o-mini"
    ) -> Tuple[str, int]:
        """
        调用 AI 生成策略代码

        Returns:
            (strategy_code: str, tokens_used: int)
        """
        if not self.client:
            raise RuntimeError("OPENAI_API_KEY 未配置")

        response = self.client.chat.completions.create(
            model=model,
            messages=session.messages,
            temperature=0.7,        # 探索期略高，鼓励多样性
            max_tokens=2000
        )

        content = response.choices[0].message.content
        tokens = response.usage.total_tokens if response.usage else 0

        # 提取代码块
        code = self._extract_code(content)

        # 保存到对话历史
        session.messages.append({"role": "assistant", "content": content})

        return code, tokens

    def _extract_code(self, content: str) -> str:
        """从 AI 回复中提取 Python 代码块"""
        import re
        pattern = r'```python\s*\n(.*?)```'
        matches = re.findall(pattern, content, re.DOTALL)
        if not matches:
            raise ValueError("AI 回复中未找到 Python 代码块")
        return matches[0].strip()
```

### 4.4 Sandbox (sandbox.py)

安全沙箱，两层防护。

```python
import ast
import subprocess
import tempfile
import json

# AST 白名单
ALLOWED_IMPORTS = {
    "pandas", "numpy", "math", "datetime", "collections",
    "dataclasses", "typing", "enum"
}

BANNED_NAMES = {
    "os", "sys", "subprocess", "shutil", "pathlib",
    "exec", "eval", "compile", "__import__", "open",
    "globals", "locals", "getattr", "setattr", "delattr",
    "breakpoint", "exit", "quit"
}

BANNED_ATTRIBUTES = {
    "system", "popen", "exec", "eval", "remove", "rmdir",
    "unlink", "write", "read"
}


class ASTChecker(ast.NodeVisitor):
    """AST 白名单检查器"""

    def __init__(self):
        self.violations: List[str] = []

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            module = alias.name.split(".")[0]
            if module not in ALLOWED_IMPORTS:
                self.violations.append(f"禁止 import: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            module = node.module.split(".")[0]
            if module not in ALLOWED_IMPORTS:
                self.violations.append(f"禁止 from {node.module} import")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        if isinstance(node.func, ast.Name):
            if node.func.id in BANNED_NAMES:
                self.violations.append(f"禁止调用: {node.func.id}()")
        elif isinstance(node.func, ast.Attribute):
            if node.func.attr in BANNED_ATTRIBUTES:
                self.violations.append(f"禁止调用: .{node.func.attr}()")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name):
        if node.id.startswith("__") and node.id.endswith("__"):
            if node.id not in ("__init__", "__name__", "__main__"):
                self.violations.append(f"禁止使用 dunder: {node.id}")
        self.generic_visit(node)


class Sandbox:
    """安全沙箱"""

    TIMEOUT_SECONDS = 60

    def ast_check(self, code: str) -> Tuple[bool, List[str]]:
        """
        第一层：AST 静态检查

        Returns:
            (is_safe, violations)
        """
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return False, [f"语法错误: {e}"]

        checker = ASTChecker()
        checker.visit(tree)

        return len(checker.violations) == 0, checker.violations

    def execute_backtest(
        self,
        strategy_code: str,
        symbols: List[str],
        train_data_paths: Dict[str, str],
        val_data_paths: Dict[str, str],
        initial_capital: float = 1000000.0
    ) -> Dict:
        """
        第二层：subprocess 隔离执行回测

        将策略代码和回测运行器写入临时文件，用独立进程执行，
        通过 JSON 文件传递结果。即使 AI 生成恶意代码，也无法影响主进程。

        Returns:
            {
                "success": bool,
                "error": str | None,
                "train_metrics": {symbol: {sharpe, return, ...}},
                "val_metrics": {symbol: {sharpe, return, ...}},
                "equity_curves": {symbol: [...]},
                "trades": {symbol: [...]}
            }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # 1. 写入策略代码
            strategy_path = os.path.join(tmpdir, "strategy.py")
            with open(strategy_path, "w") as f:
                f.write(strategy_code)

            # 2. 写入回测运行器脚本
            runner_path = os.path.join(tmpdir, "runner.py")
            result_path = os.path.join(tmpdir, "result.json")
            self._write_runner_script(runner_path, result_path, symbols,
                                       train_data_paths, val_data_paths,
                                       initial_capital)

            # 3. 在独立进程中执行
            try:
                proc = subprocess.run(
                    ["conda", "run", "-n", "quant", "python", runner_path],
                    capture_output=True,
                    text=True,
                    timeout=self.TIMEOUT_SECONDS,
                    cwd=tmpdir
                )

                if proc.returncode != 0:
                    return {
                        "success": False,
                        "error": proc.stderr[-500:] if proc.stderr else "未知错误",
                        "train_metrics": {},
                        "val_metrics": {}
                    }

                # 4. 读取结果
                with open(result_path, "r") as f:
                    return json.load(f)

            except subprocess.TimeoutExpired:
                return {
                    "success": False,
                    "error": f"回测执行超时（{self.TIMEOUT_SECONDS}秒）",
                    "train_metrics": {},
                    "val_metrics": {}
                }

    def _write_runner_script(self, runner_path, result_path, symbols,
                              train_data_paths, val_data_paths, initial_capital):
        """生成回测运行器脚本"""
        script = f'''
import sys
import json
import pandas as pd

sys.path.insert(0, "{os.path.abspath("backend")}")

from backtest_engine.engine import BacktestEngine
from backtest_engine.metrics.calculator import MetricsCalculator
from strategy.indicators import TechnicalIndicators

# 导入 AI 生成的策略
sys.path.insert(0, "{os.path.dirname(runner_path)}")
from strategy import GeneratedStrategy

result = {{"success": True, "train_metrics": {{}}, "val_metrics": {{}}, "error": None}}

symbols = {json.dumps(symbols)}
train_paths = {json.dumps(train_data_paths)}
val_paths = {json.dumps(val_data_paths)}

for sym in symbols:
    try:
        strategy = GeneratedStrategy()
        engine = BacktestEngine(initial_capital={initial_capital})

        # 训练集回测
        train_df = pd.read_parquet(train_paths[sym])
        train_result = engine.run(symbol=sym, data=train_df, strategy=strategy)
        result["train_metrics"][sym] = train_result["metrics"]

        # 验证集回测
        strategy_val = GeneratedStrategy()
        engine_val = BacktestEngine(initial_capital={initial_capital})
        val_df = pd.read_parquet(val_paths[sym])
        val_result = engine_val.run(symbol=sym, data=val_df, strategy=strategy_val)
        result["val_metrics"][sym] = val_result["metrics"]

    except Exception as e:
        result["success"] = False
        result["error"] = str(e)
        break

with open("{result_path}", "w") as f:
    json.dump(result, f, ensure_ascii=False, default=str)
'''
        with open(runner_path, "w") as f:
            f.write(script)
```

### 4.5 Evaluator (evaluator.py)

```python
@dataclass
class EvaluationResult:
    """单轮评估结果"""
    iteration: int
    strategy_code: str

    # 指标
    train_metrics: Dict[str, Dict]      # {symbol: {sharpe, return, ...}}
    val_metrics: Dict[str, Dict]        # {symbol: {sharpe, return, ...}}

    # 过拟合检测
    overfit_score: float                # 0~1，越高越过拟合
    overfit_warnings: List[str]

    # 综合评分
    composite_score: float              # 综合打分，用于排序

    # 元数据
    execution_time: float
    error: Optional[str] = None


class Evaluator:
    """回测结果评估器"""

    # 过拟合阈值
    OVERFIT_WARNING_THRESHOLD = 0.3
    OVERFIT_REJECT_THRESHOLD = 0.5

    def evaluate(
        self,
        iteration: int,
        code: str,
        backtest_result: Dict,
        optimization_goal: str
    ) -> EvaluationResult:
        """评估一轮回测结果"""

        train_metrics = backtest_result["train_metrics"]
        val_metrics = backtest_result["val_metrics"]

        # 1. 计算过拟合分数
        overfit_score, warnings = self._detect_overfitting(train_metrics, val_metrics)

        # 2. 计算综合评分（基于验证集）
        composite = self._composite_score(val_metrics, optimization_goal, overfit_score)

        return EvaluationResult(
            iteration=iteration,
            strategy_code=code,
            train_metrics=train_metrics,
            val_metrics=val_metrics,
            overfit_score=overfit_score,
            overfit_warnings=warnings,
            composite_score=composite,
            execution_time=backtest_result.get("execution_time", 0)
        )

    def _detect_overfitting(
        self,
        train_metrics: Dict[str, Dict],
        val_metrics: Dict[str, Dict]
    ) -> Tuple[float, List[str]]:
        """
        过拟合检测

        核心公式: overfit_score = 1 - (avg_val_sharpe / avg_train_sharpe)
        - 0.0 = 完美泛化
        - 0.3 = 轻微过拟合（警告）
        - 0.5+ = 严重过拟合（拒绝）
        - 负数 = 验证集比训练集更好（罕见但正常）
        """
        warnings = []

        # 计算各股票平均 Sharpe
        train_sharpes = []
        val_sharpes = []
        for symbol in train_metrics:
            ts = train_metrics[symbol].get("sharpe_ratio", 0)
            vs = val_metrics.get(symbol, {}).get("sharpe_ratio", 0)
            train_sharpes.append(ts)
            val_sharpes.append(vs)

        avg_train = sum(train_sharpes) / len(train_sharpes) if train_sharpes else 0
        avg_val = sum(val_sharpes) / len(val_sharpes) if val_sharpes else 0

        if avg_train <= 0:
            return 0.0, ["训练集 Sharpe ≤ 0，策略无效"]

        overfit_score = max(0, 1 - (avg_val / avg_train))

        if overfit_score > self.OVERFIT_REJECT_THRESHOLD:
            warnings.append(f"严重过拟合 (score={overfit_score:.2f})：验证集表现远差于训练集")
        elif overfit_score > self.OVERFIT_WARNING_THRESHOLD:
            warnings.append(f"轻微过拟合 (score={overfit_score:.2f})：建议简化策略参数")

        # 额外检查：训练集收益极高但验证集亏损
        for symbol in train_metrics:
            train_ret = train_metrics[symbol].get("total_return", 0)
            val_ret = val_metrics.get(symbol, {}).get("total_return", 0)
            if train_ret > 50 and val_ret < 0:
                warnings.append(f"{symbol}: 训练集收益{train_ret:.1f}%，验证集亏损{val_ret:.1f}%")

        return overfit_score, warnings

    def _composite_score(
        self,
        val_metrics: Dict[str, Dict],
        optimization_goal: str,
        overfit_score: float
    ) -> float:
        """
        综合评分（基于验证集指标 + 过拟合惩罚）

        score = primary_metric × (1 - overfit_penalty)
        """
        # 各股票验证集指标平均
        values = []
        for symbol, metrics in val_metrics.items():
            if optimization_goal == "sharpe":
                values.append(metrics.get("sharpe_ratio", 0))
            elif optimization_goal == "return":
                values.append(metrics.get("annualized_return", 0))
            elif optimization_goal == "win_rate":
                values.append(metrics.get("win_rate", 0))
            elif optimization_goal == "drawdown":
                # 回撤越小越好，取负数
                dd = metrics.get("max_drawdown", {})
                values.append(-abs(dd.get("max_drawdown_pct", 100)))

        avg_value = sum(values) / len(values) if values else 0

        # 过拟合惩罚
        penalty = min(overfit_score * 1.5, 1.0)  # 最多惩罚 100%

        return avg_value * (1 - penalty)

    def walk_forward_validate(
        self,
        strategy_code: str,
        symbol: str,
        full_data: pd.DataFrame,
        window_size: int = 120,
        step_size: int = 60
    ) -> List[Dict]:
        """
        Walk-forward 验证

        在多个滑动窗口上分别回测，检查策略是否在所有时间段都有效。

        Args:
            window_size: 每个窗口的交易日数
            step_size: 窗口滑动步长

        Returns:
            [{window_start, window_end, sharpe, return, ...}, ...]
        """
        results = []
        for start_idx in range(0, len(full_data) - window_size, step_size):
            window_data = full_data.iloc[start_idx:start_idx + window_size]
            # 在沙箱中执行回测
            result = self._run_single_backtest(strategy_code, symbol, window_data)
            results.append({
                "window_start": str(window_data.index[0]),
                "window_end": str(window_data.index[-1]),
                "sharpe": result.get("sharpe_ratio", 0),
                "return": result.get("total_return", 0),
                "max_drawdown": result.get("max_drawdown_pct", 0),
                "trades": result.get("num_trades", 0)
            })
        return results
```

### 4.6 StrategyStore (strategy_store.py)

```python
class StrategyStore:
    """策略持久化 — 保存到数据库"""

    def __init__(self):
        self.session = get_session()

    def save_strategy(
        self,
        session_id: str,
        iteration: int,
        code: str,
        evaluation: EvaluationResult
    ) -> str:
        """保存一轮生成的策略"""
        strategy = GeneratedStrategy(
            strategy_id=f"{session_id}_iter{iteration}",
            session_id=session_id,
            iteration=iteration,
            code=code,
            train_metrics=json.dumps(evaluation.train_metrics, default=str),
            val_metrics=json.dumps(evaluation.val_metrics, default=str),
            overfit_score=evaluation.overfit_score,
            composite_score=evaluation.composite_score,
            status="completed"
        )
        self.session.add(strategy)
        self.session.commit()
        return strategy.strategy_id

    def get_best_strategy(self, session_id: str) -> Optional[GeneratedStrategy]:
        """获取会话中综合评分最高的策略"""
        return self.session.query(GeneratedStrategy).filter(
            GeneratedStrategy.session_id == session_id,
            GeneratedStrategy.status == "completed"
        ).order_by(GeneratedStrategy.composite_score.desc()).first()

    def get_session_strategies(self, session_id: str) -> List[GeneratedStrategy]:
        """获取会话所有策略（按迭代顺序）"""
        return self.session.query(GeneratedStrategy).filter(
            GeneratedStrategy.session_id == session_id
        ).order_by(GeneratedStrategy.iteration).all()
```

---

## 五、Prompt 工程

### 5.1 System Prompt (prompts/system.py)

```python
SYSTEM_PROMPT = """你是一个专业的量化策略开发者。你的任务是编写 Python 策略代码，用于在回测引擎中执行。

## 策略接口规范

你必须实现一个 `GeneratedStrategy` 类，继承自 `BaseStrategy`：

```python
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
from enum import Enum
import pandas as pd
import numpy as np
import math

# === 订单类型 ===
class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"

# === 订单数据类 ===
@dataclass
class Order:
    symbol: str                           # 股票代码
    quantity: int                         # 正数=买入, 负数=卖出
    order_type: OrderType                 # MARKET 或 LIMIT
    timestamp: datetime                   # 下单时间
    price: Optional[float] = None         # 限价单价格（市价单不需要）

# === 策略上下文 ===
@dataclass
class StrategyContext:
    symbol: str                    # 股票代码
    current_time: datetime         # 当前日期
    current_price: float           # 当前收盘价
    data: pd.DataFrame             # 历史数据 DataFrame（含所有指标，见下方）
    cash: float                    # 可用现金
    position_quantity: int         # 当前持仓数量（0 表示空仓）
    position_avg_price: float      # 持仓均价（无持仓时为 0）
    total_value: float             # 总资产 = 现金 + 持仓市值

# === 你需要实现的类 ===
class GeneratedStrategy(BaseStrategy):
    def __init__(self):
        super().__init__(name="GeneratedStrategy", params={})

    def generate_signals(self, context: StrategyContext) -> List[Order]:
        # 在这里实现你的策略逻辑
        # 返回 Order 列表，空列表表示不操作
        pass
```

## context.data 中可用的列

基础数据列：
- `open`, `high`, `low`, `close`, `volume` — OHLCV
- `amount` — 成交额
- `turnover` — 换手率

技术指标列（已预计算）：
- `ma5`, `ma10`, `ma20`, `ma60` — 简单移动平均线
- `macd_dif`, `macd_dea`, `macd` — MACD 指标
- `kdj_k`, `kdj_d`, `kdj_j` — KDJ 指标
- `rsi` — 14日 RSI
- `boll_upper`, `boll_mid`, `boll_lower` — 布林带
- `volume_ratio` — 量比
- `atr` — 14日 ATR

## 严格约束（必须遵守）

1. **参数限制**：策略参数不超过 5 个（避免过拟合）
2. **必须止损**：每笔交易必须有止损逻辑（建议 -5% 到 -8%）
3. **禁止硬编码**：不得硬编码特定价格、日期或股票代码
4. **只用可用列**：只使用上面列出的 data 列，不要假设其他列存在
5. **安全买入**：买入数量必须取整到 100 的倍数（A股最小交易单位）
6. **仓位控制**：单次买入不超过可用资金的 95%
7. **数据检查**：使用指标前检查 data 长度是否足够

## 代码格式要求

- 只输出一个完整的 Python 代码块
- 包含所有必要的 import
- 类名必须是 `GeneratedStrategy`
- 不要包含任何 import os/sys/subprocess 等系统库
"""
```

### 5.2 首次生成 Prompt (prompts/generate.py)

```python
def build_first_generate_prompt(
    symbols: List[str],
    optimization_goal: str,
    constraints: Dict,
    sample_data_summary: Dict
) -> str:
    """构建首次生成的 user prompt"""

    goal_desc = {
        "sharpe": "最大化夏普比率（风险调整后收益）",
        "return": "最大化年化收益率",
        "win_rate": "最大化胜率（同时保持合理盈亏比）",
        "drawdown": "最小化最大回撤（同时保持正收益）"
    }

    return f"""请为以下股票生成一个交易策略：

**目标股票**: {', '.join(symbols)}
**优化目标**: {goal_desc.get(optimization_goal, optimization_goal)}
**回测时间**: 训练集约 {sample_data_summary.get('train_days', 'N/A')} 个交易日

**数据概况**:
{_format_data_summary(sample_data_summary)}

**额外约束**:
{_format_constraints(constraints)}

请生成策略代码。思考过程中请考虑：
1. 什么市场状态适合什么策略？（趋势/震荡/突破）
2. 如何利用多个指标的组合来过滤假信号？
3. 止损和止盈如何设置才合理？
4. 仓位管理怎样平衡风险和收益？

直接输出一个完整的 Python 代码块。
"""
```

### 5.3 迭代优化 Prompt (prompts/iterate.py)

```python
def build_iteration_prompt(
    iteration: int,
    prev_evaluation: EvaluationResult,
    best_evaluation: Optional[EvaluationResult],
    phase: str
) -> str:
    """构建迭代优化的 user prompt"""

    feedback = f"""## 第 {iteration - 1} 轮回测结果

### 训练集表现
{_format_metrics_table(prev_evaluation.train_metrics)}

### 验证集表现
{_format_metrics_table(prev_evaluation.val_metrics)}

### 过拟合检测
- 过拟合分数: {prev_evaluation.overfit_score:.3f} {'⚠️ 警告' if prev_evaluation.overfit_score > 0.3 else '✅ 正常'}
{chr(10).join('- ' + w for w in prev_evaluation.overfit_warnings) if prev_evaluation.overfit_warnings else '- 无警告'}

### 综合评分: {prev_evaluation.composite_score:.4f}
"""

    if best_evaluation and best_evaluation.iteration != prev_evaluation.iteration:
        feedback += f"""
### 历史最佳（第 {best_evaluation.iteration} 轮）
- 验证集 Sharpe: {_get_avg_sharpe(best_evaluation.val_metrics):.3f}
- 综合评分: {best_evaluation.composite_score:.4f}
"""

    if phase == "explore":
        instruction = """
请基于上述结果，生成一个**改进版**策略。你可以：
- 尝试完全不同的策略思路
- 调整入场/出场条件
- 改进止损逻辑
- 优化仓位管理

注意：如果过拟合分数偏高，请简化策略（减少参数、用更长周期的指标）。
"""
    else:  # refine
        instruction = """
当前进入精炼期。请基于历史最佳策略进行**微调优化**：
- 微调参数（小幅调整，不要大改）
- 增加额外的过滤条件减少假信号
- 优化止损/止盈比例
- 考虑加入趋势过滤器

目标：提高验证集表现，同时降低过拟合分数。
"""

    return feedback + instruction


def _format_metrics_table(metrics: Dict[str, Dict]) -> str:
    """格式化指标表格"""
    lines = []
    for symbol, m in metrics.items():
        lines.append(f"""| {symbol} |
| 年化收益 | {m.get('annualized_return', 0):.2f}% |
| 夏普比率 | {m.get('sharpe_ratio', 0):.3f} |
| 最大回撤 | {m.get('max_drawdown', {}).get('max_drawdown_pct', 0):.2f}% |
| 胜率 | {m.get('win_rate', 0):.1f}% |
| 交易次数 | {m.get('num_trades', 0)} |
| 盈亏比 | {m.get('profit_factor', 0):.2f} |""")
    return "\n".join(lines)
```

---

## 六、反过拟合机制详述

### 6.1 时序数据分割

```
全部数据: |============ 训练集 70% ============|==== 验证集 30% ====|

重要：时序数据必须按时间顺序分割，绝不能随机 shuffle！
      因为金融数据有时间依赖性，随机分割会导致「未来数据泄露」。
```

### 6.2 过拟合分数计算

```
overfit_score = max(0, 1 - (avg_val_sharpe / avg_train_sharpe))

| 分数范围     | 含义         | 处理方式                    |
|-------------|-------------|---------------------------|
| 0.0 ~ 0.2  | 泛化良好     | ✅ 策略有效                  |
| 0.2 ~ 0.3  | 轻微过拟合   | ⚠️ 提示 AI 简化参数          |
| 0.3 ~ 0.5  | 中度过拟合   | ⚠️ 强制 AI 减少参数到 3 个以下 |
| 0.5+        | 严重过拟合   | ❌ 拒绝该策略，要求重新生成    |
```

### 6.3 Walk-forward 验证

```
全部数据: [=====W1=====][=====W2=====][=====W3=====][=====W4=====]

每个窗口独立回测，要求：
- 至少 60% 的窗口 Sharpe > 0
- 所有窗口平均 Sharpe > 0.3
- 没有窗口回撤超过 -30%

仅在精炼期（Phase 2）使用，探索期跳过以节省时间。
```

### 6.4 多股票交叉验证

```
在精炼期，同一策略在多只股票上分别回测：
- 至少 70% 的股票验证集 Sharpe > 0
- 否则认为策略缺乏普适性，降低评分
```

### 6.5 Prompt 硬约束

以下约束直接写入 System Prompt，AI 无法绕过：
- 参数不超过 5 个（AST 检查 `__init__` 参数数量）
- 禁止硬编码价格/日期（AST 检查是否有 magic number > 1000）
- 必须有止损逻辑（AST 检查是否有 stop_loss 相关判断）
- 禁止使用 `eval`、`exec` 等危险函数

---

## 七、数据库 Schema

### 7.1 新增表

```python
# backend/data_engine/storage/models.py 新增

class AlphaLabSession(Base):
    """Alpha Lab 会话"""
    __tablename__ = "alpha_lab_sessions"

    id = Column(String(50), primary_key=True)               # alab_xxxxxxxxxxxx
    target_symbols = Column(Text, nullable=False)            # JSON: ["600519.SH", "AAPL"]
    optimization_goal = Column(String(20), nullable=False)   # sharpe/return/win_rate/drawdown
    data_start = Column(String(10), nullable=False)          # 2024-01-01
    data_end = Column(String(10), nullable=False)            # 2025-12-31
    max_iterations = Column(Integer, default=15)
    constraints = Column(Text)                               # JSON

    # 运行状态
    status = Column(String(20), default="running", index=True)  # running/paused/completed/failed
    total_iterations = Column(Integer, default=0)
    current_phase = Column(String(10), default="explore")    # explore/refine

    # 最佳结果
    best_iteration = Column(Integer)
    best_sharpe = Column(Float)
    best_composite_score = Column(Float)

    # 成本追踪
    total_tokens = Column(Integer, default=0)
    cost_usd = Column(Float, default=0.0)

    # 时间戳
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    completed_at = Column(DateTime)


class AlphaLabStrategy(Base):
    """Alpha Lab 生成的策略"""
    __tablename__ = "alpha_lab_strategies"

    id = Column(String(80), primary_key=True)                # alab_xxx_iter1
    session_id = Column(String(50), ForeignKey("alpha_lab_sessions.id"), nullable=False, index=True)
    iteration = Column(Integer, nullable=False)

    # 策略代码
    code = Column(Text, nullable=False)
    ai_reasoning = Column(Text)                              # AI 的思考过程

    # 回测指标
    train_metrics = Column(Text)                             # JSON
    val_metrics = Column(Text)                               # JSON

    # 评估
    overfit_score = Column(Float)
    composite_score = Column(Float, index=True)
    walk_forward_results = Column(Text)                      # JSON（精炼期）

    # 状态
    status = Column(String(20), default="completed")         # completed/failed/rejected
    error_message = Column(Text)
    execution_time = Column(Float)                           # 秒

    # 部署
    deployed = Column(Integer, default=0)                    # 0=未部署, 1=已部署
    deployed_at = Column(DateTime)

    created_at = Column(DateTime, server_default=func.now())


class AlphaLabLog(Base):
    """Alpha Lab 运行日志"""
    __tablename__ = "alpha_lab_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(50), ForeignKey("alpha_lab_sessions.id"), nullable=False, index=True)
    iteration = Column(Integer)

    level = Column(String(10), default="INFO")               # INFO/WARN/ERROR
    event = Column(String(50), nullable=False)               # code_generated/backtest_run/evaluation/...
    message = Column(Text)
    details = Column(Text)                                   # JSON 额外信息

    created_at = Column(DateTime, server_default=func.now())
```

### 7.2 索引说明

```sql
-- 按状态查询会话
CREATE INDEX idx_sessions_status ON alpha_lab_sessions(status);

-- 按会话查询策略（评分排序）
CREATE INDEX idx_strategies_session_score ON alpha_lab_strategies(session_id, composite_score DESC);

-- 按会话查询日志
CREATE INDEX idx_logs_session ON alpha_lab_logs(session_id, created_at);
```

---

## 八、API 接口规范

### 8.1 路由注册

```python
# backend/api/routes/alpha_lab.py
from fastapi import APIRouter
router = APIRouter(prefix="/alpha-lab", tags=["Alpha Lab"])
```

### 8.2 接口详情

#### POST /alpha-lab/start — 启动新会话

```python
class AlphaLabStartRequest(BaseModel):
    target_symbols: List[str]                    # ["600519.SH", "000858.SZ"]
    optimization_goal: str = "sharpe"            # sharpe/return/win_rate/drawdown
    data_start: str                              # "2023-01-01"
    data_end: str                                # "2025-12-31"
    max_iterations: int = 15                     # 最大迭代轮数
    initial_capital: float = 1000000.0
    constraints: Optional[Dict[str, Any]] = None # 额外约束

@router.post("/start")
async def start_alpha_lab(request: AlphaLabStartRequest):
    """启动 Alpha Lab 会话（流式 NDJSON）"""
    engine = AlphaLabEngine()

    async def _stream():
        async for event in engine.start_session(
            target_symbols=request.target_symbols,
            optimization_goal=request.optimization_goal,
            data_start=request.data_start,
            data_end=request.data_end,
            max_iterations=request.max_iterations,
            constraints=request.constraints
        ):
            yield event

    return StreamingResponse(
        _stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )
```

**NDJSON 事件流示例**：

```jsonl
{"event": "session_created", "session_id": "alab_a1b2c3d4e5f6"}
{"event": "preparing_data", "message": "正在准备数据..."}
{"event": "iteration_start", "iteration": 1, "model": "gpt-4o-mini", "phase": "explore"}
{"event": "code_generated", "iteration": 1, "code_preview": "class GeneratedStrategy..."}
{"event": "ast_check_passed", "iteration": 1}
{"event": "backtest_running", "iteration": 1, "symbol": "600519.SH"}
{"event": "backtest_running", "iteration": 1, "symbol": "000858.SZ"}
{"event": "evaluation_done", "iteration": 1, "train_sharpe": 1.23, "val_sharpe": 0.89, "overfit_score": 0.28, "composite_score": 0.64}
{"event": "iteration_complete", "iteration": 1, "is_best": true}
{"event": "iteration_start", "iteration": 2, "model": "gpt-4o-mini", "phase": "explore"}
...
{"event": "phase_change", "from": "explore", "to": "refine", "iteration": 6}
...
{"event": "session_complete", "session_id": "alab_a1b2c3d4e5f6", "best_iteration": 8, "best_sharpe": 1.45, "overfit_score": 0.15, "total_cost_usd": 0.73}
```

#### POST /alpha-lab/iterate — 继续迭代

```python
class AlphaLabIterateRequest(BaseModel):
    session_id: str
    additional_iterations: int = 5
    user_feedback: Optional[str] = None  # 用户对策略的额外要求

@router.post("/iterate")
async def iterate_alpha_lab(request: AlphaLabIterateRequest):
    """继续已有会话的迭代（流式 NDJSON）"""
    # 同 start，返回 StreamingResponse
```

#### GET /alpha-lab/sessions — 会话列表

```python
class SessionSummary(BaseModel):
    id: str
    target_symbols: List[str]
    optimization_goal: str
    status: str
    total_iterations: int
    best_sharpe: Optional[float]
    best_composite_score: Optional[float]
    cost_usd: float
    created_at: str

@router.get("/sessions", response_model=List[SessionSummary])
async def list_sessions(
    status: Optional[str] = None,  # 按状态过滤
    limit: int = 20
):
    """获取会话列表"""
```

#### GET /alpha-lab/sessions/{session_id} — 会话详情

```python
class SessionDetail(BaseModel):
    id: str
    target_symbols: List[str]
    optimization_goal: str
    data_start: str
    data_end: str
    status: str
    total_iterations: int
    current_phase: str
    best_iteration: Optional[int]
    best_sharpe: Optional[float]
    cost_usd: float
    created_at: str
    strategies: List[StrategySummary]   # 所有迭代策略

class StrategySummary(BaseModel):
    id: str
    iteration: int
    train_sharpe: float
    val_sharpe: float
    overfit_score: float
    composite_score: float
    status: str
    deployed: bool

@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def get_session_detail(session_id: str):
    """获取会话详情（含所有策略摘要）"""
```

#### GET /alpha-lab/strategies/{strategy_id} — 策略详情

```python
class StrategyDetail(BaseModel):
    id: str
    session_id: str
    iteration: int
    code: str                           # 完整策略代码
    ai_reasoning: Optional[str]
    train_metrics: Dict[str, Dict]
    val_metrics: Dict[str, Dict]
    overfit_score: float
    composite_score: float
    walk_forward_results: Optional[List[Dict]]
    deployed: bool
    created_at: str

@router.get("/strategies/{strategy_id}", response_model=StrategyDetail)
async def get_strategy_detail(strategy_id: str):
    """获取策略代码和完整回测结果"""
```

#### POST /alpha-lab/strategies/{strategy_id}/deploy — 部署策略

```python
class DeployRequest(BaseModel):
    mode: str = "paper"             # paper（模拟）/ live（实盘，预留）
    symbols: Optional[List[str]] = None  # 指定部署股票，默认用训练时的

@router.post("/strategies/{strategy_id}/deploy")
async def deploy_strategy(strategy_id: str, request: DeployRequest):
    """将策略部署到模拟交易"""
    # 1. 读取策略代码
    # 2. 安全检查
    # 3. 写入 strategies 目录
    # 4. 更新 AutomationConfig
    # 5. 标记为已部署
```

---

## 九、前端交互设计

### 9.1 页面布局

```
┌─────────────────────────────────────────────────────────────────┐
│  Alpha Lab                                        [新建会话]     │
├──────────────┬──────────────────────────────────────────────────┤
│              │                                                  │
│  会话列表     │  ┌─── 目标设定面板 ─────────────────────────┐    │
│              │  │ 股票: [600519.SH] [+添加]                 │    │
│  ● 会话 1    │  │ 优化目标: [夏普比率 ▾]                    │    │
│  ● 会话 2    │  │ 时间范围: [2023-01-01] ~ [2025-12-31]    │    │
│  ○ 会话 3    │  │ 最大迭代: [15]                           │    │
│              │  │                        [🚀 开始探索]      │    │
│              │  └──────────────────────────────────────────┘    │
│              │                                                  │
│              │  ┌─── 迭代进度面板 ─────────────────────────┐    │
│              │  │ Phase 1: 探索期  ████████░░  3/5          │    │
│              │  │                                           │    │
│              │  │ 轮次 │ 模型      │ 训练Sharpe │ 验证Sharpe│    │
│              │  │ ─────┼──────────┼──────────┼──────────── │    │
│              │  │  1   │ 4o-mini  │  0.82    │  0.65       │    │
│              │  │  2   │ 4o-mini  │  1.15    │  0.93  ⭐   │    │
│              │  │  3   │ 4o-mini  │  1.45    │  0.72  ⚠️   │    │
│              │  │  ...正在生成第4轮策略...                    │    │
│              │  └──────────────────────────────────────────┘    │
│              │                                                  │
│              │  ┌─── 结果对比图 ────────────────────────────┐   │
│              │  │                                           │   │
│              │  │  📈 权益曲线对比（验证集）                  │   │
│              │  │  [Iter1] [Iter2⭐] [Iter3]                │   │
│              │  │                                           │   │
│              │  │  (TradingView 风格图表)                    │   │
│              │  │                                           │   │
│              │  └──────────────────────────────────────────┘    │
│              │                                                  │
│              │  ┌─── 策略代码查看器 ───────────────────────┐    │
│              │  │ ▾ 第 2 轮策略 (最佳)                     │    │
│              │  │ ┌──────────────────────────────────────┐ │    │
│              │  │ │ class GeneratedStrategy(BaseStrategy):│ │    │
│              │  │ │     def __init__(self):               │ │    │
│              │  │ │         ...                           │ │    │
│              │  │ └──────────────────────────────────────┘ │    │
│              │  │ 过拟合分数: 0.15 ✅   [📋复制] [🚀部署]  │    │
│              │  └──────────────────────────────────────────┘    │
└──────────────┴──────────────────────────────────────────────────┘
```

### 9.2 前端组件结构

```
frontend/src/
├── pages/
│   └── AlphaLab.tsx                # 主页面
├── components/alpha-lab/
│   ├── SessionList.tsx             # 左侧会话列表
│   ├── TargetForm.tsx              # 目标设定表单
│   ├── IterationProgress.tsx       # 迭代进度表
│   ├── EquityCurveCompare.tsx      # 权益曲线对比图
│   ├── StrategyCodeViewer.tsx      # 策略代码（语法高亮）
│   ├── MetricsCard.tsx             # 指标卡片
│   └── OverfitBadge.tsx            # 过拟合评分徽章
└── services/
    └── alphaLabService.ts          # API 客户端
```

### 9.3 前端服务 (alphaLabService.ts)

```typescript
export interface AlphaLabStreamEvent {
  event: 'session_created' | 'preparing_data' | 'iteration_start' |
         'code_generated' | 'ast_check_passed' | 'backtest_running' |
         'evaluation_done' | 'iteration_complete' | 'phase_change' |
         'early_stop' | 'session_complete' | 'error';
  session_id?: string;
  iteration?: number;
  model?: string;
  phase?: string;
  train_sharpe?: number;
  val_sharpe?: number;
  overfit_score?: number;
  composite_score?: number;
  is_best?: boolean;
  message?: string;
  total_cost_usd?: number;
}

export const alphaLabService = {
  start: async (
    params: AlphaLabStartParams,
    onEvent: (event: AlphaLabStreamEvent) => void,
    signal?: AbortSignal
  ): Promise<void> => {
    const response = await fetch(`${API_BASE}/alpha-lab/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
      signal
    });

    // NDJSON 流式解析（复用 advisorService 的模式）
    const reader = response.body?.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader!.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed) {
          onEvent(JSON.parse(trimmed));
        }
      }
    }
  },

  getSessions: () => fetch(`${API_BASE}/alpha-lab/sessions`).then(r => r.json()),

  getSessionDetail: (id: string) =>
    fetch(`${API_BASE}/alpha-lab/sessions/${id}`).then(r => r.json()),

  getStrategy: (id: string) =>
    fetch(`${API_BASE}/alpha-lab/strategies/${id}`).then(r => r.json()),

  deploy: (id: string, mode: string = 'paper') =>
    fetch(`${API_BASE}/alpha-lab/strategies/${id}/deploy`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode })
    }).then(r => r.json())
};
```

---

## 十、成本估算

### 10.1 单次会话成本

| 阶段 | 模型 | 轮数 | 每轮 Token | 费用/轮 | 小计 |
|------|------|------|-----------|--------|------|
| 探索期 | gpt-4o-mini | 5 | ~3K input + ~1K output | ~$0.001 | ~$0.005 |
| 精炼期 | gpt-4o | 10 | ~5K input + ~1.5K output | ~$0.03 | ~$0.30 |
| **合计** | | **15 轮** | | | **~$0.30** |

> 注：随着对话上下文增长，后期轮次 token 更多。实际估计单次会话 **$0.30 ~ $1.50**。

### 10.2 月度预算

| 使用频率 | 会话/月 | 月费用 |
|---------|--------|-------|
| 低频 | 10 | $3 ~ $15 |
| 中频 | 30 | $9 ~ $45 |
| 高频 | 50 | $15 ~ $75 |

**结论：每月 $10 ~ $50 足够高频使用。**

---

## 十一、分阶段实施里程碑

### Phase 1: 核心引擎（预计 2-3 天）

- [ ] 创建 `backend/alpha_lab/` 目录结构
- [ ] 实现 `sandbox.py`（AST 检查 + subprocess 执行）
- [ ] 实现 `code_generator.py`（OpenAI 调用）
- [ ] 实现 `evaluator.py`（基础评估 + 过拟合检测）
- [ ] 编写 Prompt 模板（system / generate / iterate）
- [ ] 单元测试：沙箱安全性、代码提取、指标计算

### Phase 2: 会话管理 + API（预计 1-2 天）

- [ ] 实现 `session_manager.py`
- [ ] 实现 `strategy_store.py`
- [ ] 实现 `engine.py`（迭代主循环）
- [ ] 新增数据库表（AlphaLabSession, AlphaLabStrategy, AlphaLabLog）
- [ ] 实现 API 路由（start / iterate / sessions / strategies）
- [ ] 流式 NDJSON 响应

### Phase 3: 前端页面（预计 2-3 天）

- [ ] AlphaLab 主页面
- [ ] 目标设定表单
- [ ] 迭代进度面板（实时更新）
- [ ] 权益曲线对比图
- [ ] 策略代码查看器（语法高亮）
- [ ] 部署按钮

### Phase 4: 高级功能（预计 1-2 天）

- [ ] Walk-forward 验证
- [ ] 多股票交叉验证
- [ ] 策略一键部署到模拟交易
- [ ] 成本追踪面板

### Phase 5: 打磨优化

- [ ] 早停策略优化
- [ ] Prompt 调优（基于实际生成效果）
- [ ] 性能优化（并行回测）
- [ ] 异常处理完善

---

## 十二、关键依赖文件索引

| 用途 | 文件路径 |
|-----|---------|
| 策略基类 | `backend/backtest_engine/strategies/base.py` |
| 回测引擎 | `backend/backtest_engine/engine.py` |
| 技术指标 | `backend/strategy/indicators.py` |
| 指标计算器 | `backend/backtest_engine/metrics/calculator.py` |
| 投资组合 | `backend/backtest_engine/portfolio/portfolio.py` |
| 订单模型 | `backend/backtest_engine/portfolio/order.py` |
| 数据引擎 | `backend/data_engine/engine.py` |
| 数据库模型 | `backend/data_engine/storage/models.py` |
| 数据库连接 | `backend/data_engine/storage/database.py` |
| OpenAI 调用参考 | `backend/advisor_engine/service.py` |
| 流式响应参考 | `backend/api/routes/advisor.py` |
| 前端流式解析参考 | `frontend/src/services/advisorService.ts` |
