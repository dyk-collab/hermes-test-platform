"""Core run/grade orchestration, shared by the CLI and the web backend.

The single implementation lives here; both front-ends drive it and consume the
same ``on_event`` progress stream. Events are plain dicts (JSON-friendly):

  {"type": "run_start",  "run_id", "run_dir", "total"}
  {"type": "case_start", "i", "total", "case_id"}
  {"type": "case_done",  "i", "total", "case_id", "ok", "wall_clock",
                          "session_id", "error", "diagnostics", "stderr"}
  {"type": "grade_start", "total"}
  {"type": "case_graded", "case_id", "passed"}
  {"type": "graded",      "report"}        # final, carries the full report dict
  {"type": "error",       "message"}
"""

from __future__ import annotations

import json
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Any, Callable, Optional

from .config import DATASETS_DIR, RUNS_DIR
from .dataset import load_dataset, load_dataset_text
from .graders import grade_case
from .persist import load_raw, metrics, save_raw
from .presets import (  # noqa: F401 - re-exported for the web layer
    delete_preset,
    get_preset,
    list_presets,
    save_preset,
)
from .report import build_report, render_markdown
from .runner import run_prompt

OnEvent = Optional[Callable[[dict[str, Any]], None]]


def _emit(on_event: OnEvent, **event: Any) -> None:
    if on_event is not None:
        on_event(event)


# ---- run -------------------------------------------------------------------


def execute_run(
    dataset: str,
    *,
    out: Optional[str] = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    profile: Optional[str] = None,
    timeout: float = 600.0,
    max_turns: Optional[int] = None,
    toolsets: Optional[list[str]] = None,  # run-level override of each case's toolsets
    yolo: bool = True,
    accept_hooks: bool = True,
    ignore_rules: bool = True,
    preset: Optional[str] = None,  # preset name, for labeling/manifest only
    grade: bool = True,
    concurrency: int = 1,
    cancel_event: Optional[Event] = None,
    on_event: OnEvent = None,
) -> str:
    """Run every case, store trajectories, optionally grade. Returns the run dir.

    The runner knobs (model/provider/flags/...) come from the selected preset,
    resolved by the caller. ``toolsets`` here overrides each case's own toolsets
    for the whole run when set; otherwise the case's toolsets are used.
    """
    cases = load_dataset(dataset)
    concurrency = max(1, int(concurrency or 1))
    runs_root = Path(out) if out else RUNS_DIR
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    runner_cfg = {
        "preset": preset,
        "model": model,
        "provider": provider,
        "profile": profile,
        "timeout": timeout,
        "max_turns": max_turns,
        "toolsets": toolsets,
        "yolo": yolo,
        "accept_hooks": accept_hooks,
        "ignore_rules": ignore_rules,
        "concurrency": concurrency,
    }
    manifest = {
        "run_id": run_id,
        "dataset": str(dataset),
        "model": model,
        "profile": profile,
        "preset": preset,
        "runner": runner_cfg,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "case_count": len(cases),
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

    _emit(on_event, type="run_start", run_id=run_id, run_dir=str(run_dir), total=len(cases))
    cancelled = False

    def run_case(i: int, case) -> None:
        _emit(on_event, type="case_start", i=i, total=len(cases), case_id=case.id)
        run = run_prompt(
            case.prompt,
            case_id=case.id,
            model=model or case.model,
            provider=provider or case.provider,
            toolsets=toolsets or case.toolsets,
            skills=case.skills,
            profile=profile,
            max_turns=max_turns,
            timeout=timeout,
            yolo=yolo,
            accept_hooks=accept_hooks,
            ignore_rules=ignore_rules,
            cancel_event=cancel_event,
        )
        save_raw(run_dir, case, run)
        _emit(
            on_event, type="case_done", i=i, total=len(cases), case_id=case.id,
            ok=run.ok, wall_clock=run.wall_clock, session_id=run.session_id, error=run.error,
            diagnostics=run.diagnostics[-4000:] if run.diagnostics else "",
            stderr=run.stderr[-2000:] if run.stderr else "",
        )

    if concurrency == 1:
        for i, case in enumerate(cases, 1):
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            run_case(i, case)
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
    else:
        case_iter = iter(enumerate(cases, 1))
        futures: set[Future[None]] = set()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            while len(futures) < concurrency:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    break
                try:
                    i, case = next(case_iter)
                except StopIteration:
                    break
                futures.add(pool.submit(run_case, i, case))

            while futures:
                done, futures = wait(futures, return_when=FIRST_COMPLETED)
                for fut in done:
                    fut.result()
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    continue
                while len(futures) < concurrency:
                    try:
                        i, case = next(case_iter)
                    except StopIteration:
                        break
                    futures.add(pool.submit(run_case, i, case))

    if cancelled:
        raw_count = len(list((run_dir / "raw").glob("*.json"))) if (run_dir / "raw").is_dir() else 0
        _emit(on_event, type="run_cancelled", completed=raw_count, total=len(cases))

    if grade and not cancelled and (run_dir / "raw").is_dir():
        execute_grade(str(run_dir), cancel_event=cancel_event, on_event=on_event)
    return str(run_dir)


# ---- grade -----------------------------------------------------------------


def execute_grade(
    run_dir: str,
    *,
    cancel_event: Optional[Event] = None,
    on_event: OnEvent = None,
) -> dict[str, Any]:
    """(Re)grade every stored case and write report.json/.md. Returns the report."""
    rd = Path(run_dir)
    raw_dir = rd / "raw"
    if not raw_dir.is_dir():
        raise FileNotFoundError(f"no raw/ dir in {rd} — did `run` complete?")
    graded_dir = rd / "graded"
    graded_dir.mkdir(exist_ok=True)
    manifest = (
        json.loads((rd / "manifest.json").read_text())
        if (rd / "manifest.json").is_file()
        else {}
    )

    raw_paths = sorted(raw_dir.glob("*.json"))
    _emit(on_event, type="grade_start", total=len(raw_paths))
    graded: list[dict[str, Any]] = []
    for raw_path in raw_paths:
        if cancel_event is not None and cancel_event.is_set():
            _emit(on_event, type="grade_cancelled", completed=len(graded), total=len(raw_paths))
            break
        d, run = load_raw(raw_path)
        grades = grade_case(run, d.get("graders") or [])
        passed = bool(grades) and all(g.passed for g in grades)
        if run.error or run.session is None:
            passed = False
        entry = {
            "case_id": d["case_id"],
            "type": d.get("type", "task"),
            "passed": passed,
            "error": run.error,
            "grades": [g.to_dict() for g in grades],
            "metrics": metrics(run),
            "answer": run.answer,
            "session_id": run.session_id,
        }
        (graded_dir / f"{d['case_id']}.json").write_text(
            json.dumps(entry, ensure_ascii=False, indent=2)
        )
        graded.append(entry)
        _emit(on_event, type="case_graded", case_id=d["case_id"], passed=passed)

    report = build_report(graded, manifest)
    (rd / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    (rd / "report.md").write_text(render_markdown(report))
    _emit(on_event, type="graded", report=report)
    return report


# ---- read-side queries (for the web/UI and `report`/`show`) ----------------


def list_datasets() -> list[str]:
    """Available dataset files under datasets/ (relative paths)."""
    if not DATASETS_DIR.is_dir():
        return []
    return sorted(
        str(p.relative_to(DATASETS_DIR.parent))
        for p in DATASETS_DIR.glob("*.y*ml")
    )


def list_runs(runs_root: Optional[Path] = None) -> list[dict[str, Any]]:
    """Summaries of all runs, newest first (run_id, model, pass rate, counts)."""
    root = runs_root or RUNS_DIR
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for d in sorted(root.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        man = json.loads((d / "manifest.json").read_text()) if (d / "manifest.json").is_file() else {}
        summary: dict[str, Any] = {}
        rp = d / "report.json"
        if rp.is_file():
            summary = json.loads(rp.read_text()).get("summary", {})
        out.append({
            "run_id": d.name,
            "model": man.get("model"),
            "dataset": man.get("dataset"),
            "started_at": man.get("started_at"),
            "case_count": man.get("case_count"),
            "graded": rp.is_file(),
            "total": summary.get("total"),
            "passed": summary.get("passed"),
            "pass_rate": summary.get("pass_rate"),
        })
    return out


def get_report(run_id: str, runs_root: Optional[Path] = None) -> dict[str, Any]:
    root = runs_root or RUNS_DIR
    rp = root / run_id / "report.json"
    if not rp.is_file():
        raise FileNotFoundError(f"no report.json for run {run_id} — grade it first")
    return json.loads(rp.read_text())


def get_trajectory(run_id: str, case_id: str, runs_root: Optional[Path] = None) -> dict[str, Any]:
    """A case's full trajectory for replay: messages + tool calls + metrics."""
    root = runs_root or RUNS_DIR
    case_dir = root / run_id
    raw_path = root / run_id / "raw" / f"{case_id}.json"
    if not raw_path.is_file():
        raise FileNotFoundError(f"no raw trajectory for {run_id}/{case_id}")
    d, run = load_raw(raw_path)
    graded_path = case_dir / "graded" / f"{case_id}.json"
    graded = json.loads(graded_path.read_text()) if graded_path.is_file() else {}
    s = run.session
    messages: list[dict[str, Any]] = []
    if s is not None:
        for m in s.messages:
            messages.append({
                "role": m.role,
                "content": m.content,
                "tool_name": m.tool_name,
                "tool_calls": [
                    {"name": tc.name, "arguments": tc.arguments} for tc in m.tool_calls
                ],
            })
    return {
        "case_id": case_id,
        "prompt": d.get("prompt", ""),
        "answer": run.answer,
        "session_id": run.session_id,
        "error": run.error,
        "diagnostics": run.diagnostics,
        "stderr": run.stderr,
        "graders": graded.get("grades") or d.get("graders") or [],
        "metrics": graded.get("metrics") or metrics(run),
        "messages": messages,
    }


# ---- dataset authoring (guided builder; YAML stays the source of truth) -----


def _dataset_path(name: str) -> Path:
    """Resolve a bare dataset filename to a path under datasets/, safely.

    Rejects path traversal and anything that isn't a plain ``*.yaml/*.yml`` file
    name — the web layer accepts ``name`` from the client, so this is the gate.
    """
    raw = (name or "").strip()
    if not raw:
        raise ValueError("dataset name is required")
    # Accept an optional leading "datasets/" (list_datasets returns that form).
    if raw.startswith("datasets/"):
        raw = raw[len("datasets/"):]
    if "/" in raw or "\\" in raw or raw.startswith("."):
        raise ValueError(f"invalid dataset name: {name!r}")
    if not raw.endswith((".yaml", ".yml")):
        raw += ".yaml"
    p = (DATASETS_DIR / raw).resolve()
    if p.parent != DATASETS_DIR.resolve():
        raise ValueError(f"dataset name escapes datasets/: {name!r}")
    return p


_NEW_DATASET_TEMPLATE = """\
# {name}
#
# 每条用例: id / type / prompt / (toolsets) / graders[]
#   id        唯一, 决定产物文件名
#   type      仅用于报告分组 (qa / tool_use / task)
#   prompt    喂给 hermes chat -q 的内容
#   toolsets  可选, 映射到 hermes -t (注意: skill 工具集名是 `skills` 复数!)
#   graders[] 一条用例可挂多个 grader, 全 pass 才算整体 pass
#
# Grader kinds:
#   tool_call: must_call / must_call_any / must_not_call / args_match / expect_success
#   timing:    max_seconds / max_api_calls / max_tool_calls / max_output_tokens /
#              max_total_tokens / max_cost_usd
#   llm_judge: rubric / pass_threshold / model

- id: example-qa
  type: qa
  prompt: "用一句话回答：1+1 等于几？"
  graders:
    - kind: timing
      max_seconds: 60
    - kind: llm_judge
      rubric: "回答是否正确（结果为 2）且简洁。"
      pass_threshold: 7
"""


def read_dataset_text(name: str) -> dict[str, Any]:
    """Return the raw YAML text of a dataset for editing."""
    p = _dataset_path(name)
    if not p.is_file():
        raise FileNotFoundError(f"dataset not found: {name}")
    return {"name": p.name, "text": p.read_text()}


def validate_dataset_text(text: str) -> dict[str, Any]:
    """Parse YAML text without writing; return an outline or a friendly error.

    Drives the editor's live validation. Uses the SAME parser as on-disk loading
    (:func:`load_dataset_text`) so "valid in the browser" == "loadable by run".
    """
    try:
        cases = load_dataset_text(text)
    except Exception as exc:  # noqa: BLE001 - surface the message to the editor
        return {"ok": False, "error": str(exc), "cases": []}
    return {
        "ok": True,
        "error": None,
        "cases": [
            {
                "id": c.id,
                "type": c.type,
                "prompt": c.prompt,
                "grader_kinds": [str(g.get("kind", "?")) for g in c.graders],
            }
            for c in cases
        ],
    }


def write_dataset_text(name: str, text: str) -> dict[str, Any]:
    """Validate then persist YAML text verbatim (comments/formatting preserved)."""
    p = _dataset_path(name)
    result = validate_dataset_text(text)
    if not result["ok"]:
        raise ValueError(result["error"])
    p.write_text(text)
    return {"name": p.name, "ok": True, "case_count": len(result["cases"])}


def create_dataset(name: str) -> dict[str, Any]:
    """Create a new dataset file seeded with a commented starter template."""
    p = _dataset_path(name)
    if p.is_file():
        raise ValueError(f"dataset already exists: {p.name}")
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    p.write_text(_NEW_DATASET_TEMPLATE.format(name=p.name))
    return {"name": p.name}


def delete_dataset(name: str) -> dict[str, Any]:
    """Delete a dataset file (does not touch any runs already produced from it)."""
    p = _dataset_path(name)
    if not p.is_file():
        raise FileNotFoundError(f"dataset not found: {name}")
    p.unlink()
    return {"name": p.name, "deleted": True}


# ---- meta: declarative spec driving the guided form (single source) --------


def grader_meta() -> dict[str, Any]:
    """Known toolsets + grader-kind field specs + case/grader YAML snippets.

    Hand-authored here (graders don't carry schema) so the frontend form, the
    snippet templates, and the hints stay in one place. Semantics mirror
    datasets/tasks.yaml's header and evalkit/graders/*.py.
    """
    return {
        "toolsets": [
            {"name": "skills", "desc": "skill_view/skill_manage/skills_list（注意复数）"},
            {"name": "web", "desc": "网页搜索/抓取"},
            {"name": "terminal", "desc": "执行 shell（headless 有独立审批拦截，需配允许列表）"},
            {"name": "vision", "desc": "图像理解"},
            {"name": "reasoning", "desc": "结构化推理"},
            {"name": "creative", "desc": "创意写作"},
            {"name": "research", "desc": "组合: 研究类"},
            {"name": "development", "desc": "组合: 开发类"},
            {"name": "analysis", "desc": "组合: 分析类"},
            {"name": "content_creation", "desc": "组合: 内容创作"},
            {"name": "full_stack", "desc": "组合: 全栈"},
        ],
        "grader_kinds": [
            {
                "kind": "tool_call",
                "desc": "断言工具调用：是否调了某工具、参数子集匹配、tool 结果成败。",
                "fields": [
                    {"name": "must_call", "type": "string", "desc": "必须调用的工具名"},
                    {"name": "must_call_any", "type": "list", "desc": "调用其一即可"},
                    {"name": "must_not_call", "type": "list", "desc": "禁止调用"},
                    {"name": "args_match", "type": "map", "desc": "参数子集匹配（parse 后）"},
                    {"name": "expect_success", "type": "bool", "desc": "候选调用至少一次结果非报错", "default": True},
                ],
                "snippet": (
                    "    - kind: tool_call\n"
                    "      must_call: TOOL_NAME\n"
                    "      expect_success: true\n"
                ),
            },
            {
                "kind": "timing",
                "desc": "阈值：耗时 / 调用次数 / token / 成本上限。",
                "fields": [
                    {"name": "max_seconds", "type": "number", "desc": "墙钟秒数上限"},
                    {"name": "max_api_calls", "type": "number", "desc": "API 调用次数上限"},
                    {"name": "max_tool_calls", "type": "number", "desc": "工具调用次数上限"},
                    {"name": "max_output_tokens", "type": "number", "desc": "输出 token 上限"},
                    {"name": "max_total_tokens", "type": "number", "desc": "总 token 上限"},
                    {"name": "max_cost_usd", "type": "number", "desc": "成本上限（部分 provider 不可用）"},
                ],
                "snippet": (
                    "    - kind: timing\n"
                    "      max_seconds: 60\n"
                ),
            },
            {
                "kind": "llm_judge",
                "desc": "LLM 判官：按 rubric 打 0-10 分，≥阈值算 pass（会花 token）。",
                "fields": [
                    {"name": "rubric", "type": "text", "desc": "评分标准（必填）"},
                    {"name": "pass_threshold", "type": "number", "desc": "通过阈值 0-10", "default": 7},
                    {"name": "model", "type": "string", "desc": "可选：指定便宜的判官模型"},
                    {"name": "record_process", "type": "bool", "desc": "是否保存判官 prompt/原始输出", "default": True},
                ],
                "snippet": (
                    "    - kind: llm_judge\n"
                    "      rubric: \"答案是否准确、是否抓住要点。\"\n"
                    "      pass_threshold: 7\n"
                    "      record_process: true\n"
                ),
            },
        ],
        "case_templates": [
            {
                "label": "QA 问答",
                "snippet": (
                    "- id: CASE_ID\n"
                    "  type: qa\n"
                    "  prompt: \"在此填写问题\"\n"
                    "  graders:\n"
                    "    - kind: llm_judge\n"
                    "      rubric: \"答案是否准确、是否抓住要点。\"\n"
                    "      pass_threshold: 7\n"
                ),
            },
            {
                "label": "工具调用",
                "snippet": (
                    "- id: CASE_ID\n"
                    "  type: tool_use\n"
                    "  prompt: \"在此填写指令\"\n"
                    "  toolsets: [skills]\n"
                    "  graders:\n"
                    "    - kind: tool_call\n"
                    "      must_call: skill_view\n"
                    "      expect_success: true\n"
                    "    - kind: timing\n"
                    "      max_seconds: 90\n"
                ),
            },
            {
                "label": "综合任务",
                "snippet": (
                    "- id: CASE_ID\n"
                    "  type: task\n"
                    "  prompt: \"在此填写任务\"\n"
                    "  graders:\n"
                    "    - kind: llm_judge\n"
                    "      rubric: \"任务是否真正完成。\"\n"
                    "      pass_threshold: 7\n"
                ),
            },
        ],
    }


# ---- ad-hoc try-run (single prompt; nothing persisted to runs/) ------------


def try_prompt(
    prompt: str,
    *,
    toolsets: Optional[list[str]] = None,
    skills: Optional[list[str]] = None,
    model: Optional[str] = None,
    profile: Optional[str] = None,
    timeout: float = 600.0,
    yolo: bool = True,
    accept_hooks: bool = True,
    ignore_rules: bool = True,
    cancel_event: Optional[Event] = None,
    on_event: OnEvent = None,
) -> dict[str, Any]:
    """Run a single prompt and return its trajectory (no run dir written).

    Reuses :func:`runner.run_prompt` (case_id="adhoc") and the same trajectory
    shape as :func:`get_trajectory`, so the frontend can render it with the
    existing trajectory renderer and turn observed tool_calls into assertions.
    """
    _emit(on_event, type="try_start", prompt=prompt)
    run = run_prompt(
        prompt,
        case_id="adhoc",
        model=model,
        toolsets=toolsets,
        skills=skills,
        profile=profile,
        timeout=timeout,
        yolo=yolo,
        accept_hooks=accept_hooks,
        ignore_rules=ignore_rules,
        cancel_event=cancel_event,
    )
    s = run.session
    messages: list[dict[str, Any]] = []
    if s is not None:
        for m in s.messages:
            messages.append({
                "role": m.role,
                "content": m.content,
                "tool_name": m.tool_name,
                "tool_calls": [
                    {"name": tc.name, "arguments": tc.arguments} for tc in m.tool_calls
                ],
            })
    trajectory = {
        "case_id": "adhoc",
        "prompt": prompt,
        "answer": run.answer,
        "session_id": run.session_id,
        "error": run.error,
        "diagnostics": run.diagnostics,
        "stderr": run.stderr,
        "graders": [],
        "metrics": metrics(run),
        "messages": messages,
    }
    _emit(on_event, type="try_done", ok=run.ok, trajectory=trajectory)
    return trajectory
