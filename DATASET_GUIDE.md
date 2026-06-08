# 数据集构建说明

本文说明当前 `evalkit` 的数据集如何编写、如何被执行、以及如何配置 grader。

## 1. 数据集放在哪里

数据集文件放在 `datasets/` 目录下，文件名使用 `.yaml` 或 `.yml`。

当前示例：

- `datasets/smoke.yaml`：最小冒烟数据集。
- `datasets/test.yaml`：Web 新建数据集的模板风格。
- `datasets/tasks.yaml`：更完整的示例集。

Web 控制台和 CLI 都以这些 YAML 文件作为数据集来源。

## 2. 基本结构

一个数据集是一个 YAML list。每一项是一条 case。

最小例子：

```yaml
- id: qa-arithmetic
  type: qa
  prompt: "用一句话回答：1+1 等于几？"
  graders:
    - kind: timing
      max_seconds: 60
    - kind: llm_judge
      rubric: "回答是否正确（结果为 2）且简洁（一句话）。"
      pass_threshold: 7
```

顶层必须是 list，不能是 map。也就是说文件应该以 `- id: ...` 这种列表项开始。

## 3. Case 字段

每条 case 支持以下字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 是 | 用例唯一 ID，也会决定产物文件名，例如 `raw/<id>.json`。 |
| `prompt` | 是 | 传给 `hermes chat -q` 的任务内容。 |
| `type` | 否 | 报告分组字段，默认是 `task`。常见值：`qa`、`tool_use`、`task`。 |
| `toolsets` | 否 | case 级 toolset，映射到 Hermes CLI 的 `-t`。 |
| `skills` | 否 | case 级 skills，映射到 Hermes CLI 的 `-s`。 |
| `model` | 否 | case 级模型覆盖。通常建议在运行时统一指定，而不是写在数据集里。 |
| `provider` | 否 | case 级 provider 覆盖。 |
| `graders` | 否 | grader 配置列表。一条 case 可挂多个 grader。 |

校验规则：

- `id` 和 `prompt` 必须存在。
- 同一个数据集里的 `id` 必须唯一。
- YAML 顶层必须是 list。

字段解析逻辑见 `evalkit/dataset.py`。

## 4. 执行流程

运行数据集时，`evalkit` 会逐条执行 case：

```bash
hermes chat -q "<prompt>" -Q --yolo --accept-hooks --ignore-rules
```

然后从 Hermes stderr 里提取 `session_id`，再执行：

```bash
hermes sessions export --session-id <session_id> -
```

导出的 session 会被保存成 raw 轨迹，grader 再基于 raw 轨迹和最终回答进行评分。

产物大致如下：

```text
runs/<run-id>/
├── manifest.json
├── raw/<case-id>.json
├── graded/<case-id>.json
├── report.json
└── report.md
```

## 5. Grader 总览

当前注册了三类 grader：

| kind | 用途 | 是否会再次调用模型 |
| --- | --- | --- |
| `timing` | 检查耗时、API 次数、工具调用次数、token、成本等阈值。 | 否 |
| `tool_call` | 检查是否调用了指定工具、参数是否匹配、工具结果是否成功。 | 否 |
| `llm_judge` | 用一个模型作为裁判，根据 rubric 给最终回答打分。 | 是 |

一条 case 的所有 grader 都通过，case 才算通过。没有 grader 的 case 会在报告里没有可用评分，不建议这样写。

## 6. timing grader

`timing` 用来约束运行指标。所有字段都是可选阈值，但至少应该配置一个。

可用字段：

```yaml
- kind: timing
  max_seconds: 60
  max_api_calls: 2
  max_tool_calls: 5
  max_output_tokens: 1000
  max_total_tokens: 4000
  max_cost_usd: 0.01
```

含义：

- `max_seconds`：子进程 wall-clock 时间上限。
- `max_api_calls`：session 里的 API 调用次数上限。
- `max_tool_calls`：工具调用次数上限。
- `max_output_tokens`：输出 token 上限。
- `max_total_tokens`：输入、输出、reasoning token 总和上限。
- `max_cost_usd`：估算成本上限。若 provider 没有成本数据，会跳过成本检查。

适合给所有 case 加一个基本耗时保护，例如：

```yaml
graders:
  - kind: timing
    max_seconds: 90
```

## 7. llm_judge grader

`llm_judge` 用模型裁判最终回答。它只看：

- 原始任务 `prompt`
- 被测 agent 的最终回答
- 数据集里写的 `rubric`

它看不到中间过程、工具调用轨迹和工具结果。如果要判断过程，应该配合 `tool_call`。

配置例子：

```yaml
- kind: llm_judge
  rubric: >
    准确性：是否说明 404 表示资源未找到；
    完整性：是否说明这通常不是服务器崩溃；
    清晰度：表达是否简洁易懂。
  pass_threshold: 7
  model: "some-cheap-judge-model"
  judge_timeout: 120
```

字段：

- `rubric`：必填，评分标准。
- `pass_threshold`：通过阈值，默认 `7`。judge 打分是 0 到 10，分数大于等于阈值即通过。
- `model`：可选，裁判模型覆盖。不写则使用 Hermes 当前默认模型。
- `judge_timeout`：可选，裁判调用超时时间，默认 `120` 秒。

当前 judge prompt 模板大致是：

```text
You are an impartial grader. Score how well the ANSWER satisfies the TASK against the RUBRIC.

TASK:
{task}

ANSWER:
{answer}

RUBRIC:
{rubric}

Respond with ONLY a single JSON object, no prose, no code fences:
{"score": <integer 0-10>, "pass": <true|false>, "reason": "<one short sentence>"}
The "pass" field must reflect whether score >= {threshold}.
```

注意：每次 `llm_judge` 都会再次调用一次模型。重新 grade 同一个 run 时，也会重新产生 judge 调用。

## 8. tool_call grader

`tool_call` 用来检查 agent 是否按预期使用工具。它基于导出的 session 轨迹，不会再次调用模型。

常见配置：

```yaml
- kind: tool_call
  must_call: skill_view
  args_match:
    name: "example"
  expect_success: true
```

可用字段：

| 字段 | 说明 |
| --- | --- |
| `must_call` | 必须调用的工具名。可以是字符串或列表。 |
| `must_call_any` | 给一组工具名，至少调用其中一个。 |
| `must_not_call` | 禁止调用的工具名。可以是字符串或列表。 |
| `args_match` | 参数子集匹配。只要某次候选工具调用的参数包含这些键值，就算匹配。 |
| `expect_success` | 检查匹配工具调用的结果是否成功。当前用错误关键词做启发式判断。 |

例子：必须调用 `skill_view`，且工具结果不能明显报错：

```yaml
- id: skill-view-self
  type: tool_use
  prompt: "先列出可用的 skill，然后挑其中一个查看它的完整内容。"
  toolsets: [skills]
  graders:
    - kind: tool_call
      must_call: skill_view
      expect_success: true
    - kind: timing
      max_seconds: 120
      max_tool_calls: 10
```

例子：允许多个可接受工具，只要调用其中一个：

```yaml
- kind: tool_call
  must_call_any: [skills_list, skill_view]
  expect_success: true
```

例子：确保没有调用某个工具：

```yaml
- kind: tool_call
  must_not_call: dangerous_tool
```

## 9. 推荐写法

### QA 类任务

适合用 `llm_judge + timing`：

```yaml
- id: qa-explain-concept
  type: qa
  prompt: "用两三句话解释什么是 HTTP 状态码 404，以及它通常意味着什么。"
  graders:
    - kind: llm_judge
      rubric: >
        准确性：是否说明 404 表示 Not Found；
        完整性：是否说明请求资源不存在；
        清晰度：是否简洁易懂。
      pass_threshold: 7
    - kind: timing
      max_seconds: 90
```

### 工具使用类任务

适合用 `tool_call + timing`，必要时再加 `llm_judge` 看最终回答质量：

```yaml
- id: skills-list-basic
  type: tool_use
  prompt: "列出你当前可用的所有 skill。"
  toolsets: [skills]
  graders:
    - kind: tool_call
      must_call_any: [skills_list, skill_view]
      expect_success: true
    - kind: timing
      max_seconds: 90
```

### 复杂任务

复杂任务通常建议同时检查结果和过程：

```yaml
- id: research-answer
  type: task
  prompt: "查找并总结某个主题的最新信息，回答时给出关键依据。"
  toolsets: [web]
  graders:
    - kind: tool_call
      must_call_any: [web_search, web_open]
      expect_success: true
    - kind: llm_judge
      rubric: >
        是否完成检索；
        是否回答了问题；
        是否给出关键依据；
        是否避免无依据断言。
      pass_threshold: 7
    - kind: timing
      max_seconds: 180
      max_tool_calls: 15
```

## 10. 常见注意事项

- `toolsets: [skills]` 里的 `skills` 是 toolset 名，不是 `skill`。
- `llm_judge` 只评最终回答，不评过程。
- `tool_call` 只检查工具调用和工具结果，不理解最终答案质量。
- `timing` 没有配置任何阈值会失败，因为它没有可检查内容。
- `args_match` 是精确键值匹配，不做模糊语义匹配。
- `expect_success` 当前是基于工具返回文本里的错误关键词做启发式判断。
- 如果 run 级别传了 `toolsets`，会覆盖 case 自己的 `toolsets`。
- 如果 run 级别传了 `model/provider`，会优先于 case 自己的 `model/provider`。

## 11. 新增数据集建议流程

1. 先从 1 到 3 条 case 开始，不要一次写很大。
2. 每条 case 先加 `timing`，避免意外长跑。
3. QA 类加 `llm_judge`，rubric 写清楚“什么算对”。
4. 工具类加 `tool_call`，明确必须调用或禁止调用的工具。
5. 用 Web 控制台或 `/api/datasets/validate` 校验 YAML。
6. 跑 `smoke.yaml` 确认链路通，再跑新数据集。
7. 看 `runs/<run-id>/raw/` 和 `graded/`，根据真实轨迹调整 grader。

