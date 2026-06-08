"""Run one prompt through hermes and print a readable summary.

This is a thin smoke-test / exploration entry for the current minimal loop
(runner + session). The full eval CLI (run/grade/report/show) comes later.

Usage:
    python -m evalkit "your prompt here"
    python -m evalkit "查看 hermes-agent skill" -t skill
    python -m evalkit "..." -t terminal -s some-skill -m anthropic/claude-sonnet-4.6
"""

from __future__ import annotations

import argparse
import sys

from .runner import run_prompt


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evalkit", description=__doc__)
    ap.add_argument("prompt", help="The task prompt to send to hermes")
    ap.add_argument("-t", "--toolsets", help="Comma-separated toolsets (e.g. skill,terminal)")
    ap.add_argument("-s", "--skills", help="Comma-separated skills to preload")
    ap.add_argument("-m", "--model", help="Model override (default: hermes configured default)")
    ap.add_argument("--timeout", type=float, default=600.0, help="Per-run timeout in seconds")
    args = ap.parse_args(argv)

    r = run_prompt(
        args.prompt,
        case_id="cli",
        toolsets=args.toolsets.split(",") if args.toolsets else None,
        skills=args.skills.split(",") if args.skills else None,
        model=args.model,
        timeout=args.timeout,
    )

    print("=" * 60)
    print(f"ok:          {r.ok}")
    print(f"session_id:  {r.session_id}")
    print(f"wall_clock:  {r.wall_clock:.1f}s" if r.wall_clock else "wall_clock:  -")
    if r.error:
        print(f"error:       {r.error}")
    print("-" * 60)
    print("ANSWER:")
    print(r.answer or "(empty)")

    s = r.session
    if s:
        print("-" * 60)
        print(f"model:       {s.model}")
        print(f"messages:    {s.message_count}   tool_calls: {s.tool_call_count}   api_calls: {s.api_call_count}")
        print(f"tokens:      in={s.input_tokens} out={s.output_tokens} reasoning={s.reasoning_tokens}")
        print(f"cost:        {s.estimated_cost_usd} ({s.cost_status})")
        calls = s.all_tool_calls()
        if calls:
            print("tool calls:")
            for tc in calls:
                res = s.tool_result_for(tc.id)
                preview = (res.content if res else "") or ""
                print(f"  - {tc.name}({tc.arguments})")
                print(f"      -> {preview[:140]!r}")
        else:
            print("tool calls: (none)")
    print("=" * 60)

    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main())
