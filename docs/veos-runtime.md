# VeOS Mission Desk integration

The optional [runtime package](../packages/veos-runtime/README.md) owns local projects, task execution, evidence and exact-action review. It is kept separate from the desktop shell and upstream OS update/install pipeline.

```text
Mission Desk or optional bar launcher
  → authenticated loopback API
  → durable SQLite task/evidence journal
  → bounded native subscription-client worker
  → project-scoped MCP tools
      ├─ local evidence and files
      ├─ local browser with per-project profiles
      └─ explicitly configured Ve Integration adapter
  → action approval and receipt
  → continuation with the stored result
```

The package exposes `veos` as its console entry point. Its Linux integration is an opt-in user service and third-party-style plugin under the user's Omarchy configuration. It does not patch the shell, install kernel components, replace an agent account manager or enable subscriptions during OS installation.

This separation allows the existing Ve branding/upstream work to merge independently. A future native shell panel should call the runtime's authenticated API rather than maintain another task database or planner. Remote access needs a separate authenticated device/tenant protocol; do not expose the current personal loopback service to a network.

The dedicated `VeOS runtime` workflow runs package tests, builds and installs a wheel in an isolated environment, and exercises the browser/UI against fixture data. Screenshots and logs are retained as workflow artifacts. The upstream Linux VM suite remains responsible for desktop behavior. Neither workflow uses real provider accounts.

Local source, tests and review evidence do not mean the runtime is automatically present in a released Ve OS image. Live provider activation, mailbox sign-in and Linux desktop qualification remain explicit release work.
