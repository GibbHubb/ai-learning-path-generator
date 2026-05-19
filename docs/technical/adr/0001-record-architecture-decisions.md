<!-- Format: Michael Nygard's ADR template, https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions.html -->

# 0001. Record architecture decisions

## Status

Accepted

## Context

We want architecturally-significant decisions — and the reasoning behind them — to survive longer than a chat thread or a single contributor's memory. Without a record, future contributors (human or agent) re-litigate settled questions and silently violate constraints they never knew existed.

## Decision

We will record architecture decisions as ADRs in this directory, using Michael Nygard's format (Status, Context, Decision, Consequences). Each ADR is numbered, immutable once Accepted, and superseded (not edited) when a decision changes.

## Consequences

- Decisions and their rationale are discoverable in-repo, next to the code.
- New contributors can read the ADR log to understand "why is it like this".
- There is a small per-decision writing cost; we accept it for decisions that are expensive to reverse or non-obvious.
- ADRs must be kept honest — a stale "Accepted" ADR that no longer reflects reality is worse than none. Superseding is mandatory when a decision is reversed.
