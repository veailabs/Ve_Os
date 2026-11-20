# Ve OS CLI

O Ve OS é normalmente controlado através das teclas de atalho e do menu Ve OS (`Super + Space`). Mas também o pode controlar através da CLI `omarchy`. Isto é particularmente útil quando tem um agente de IA a trabalhar consigo na personalização ou configuração.

A CLI tem acesso a todas as ferramentas internas que são utilizadas tanto através do menu como de outra forma. Pode ver tudo o que está disponível ao executar `omarchy` no terminal.

Tem um aspeto semelhante a este:

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

Para diagnóstico, execute `omarchy-debug --no-sudo --print` diretamente. Imprime o diagnóstico e escreve `/tmp/omarchy-debug.log` sem pedir sudo, incluindo em instalações de pacotes onde `omarchy debug` pode falhar com "Unknown Ve OS command".

E pode aprofundar cada grupo:

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

Todos os comandos também aceitam `--help`, quer pergunte a um grupo inteiro (`omarchy capture --help`) quer a um comando individual (`omarchy capture screenshot --help`).

### Abrir o menu a partir do terminal

O menu Ve OS também pode ser controlado por script, o que é útil para os seus próprios atalhos de teclado. `omarchy menu` abre-o na raiz, e pode saltar diretamente para qualquer ponto da árvore ao nomeá-lo: `omarchy menu summon appearance.theme` vai diretamente para o seletor de temas, `omarchy menu toggle system` abre o menu do sistema e volta a fechá-lo se já estiver aberto, e `omarchy menu close` fecha-o.
