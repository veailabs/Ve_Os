#!/bin/bash

set -euo pipefail
source "$(dirname "$0")/base-test.sh"

test_tmp=$(mktemp -d)
trap 'rm -rf "$test_tmp"' EXIT
export HOME="$test_tmp/home with spaces" XDG_RUNTIME_DIR="$test_tmp/runtime"
export HERDR_APP_HOME="$HOME/herdr" T3CODE_HOME="$HOME/t3"
current="$HOME/.local/state/omarchy/current/theme"
mkdir -p "$current" "$XDG_RUNTIME_DIR" "$HERDR_APP_HOME" "$T3CODE_HOME/userdata" "$test_tmp/bin"
export APP_CALLS="$test_tmp/app-calls"
for command in herdr-app t3; do
  printf '#!/bin/bash\nprintf "%%s\\n" "$*" >>"$APP_CALLS"\nexit 1\n' >"$test_tmp/bin/$command"
  chmod +x "$test_tmp/bin/$command"
done
export PATH="$test_tmp/bin:$PATH"
jq -n '{background:"#112233",foreground:"#ffffff",selection:"#445566",ansi:[range(16) | "#112233"]}' >"$current/herdr-app.json"
printf '{"name":"Ve OS","accent":"#112233"}\n' >"$current/t3code.json"

for app in herdr t3; do
  if [[ $app == herdr ]]; then
    app_home="$HERDR_APP_HOME"
    helper=omarchy-theme-set-herdr-app
    source="$current/herdr-app.json"
  else
    app_home="$T3CODE_HOME/userdata"
    helper=omarchy-theme-set-t3code
    source="$current/t3code.json"
  fi
  mkdir -p "$app_home/themes"
  printf '{ "theme": "custom", "keep": true }\n' >"$app_home/settings.json"
  printf '{"name":"custom","accent":"#abcdef"}\n' >"$app_home/themes/custom.json"
  cp "$app_home/settings.json" "$test_tmp/settings-before"
  cp "$app_home/themes/custom.json" "$test_tmp/custom-before"

  bash "$ROOT/bin/$helper"
  [[ -f $app_home/themes/ve.json && ! -e $app_home/themes/omarchy.json ]] || fail "$app publishes Ve without creating a legacy alias"
  printf '{"stale":true}\n' >"$app_home/themes/omarchy.json"
  bash "$ROOT/bin/$helper"
  [[ $(jq -S . "$app_home/themes/ve.json") == "$(jq -S . "$source")" ]] || fail "$app publishes the current palette"
  cmp -s "$app_home/themes/ve.json" "$app_home/themes/omarchy.json" || fail "$app refreshes a present legacy alias to match Ve"
  cmp -s "$app_home/settings.json" "$test_tmp/settings-before" || fail "$app leaves custom selection settings byte-identical"
  cmp -s "$app_home/themes/custom.json" "$test_tmp/custom-before" || fail "$app leaves a custom palette byte-identical"
  [[ ! -e $APP_CALLS ]] || fail "$app palette refresh never invokes theme selection"
  pass "$app refreshes existing legacy palettes without creating aliases or changing custom selections"
done
