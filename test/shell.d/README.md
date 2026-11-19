# Shell checks

These behavioral checks exercise desktop components and their command helpers through `test/shell`. Shared fixtures live in `base-test.sh`; see `docs/testing.md` for the required compositor environment and complete gate.

The Grok Bot launcher check covers exact native-window targeting, PATH collisions, input validation and draft URI encoding without sending a message. The Grok control check uses a fake ACP process to exercise task streaming, explicit permission decisions, cancellation, result persistence and failures. Live account-backed task execution needs the user's Grok sign-in and is separate from these deterministic checks.
