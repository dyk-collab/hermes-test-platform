"""evalkit — a CLI-based evaluation harness for Hermes Agent.

Drives the installed ``hermes`` CLI to run eval tasks, reads back full
trajectories + metrics via ``hermes sessions export``, grades them, and
reports. Zero-intrusion: never imports hermes-agent internals.

See EVAL_PLATFORM_PLAN.md for the full design.
"""

__version__ = "0.1.0"
