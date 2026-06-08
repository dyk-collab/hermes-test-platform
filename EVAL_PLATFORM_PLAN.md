# Hermes Agent 测评平台 — 执行方案

> 基于 `/Users/dyk/opt/project/hermes-agent`（Nous Research Hermes Agent v0.15.1）构建的 CLI 测评平台。
> 工作目录：`/Users/dyk/opt/project/hermes-dev/hermes-test`
> 设计原则：**零侵入** hermes-agent 源码，只通过已安装的 `hermes` CLI 和 session JSON 交互。

---

## 0. 一句话目标

给 Hermes Agent 跑一批设定好的任务，按任务类型判定完成情况，输出 pass 率 / 工具调用正确性 / 耗时 / 成本的报告。

> **模型策略（用户确认）**：默认**不指定模型**，直接用 hermes 当前配置好的默认模型（用户已配好）。即 runner **默认不传 `-m`**。`-m` / 多模型 sweep 仅作为可选能力保留，默认关闭。

---

## 1. 已验证的关键事实（承重点，别再重复踩坑）

### 1.1 非交互调用方式 ✅（已实测，采用 `hermes chat -q -Q`）
**最终方案：`hermes chat -q "PROMPT" -Q`**（`--query` 非交互单次 + `--quiet`）——实测最佳，因为它**同时给出最终回答和 session_id**：
- **stdout** = 纯最终回答文本（无 banner/spinner/工具预览）
- **stderr** = `session_id: <id>` 一行 ← 用正则 `session_id:\s*(\S+)` 抽取，精确对应本次运行
- 实测：`hermes chat -q "1+1等于几？" -Q` → stdout `1+1等于2。`，stderr `session_id: 20260530_160958_1b6706`，exit 0，用默认模型（未传 -m）✅

> 另有顶层 `hermes -z PROMPT`（`--oneshot`）也是 headless，但**只输出回答、不给 session_id**，故不采用。
> **坑**：`hermes chat` 不带 `-q` 是交互 TUI，有 `_require_tty()` 守卫（`hermes_cli/main.py:186`），`stdin` 非终端直接报错；必须带 `-q`。

配套 flag（对 `chat -q` 生效）：
- `-m MODEL` / `--model`：单次模型覆盖（**默认不传，用配好的默认模型**）
- `--provider PROVIDER`：单次 provider 覆盖
- `-t TOOLSETS` / `--toolsets`：逗号分隔启用的 toolset
- `-s SKILLS` / `--skills`：预加载 skill
- `-p NAME` / `--profile`：隔离的 Hermes 实例（独立 HERMES_HOME）
- `--max-turns N`：每轮最大工具迭代（默认 90 或 config）
- `--yolo`：跳过危险命令审批
- `--accept-hooks`：headless 下自动批准 shell hooks
- `--ignore-rules`：跳过 AGENTS.md/SOUL.md/memory/preloaded skills 注入（干净评测环境）
- `--ignore-user-config`：忽略 `~/.hermes/config.yaml`，用内置默认
- `--source SOURCE`：会话来源 tag（默认 cli；第三方集成可用 `tool` 以不进用户会话列表）

### 1.2 Profile 隔离 ✅
- `--profile NAME` 会把 `HERMES_HOME` 指向 profile 目录（`hermes_cli/main.py:216` `_apply_profile_override`）。
- **每个 profile 有独立的 `sessions/` 和 `state.db`**。
- 现有 profile：`~/.hermes/profiles/skill_helper/`。
- **测评策略**：用专属 `eval` profile 隔离运行，避免污染主 session 库，且方便定位本次产生的 session。
  - ⚠️ profile 隔离了 HERMES_HOME → **该 profile 不一定继承用户配好的默认模型/key**。开工时先确认 `hermes -z "..." -p eval` 能出结果；若 eval profile 没配置，可改用主 profile（默认 `~/.hermes`，已配好模型）或给 eval profile 单独配一次。**优先保证"用配好的默认模型"这个诉求。**

### 1.3 读取结果：`hermes sessions export` ✅（已实测，**取代 JSON 快照和 state.db import**）
拿到 session_id 后，用 CLI 导出完整轨迹 + 指标，一行 JSONL：
```bash
hermes sessions export --session-id <id> -      # 输出到 stdout，一行 JSONL
```
- ⚠️ **重要**：`hermes chat -q -Q` 默认**不写** `sessions/session_<id>.json` 快照（实测今天的 run 在 sessions 目录无对应文件，id 只在 `logs/agent.log`）。canonical 存储是 SQLite，**export 是正确的读取入口**——纯 CLI，不用 import 内部 API，不用 mtime 取巧。
- `hermes sessions` 子命令：`list / export / delete / prune / stats / rename / browse`。

### 1.4 Export JSONL Schema ✅（实测）
单行 JSON，顶层字段（即 state.db 的 sessions 行 + 内嵌 messages）：
```jsonc
{
  "id": "20260530_160958_1b6706",
  "source": "cli",
  "model": "MiniMax-M2.7-highspeed",
  "model_config": "{\"max_iterations\":60,\"reasoning_config\":{...},\"max_tokens\":null}", // JSON 字符串
  "system_prompt": "You are Hermes Agent...",
  "started_at": 1780128599.394111,   // unix float
  "ended_at": null,                  // ⚠️ oneshot 不标结束 → 为 null，别用它算耗时
  "end_reason": null,                // ⚠️ 同上，oneshot 常为 null
  "message_count": 2,
  "tool_call_count": 0,
  "api_call_count": 1,
  "input_tokens": 51, "output_tokens": 59,
  "cache_read_tokens": ..., "cache_write_tokens": ..., "reasoning_tokens": ...,
  "estimated_cost_usd": 0.0, "actual_cost_usd": ..., "cost_status": "unknown", // ⚠️ 依 provider，可能 unknown/0
  "title": ...,
  "messages": [ /* 完整轨迹，见下 */ ]
}
```
`messages[]` 每条字段（user/assistant/tool 同构，按需取）：
`id, session_id, role, content, tool_call_id, tool_calls, tool_name, timestamp, token_count, finish_reason, reasoning, reasoning_content, reasoning_details, finish_reason, ...`
- assistant 调工具：`tool_calls`（非空）；工具返回：`role=tool` + `tool_name` + `content` + `tool_call_id`。
- `tool_calls[]` 结构：`{id, call_id, type, function: {name, arguments(JSON 字符串，需二次 parse)}}` ← 工具断言核心。
- 每条 message 有 `timestamp`（unix），可兜底算耗时。

### 1.5 指标来源小结
- **耗时**：在 runner 量 **subprocess wall-clock**（最准，因 `ended_at` 为 null）；兜底用 `messages[-1].timestamp - started_at`。
- **token**：export 的 `input_tokens/output_tokens/reasoning_tokens/cache_*`。
- **成本**：export 的 `estimated_cost_usd` + `cost_status`；⚠️ 部分 provider（如 MiniMax）为 `unknown/0`，报告里需标注"成本不可用"。
- **工具调用**：export 的 `tool_call_count` + 遍历 `messages[].tool_calls`。
- 不再需要 `from hermes_state import SessionDB`（export 已覆盖）；保留为备选。

### 1.6 现成可参考的批量工具（暂不直接复用，但可借鉴）
- `batch_runner.py`：JSONL 多任务并行跑，输出 trajectory + `tool_stats` + `reasoning_stats`，为训练轨迹设计。
- `mini_swe_runner.py`：SWE 类任务 runner。
- `run_agent.py:main()`（fire）：`python run_agent.py --query=... --model=... --max_turns=N`，另一条 headless 路径（无 tty 守卫）。

---

## 2. 架构与数据流

```
评测集 (datasets/*.yaml)
   │
   ▼  Runner: hermes chat -q "prompt" -Q -t <toolsets> --yolo --accept-hooks --ignore-rules
   │          (默认不传 -m，用配好的默认模型；量 wall-clock；从 stderr 抽 session_id)
   ▼
   │  hermes sessions export --session-id <id> -   (导出完整轨迹+指标)
   ▼
原始结果 (runs/<run-id>/raw/<case-id>.json = export JSONL + stdout回答 + wall-clock + 运行元数据)
   │
   ▼  Grader: 工具调用断言 + LLM 判官 + 耗时/指标
   ▼
打分结果 (runs/<run-id>/graded/<case-id>.json)
   │
   ▼  Reporter: 聚合
   ▼
报告 (runs/<run-id>/report.md + report.json，多模型对比表)
```

**核心设计：run 与 grade 分离** —— 跑一次存轨迹，打分可反复调 rubric 不重复花 token。

---

## 3. 目录结构

```
hermes-test/
├── EVAL_PLATFORM_PLAN.md          # 本文档
├── evalkit/
│   ├── __init__.py
│   ├── cli.py                     # fire 入口: run / grade / report / show
│   ├── config.py                  # 路径解析 (hermes 可执行、profile)
│   ├── runner.py                  # 调 hermes chat -q -Q；从 stderr 抽 session_id；量 wall-clock
│   ├── session.py                 # 调 hermes sessions export → 解析 JSONL → 结构化对象
│   ├── graders/
│   │   ├── __init__.py
│   │   ├── base.py                # Grader 接口 + 注册表 (kind -> grader)
│   │   ├── tool_use.py            # 断言: 工具是否被调、参数匹配、tool 结果成败
│   │   ├── llm_judge.py           # LLM 判官 (默认用 hermes -z 当裁判)
│   │   └── timing.py              # 耗时 / turn 数 / 工具调用次数 等阈值
│   └── report.py                  # 聚合 + Rich 表格 + Markdown/JSON 导出
├── datasets/
│   └── tasks.yaml                 # 评测用例 (起步几条，按任务类型分)
└── runs/                          # 每次评测产物 (git 可忽略)
    └── <run-id>/
        ├── manifest.json          # 本次 run 的配置 (model, dataset, 时间)
        ├── raw/<case-id>.json     # 复制的 session JSON + 运行元数据
        ├── graded/<case-id>.json  # 每条打分明细
        ├── report.json
        └── report.md
```

---

## 4. 评测用例格式（datasets/tasks.yaml）

按**任务类型**组织，先几条，后续增量扩展：

```yaml
- id: skill-view-basic
  type: tool_use
  prompt: "查看 xxx skill 的内容"
  toolsets: [skill]                 # 映射到 hermes -t
  graders:
    - kind: tool_call               # 工具调用断言
      must_call: skill_view         # 必须调用的工具名 (匹配 tool_calls[].function.name)
      args_match: { name: "xxx" }   # arguments(parse 后) 子集匹配
      expect_success: true          # 对应 tool 消息结果非报错
    - kind: timing
      max_seconds: 30

- id: research-summary
  type: qa
  prompt: "总结 X 的现状"
  toolsets: [research]
  graders:
    - kind: llm_judge
      rubric: "答案是否准确、是否引用来源、是否抓住要点"
      pass_threshold: 7             # 判官打分 (0-10) ≥ 阈值算 pass

- id: multi-step-task
  type: task
  prompt: "..."
  graders:
    - kind: tool_call
      must_call_any: [terminal, write_file]
    - kind: llm_judge
      rubric: "任务是否真正完成"
```

**字段说明**
- `id`：唯一，决定产物文件名。
- `type`：任务类型，仅用于报告分组。
- `prompt`：喂给 `hermes -z` 的内容。
- `toolsets`：可选，映射 `-t`。
- `model` / `provider`：可选，case 级覆盖（一般在 run 命令统一指定）。
- `graders[]`：一条用例可挂多个 grader，全 pass 才算整体 pass（可后续改加权）。

---

## 5. 三类 Grader（对应三个打分维度）

数据均来自 export JSONL（§1.4），最终回答也可直接用 runner 捕获的 stdout。

| kind | 判定依据 | 数据来源 |
|------|----------|----------|
| `tool_call` | 是否调了预期工具 / 参数子集匹配 / 工具结果是否成功 | export `messages[].tool_calls` + 对应 `role=tool` 消息 |
| `llm_judge` | 最终回答 + rubric → 判官模型打分 | runner stdout（或 export 最后一条 assistant `content`）+ rubric |
| `timing` | wall-clock 耗时 / turn 数 / api_calls / token 阈值 | runner wall-clock + export `tool_call_count`/`api_call_count`/`*_tokens` |

**tool_call 判定细节**
- 遍历所有 assistant 消息的 `tool_calls`，收集 `(name, parsed_arguments)` 序列。
- `must_call` / `must_call_any` / `must_not_call`。
- `args_match`：parse `function.arguments`(JSON 字符串) 后做子集匹配。
- `expect_success`：用 `tool_call_id` 找到对应 `role=tool` 消息，判断 `content` 是否为报错（启发式：含 `Error`/`Traceback`/`failed` 等，或后续可让 hermes 自己标记）。

**llm_judge 实现（省事路线）**
- 直接再起一个 `hermes -z "<judge prompt>"`（或指定一个便宜的裁判模型 `-m`）作裁判，要求输出固定 JSON：`{"score": 0-10, "pass": bool, "reason": "..."}`。
- 用 `--ignore-rules` 让裁判环境干净。
- 解析裁判 stdout（注意 `-z` 只输出最终文本，提示裁判**只输出 JSON**）。

**timing 实现**
- 耗时：runner 量的 wall-clock（首选）；兜底 `messages[-1].timestamp - started_at`。
- turn 数 / 工具次数 / token / 成本：直接取 export 的 `api_call_count` / `tool_call_count` / `*_tokens` / `estimated_cost_usd`。

---

## 6. 平台 CLI（fire，与 hermes 风格一致）

```bash
# 跑评测（默认用 hermes 当前配置好的默认模型，不传 -m）
python -m evalkit.cli run --dataset datasets/tasks.yaml --out runs/

# 可选：临时覆盖模型 / 多模型 sweep（默认不用）
python -m evalkit.cli run --dataset datasets/tasks.yaml --model anthropic/claude-sonnet-4.6 --out runs/
python -m evalkit.cli run --dataset datasets/tasks.yaml --models m1,m2 --out runs/

# 单独重跑打分（轨迹已存，不重复花钱）
python -m evalkit.cli grade runs/<run-id>

# 聚合报告（pass率 / 工具正确率 / 耗时 / 成本对比表）
python -m evalkit.cli report runs/<run-id>

# 回放单条完整轨迹
python -m evalkit.cli show --session <id>           # 或 --case runs/<run-id>/raw/<case-id>.json
```

Runner 每条用例执行（伪代码）：
```python
cmd = [HERMES, "chat", "-q", case.prompt, "-Q",
       "--yolo", "--accept-hooks", "--ignore-rules"]
if model:                              # 默认 None → 不传 -m，用配好的默认模型
    cmd += ["-m", model]
if case.toolsets:
    cmd += ["-t", ",".join(case.toolsets)]
# 可选 -p eval 隔离（见 §8）；--source tool 可让会话不进用户列表

t0 = time.monotonic()
p = subprocess.run(cmd, capture_output=True, text=True, timeout=...)
wall_clock = time.monotonic() - t0
answer = p.stdout.strip()                              # 最终回答
sid = re.search(r"session_id:\s*(\S+)", p.stderr).group(1)  # 从 stderr 抽 id

export = subprocess.run([HERMES, "sessions", "export", "--session-id", sid, "-"],
                        capture_output=True, text=True)
session = json.loads(export.stdout.splitlines()[0])    # 一行 JSONL
save(runs/<run-id>/raw/<case-id>.json,
     {"case_id": case.id, "answer": answer, "session_id": sid,
      "wall_clock": wall_clock, "session": session, "stderr": p.stderr})
```

---

## 7. 实施顺序（里程碑）

1. **最小闭环**：`config.py` + `runner.py` + `session.py`
   → 跑一个 `hermes chat -q -Q` 任务 → 从 stderr 抽 session_id → `sessions export` 拿轨迹 → 解析出工具调用序列。**链路已手动验证通（§9），先代码化。**
2. **核心 grader**：`graders/tool_use.py` + `graders/timing.py`（你最关心的工具调用判定）。
3. **CLI 骨架**：`cli.py` 的 `run` / `show` —— 能跑能看。
4. **判官 + 报告**：`graders/llm_judge.py` + `report.py`（Rich 表格 + Markdown 导出）。
5. **成本/token 指标 +（可选）多模型 sweep**：export 已带 token/cost，聚合进报告（标注 cost 可能 unknown）；`run --models` 仅在需要横向对比时启用（默认不用）。

---

## 8. 待定 / 风险点

- **session 对应可靠性**：已解决——`chat -q -Q` 的 stderr 直接打 `session_id: <id>`，精确对应，无需 mtime/profile 取巧。并行也安全（每条各自拿自己的 id）。
- **profile 隔离 vs 默认模型**：默认**不加 `-p`**，直接用主 `~/.hermes`（已配好默认模型/key），会话会进用户列表——可加 `--source tool` 让其不进主列表，或用 `-p eval` 隔离（但需确认 eval profile 也配了模型/key）。MVP 先用主 profile + `--source tool`。
- **tool 成功/失败判定**：靠 `role=tool` 消息 `content` 启发式判断（含 Error/Traceback/failed 等）；可能不准，后续可优化。
- **耗时**：`ended_at` 为 null，用 runner 量 wall-clock（含进程启动开销，约略偏大但稳定）；要纯推理耗时用 `messages[-1].timestamp - started_at`。
- **成本**：依 provider，可能 `unknown/0`（MiniMax 实测如此），报告需标注。
- **判官成本**：LLM 判官额外耗 token；run/grade 分离已缓解（grade 可单独重跑/换裁判）。
- **export 性能**：每条用例额外起一次 `hermes sessions export` 子进程（~秒级），用例多时可考虑跑完批量 export。

---

## 9. 链路状态：已手动打通 ✅

```bash
# 实测通过（用配好的默认模型，未传 -m）：
hermes chat -q "用一句话回答：1+1 等于几？" -Q
#   stdout -> "1+1等于2。"
#   stderr -> "session_id: 20260530_160958_1b6706"
hermes sessions export --session-id 20260530_160958_1b6706 -
#   -> 一行 JSONL，含 messages + tokens + cost + started_at + tool_call_count

# 下次开工：直接从里程碑 1 把上面两步代码化（evalkit/runner.py + session.py）
```
