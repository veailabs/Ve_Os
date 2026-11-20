#!/bin/bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/base-test.sh"
python3 - "$OMARCHY_PATH/bin/omarchy-agent-grok-bot" <<'PY'
import json, runpy, sys
from unittest.mock import patch, Mock
from urllib.parse import parse_qs, urlparse
m = runpy.run_path(sys.argv[1])
run = m['run']
with patch('subprocess.Popen') as launch:
  text = 'a & b? # / ; $(touch /tmp/should-not-exist)\nTelugu'
  assert run(['draft', text])['status'] == 'draft'
  argv = launch.call_args.args[0]
  assert argv[0] == '/usr/bin/grok-bot'
  assert parse_qs(urlparse(argv[1]).query)['text'] == [text]
  for args in (['draft', ''], ['draft', 'x'*1001], ['draft', 'a\x00b'], ['open','x'], ['close','x'], []):
    try: run(args)
    except ValueError: pass
    else: raise AssertionError(args)
  assert launch.call_count == 1
clients = [{'class':'grok-bot','address':'0xabc'}, {'class':'other','address':'0x123'}, {'class':'grok-bot','address':'invalid'}]
with patch('subprocess.run', return_value=Mock(stdout=json.dumps(clients))) as proc, patch('subprocess.Popen') as launch:
  assert run(['open'])['status'] == 'opened'
  assert proc.call_args.args[0][-1] == 'hl.dsp.focus({ window = "address:0xabc" })'
  assert not launch.called
  assert 'Cloud work continues' in run(['close'])['message']
  assert proc.call_args.args[0][-1] == 'hl.dsp.window.close({ window = "address:0xabc" })'
with patch('subprocess.run',return_value=Mock(stdout='[]')), patch('subprocess.Popen') as launch:
  run(['open'])
  assert launch.call_args.args[0] == ['/usr/bin/grok-bot']
print('Grok Bot launcher: draft encoding, validation, exact window targeting and PATH collision checks passed')
PY
