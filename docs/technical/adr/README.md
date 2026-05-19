# Architecture Decision Records

An ADR captures one architecturally-significant decision: the context, the decision, and the consequences. We use [Michael Nygard's format](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions.html).

## How to add one

1. Copy [`_template.md`](_template.md) → `NNNN-short-title.md` (NNNN = next number, zero-padded).
2. Fill Context / Decision / Consequences. Status starts `Proposed`.
3. On acceptance, set Status to `Accepted`. Superseding a decision: new ADR, and set the old one's Status to `Superseded by [NNNN](NNNN-title.md)`.
4. ADRs are immutable once Accepted — you add a new one rather than rewrite history.

## Index

| ADR | Status | Title |
|-----|--------|-------|
| [0001](0001-record-architecture-decisions.md) | Accepted | Record architecture decisions |
