# Updates

Open _Update > Ve OS_ in the Ve OS menu (`Super + Space`) to run the inherited update workflow. A circle-arrow badge beside the clock indicates an available update. The menu’s Ve name does not change the origin of system packages.

## Where updates come from

System packages continue to come from the configured Arch and upstream [Omarchy package repositories](https://github.com/omacom-io/omarchy-pkgs), with any additional repositories or AUR packages you installed. The upstream [Omarchy releases](https://github.com/omacom/omarchy/releases) and [Arch mirror](https://github.com/omacom-io/omarchy-mirror) are not Ve release services.

On a developer-linked Ve installation, the active desktop source is your Ve checkout. Review its branch and local changes before updating it. The team integrates upstream code through reviewed changes in the Ve repository; switching to an upstream release channel or reinstalling upstream packages is not a Ve upgrade. See the repository’s [upstream update procedure](../README.md#development-and-upstream-updates).

Use `omarchy dev status` to inspect the active checkout. Commands, package names and paths retain the `omarchy` compatibility name.

## Upstream channels

The inherited channel selector offers stable, RC, edge and dev. These are upstream Omarchy package and development channels. Stable follows upstream releases; RC and edge are testing channels. The dev workflow selects a source checkout. For Ve OS, use `omarchy dev link` with the Ve checkout you reviewed; do not assume switching channels will select this fork.

## Kernels and firmware

_Update > Kernel_ lists supported installed kernels and allows you to choose the default. The upstream kernel package is named `linux-omarchy`; the Ve display name does not mean Ve built a new kernel. In a terminal, use `omarchy-kernel`, `omarchy-kernel list` or `omarchy-kernel set <kernel>`. Kernel selection and boot support depend on the platform.

_Update > Firmware_ uses `fwupd` and the Linux Vendor Firmware Service for supported hardware. Some firmware updates require a reboot. In the system menu (`Super + Escape`), hold `Alt` to reveal _Reboot to BIOS_ where supported; the terminal command is `omarchy system firmware`.

## System upgrades and recovery

Use `omarchy update` for a system upgrade so the workflow can coordinate snapshots, package installation, migrations and configuration updates. Direct `pacman -Syu` and `yay -Syu` upgrades bypass those steps and are guarded by the inherited update tooling.

If an update fails, read [system snapshots](47-system-snapshots.md) before restoring a supported snapshot. Home-directory contents, including a Ve source checkout stored there, are not rolled back with the root filesystem.

`omarchy reinstall` restores upstream packages and defaults and can move the system back to the stable channel. It can overwrite configuration customizations and remove the active Ve setup. Treat it as an upstream recovery operation, then deliberately reactivate the reviewed Ve checkout if needed.
