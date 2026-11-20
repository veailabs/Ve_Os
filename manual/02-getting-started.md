# Getting Started

Ve OS is currently a development fork activated on a supported Omarchy installation. Downloading the upstream ISO installs Omarchy; it does not install the Ve checkout. This repository does not publish a separate Ve OS ISO.

For the team’s source setup, follow the repository’s [quick start](../README.md#team-quick-start). On a supported Linux installation, clone the Ve repository, review the checkout you intend to trust, then run the existing developer-link command as your normal user:

```sh
omarchy dev link ~/Development/Ve_Os
```

Use the actual location of your Ve checkout. The command requests local administrator authentication and coordinates a reboot. A permanent developer link changes the active desktop source and sudo’s command lookup path; keep the checkout under your control. After reboot, `omarchy dev status` shows the active checkout. A clone by itself does not activate the desktop or replace installed boot and login artwork.

On Apple Silicon, use the existing Try Omarchy runtime and the Ve launcher described under [Mac support](44-mac-support.md). Keep installation and reset operations inside the intended Linux VM.

Open the Ve OS menu with `Super + Space`. Choose _Setup > Language_ to change the desktop language. The compatibility command remains `omarchy language <code>`; `omarchy language list` shows available codes.

Report Ve-specific problems in the [Ve OS issue tracker](https://github.com/veailabs/Ve_Os/issues). Include the active source version and steps to reproduce, without account tokens or private data.

## Upstream installation reference

The following instructions describe upstream Omarchy installation media. They prepare the underlying system; activate the Ve checkout afterwards. Screenshots are upstream reference images. The full-disk option erases the selected drive, so back up your data first.

Omarchy is installed using an ISO. You can choose between a full-disk install, which takes over the entire drive, or a free-space install, which puts Omarchy in the unallocated space on a drive — that's how you dual boot alongside Windows or another OS (see [dual-boot install](50-dual-boot-install.md) — note that you'll need to turn off BitLocker in Windows first). Either way, the installation defaults to full encryption, and the full-disk option will wipe the selected drive, so be sure to take a backup before using an existing one!

[Download the Omarchy ISO](https://omarchy.org/) first, put it on a USB stick (use [Rufus](https://rufus.ie/) on Windows, [balenaEtcher](https://etcher.balena.io/) on Mac, or [caligula](https://github.com/ifd3f/caligula) on Linux), and boot off the stick.

_You must turn off Secure Boot and/or TPM in the BIOS. You have to turn these off to be able to install Omarchy. They're Microsoft security schemes meant for Windows and Microsoft-affiliated Linux distributions._

Then answer the configuration questions, and confirm them like this:

 ![install-config](images/install-config.webp)

Then select a drive for your installation, and sit back and watch the installation show go. It can be done in under a minute on the fastest modern machines, but it shouldn't take more than 5 minutes even on an older computer.

 ![install-done](images/install-done.webp)

Now you're ready to Omarchy!

### Use a wired or 2.4ghz keyboard!

The full-disk encryption won't allow you to enter the password from a Bluetooth keyboard at startup. Just like you can't use a Bluetooth keyboard to enter the BIOS on a PC. You'll need a keyboard that either uses a 2.4ghz dongle or a cable (which is much nicer for latency anyway!). I personally love the [Lofree Flow84](https://www.lofree.co/products/lofree-flow-the-smoothest-mechanical-keyboard)!

### Installing for another owner

If you're setting up a machine for someone else — a family member, a new employee, a buyer — you shouldn't be answering the personal questions on their behalf. Hit `Ctrl + C` on the very first screen of the installer (the keyboard selection), and Omarchy will offer to prepare the machine for another owner instead. The system installs right away, but all the personal setup — keyboard layout, username, password — is deferred until the machine boots for the first time. The drive is still encrypted by default, and the password the new owner picks on that first boot becomes the encryption password too. (A machine you've already been using can be handed over without a reinstall too — see [resetting the computer](48-security.md).)

### Unattended installs

The ISO can also install completely on its own — no keyboard, no wizard — when it's handed its configuration on a second drive. That's the way to treat Omarchy as a base image for VMs and fleet machines. See [unattended installs](51-unattended-installs.md).

### No-encryption installations

Omarchy is installed with encryption by default. It's the safe, responsible choice for any computer that can possibly be lost or stolen. You don't want anyone with access to your hardware to be able to get your data!

But in special circumstances, like remote Omarchy installs on protected computers or for throw-away installations without sensitive data, you may want to install without encryption. You can hit `Ctrl + C` on the disk formatting confirmation to switch to an encryption-less installation.

### Language

Omarchy speaks 50 languages — the same set as omarchy.org — and English is the default. To switch, open the Omarchy menu and go to _Setup > Language_, or run `omarchy language <code>` in a terminal (`omarchy language list` shows the codes). The menu, desktop, and Omarchy's own notifications change language immediately, and every application you open afterwards follows too. Apps that were already open switch once you restart them.

### Help if you're stuck

If you get stuck, you can usually find someone willing to help in the _#omarchy-help_ channel on [the community Discord](https://omarchy.org/discord).
