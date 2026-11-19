# Ve_Os

Ve team development fork of [Omarchy](https://github.com/omacom/omarchy). This repository starts from the existing Omarchy desktop; Ve-specific interface and service integration work has not been implemented yet.

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

Cloning source does not install Omarchy. Edit and inspect files on macOS if needed; test Linux desktop behaviour in a disposable supported Omarchy environment. Do not run the installer or migrations on the host just to inspect this fork.

Read [testing guidance](docs/testing.md) and [visual verification guidance](agents/skills/visual-verification.md). Run applicable focused tests for the changed area and attach visual evidence for UI changes. The upstream aggregate runner is `./test/all`; live desktop and graphical acceptance checks require their documented environment. A source checkout is not evidence that those checks passed.

Inherited Actions workflows are present, but their activation and successful execution on this fork are not yet verified. Do not describe CI as passing until a run proves it. The translation-sync workflow requires separate credentials and must not be enabled without review.

Fetch upstream changes with `git fetch upstream`. Integrate `upstream/quattro` on a dedicated feature branch, resolve conflicts, run applicable tests and open a PR against `veailabs/Ve_Os:master`. Do not reset or force-push the team's `master` to synchronize upstream.

The remaining documentation below is the upstream project reference.

---

# Omarchy

Omarchy is a beautiful, fun & agentic Linux distribution by DHH.

Read more at [omarchy.org](https://omarchy.org).

## The Omarchy Manual

The manual lives in [`manual/`](manual/), which is its authoritative source.

- [Welcome to Omarchy!](manual/01-welcome-to-omarchy.md)

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
- [Omarchy CLI](manual/14-omarchy-cli.md)

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
- [Omarchy on...](manual/49-omarchy-on.md)
- [Dual Boot Install](manual/50-dual-boot-install.md)
- [Unattended Installs](manual/51-unattended-installs.md)

## License

Omarchy is released under the [MIT License](https://opensource.org/licenses/MIT).
