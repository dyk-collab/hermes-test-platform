"""evalkit CLI — run / grade / report / show / serve.

Design: run and grade are SEPARATE (PLAN §2). ``run`` executes each case through
hermes and stores the raw trajectory; ``grade`` scores stored trajectories (re-run
cheaply with a tweaked rubric — free unless a case uses llm_judge); ``report``
aggregates; ``serve`` launches the web control panel.

The core orchestration lives in evalkit/service.py (shared with the web backend);
this module just renders its progress events to the console.

Usage:
    python -m evalkit.cli run    --dataset datasets/tasks.yaml
    python -m evalkit.cli run    --dataset datasets/tasks.yaml --model anthropic/claude-sonnet-4.6
    python -m evalkit.cli grade  runs/<run-id>
    python -m evalkit.cli report runs/<run-id>
    python -m evalkit.cli show   --run runs/<run-id> --case skills-list-basic
    python -m evalkit.cli show   --session 20260530_160958_1b6706
    python -m evalkit.cli serve  --port 8765
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import fire
from rich.console import Console

from . import service
from .persist import load_raw
from .report import render_console, render_markdown  # noqa: F401 (render_markdown via service)
from .session import export_session

console = Console()


def _console_reporter(total_holder: dict[str, int]):
    """Build an on_event handler that mirrors service progress to the console."""

    def handle(ev: dict[str, Any]) -> None:
        t = ev["type"]
        if t == "run_start":
            console.print(
                f"[bold cyan]Running[/] {ev['total']} cases → [dim]{ev['run_dir']}[/]"
            )
        elif t == "case_start":
            console.print(f"  [{ev['i']}/{ev['total']}] [bold]{ev['case_id']}[/] … ", end="")
        elif t == "case_done":
            if ev["ok"]:
                console.print(
                    f"[green]ok[/] [dim]{ev['wall_clock']:.1f}s · {ev['session_id']}[/]"
                )
            else:
                session = ev.get("session_id") or "-"
                console.print(
                    f"[red]run-error[/] [dim]details available in run history · "
                    f"session {session}[/]"
                )
        elif t == "graded":
            render_console(ev["report"], console)

    return handle


class EvalKit:
    """Hermes Agent evaluation harness."""

    def run(
        self,
        dataset: str,
        out: Optional[str] = None,
        model: Optional[str] = None,
        profile: Optional[str] = None,
        timeout: float = 600.0,
        concurrency: int = 1,
        grade: bool = True,
    ) -> str:
        """Run every case in a dataset, store trajectories, then grade + report.

        Args:
            dataset: path to a YAML dataset (e.g. datasets/tasks.yaml).
            out: runs dir (default: ./runs). A timestamped run-id is created under it.
            model: optional model override; default uses hermes's configured model.
            profile: optional hermes --profile for isolation.
            timeout: per-case timeout in seconds.
            concurrency: number of cases to run at the same time.
            grade: also grade + report after running (default True).
        """
        run_dir = service.execute_run(
            dataset, out=out, model=model, profile=profile, timeout=timeout,
            concurrency=concurrency, grade=grade, on_event=_console_reporter({}),
        )
        console.print(f"\n[dim]run → {run_dir}[/]")
        return run_dir

    def grade(self, run_dir: str) -> str:
        """(Re)grade every stored case in a run dir and write report.json/.md."""
        service.execute_grade(run_dir, on_event=_console_reporter({}))
        console.print(f"\n[dim]report → {Path(run_dir) / 'report.md'}[/]")
        return run_dir

    def report(self, run_dir: str) -> None:
        """Re-render a previously graded run's report to the console."""
        rp = Path(run_dir) / "report.json"
        if not rp.is_file():
            raise FileNotFoundError(f"no report.json in {run_dir} — run `grade` first.")
        render_console(json.loads(rp.read_text()), console)

    def show(
        self,
        session: Optional[str] = None,
        run: Optional[str] = None,
        case: Optional[str] = None,
    ) -> None:
        """Replay one full trajectory — by --session <id>, or --run <dir> --case <id>."""
        if session:
            s = export_session(session)
            answer = s.final_answer()
        elif run and case:
            _, rr = load_raw(Path(run) / "raw" / f"{case}.json")
            s = rr.session
            answer = rr.answer
            if s is None:
                console.print(f"[red]no session stored for case {case}[/]")
                return
        else:
            raise ValueError("provide either --session <id>, or both --run <dir> and --case <id>")

        console.rule(f"session {s.id}  ·  model {s.model}")
        for m in s.messages:
            if m.role == "user":
                console.print(f"[bold blue]user[/]: {m.content[:500]}")
            elif m.role == "assistant":
                if m.content:
                    console.print(f"[bold green]assistant[/]: {m.content[:500]}")
                for tc in m.tool_calls:
                    console.print(f"  [yellow]→ {tc.name}[/]({tc.arguments})")
            elif m.role == "tool":
                preview = (m.content or "")[:200].replace("\n", " ")
                console.print(f"  [dim]← {m.tool_name}: {preview!r}[/]")
        console.rule("metrics")
        console.print(
            f"tool_calls={s.tool_call_count}  api_calls={s.api_call_count}  "
            f"tokens(in/out/reason)={s.input_tokens}/{s.output_tokens}/{s.reasoning_tokens}  "
            f"cost={s.estimated_cost_usd} ({s.cost_status})"
        )
        console.print(f"[bold]final answer:[/] {answer[:1000]}")
        if run and case and rr.diagnostics:
            console.rule("Hermes diagnostics")
            console.print(rr.diagnostics)

    def serve(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        """Launch the web control panel (http://host:port)."""
        import uvicorn

        console.print(f"[bold cyan]evalkit web[/] → http://{host}:{port}")
        uvicorn.run("evalkit.web:app", host=host, port=port, log_level="info")


def main() -> None:
    fire.Fire(EvalKit)


if __name__ == "__main__":
    main()
