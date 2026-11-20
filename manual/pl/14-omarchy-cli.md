# Ve OS CLI

Ve OS zazwyczaj obsługuje się za pomocą skrótów klawiszowych i menu Ve OS (`Super + Space`). Możesz jednak obsługiwać go również za pomocą CLI `omarchy`. Jest to szczególnie przydatne, gdy współpracujesz z agentem AI przy dostosowywaniu lub konfiguracji.

CLI ma dostęp do wszystkich wewnętrznych narzędzi używanych zarówno przez menu, jak i w inny sposób. Możesz zobaczyć wszystko, co jest dostępne, uruchamiając `omarchy` w terminalu.

Wygląda to mniej więcej tak:

```
~ ❯ omarchy
Ve OS command center

Usage:
  omarchy <command> [args...]
  omarchy commands [--all] [--json] [--check]
  omarchy <group> --help
  omarchy <group> <command> --help

Common commands:
  omarchy update              Update Ve OS and system packages
  omarchy theme list          List available themes
  omarchy theme set <name>    Apply a theme
  omarchy font list           List available fonts
  omarchy screenshot          Take a screenshot
  omarchy-debug               Print debugging information

Groups:
  agent          AI coding agents, their usage, and subscription accounts
  audio          Audio input and output controls
  bar            Ve OS shell bar layout and settings
  battery        Battery status helpers
  bluetooth      Bluetooth device controls
  branch         Ve OS git branch management
  branding       About and screensaver branding
  brightness     Display and keyboard brightness
  capture        Screenshots and screen recording
  channel        Ve OS release channel management
  clipboard      Clipboard helpers
  cmd            Command and shortcut helpers
  config         System configuration helpers
  ...
```

W celach diagnostycznych uruchom bezpośrednio `omarchy-debug --no-sudo --print`. Wypisuje on diagnostykę i zapisuje `/tmp/omarchy-debug.log` bez pytania o sudo, również przy instalacjach pakietów, w przypadku których `omarchy debug` może zakończyć się błędem "Unknown Ve OS command".

Możesz też zagłębić się w każdą grupę:

```
~ ❯ omarchy capture
Capture commands — Screenshots and screen recording:
  omarchy capture qr                                                                                                                                                                                                       Decode a QR code from a screenshot region
  omarchy capture screenrecording [--fullscreen] [--with-desktop-audio] [--with-microphone-audio] [--with-webcam] [--webcam-device=<device>] [--webcam-size=<small|medium|large>] [--resolution=<size>] [--stop-recording]  Start or stop screen recording
  omarchy capture screenrecording with webcam                                                                                                                                                                              Pick a webcam and start a screen recording with it
  omarchy capture screenshot [smart|region|windows|fullscreen|scroll] [copy|save]                                                                                                                                          Take a screenshot
  omarchy capture text                                                                                                                                                                                                     Extract text from a screenshot region with OCR
  omarchy capture webcam resize <smaller|larger|reset|small|medium|large>                                                                                                                                                  Resize the active webcam recording overlay
```

Każde polecenie również przyjmuje `--help`, niezależnie od tego, czy zapytasz całą grupę (`omarchy capture --help`), czy pojedyncze polecenie (`omarchy capture screenshot --help`).

### Otwieranie menu z terminala

Menu Ve OS również można obsługiwać za pomocą skryptów, co przydaje się przy własnych skrótach klawiszowych. `omarchy menu` otwiera je na poziomie głównym, a Ty możesz przejść bezpośrednio do dowolnego miejsca w drzewie, podając jego nazwę: `omarchy menu summon appearance.theme` przechodzi od razu do wyboru motywu, `omarchy menu toggle system` otwiera menu systemowe i zamyka je ponownie, jeśli jest już otwarte, a `omarchy menu close` je chowa.
