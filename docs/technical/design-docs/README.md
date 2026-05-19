# Design docs

A technical design doc is written **before building** something non-trivial: a new subsystem, a risky change, anything where the approach isn't obvious. It is the "how", where an ADR is the "what/why".

## When to write one

- The change touches multiple components or has migration risk.
- There is more than one reasonable approach and the choice matters.
- Someone other than the author will implement or maintain it.

If it's a single obvious change, skip it. If it's a settled cross-cutting decision, that's an ADR, not a design doc.

## How

Copy [`_template.md`](_template.md) → `NN-short-name.md`, fill it, get it reviewed before coding.

## Index

| Doc | Status | Description |
|-----|--------|-------------|
| <!-- TODO --> | <!-- Draft / Approved / Implemented --> | <!-- one line --> |
