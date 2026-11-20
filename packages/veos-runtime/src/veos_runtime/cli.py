import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from .providers import PROVIDERS, probe, read_config, save_config
from .server import Application, make_server
from .store import Refused, Store
from .worker import Worker


def data_root():
  return (
    Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "veos"
  )


def main(argv=None):
  os.umask(0o077)
  parser = argparse.ArgumentParser(description="VeOS personal subscription runtime")
  parser.add_argument("--state-dir", type=Path, default=data_root())
  sub = parser.add_subparsers(dest="command", required=True)
  sub.add_parser("init")
  serve = sub.add_parser("serve")
  serve.add_argument("--port", type=int, default=8764)
  serve.add_argument("--no-worker", action="store_true")
  sub.add_parser("open")
  sub.add_parser("status")
  sub.add_parser("install-desktop")
  incoming = sub.add_parser("incoming-mcp")
  incoming.add_argument("--project-id", required=True)
  incoming.add_argument(
    "--provider", choices=["codex", "claude", "hermes"], required=True
  )
  provider = sub.add_parser("provider")
  provider.add_argument("name", choices=PROVIDERS)
  provider.add_argument(
    "action", choices=["check", "enable", "disable", "login", "stop"]
  )
  provider.add_argument("--model")
  provider.add_argument("--confirm-no-paid-extra-usage", action="store_true")
  export = sub.add_parser("export")
  export.add_argument("project_id")
  args = parser.parse_args(argv)
  try:
    store = Store(args.state_dir)
    config = read_config(store.root)
    if args.command == "incoming-mcp":
      import urllib.request
      from urllib.parse import urlsplit

      from .harness import serve_stdio

      info = json.loads((store.root / "connection.json").read_text())
      parsed = urlsplit(info["url"])
      if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
      ):
        raise Refused("Only the local running VeOS service is supported")
      body = json.dumps({"pid": args.project_id, "provider": args.provider}).encode()
      request = urllib.request.Request(
        info["url"] + "/api/harness/connect",
        body,
        {
          "Authorization": "Bearer " + info["token"],
          "Content-Type": "application/json",
        },
        method="POST",
      )
      with urllib.request.urlopen(request, timeout=10) as response:
        connection = json.loads(response.read(20000))
      try:
        serve_stdio(info["url"], connection["token"])
      finally:
        request = urllib.request.Request(
          info["url"] + "/api/harness/disconnect",
          b"{}",
          {
            "Authorization": "Bearer " + connection["token"],
            "Content-Type": "application/json",
          },
          method="POST",
        )
        try:
          urllib.request.urlopen(request, timeout=5).close()
        except OSError:
          pass
    elif args.command == "init":
      print(
        f"Initialized {store.root}. Providers are disabled until subscription billing is verified."
      )
    elif args.command == "install-desktop":
      from .desktop import install

      for path in install():
        print(path)
      print(
        "Assets installed. Start the user service using the README steps for this operating system."
      )
    elif args.command == "provider":
      if args.action == "check":
        print(json.dumps(probe(args.name, config)))
      elif args.action in {"login", "stop"}:
        from .hermes import PROVIDER, configure, environment, qualified, stop
        from .providers import clean_env

        if args.name != "hermes":
          raise Refused("Use this provider's native sign-in command")
        executable = shutil.which("hermes")
        if not executable:
          raise Refused("Install the qualified Hermes revision first")
        env = environment(store.root, clean_env())
        if not qualified(executable, env):
          raise Refused("Install the qualified Hermes revision listed in the README")
        import fcntl

        with open(store.root / "worker.lock", "a+") as lock:
          try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
          except BlockingIOError:
            raise Refused(
              "Stop the VeOS worker before managing its Hermes profile"
            ) from None
          if args.action == "stop":
            if not stop(executable, env):
              raise Refused("The owned Hermes gateway did not confirm it stopped")
          else:
            configure(
              store.root, args.model or config["providers"]["hermes"].get("model", "")
            )
            subprocess.run(
              [executable, "auth", "add", PROVIDER, "--type", "oauth"],
              env=env,
              check=True,
            )

      else:
        if args.action == "enable" and not args.confirm_no_paid_extra_usage:
          raise Refused(
            "First disable paid extra usage in the provider account, then pass --confirm-no-paid-extra-usage"
          )
        if args.name == "antigravity" and args.action == "enable":
          raise Refused(
            "Antigravity subscription authentication probe is not qualified; it remains disabled"
          )
        setting = config["providers"][args.name]
        model = args.model or setting.get("model")
        if model and (
          len(model) > 160 or model.startswith("-") or any(ord(c) < 32 for c in model)
        ):
          raise Refused("Invalid model")
        if args.name == "hermes" and args.action == "enable" and not model:
          raise Refused("Choose the ChatGPT subscription model with --model")
        config["providers"][args.name] = {
          "enabled": args.action == "enable",
          "extra_usage_disabled_confirmed": args.confirm_no_paid_extra_usage,
          **({"model": model} if model else {}),
        }
        save_config(store.root, config)
        print(
          f"{args.name}: {args.action}d. Every task still verifies native subscription sign-in."
        )
    elif args.command == "export":
      print(json.dumps(store.snapshot(args.project_id), indent=2))
    elif args.command == "status":
      state = store.snapshot()
      counts = {}
      for task in state["tasks"]:
        counts[task["state"]] = counts.get(task["state"], 0) + 1
      print(
        json.dumps(
          {
            "projects": len(state["projects"]),
            "tasks": counts,
            "billing": config["billing"],
          }
        )
      )
    elif args.command == "open":
      if not (store.root / "connection.json").exists():
        raise Refused(
          "VeOS is not running. Start 'veos serve' or its login service first."
        )
      info = json.loads((store.root / "connection.json").read_text())
      webbrowser.open(info["url"] + "/#" + info["token"])
    elif args.command == "serve":
      app = Application(store)
      server = make_server(app, args.port)
      worker = Worker(store, app.capabilities, app.base_url)
      if not args.no_worker:
        worker.start()
      connection = store.root / "connection.json"
      connection.write_text(json.dumps({"url": app.base_url, "token": app.token}))
      os.chmod(connection, 0o600)

      def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()

      signal.signal(signal.SIGTERM, stop)
      signal.signal(signal.SIGINT, stop)
      print(
        f"VeOS listening on {app.base_url}. Run 'veos open' to open your private workspace.",
        flush=True,
      )
      try:
        server.serve_forever(poll_interval=0.2)
      finally:
        worker.close()
        app.browser.close()
        server.server_close()
        connection.unlink(missing_ok=True)
  except (Refused, OSError, ValueError) as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(2)
