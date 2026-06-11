[中文文档](README.zh-CN.md)

# Hermes Agent Evaluation Platform

This platform evaluates whether Hermes Agent behaves correctly for a defined set of user requests. It runs YAML-based test cases through the local Hermes CLI, records each session, checks expected Skill and Tool usage, optionally evaluates answer quality and operational limits, and produces repeatable reports for comparing Agent configurations.

Use it to validate Agent routing, Skill selection, Tool calls, tool arguments, failure handling, response quality, latency, token usage, and cost before releasing changes to a Hermes environment.

## 1. Installation and Startup

### 1.1 Requirements

- Python 3.11 or later
- The `uv` Python package manager
- An installed and configured Hermes CLI
- A working Hermes model, Skills, and Tools

Verify Hermes before installing this platform:

```bash
hermes --version
hermes status
```

If Hermes is not available on `PATH`, set its executable path.

Linux:

```bash
export HERMES_BIN=<path-to-hermes>
```

Windows PowerShell:

```powershell
$env:HERMES_BIN="<path-to-hermes>"
```

### 1.2 Linux

Extract the project and enter its root directory:

```bash
cd <project-directory>
```

Install `uv` if necessary:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
```

Install the project dependencies:

```bash
uv sync
```

Start the Web service:

```bash
uv run python -m evalkit.cli serve --host 0.0.0.0 --port 8765
```

Open:

```text
http://<server-address>:8765
```

Run the service in the background:

```bash
nohup uv run python -m evalkit.cli serve --host 0.0.0.0 --port 8765 \
  > evalkit.log 2>&1 &
```

View logs and check the listening port:

```bash
tail -f evalkit.log
ss -lntp | grep 8765
```

Stop the service:

```bash
pkill -f "evalkit.cli serve"
```

The platform does not provide user authentication. Do not expose it directly to the public Internet. To use an SSH tunnel, start the service on `127.0.0.1`:

```bash
uv run python -m evalkit.cli serve --host 127.0.0.1 --port 8765
```

Then create a tunnel from the client:

```bash
ssh -L 8765:127.0.0.1:8765 <user>@<server-address>
```

Open `http://127.0.0.1:8765` locally.

### 1.3 Windows

Install `uv` in PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Open a new PowerShell window, enter the project directory, and install dependencies:

```powershell
cd <project-directory>
uv sync
```

Verify the environment:

```powershell
uv run python --version
hermes --version
```

Start the Web service:

```powershell
uv run python -m evalkit.cli serve --host 127.0.0.1 --port 8765
```

Open:

```text
http://127.0.0.1:8765
```

Always use `uv run python` so that the project virtual environment and dependencies are used.

## 2. Using the Platform

### 2.1 Run an Evaluation in the Web UI

1. Start the service and open the Web UI.
2. Open the **Evaluation** page.
3. Select a dataset from `datasets/`.
4. Select a runner preset.
5. Optionally override the model for this run.
6. Set the concurrency. Start with `1` for real external systems.
7. Click **Run Evaluation**.
8. Monitor progress. When grading finishes, the platform opens the report in **History**.

Evaluation calls real models and Tools. It may incur model cost and access real business resources.

### 2.2 Command-Line Usage

Run and grade a complete dataset:

```bash
uv run python -m evalkit.cli run \
  --dataset datasets/database-top-scene-handler.yaml
```

Set concurrency:

```bash
uv run python -m evalkit.cli run \
  --dataset datasets/database-top-scene-handler.yaml \
  --concurrency 4
```

Override the model:

```bash
uv run python -m evalkit.cli run \
  --dataset datasets/tasks.yaml \
  --model provider/model-name
```

Run without grading:

```bash
uv run python -m evalkit.cli run \
  --dataset datasets/tasks.yaml \
  --grade false
```

Grade an existing run:

```bash
uv run python -m evalkit.cli grade runs/<run-id>
```

Display a report:

```bash
uv run python -m evalkit.cli report runs/<run-id>
```

Display the full trajectory of one case:

```bash
uv run python -m evalkit.cli show \
  --run runs/<run-id> \
  --case <case-id>
```

### 2.3 Result Files

Each run creates a timestamped directory:

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

- `manifest.json`: dataset, model, preset, and runner configuration.
- `raw/`: Hermes output, exported session trajectory, errors, and diagnostics.
- `graded/`: grader results and failure reasons for each case.
- `report.json`: structured aggregate report.
- `report.md`: human-readable report.

When Hermes exits with an error but provides a Session ID, the platform attempts to export that Session and collect session-scoped Hermes logs. The Web trajectory view then shows the run error, diagnostics, and any messages or Tool calls recorded before the failure.

## 3. Writing YAML Test Cases

### 3.1 Dataset Format

Datasets are stored in `datasets/` as `.yaml` or `.yml` files. The top level must be a YAML list:

```yaml
- id: example-case
  type: tool_use
  prompt: "List my database instances."
  graders:
    - kind: tool_call
      must_call: list_database_info
```

Every `id` must be unique within the file.

### 3.2 Case Fields

| Field | Required | Description |
| --- | --- | --- |
| `id` | Yes | Unique case ID and result filename. |
| `prompt` | Yes | User request sent to Hermes. |
| `type` | No | Report grouping, such as `qa`, `tool_use`, `clarification`, or `task`. It does not change execution behavior. |
| `toolsets` | No | Hermes Toolsets enabled for this case, for example `[skills]`. |
| `skills` | No | Skills preloaded before the case runs. |
| `model` | No | Model override for this case. |
| `provider` | No | Provider override for this case. |
| `graders` | Recommended | Assertions used to grade the result. All configured graders must pass. |

Run-level presets and CLI overrides take precedence over case-level model, provider, and Toolset configuration.

### 3.3 Tool and Skill Assertions

Require a Tool call:

```yaml
graders:
  - kind: tool_call
    must_call: list_database_info
    expect_success: true
```

Require a specific Skill:

```yaml
- kind: tool_call
  must_call: skill_view
  args_match:
    name: database-top-scene-handler
  expect_success: true
```

Require multiple Tool calls:

```yaml
graders:
  - kind: tool_call
    must_call: list_aksk_credentials
    expect_success: true
  - kind: tool_call
    must_call: list_database_info
    expect_success: true
```

Available fields:

| Field | Description |
| --- | --- |
| `must_call` | Tool or list of Tools that must be called. |
| `must_call_any` | At least one Tool from the list must be called. |
| `must_not_call` | Tool or list of Tools that must not be called. |
| `args_match` | Required exact subset of parsed Tool arguments. |
| `expect_success` | Checks whether the matching Tool result appears successful. |

The current grader checks whether Tool calls occurred, but it does not validate call order. Tool success detection is heuristic and is based on error markers in the Tool result.

### 3.4 Answer-Quality Grading

Use `llm_judge` to evaluate the final answer:

```yaml
- id: explain-404
  type: qa
  prompt: "Explain what HTTP 404 means."
  graders:
    - kind: llm_judge
      rubric: >
        The answer should explain that 404 means the requested resource
        was not found and should be accurate and concise.
      pass_threshold: 7
```

| Field | Description |
| --- | --- |
| `rubric` | Evaluation criteria. |
| `pass_threshold` | Passing score from 0 to 10. Default: 7. |
| `model` | Optional judge model override. |
| `judge_timeout` | Judge timeout in seconds. Default: 120. |

`llm_judge` makes an additional model request. Regrading cases that use it also makes another judge request.

### 3.5 Timing and Cost Limits

```yaml
- kind: timing
  max_seconds: 120
  max_api_calls: 5
  max_tool_calls: 10
  max_output_tokens: 2000
  max_total_tokens: 8000
  max_cost_usd: 0.05
```

Every configured value is an upper limit. Cost checks are skipped when the Provider does not report cost data.

### 3.6 Create a Custom Dataset

Copy an existing dataset:

Linux:

```bash
cp datasets/tasks.yaml datasets/my-eval.yaml
```

Windows PowerShell:

```powershell
Copy-Item datasets\tasks.yaml datasets\my-eval.yaml
```

You can also create a dataset from the **Datasets** page in the Web UI.

Recommended workflow:

1. Start with one to three cases.
2. Add different user phrasings for the same scenario.
3. Use `tool_call` for Skill and Tool behavior.
4. Use `llm_judge` for answer quality.
5. Add `timing` limits to prevent unexpectedly long runs.
6. Validate the YAML in the Web editor.
7. Use a trial run to inspect real Tool names and arguments.
8. Run the complete dataset.

## 4. Web UI

### 4.1 Evaluation

The **Evaluation** page supports:

- Dataset selection
- Runner preset selection
- One-time model override
- Concurrency configuration
- Live progress
- Run cancellation

Higher concurrency increases load on the model service and business Tools. Start with `1` when testing real systems.

### 4.2 Datasets

The **Datasets** page supports:

- Creating, selecting, editing, saving, and deleting YAML datasets
- Live YAML validation
- Inserting QA, Tool-use, and general-task templates
- Inserting `tool_call`, `timing`, and `llm_judge` graders
- Viewing known Toolset names
- Running one prompt as a trial
- Inspecting the resulting Tool trajectory
- Converting observed Tool calls into YAML assertions

A trial run calls Hermes but does not create a formal run under `runs/`.

### 4.3 Runner Presets and Agent Modes

The **Presets** page stores reusable Hermes runner configurations. Create separate presets for fast tests, strict tests, different models, or isolated Agent environments.

| Field | Description |
| --- | --- |
| `model` | Hermes model. Empty means the Hermes default. |
| `provider` | Model Provider. Empty means the default Provider. |
| `profile` | Hermes Profile used to isolate configuration and Sessions. |
| `toolsets` | Comma-separated run-level Toolset override. Empty uses each case configuration. |
| `max_turns` | Maximum Agent Tool iterations. |
| `timeout` | Timeout for each case. |
| `yolo` | Bypass dangerous-operation approvals. |
| `accept_hooks` | Automatically approve Shell Hooks. |
| `ignore_rules` | Skip rules, Memory, and preloaded Skills for a cleaner evaluation environment. |

Example fast preset:

```text
name: quick
model: empty
provider: empty
profile: empty
toolsets: empty
max_turns: 15
timeout: 180
yolo: enabled
accept_hooks: enabled
ignore_rules: enabled
```

Use `profile` when evaluating a separately configured Agent environment. Use `ignore_rules` carefully: enabling it removes rules, Memory, and other context that may normally affect Agent behavior.

Presets are stored in `runner_presets.json`. A model entered on the Evaluation page overrides the model in the selected preset for that run.

### 4.4 History and Reports

The **History** page shows:

- Model and dataset
- Total, passed, and failed cases
- Pass rate
- Results grouped by `type`
- Grader results for every case
- Duration, Tool calls, API calls, tokens, and cost

Click a case to inspect:

- User input
- Hermes final answer
- Tool calls and arguments
- Tool results
- Session ID
- Grader details
- Run errors and Hermes diagnostics

Click **Regrade** to run graders again against the stored `raw/` trajectory without rerunning the original Agent task. Cases using `llm_judge` still make a new judge-model request.
