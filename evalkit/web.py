"""FastAPI backend for the evalkit web control panel.

Read APIs surface datasets, runs, reports, and trajectories. The write APIs
(/api/run, /api/grade) kick off the work in a background thread and stream
progress into an in-memory job; the frontend polls /api/jobs/<id>.

Launch with ``python -m evalkit.cli serve`` (or ``uvicorn evalkit.web:app``).
Static frontend lives in evalkit/webui/ and is served at /.
"""

from __future__ import annotations

import threading
import traceback
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import service

app = FastAPI(title="evalkit", description="Hermes Agent 测评控制台")

_WEBUI = Path(__file__).resolve().parent / "webui"


@app.middleware("http")
async def _no_store(request, call_next):
    """Disable browser caching for all assets.

    This is a localhost dev tool; caching only causes stale app.js/style.css to
    be served after edits (the browser keeps an old SPA and clicks silently die).
    no-store forces a fresh fetch on every load.
    """
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response

# ---- in-memory job registry -----------------------------------------------

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def _new_job(kind: str) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {
            "id": job_id,
            "kind": kind,
            "status": "running",  # running | cancelling | done | error | cancelled
            "events": [],
            "run_id": None,
            "error": None,
            "cancel_event": threading.Event(),
        }
    return job_id


def _make_emitter(job_id: str):
    def emit(ev: dict[str, Any]) -> None:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job is None:
                return
            job["events"].append(ev)
            if ev.get("type") == "run_start":
                job["run_id"] = ev.get("run_id")
    return emit


def _get_cancel_event(job_id: str) -> threading.Event:
    with _jobs_lock:
        return _jobs[job_id]["cancel_event"]


def _run_in_thread(job_id: str, fn, *args, **kwargs) -> None:
    def worker() -> None:
        try:
            result = fn(*args, **kwargs)
            with _jobs_lock:
                job = _jobs[job_id]
                job["status"] = "cancelled" if job["cancel_event"].is_set() else "done"
                # execute_run returns the run dir; capture its id if not already set
                if job["run_id"] is None and isinstance(result, str):
                    job["run_id"] = Path(result).name
        except Exception as exc:  # noqa: BLE001 - record failures into the job
            with _jobs_lock:
                job = _jobs[job_id]
                job["status"] = "error"
                job["error"] = f"{exc}"
                job["events"].append({"type": "error", "message": str(exc)})
            traceback.print_exc()

    threading.Thread(target=worker, daemon=True).start()


# ---- request models --------------------------------------------------------


class RunRequest(BaseModel):
    dataset: str
    model: Optional[str] = None
    provider: Optional[str] = None
    profile: Optional[str] = None
    timeout: float = 600.0
    max_turns: Optional[int] = None
    toolsets: Optional[list[str]] = None
    yolo: bool = True
    accept_hooks: bool = True
    ignore_rules: bool = True
    preset: Optional[str] = None  # preset name (labeling only; params are resolved by caller)
    concurrency: int = Field(default=1, ge=1, le=32)


class PresetRequest(BaseModel):
    model: Optional[str] = None
    provider: Optional[str] = None
    profile: Optional[str] = None
    toolsets: Optional[list[str]] = None
    max_turns: Optional[int] = None
    timeout: Optional[float] = 600.0
    yolo: bool = True
    accept_hooks: bool = True
    ignore_rules: bool = True


class DatasetTextRequest(BaseModel):
    text: str


class CreateDatasetRequest(BaseModel):
    name: str


class ValidateRequest(BaseModel):
    text: str


class TryRequest(BaseModel):
    prompt: str
    toolsets: Optional[list[str]] = None
    skills: Optional[list[str]] = None
    model: Optional[str] = None
    profile: Optional[str] = None
    timeout: float = 600.0
    yolo: bool = True
    accept_hooks: bool = True
    ignore_rules: bool = True


# ---- read APIs -------------------------------------------------------------


@app.get("/api/meta")
def api_meta() -> dict[str, Any]:
    return service.grader_meta()


@app.get("/api/datasets")
def api_datasets() -> dict[str, Any]:
    return {"datasets": service.list_datasets()}


@app.get("/api/datasets/{name}/text")
def api_dataset_text(name: str) -> dict[str, Any]:
    try:
        return service.read_dataset_text(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.put("/api/datasets/{name}/text")
def api_save_dataset(name: str, req: DatasetTextRequest) -> dict[str, Any]:
    try:
        return service.write_dataset_text(name, req.text)
    except ValueError as exc:
        # invalid YAML / failed validation → 422 so the editor shows the message
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/api/datasets")
def api_create_dataset(req: CreateDatasetRequest) -> dict[str, Any]:
    try:
        return service.create_dataset(req.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/datasets/{name}")
def api_delete_dataset(name: str) -> dict[str, Any]:
    try:
        return service.delete_dataset(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/datasets/validate")
def api_validate_dataset(req: ValidateRequest) -> dict[str, Any]:
    return service.validate_dataset_text(req.text)


@app.get("/api/runs")
def api_runs() -> dict[str, Any]:
    return {"runs": service.list_runs()}


@app.get("/api/runs/{run_id}")
def api_run(run_id: str) -> dict[str, Any]:
    try:
        return service.get_report(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.delete("/api/runs/{run_id}")
def api_delete_run(run_id: str) -> dict[str, Any]:
    try:
        return service.delete_run(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/runs/{run_id}/cases/{case_id}")
def api_case(run_id: str, case_id: str) -> dict[str, Any]:
    try:
        return service.get_trajectory(run_id, case_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/runs/{run_id}/cases/{case_id}/refresh-session")
def api_refresh_case_session(run_id: str, case_id: str) -> dict[str, Any]:
    try:
        return service.refresh_case_session(run_id, case_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))


# ---- write APIs (background jobs) ------------------------------------------


@app.post("/api/run")
def api_start_run(req: RunRequest) -> dict[str, Any]:
    job_id = _new_job("run")
    _run_in_thread(
        job_id,
        service.execute_run,
        req.dataset,
        model=req.model or None,
        provider=req.provider or None,
        profile=req.profile or None,
        timeout=req.timeout,
        max_turns=req.max_turns,
        toolsets=req.toolsets or None,
        yolo=req.yolo,
        accept_hooks=req.accept_hooks,
        ignore_rules=req.ignore_rules,
        preset=req.preset or None,
        grade=True,
        concurrency=req.concurrency,
        cancel_event=_get_cancel_event(job_id),
        on_event=_make_emitter(job_id),
    )
    return {"job_id": job_id}


# ---- runner presets --------------------------------------------------------


@app.get("/api/presets")
def api_presets() -> dict[str, Any]:
    return {"presets": service.list_presets()}


@app.put("/api/presets/{name}")
def api_save_preset(name: str, req: PresetRequest) -> dict[str, Any]:
    try:
        return service.save_preset(name, req.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/presets/{name}")
def api_delete_preset(name: str) -> dict[str, Any]:
    try:
        return service.delete_preset(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/runs/{run_id}/grade")
def api_start_grade(run_id: str, concurrency: Optional[int] = None) -> dict[str, Any]:
    job_id = _new_job("grade")
    _run_in_thread(
        job_id,
        service.execute_grade,
        str(service.RUNS_DIR / run_id),
        concurrency=concurrency,
        cancel_event=_get_cancel_event(job_id),
        on_event=_make_emitter(job_id),
    )
    return {"job_id": job_id}


@app.post("/api/runs/{run_id}/rerun-failed")
def api_start_rerun_failed(run_id: str) -> dict[str, Any]:
    job_id = _new_job("rerun_failed")
    _run_in_thread(
        job_id,
        service.execute_rerun_failed,
        run_id,
        cancel_event=_get_cancel_event(job_id),
        on_event=_make_emitter(job_id),
    )
    return {"job_id": job_id}


@app.post("/api/try")
def api_try(req: TryRequest) -> dict[str, Any]:
    job_id = _new_job("try")
    _run_in_thread(
        job_id,
        service.try_prompt,
        req.prompt,
        toolsets=req.toolsets or None,
        skills=req.skills or None,
        model=req.model or None,
        profile=req.profile or None,
        timeout=req.timeout,
        yolo=req.yolo,
        accept_hooks=req.accept_hooks,
        ignore_rules=req.ignore_rules,
        cancel_event=_get_cancel_event(job_id),
        on_event=_make_emitter(job_id),
    )
    return {"job_id": job_id}


@app.post("/api/jobs/{job_id}/cancel")
def api_cancel_job(job_id: str) -> dict[str, Any]:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if job["status"] not in {"running", "cancelling"}:
            return {"ok": True, "status": job["status"]}
        job["cancel_event"].set()
        job["status"] = "cancelling"
        job["events"].append({"type": "cancel_requested"})
        return {"ok": True, "status": job["status"]}


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str) -> dict[str, Any]:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        # shallow copy so the response isn't mutated mid-serialization
        return {
            "id": job["id"],
            "kind": job["kind"],
            "status": job["status"],
            "run_id": job["run_id"],
            "error": job["error"],
            "events": list(job["events"]),
        }


# ---- static frontend (mounted last so /api/* wins) -------------------------


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_WEBUI / "index.html")


app.mount("/", StaticFiles(directory=str(_WEBUI)), name="static")
