# Ve OS CLI

Ve OS معمولاً از طریق کلیدهای میانبر و منوی Ve OS (`Super + Space`) کنترل می‌شود. اما می‌توانید آن را از طریق CLI به نام `omarchy` هم کنترل کنید. این قابلیت به‌ویژه زمانی مفید است که یک ایجنت هوش مصنوعی را برای کار روی سفارشی‌سازی یا پیکربندی در کنار خود داشته باشید.

این CLI به تمام ابزارهای داخلی‌ای دسترسی دارد که هم از طریق منو و هم به روش‌های دیگر استفاده می‌شوند. با اجرای `omarchy` در ترمینال می‌توانید هر چیزی را که در دسترس است ببینید.

ظاهرش چیزی شبیه به این است:

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

برای عیب‌یابی، `omarchy-debug --no-sudo --print` را مستقیماً اجرا کنید. این دستور اطلاعات عیب‌یابی را چاپ می‌کند و بدون درخواست sudo، فایل `/tmp/omarchy-debug.log` را می‌نویسد، از جمله روی نصب‌های بسته‌ای که در آن‌ها `omarchy debug` می‌تواند با خطای "Unknown Ve OS command" شکست بخورد.

و می‌توانید در هر گروه عمیق‌تر شوید:

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

هر فرمان `--help` را هم می‌پذیرد، چه از یک گروه کامل بپرسید (`omarchy capture --help`) و چه از یک فرمان واحد (`omarchy capture screenshot --help`).

### باز کردن منو از ترمینال

منوی Ve OS هم قابل اسکریپت‌نویسی است، که برای میانبرهای صفحه‌کلید خودتان مفید است. `omarchy menu` آن را در ریشه باز می‌کند، و می‌توانید با نام بردن از هر نقطه‌ای از درخت، مستقیماً به همان‌جا بروید: `omarchy menu summon appearance.theme` مستقیماً به انتخاب‌گر تم می‌رود، `omarchy menu toggle system` منوی سیستم را باز می‌کند و اگر از قبل باز باشد دوباره آن را می‌بندد، و `omarchy menu close` آن را کنار می‌گذارد.
