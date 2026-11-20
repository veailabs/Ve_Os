# Ve OS CLI

Ve OS juhtimine käib tavaliselt kiirklahvide ja Ve OS menüü (`Super + Space`) kaudu. Aga seda saab juhtida ka `omarchy` CLI kaudu. See on eriti abiks siis, kui AI agent töötab koos sinuga kohanduste või seadistuste kallal.

CLI kaudu on ligipääs kõigile sisemistele tööriistadele, mida kasutatakse nii menüü kaudu kui ka muul viisil. Kõike, mis on saadaval, näed, kui käivitad terminalis `omarchy`.

See näeb välja umbes selline:

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

Diagnostikaks käivita otse `omarchy-debug --no-sudo --print`. See prindib diagnostika ja kirjutab `/tmp/omarchy-debug.log` ilma sudo parooli küsimata, ka pakettide installide korral, kus `omarchy debug` võib nurjuda veaga "Unknown Ve OS command".

Ja igasse rühma saad süveneda:

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

Iga käsk toetab ka `--help` valikut, olenemata sellest, kas küsid tervet rühma (`omarchy capture --help`) või ühte käsku (`omarchy capture screenshot --help`).

### Menüü avamine terminalist

Ve OS menüü on samuti skriptitav, mis on mugav oma klahviseadete jaoks. `omarchy menu` avab selle juurtasemel ja saad hüpata otse ükskõik millisesse kohta puus, kui selle nimetad: `omarchy menu summon appearance.theme` viib otse teemavalijasse, `omarchy menu toggle system` avab süsteemimenüü ja sulgeb selle uuesti, kui see on juba avatud, ning `omarchy menu close` peidab selle.
