# Commands

Executable Omarchy commands live here. `omarchy` routes their names and metadata into the user-facing CLI; task guidance lives in `agents/skills/command-metadata.md`.

Ve's Grok integration uses `omarchy-agent-grok-bot` for native desktop focus/open/close and an unsent primary-Bot draft. It invokes the packaged desktop executable directly so a Node helper on PATH cannot shadow it. Closing local windows does not stop cloud Bot work.

`omarchy-agent-grok-control` is the agents panel's JSON-lines bridge to Grok Build's ACP process. It uses the CLI's own sign-in, explicit default permissions and one-time permission decisions. It stores the last bounded task result in the user's state directory and never reads account tokens. Unsupported Bot cloud controls remain in the native app.
