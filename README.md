# Hermes Agent 测评平台

这是一个基于 Hermes CLI 的自动化测评平台。平台读取 `datasets/*.yaml` 中的测试用例，调用 Hermes Agent 执行任务，导出完整 Session 轨迹，并根据配置检查：

- 是否调用了指定 Skill 或 Tool
- Tool 参数和执行结果是否符合要求
- 最终回答质量是否达标
- 耗时、Token、API 调用次数和成本是否超过限制

平台提供 Web 控制台和命令行两种使用方式。它不是 Hermes Agent 本体，运行前必须先安装并配置好 Hermes。

## 1. 安装与启动

### 1.1 环境要求

- Python 3.11 或更高版本
- `uv` Python 包管理工具
- 已安装并配置好的 Hermes CLI
- 可以通过 `hermes --version` 找到 Hermes
- Hermes 已配置可用的模型、Skill 和 Tool

先检查 Hermes：

```bash
hermes --version
hermes status
```

如果 `hermes` 不在 `PATH` 中，可以设置其绝对路径：

```bash
export HERMES_BIN=<Hermes可执行文件路径>
```

Windows PowerShell：

```powershell
$env:HERMES_BIN="<Hermes可执行文件路径>"
```

### 1.2 Linux 安装

解压项目后进入项目根目录：

```bash
cd <项目目录>
```

如果没有安装 `uv`：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
```

安装项目依赖：

```bash
uv sync
```

启动 Web 服务：

```bash
uv run python -m evalkit.cli serve --host 0.0.0.0 --port 8765
```

浏览器访问：

```text
http://<服务器地址>:8765
```

后台启动：

```bash
nohup uv run python -m evalkit.cli serve --host 0.0.0.0 --port 8765 \
  > evalkit.log 2>&1 &
```

查看日志和监听端口：

```bash
tail -f evalkit.log
ss -lntp | grep 8765
```

停止服务：

```bash
pkill -f "evalkit.cli serve"
```

如果服务暴露到其他机器，请配置防火墙或云安全组。当前平台没有用户登录功能，不建议直接暴露到公网。可以让服务只监听本机：

```bash
uv run python -m evalkit.cli serve --host 127.0.0.1 --port 8765
```

然后从客户端建立 SSH 隧道：

```bash
ssh -L 8765:127.0.0.1:8765 <用户>@<服务器地址>
```

浏览器仍访问 `http://127.0.0.1:8765`。

### 1.3 Windows 安装

在 PowerShell 中安装 `uv`：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

重新打开 PowerShell，然后进入项目目录：

```powershell
cd <项目目录>
uv sync
```

确认项目环境和 Hermes：

```powershell
uv run python --version
hermes --version
```

启动 Web 服务：

```powershell
uv run python -m evalkit.cli serve --host 127.0.0.1 --port 8765
```

浏览器访问：

```text
http://127.0.0.1:8765
```

不要直接使用系统 Python 启动：

```powershell
python -m evalkit.cli serve
```

如果出现 `No module named 'fire'`，说明使用了错误的 Python 环境。请使用 `uv run python ...`，或者先激活虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
python -m evalkit.cli serve --port 8765
```

## 2. 使用平台

### 2.1 Web 页面运行评测

1. 启动服务并打开 `http://127.0.0.1:8765`。
2. 进入“评测”页面。
3. 选择 `datasets/` 下的数据集。
4. 选择运行预设。
5. 按需填写本次模型覆盖。
6. 设置并发数。第一次运行建议使用 `1`。
7. 点击“运行评测”。
8. 等待运行和评分完成，页面会自动跳转到“历史”报告。

运行过程中可以查看每条用例的状态，也可以点击“停止”请求取消尚未执行的任务。

注意：评测会真实调用模型和 Tool，可能产生模型费用并访问实际业务资源。

### 2.2 命令行运行

运行整个数据集并自动评分：

```bash
uv run python -m evalkit.cli run \
  --dataset datasets/database-top-scene-handler.yaml
```

指定并发数：

```bash
uv run python -m evalkit.cli run \
  --dataset datasets/database-top-scene-handler.yaml \
  --concurrency 4
```

指定模型：

```bash
uv run python -m evalkit.cli run \
  --dataset datasets/tasks.yaml \
  --model provider/model-name
```

只运行并保存轨迹，不立即评分：

```bash
uv run python -m evalkit.cli run \
  --dataset datasets/tasks.yaml \
  --grade false
```

重新评分：

```bash
uv run python -m evalkit.cli grade runs/<run-id>
```

查看报告：

```bash
uv run python -m evalkit.cli report runs/<run-id>
```

查看某条用例的完整轨迹：

```bash
uv run python -m evalkit.cli show \
  --run runs/<run-id> \
  --case <case-id>
```

### 2.3 结果目录

每次运行都会在 `runs/` 下生成一个时间戳目录：

```text
runs/<run-id>/
├── manifest.json
├── raw/
│   └── <case-id>.json
├── graded/
│   └── <case-id>.json
├── report.json
└── report.md
```

- `manifest.json`：数据集、模型、预设和 runner 参数。
- `raw/`：Hermes 原始回答、Session 轨迹和运行信息。
- `graded/`：每条用例的评分结果及失败原因。
- `report.json`：结构化汇总报告。
- `report.md`：便于人工阅读的 Markdown 报告。

## 3. 编写 YAML 测试用例

### 3.1 文件位置和基本格式

数据集放在 `datasets/` 下，扩展名为 `.yaml` 或 `.yml`。文件顶层必须是列表：

```yaml
- id: example-case
  type: tool_use
  prompt: "列出我的数据库实例"
  graders:
    - kind: tool_call
      must_call: list_database_info
```

同一个文件中的 `id` 必须唯一。

### 3.2 Case 字段

| 字段 | 必填 | 含义 |
| --- | --- | --- |
| `id` | 是 | 用例唯一 ID，也用于生成结果文件名。 |
| `prompt` | 是 | 发送给 Hermes 的用户问题。 |
| `type` | 否 | 报告分组名称，例如 `qa`、`tool_use`、`clarification`、`task`。不影响执行逻辑。 |
| `toolsets` | 否 | 本用例启用的 Hermes Toolset，例如 `[skills]`。 |
| `skills` | 否 | 运行前预加载的 Skill 列表。 |
| `model` | 否 | 本用例使用的模型覆盖。 |
| `provider` | 否 | 本用例使用的 Provider 覆盖。 |
| `graders` | 建议 | 评分规则列表。所有 grader 都通过，用例才通过。 |

运行预设或命令行指定的模型、Provider、Toolset 会优先于用例配置。

### 3.3 Tool 调用评分

检查必须调用某个 Tool：

```yaml
graders:
  - kind: tool_call
    must_call: list_database_info
    expect_success: true
```

检查加载了指定 Skill：

```yaml
- kind: tool_call
  must_call: skill_view
  args_match:
    name: database-top-scene-handler
  expect_success: true
```

检查多个 Tool 都被调用：

```yaml
graders:
  - kind: tool_call
    must_call: list_aksk_credentials
    expect_success: true
  - kind: tool_call
    must_call: list_database_info
    expect_success: true
```

其他字段：

| 字段 | 含义 |
| --- | --- |
| `must_call` | 必须调用的 Tool，可以是字符串或列表。 |
| `must_call_any` | 给定多个 Tool，至少调用其中一个。 |
| `must_not_call` | 不允许调用的 Tool。 |
| `args_match` | Tool 参数子集必须精确匹配。 |
| `expect_success` | `true` 表示 Tool 结果中不能出现明显错误。 |

当前 grader 检查 Tool 是否出现，不检查多个 Tool 的调用顺序。`expect_success` 根据 Tool 返回文本中的错误关键词进行启发式判断。

### 3.4 最终回答评分

使用 `llm_judge` 让另一个模型根据规则评价最终回答：

```yaml
- id: explain-404
  type: qa
  prompt: "解释 HTTP 404 的含义。"
  graders:
    - kind: llm_judge
      rubric: >
        是否说明 404 表示资源未找到；
        表达是否准确、清晰、简洁。
      pass_threshold: 7
```

| 字段 | 含义 |
| --- | --- |
| `rubric` | 评分标准。 |
| `pass_threshold` | 0 到 10 的通过阈值，默认 7。 |
| `model` | 可选的裁判模型。 |
| `judge_timeout` | 裁判调用超时时间，默认 120 秒。 |

`llm_judge` 会额外调用一次模型，因此会增加耗时和费用。重新评分也会重新调用裁判模型。

### 3.5 性能和成本评分

```yaml
- kind: timing
  max_seconds: 120
  max_api_calls: 5
  max_tool_calls: 10
  max_output_tokens: 2000
  max_total_tokens: 8000
  max_cost_usd: 0.05
```

所有字段都是上限。Provider 未提供成本数据时，成本检查会跳过。

### 3.6 创建自己的数据集

可以直接复制现有文件：

```bash
cp datasets/tasks.yaml datasets/my-eval.yaml
```

Windows PowerShell：

```powershell
Copy-Item datasets\tasks.yaml datasets\my-eval.yaml
```

也可以在 Web 页面进入“数据集”，点击“＋”新建文件。建议流程：

1. 先编写 1 至 3 条用例。
2. 使用不同用户表达覆盖同一场景。
3. Tool 测试使用 `tool_call`。
4. 输出质量测试使用 `llm_judge`。
5. 增加 `timing` 防止异常长时间运行。
6. 在 Web 编辑器中确认 YAML 校验通过。
7. 先“试跑”一条输入，核对真实 Tool 名和参数。
8. 再批量运行完整数据集。

## 4. Web 页面功能

### 4.1 评测

“评测”页面用于发起批量运行：

- 选择数据集
- 选择运行预设
- 临时覆盖模型
- 设置并发数
- 查看实时进度
- 请求停止运行

并发数越高，对模型服务和业务 Tool 的压力越大。涉及真实数据库或外部系统时建议从 `1` 开始。

### 4.2 数据集

“数据集”页面支持：

- 新建、选择、编辑、保存和删除 YAML 数据集
- 实时校验 YAML 结构
- 插入 QA、Tool 调用和综合任务模板
- 插入 `tool_call`、`timing`、`llm_judge` grader
- 查看可用 Toolset 名称
- 输入单条 Prompt 进行试跑
- 查看试跑产生的完整 Tool 调用轨迹
- 将观察到的 Tool 调用转换成 YAML 断言

试跑会真实调用 Hermes，但不会写入正式的 `runs/` 报告目录。

### 4.3 运行预设和 Agent 模式

“预设”页面用于保存一组可重复使用的 Hermes runner 参数。可以为快速测试、严格测试或不同 Agent 环境分别建立预设。

可配置字段：

| 字段 | 含义 |
| --- | --- |
| `model` | Hermes 模型；留空使用 Hermes 默认模型。 |
| `provider` | 模型 Provider；留空使用默认配置。 |
| `profile` | Hermes Profile，用于隔离配置和 Session。 |
| `toolsets` | 覆盖所有用例的 Toolset，逗号分隔。留空使用用例自己的配置。 |
| `max_turns` | Agent 最大工具迭代轮数。 |
| `timeout` | 每条用例的超时时间。 |
| `yolo` | 跳过危险操作审批。 |
| `accept_hooks` | 自动批准 Shell Hook。 |
| `ignore_rules` | 不注入规则、Memory 和预加载 Skill，形成更干净的评测环境。 |

示例“快速模式”：

```text
名称: quick
model: 留空
provider: 留空
profile: 留空
toolsets: 留空
max_turns: 15
timeout: 180
yolo: 开
accept_hooks: 开
ignore_rules: 开
```

如果需要测试一个预先配置好的 Agent，可以填写对应 `profile`。如果希望测试 Hermes 的真实用户环境，应谨慎使用 `ignore_rules`；开启后会跳过规则、Memory 等上下文。

预设保存在根目录的 `runner_presets.json`。评测页面选择预设后，单次填写的模型会覆盖预设模型。

### 4.4 历史和测试结果

“历史”页面列出所有 `runs/<run-id>`：

- 模型和数据集
- 总用例数
- 通过数和通过率
- 按 `type` 分组的通过情况
- 每条用例的 grader 结果
- 耗时、Tool 次数、API 次数、Token 和成本

点击某条用例可以打开轨迹抽屉，查看：

- 用户输入
- Hermes 最终回答
- 每次 Tool 调用及参数
- Tool 返回结果
- Session ID
- grader 配置；评分结果可在报告表格中查看
- 运行错误和指标

点击“重新打分”会使用已经保存的 `raw/` 轨迹再次运行 grader，不会重新执行原始 Agent 任务。但包含 `llm_judge` 时，重新打分仍会额外调用裁判模型。
