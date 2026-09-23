# ADR 0001: Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-09-24

## Context
ReviewPilot is a portfolio project, so every design choice may come up in an interview. Decisions that live only in someone's head, or in a chat history, get lost.

## Decision
We record every significant decision as a short Architecture Decision Record in `docs/adr/`, numbered in order. Each ADR has four parts: **Context** (the forces at play), **Decision**, **Consequences** (good and bad), and **Alternatives considered**.

We never edit an accepted ADR's decision. If a decision changes, we write a new ADR and mark the old one "Superseded by ADR NNNN".

## Consequences
- The README can link to an ADR index. Each ADR is a ready-made interview answer.
- There's a small writing cost per decision, which we keep low by keeping ADRs under one page.
