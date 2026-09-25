"""Offline evaluation harness (Phase 6, ADR 0013).

Runs the pure review engine over cases with planted bugs and measures how many it
finds (recall), how many of its comments are real (precision), and what that costs.
The scripts in /evals are thin wrappers around this package.
"""
