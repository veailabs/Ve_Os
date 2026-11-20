<img src="logo.svg" alt="Ve" width="88" height="88">

# Ve OS

Ve team development fork of [Omarchy](https://github.com/omacom/omarchy), with the Ve mail logo and dark palette. Ve service integrations are not implemented yet.

## Team quick start

The default and PR target branch is `master`. Its initial baseline is upstream `quattro` at `b6440cfbd0d5806b7e4a361046148e4e600dcf29`. The inherited older upstream `master` is preserved as `upstream-master`. Other inherited branches are upstream history, not team task assignments.

```sh
git clone --branch master https://github.com/veailabs/Ve_Os.git
cd Ve_Os
git remote add upstream https://github.com/omacom/omarchy.git
git config remote.pushDefault origin
gh repo set-default veailabs/Ve_Os
git fetch origin
git switch -c ve/feat/123-short-description origin/master
```

Replace `123-short-description` with your assigned task number and description. When working through ve-core-workspace, follow its issue and `.worktrees/` layout rules instead of making an extra checkout outside that workspace.

- Read [AGENTS.md](AGENTS.md) and the matching guide in `agents/skills/` before editing.
- Use a separate feature branch for each task and open a draft PR explicitly against `veailabs/Ve_Os:master` with `gh pr create --repo veailabs/Ve_Os --base master --draft`.
- `master` requires one independent approving review and resolved review conversations, including for administrators. Force pushes and branch deletion are blocked. A maintainer merges after review and applicable checks pass.
- Keep original copyright and licence notices. Bundled components and assets may have their own licences.
- This fork is public. Keep confidential plans, customer data, credentials and internal documents in approved private systems.

## Development and upstream updates

Cloning source does not install Ve OS. Edit and inspect files on macOS if needed; test Linux desktop behaviour in a disposable supported Linux VM with this fork activated. Do not run the installer or migrations on the host just to inspect this fork.

Read [testing guidance](docs/testing.md) and [visual verification guidance](agents/skills/visual-verification.md). Run applicable focused tests for the changed area and attach visual evidence for UI changes. The upstream aggregate runner is `./test/all`; live desktop and graphical acceptance checks require their documented environment. A source checkout is not evidence that those checks passed.

The Tests workflow runs translation checks and the Linux VM suite for ready PRs into `master` and manual runs. Its `ci-ok` job is required by branch protection. Check the run for the current commit before merging. Upstream credential-backed translation automation is restricted to the upstream repository.

Fetch upstream changes with `git fetch upstream`. Integrate `upstream/quattro` on a dedicated feature branch, resolve conflicts, run applicable tests and open a PR against `veailabs/Ve_Os:master`. Do not reset or force-push the team's `master` to synchronize upstream.

## Ve OS manual

The [manual](manual/README.md) covers the Ve desktop and its inherited Linux tools. Ve OS is based on Omarchy by DHH and its contributors. Installation media, package repositories, third-party apps and community resources keep their upstream names and ownership; this fork does not publish a separate ISO or package distribution.

- [Welcome to Ve OS!](manual/01-welcome-to-omarchy.md)

**The Basics**

- [Getting Started](manual/02-getting-started.md)
- [Coming From Mac or Windows](manual/03-coming-from-mac-or-windows.md)
- [Navigation](manual/04-navigation.md)
- [The top bar](manual/05-the-top-bar.md)
- [Themes](manual/06-themes.md)
- [Hotkeys](manual/07-hotkeys.md)
- [Unified Clipboard & History](manual/08-unified-clipboard-history.md)
- [Reminders](manual/09-reminders.md)
- [Notices](manual/10-notices.md)
- [Text Extraction & Dictation](manual/11-text-extraction-dictation.md)
- [Screenshots & Recording](manual/12-screenshots-recording.md)
- [Toggles, idle & screensaver](manual/13-toggles-idle-screensaver.md)
- [Ve OS CLI](manual/14-omarchy-cli.md)

**The Applications**

- [Terminal](manual/15-terminal.md)
- [Neovim](manual/16-neovim.md)
- [AI](manual/17-ai.md)
- [Development Tools](manual/18-development-tools.md)
- [Shell Tools](manual/19-shell-tools.md)
- [Shell Functions](manual/20-shell-functions.md)
- [TUIs](manual/21-tuis.md)
- [GUIs](manual/22-guis.md)
- [Browsers](manual/23-browsers.md)
- [Commercial apps/services](manual/24-commercial-apps-services.md)
- [Web Apps](manual/25-web-apps.md)
- [Gaming](manual/26-gaming.md)
- [Filling out PDFs](manual/27-filling-out-pdfs.md)
- [Windows VM](manual/28-windows-vm.md)
- [Other Packages](manual/29-other-packages.md)

**Configuration**

- [Updates](manual/30-updates.md)
- [Dotfiles](manual/31-dotfiles.md)
- [Shell plugins](manual/32-shell-plugins.md)
- [Monitors](manual/33-monitors.md)
- [Keyboard, Mouse, Trackpad](manual/34-keyboard-mouse-trackpad.md)
- [Networking](manual/35-networking.md)
- [System sleep](manual/36-system-sleep.md)
- [Hardware authentication](manual/37-hardware-authentication.md)
- [Fonts](manual/38-fonts.md)
- [Backgrounds](manual/39-backgrounds.md)
- [Prompt](manual/40-prompt.md)
- [Branding](manual/41-branding.md)
- [Common tweaks](manual/42-common-tweaks.md)
- [Making your own theme](manual/43-making-your-own-theme.md)

**The Rest**

- [Mac support](manual/44-mac-support.md)
- [Troubleshooting](manual/45-troubleshooting.md)
- [FAQ](manual/46-faq.md)
- [System snapshots](manual/47-system-snapshots.md)
- [Security](manual/48-security.md)
- [Other platforms (upstream reference)](manual/49-omarchy-on.md)
- [Dual Boot Install](manual/50-dual-boot-install.md)
- [Unattended Installs](manual/51-unattended-installs.md)

## License

This fork retains Omarchy’s [MIT License](LICENSE) and original copyright notices. Bundled components and assets retain their respective licences.

## Ve appearance

The desktop menu and dock app launcher use the Ve mail mark. Select the matching palette with `omarchy theme set Ve`. See [Ve branding](docs/ve-branding.md) for asset provenance, compatibility names and installation scope.

On an Apple Silicon Mac with Try Omarchy installed, run `scripts/ve-os-macos-app` to create `~/Applications/Ve OS.app`. Close the existing VM before opening the Ve launcher; it uses the same VM disk.

## Default AI apps

Grok Bot is included in the default package list and pinned to the dock. Existing installations can add it with `omarchy pkg add grok-bot` and `omarchy dock pin grok-bot`. Grok Build CLI is already a default mise tool; `mise install grok` installs it immediately instead of waiting for its first use. Open the desktop app with `gtk-launch grok-bot` and the CLI with `grok`. Sign in within each app as needed.

## Grok controls

Expand Grok controls in the agents panel to open or close the native Bot app, prepare an unsent draft for your primary Bot, or run a Grok Build task in a selected directory. The CLI task view shows progress, the latest result, one-time permission requests, cancellation and session continuation. Sign in through the official CLI when prompted. Closing Bot windows does not stop its cloud work; cloud routines and other unsupported controls stay in Grok Bot.
