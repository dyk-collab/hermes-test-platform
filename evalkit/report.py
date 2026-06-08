"""Aggregate graded cases into a report (Rich table to console + Markdown file).

Consumes the per-case graded dicts written by ``cli grade`` (runs/<id>/graded/*.json)
and produces report.json + report.md. See PLAN §6.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table


def _fmt_secs(v: Any) -> str:
    return f"{v:.1f}s" if isinstance(v, (int, float)) else "-"


def _fmt_cost(graded: dict[str, Any]) -> str:
    m = graded.get("metrics") or {}
    status = m.get("cost_status")
    cost = m.get("cost")
    if status == "unknown" or cost is None:
        return "unknown"
    return f"${cost:.4f}"


def build_report(graded: list[dict[str, Any]], manifest: dict[str, Any]) -> dict[str, Any]:
    """Aggregate graded case dicts into a report dict."""
    total = len(graded)
    passed = sum(1 for g in graded if g.get("passed"))
    by_type: dict[str, dict[str, int]] = {}
    for g in graded:
        t = g.get("type", "task")
        bucket = by_type.setdefault(t, {"total": 0, "passed": 0})
        bucket["total"] += 1
        bucket["passed"] += 1 if g.get("passed") else 0

    return {
        "manifest": manifest,
        "summary": {
            "total": total,
            "passed": passed,
            "pass_rate": (passed / total) if total else 0.0,
            "by_type": by_type,
        },
        "cases": graded,
    }


def render_console(report: dict[str, Any], console: Console | None = None) -> None:
    console = console or Console()
    summary = report["summary"]
    man = report.get("manifest", {})

    table = Table(title="Hermes Eval — Results", title_style="bold cyan")
    table.add_column("case", style="bold")
    table.add_column("type")
    table.add_column("pass")
    table.add_column("graders")
    table.add_column("wall")
    table.add_column("tools", justify="right")
    table.add_column("api", justify="right")
    table.add_column("tokens", justify="right")
    table.add_column("cost")

    for g in report["cases"]:
        m = g.get("metrics") or {}
        grades = g.get("grades") or []
        gtxt = " ".join(
            f"[green]{gr['kind']}✓[/]" if gr.get("passed") else f"[red]{gr['kind']}✗[/]"
            for gr in grades
        )
        toks = m.get("total_tokens")
        table.add_row(
            g.get("case_id", "?"),
            g.get("type", "-"),
            "[green]PASS[/]" if g.get("passed") else "[red]FAIL[/]",
            gtxt or "-",
            _fmt_secs(m.get("wall_clock")),
            str(m.get("tool_calls", "-")),
            str(m.get("api_calls", "-")),
            str(toks) if toks is not None else "-",
            _fmt_cost(g),
        )

    console.print(table)
    rate = summary["pass_rate"] * 100
    color = "green" if rate == 100 else ("yellow" if rate >= 50 else "red")
    console.print(
        f"\n[bold]{summary['passed']}/{summary['total']} passed[/] "
        f"([{color}]{rate:.0f}%[/])"
        + (f"   model: {man.get('model') or 'default'}" if man else "")
    )
    for t, b in sorted(summary["by_type"].items()):
        console.print(f"  • {t}: {b['passed']}/{b['total']}")


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    man = report.get("manifest", {})
    lines: list[str] = []
    lines.append("# Hermes Eval Report\n")
    lines.append(f"- **run-id**: `{man.get('run_id', '?')}`")
    lines.append(f"- **dataset**: `{man.get('dataset', '?')}`")
    lines.append(f"- **model**: `{man.get('model') or 'hermes default'}`")
    lines.append(f"- **started**: {man.get('started_at', '?')}")
    rate = summary["pass_rate"] * 100
    lines.append(f"- **pass rate**: **{summary['passed']}/{summary['total']} ({rate:.0f}%)**\n")

    lines.append("## By type\n")
    lines.append("| type | passed | total |")
    lines.append("|------|-------:|------:|")
    for t, b in sorted(summary["by_type"].items()):
        lines.append(f"| {t} | {b['passed']} | {b['total']} |")
    lines.append("")

    lines.append("## Cases\n")
    lines.append("| case | type | result | graders | wall | tools | api | tokens | cost |")
    lines.append("|------|------|--------|---------|------|------:|----:|-------:|------|")
    for g in report["cases"]:
        m = g.get("metrics") or {}
        grades = g.get("grades") or []
        gtxt = " ".join(
            f"{gr['kind']}✓" if gr.get("passed") else f"{gr['kind']}✗" for gr in grades
        )
        toks = m.get("total_tokens")
        lines.append(
            f"| {g.get('case_id','?')} | {g.get('type','-')} | "
            f"{'PASS' if g.get('passed') else 'FAIL'} | {gtxt or '-'} | "
            f"{_fmt_secs(m.get('wall_clock'))} | {m.get('tool_calls','-')} | "
            f"{m.get('api_calls','-')} | {toks if toks is not None else '-'} | {_fmt_cost(g)} |"
        )
    lines.append("")

    # Failure detail
    fails = [g for g in report["cases"] if not g.get("passed")]
    if fails:
        lines.append("## Failures\n")
        for g in fails:
            lines.append(f"### {g.get('case_id','?')}")
            if g.get("error"):
                lines.append(f"- run error: `{g['error']}`")
            for gr in g.get("grades") or []:
                if not gr.get("passed"):
                    lines.append(f"- **{gr['kind']}** ✗ — {gr.get('reason','')}")
            lines.append("")

    return "\n".join(lines)
