# Ve branding

Ve OS uses the Ve mark from the mail client (`public/ve-logo.svg`) and its dark UI palette (`src/renderer/v2/styles/tokens.css`). The logo is copied unchanged; the bar and dock app launcher share `Ui/VeLogo.qml`, which masks it with the current foreground so it remains visible in light and dark themes.

The `Ve` theme is selectable with `omarchy theme set Ve`. The root `logo.txt` and `icon.txt` supply terminal and About defaults. Existing user branding is not overwritten by a checkout update.

The fork retains Omarchy command names, package names, plugin IDs, upstream authorship and licences for compatibility. This branding change does not claim a new kernel or a replacement package distribution.

Development on Apple Silicon uses Try Omarchy with this checkout selected as the Shared Folder. It appears at `~/Ve_Os-team-setup`. Temporary shell checks must restore the installed shell with `omarchy-restart-shell`. A permanent dev link uses the upstream `omarchy dev link` workflow and needs a coordinated reboot.

## Preview status

The Apple Silicon dev VM loaded this fork’s shell and returned three consecutive IPC pings. The Ve bar logo, dock launcher and dark palette were visually checked. The installed shell was restored after the check. Boot/login artwork uses the unchanged Ve mail `public/app-icons/icon-192.png`; publishing those files into an existing installation requires its supported refresh commands. A checkout update alone does not replace installed boot artwork or packaged OS naming.

## Mac Dock

Run `scripts/ve-os-macos-app` on an Apple Silicon Mac with Try Omarchy installed. It creates `~/Applications/Ve OS.app` using the exact mail client `.icns` icon and the runtime’s supported `OMARCHY_PRODUCT_NAME` / `OMARCHY_PRODUCT_ICON` settings. The signed runtime stays intact. Close the existing VM before launching this app: the runtime prevents two processes opening the same disk. The launcher stores runtime output in `~/Library/Logs/Ve OS/runtime.log`.
