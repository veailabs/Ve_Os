# Ve OS shell

The Ve OS desktop runs in one long-lived Quickshell process. Its bar, panels, overlays, menus, and services are plugins hosted by `shell.qml`.

- [Shell reference](../docs/omarchy-shell.md) — plugin manifests, IPC, configuration, and shared UI contracts.
- [Plugin user guide](../manual/32-shell-plugins.md) — install, configure, and clone plugins.
- [First-party plugins](plugins/README.md) — bundled plugin IDs, kinds, and entry points.
- [Shell development guide](../agents/skills/shell-dev.md) — conventions for changing the desktop.
- [Theming reference](../docs/theming.md) — theme tokens, templates, and generated configuration.

Ve OS uses the shared `Ui/VeLogo.qml` component for its desktop launcher and branded menu icons. Product labels follow the catalogs under `default/i18n/`; the `omarchy.*` plugin IDs and command interfaces remain compatible with upstream. Plugin author fields and references to the upstream installer retain their attribution.
