# Mac support

## Apple Silicon development VM

Ve OS development on Apple Silicon uses the existing Try Omarchy runtime. The Ve macOS launcher changes the app name and icon while keeping that signed runtime and its VM disk. It does not install Linux directly on the Mac or replace macOS.

With Try Omarchy installed, run `scripts/ve-os-macos-app` from the Ve repository to create `~/Applications/Ve OS.app`. Close the existing VM before opening the Ve launcher because both use the same disk. The runtime log is `~/Library/Logs/Ve OS/runtime.log`.

Inside Linux, activate the Ve source checkout using the [Getting Started](02-getting-started.md) instructions. A shared folder makes source visible to the VM; it does not by itself switch the installed desktop to that source.

## Intel Mac installation reference

The inherited installer contains Intel Mac hardware support. The reference below describes upstream Omarchy installation and known hardware limitations, not a Ve hardware certification. Upstream screenshots and community projects retain their own branding. Review [Getting Started](02-getting-started.md) and back up the Mac before any disk installation. The full-disk installation option erases the selected drive.

#### Disable Secure Boot

It is necessary to disable Apple's Secure Boot in order to boot the bootable USB, as well as the OS. To disable it, perform the following:

1. Turn off your Mac
2. Turn it on and _immediately_ press and hold Command-R until you see the loading screen appear
3. Select your user and enter your password if prompted
4. Once in the recovery screen, choose **Utilities > Startup Security Utility** from the menubar
5. Enter your password when prompted to authenticate
6. Choose "No Security" from the Secure Boot options
7. Choose "Allow booting from external or removable media" from the External Boot options

#### Start the Installation

1. Insert the USB drive
2. Restart your Mac and _immediately_ press and hold Option until you see a screen of boot devices
3. Select the orange EFI Boot device
4. Proceed with the [install as normal](02-getting-started.md)

The installer detects Mac hardware and applies the needed fixes automatically: Broadcom Wi-Fi drivers and firmware, the SPI keyboard driver on the MacBook models that need it, and an NVMe suspend fix for those same models.

### Known Limitations

Members of the community are constantly working on solutions to these challenges so if these are problematic for you, join #omarchy-on-other in our [Discord](https://discord.gg/tXFUdasqhY) and see if there's any up-to-date methods for resolving these.

#### Devices with T1 Chip

The Apple T1 chip was introduced in late 2016 and used exclusively in the first-generation MacBook Pro models with Touch Bar.
- MacBook Pro 13-inch (2016, two Thunderbolt 3 ports) – Model: A1706
- MacBook Pro 13-inch (2016, four Thunderbolt 3 ports) – Model: A1708
- MacBook Pro 15-inch (2016) – Model: A1707

#### Known Issues

- Touch Bar is non-functional
- Sound is not functioning

#### Devices with T2 Chip

The Apple T2 Security Chip was introduced in 2017. The T2 chip was discontinued with the transition to Apple silicon (M-series chips) starting in 2020.

- iMac Pro (2017) – Model: A1862
- MacBook Pro 13-inch (2018, four Thunderbolt 3 ports) – Model: A1989
- MacBook Pro 15-inch (2018) – Model: A1990
- MacBook Air (Retina, 13-inch, 2018) – Model: A1932
- Mac mini (2018) – Model: A1998
- MacBook Pro 13-inch (2019, two Thunderbolt 3 ports) – Model: A2159
- MacBook Pro 13-inch (2019, four Thunderbolt 3 ports) – Model: A2178
- MacBook Pro 15-inch (2019) – Model: A1990
- iMac (Retina 5K, 27-inch, 2020) – Model: A2115 (iMac20,1 and iMac20,2)
- MacBook Pro 13-inch (2020, two Thunderbolt 3 ports) – Model: A2265
- MacBook Pro 15-inch (2020) – Model: A1990

On these models, the installer automatically sets up the patched `linux-t2` kernel, the T2 audio configuration, Apple's Broadcom Wi-Fi/Bluetooth firmware, and fan control via `t2fanrd`. The Touch Bar runs on the kernel's built-in Boot Camp-style support.

#### iMac Retina 5K (2017)

The 2017 27-inch iMac has no T2 chip, so the installer sets it up like the other Intel Macs.

- iMac (Retina 5K, 27-inch, 2017) – Model: A1419

#### Known Issues

- The display doesn't run at its native 5120×2880: the panel is two tiles, and the stock driver lights only one
- Sound is not functioning
- The brightness control has no effect
- Sleep hangs the machine; only a power cycle recovers it

A community patcher for this model fixes the display, sound and brightness: [omarchy-imac18-3](https://github.com/ahmadtv/omarchy-imac18-3).
