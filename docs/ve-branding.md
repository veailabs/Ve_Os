# Ve branding

Ve OS uses the Ve mark from the mail client (`public/ve-logo.svg`) and its dark UI palette (`src/renderer/v2/styles/tokens.css`). The logo is copied unchanged; the bar and dock app launcher share `Ui/VeLogo.qml`, which masks it with the current foreground so it remains visible in light and dark themes.

The `Ve` theme is selectable with `omarchy theme set Ve`. The root `logo.txt` and `icon.txt` supply terminal, screensaver and About defaults. User-supplied branding can override these defaults. The boot and login artwork uses the unchanged Ve mail `public/app-icons/icon-192.png`.

## Scope and upstream attribution

Ve branding covers the fork’s desktop identity, menus, notifications, command help, bundled launcher descriptions, default artwork and manual navigation. The manual describes Ve OS while explicitly identifying inherited installation and platform guidance as upstream reference. Existing manual screenshots are historical upstream illustrations; they are not screenshots of a fully deployed Ve system. The translated setup and platform chapters retain labelled upstream material pending Ve-specific translation.

The fork retains `omarchy` command names, package names, configuration paths, plugin IDs, signing keys, upstream authorship and licences. These are compatibility interfaces or provenance. Omarchy package repositories, release channels, installation ISOs, mirrors, community sites and third-party applications retain their original ownership. This fork does not publish a separate Ve kernel, ISO or package distribution.

The bundled signed browser extensions remain upstream-attributed artifacts. Their signed Chrome bundles and matching source manifest descriptions retain Omarchy attribution; changing those signatures or vendor keys is outside branding. Native host descriptions identify Ve OS. The Try Omarchy macOS runtime remains its original signed product inside the Ve launcher.

## Source and installed appearance

A source change does not by itself update an installed system. In a supported Linux environment, `omarchy dev link <checkout>` selects the source checkout for the desktop and command paths and coordinates a reboot. Read the command’s help before using it; it changes the active checkout and sudo’s command lookup path. `omarchy dev status` reports the active checkout.

A shared folder only exposes source files to a VM. Temporary shell previews must restore the installed shell with `omarchy-restart-shell`; a permanent developer link remains active across a reboot. Boot and login artwork at fixed system paths requires the supported Plymouth/SDDM refresh operations, and other packaged files require their installation or refresh workflow. A checkout update alone is not proof that those surfaces changed on the running machine.

Verification must distinguish source checks, the active desktop and installed boot/login assets. Current run results and screenshots belong in the task and pull request; this document describes the supported scope rather than certifying an untested installation. Upstream third-party branding and a user’s own overrides are intentional exceptions to the Ve-owned appearance.

## Mac Dock

Run `scripts/ve-os-macos-app` on an Apple Silicon Mac with Try Omarchy installed. It creates `~/Applications/Ve OS.app` using the exact mail client `.icns` icon and the runtime’s supported `OMARCHY_PRODUCT_NAME` / `OMARCHY_PRODUCT_ICON` settings. The signed runtime stays intact. Close the existing VM before launching this app: the runtime prevents two processes opening the same disk. The launcher stores runtime output in `~/Library/Logs/Ve OS/runtime.log`.
