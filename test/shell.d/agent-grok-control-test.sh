#!/bin/bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/base-test.sh"

python3 - <<'PY'
import atexit
import json
import os
import queue
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

root = Path(os.environ['ROOT'])
with tempfile.TemporaryDirectory() as temp:
  home = Path(temp)
  binary = home / 'grok'
  binary.write_text('''#!'''+sys.executable+'''
import json, os, signal, sys, time
assert sys.argv[1:] == ['--permission-mode', 'default', 'agent', 'stdio']
mode = os.environ.get('FAKE_MODE', 'permission')
open(os.environ['FAKE_PID_FILE'], 'w').write(str(os.getpid()))
def emit(v): print(json.dumps(dict(jsonrpc='2.0', **v)), flush=True)
prompt = None
for raw in sys.stdin:
 v=json.loads(raw); m=v.get('method'); i=v.get('id')
 if m=='initialize':
  assert v['params']['clientCapabilities']=={}
  if mode=='slow': time.sleep(20)
  emit({'id':i,'result':{'protocolVersion':1,'agentCapabilities':{'loadSession':True},'authMethods':[{'id':'grok.com'}]}})
 elif m in ('session/new','session/load'):
  if mode=='auth': emit({'id':i,'error':{'code':-32000,'message':'SECRET_AUTH_VALUE','data':{'authRelated':True}}})
  else:
   emit({'id':i,'result':{'sessionId':'test-session'}})
   if mode=='blocked-write': time.sleep(30)
   if mode=='descendant':
    pid=os.fork()
    if pid: os._exit(0)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    open(os.environ['FAKE_PID_FILE']+'.descendant','w').write(str(os.getpid()))
    time.sleep(30)
    os._exit(0)
 elif m=='session/prompt':
  prompt=i
  emit({'method':'session/update','params':{'sessionId':'test-session','update':{'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':'Working'}}}})
  emit({'id':77,'method':'fs/write_text_file','params':{'path':'/do-not-write','content':'bad'}})
 elif i==77:
  assert v['error']['code']==-32601
  if mode=='closed-input': os.close(0)
  emit({'id':'approval','method':'session/request_permission','params':{'sessionId':'test-session','toolCall':{'title':'Write output.txt'},'options':[{'optionId':'allow','name':'Allow once','kind':'allow_once'},{'optionId':'deny','name':'Deny once','kind':'reject_once'},{'optionId':'always','name':'Always','kind':'allow_always'}]}})
  if mode=='closed-input': time.sleep(30)
 elif i=='approval':
  outcome=v['result']['outcome']
  if outcome.get('optionId')=='allow':
   open(os.path.join(os.getcwd(),'approved-result'),'w').write('approved')
  emit({'method':'session/update','params':{'sessionId':'test-session','update':{'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':' finished'}}}})
  emit({'id':prompt,'result':{'stopReason':'end_turn'}})
 elif m=='session/cancel':
  emit({'id':prompt,'result':{'stopReason':'cancelled'}})
''')
  binary.chmod(0o755)
  env = dict(os.environ, HOME=temp, XDG_STATE_HOME=str(home/'state'), PATH=temp+os.pathsep+os.environ['PATH'])
  class Client:
    def __init__(self, mode='permission'):
      self.pidfile = home / ('pid-' + str(time.monotonic_ns()))
      self.p = subprocess.Popen([sys.executable,str(root/'bin/omarchy-agent-grok-control')],env=dict(env,FAKE_MODE=mode,FAKE_PID_FILE=str(self.pidfile)),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
      def cleanup():
        if self.p.poll() is None:
          self.p.kill()
          self.p.wait()
        if self.pidfile.exists():
          try: os.killpg(int(self.pidfile.read_text()), signal.SIGKILL)
          except ProcessLookupError: pass
      atexit.register(cleanup)
      self.q=queue.Queue();self.seen=[]
      def read():
        for line in self.p.stdout: self.q.put(json.loads(line))
      threading.Thread(target=read,daemon=True).start()
      self.wait('status')
    def send(self, value):
      self.p.stdin.write(json.dumps(value)+'\n');self.p.stdin.flush()
    def wait(self,kind,status=None):
      while True:
        v=self.q.get(timeout=8);self.seen.append(v)
        if v['type']==kind and (status is None or v.get('status')==status): return v
    def start(self, **extra): self.send(dict({'action':'start','cwd':temp,'prompt':'Explain only'},**extra))
    def close(self):
      self.p.stdin.close();self.p.wait(timeout=5)
      assert self.p.returncode==0, self.p.stderr.read()
      assert 'SECRET_AUTH_VALUE' not in json.dumps(self.seen)
  c=Client()
  c.send(['invalid'])
  c.wait('error')
  c.send({'action':'permission','id':[], 'optionId':'allow'})
  c.wait('error')
  c.send({'action':'start','cwd':'bad\0path','prompt':'hi'})
  c.wait('error')
  c.send({'action':'start','cwd':'relative','prompt':'hi'})
  assert 'absolute' in c.wait('error')['message']
  c.start()
  approval=c.wait('permission')
  assert [o['optionId'] for o in approval['options']]==['allow','deny']
  assert not (home/'approved-result').exists()
  c.send({'action':'permission','id':'approval','optionId':'always'})
  c.wait('error')
  assert not (home/'approved-result').exists()
  c.send({'action':'permission','id':'approval','optionId':'deny'})
  done=c.wait('status','completed')
  assert done['text']=='Working finished'
  assert not (home/'approved-result').exists()
  elsewhere = home / 'elsewhere'
  elsewhere.mkdir()
  saved = dict(done)
  c.start(resume=True, cwd=str(elsewhere))
  assert 'workspace' in c.wait('error')['message']
  rejected = c.wait('status')
  assert rejected['sessionId']==saved['sessionId'] and rejected['text']==saved['text'] and rejected['cwd']==saved['cwd']
  c.start(resume=True)
  c.wait('permission')
  c.send({'action':'permission','id':'approval','optionId':'allow'})
  c.wait('status','completed')
  assert (home/'approved-result').read_text()=='approved'
  c.close()
  state=home/'state/omarchy/grok-control/last.json'
  assert stat.S_IMODE(state.stat().st_mode)==0o600
  c=Client('auth');c.start();c.wait('status','sign_in_required');c.close()
  assert 'SECRET_AUTH_VALUE' not in state.read_text()
  c=Client('slow');c.start();c.wait('status','starting');c.send({'action':'cancel'});c.wait('status','cancelled');c.close()
  c=Client();c.start();c.wait('permission');c.send({'action':'cancel'});c.wait('status','cancelled');c.close()
  c=Client();c.start();c.wait('permission');c.close()
  assert json.loads(state.read_text())['status']=='interrupted'
  c=Client();c.start();c.wait('permission');c.p.terminate();c.p.wait(timeout=5)
  assert json.loads(state.read_text())['status']=='interrupted'
  for action in ({'action':'cancel'}, {'action':'permission','id':'approval','optionId':'allow'}):
    c=Client('closed-input');c.start();c.wait('permission');c.send(action)
    c.wait('status','error')
    c.send({'action':'status'})
    assert c.wait('status')['status']=='error'
    c.close()
  c=Client('blocked-write');c.start(prompt='x'*65536);c.wait('status','running')
  started=time.monotonic();c.send({'action':'cancel'});c.wait('status','cancelled')
  assert time.monotonic()-started<5
  c.close()
  c=Client('descendant');c.start();c.wait('status','running')
  descendant=Path(str(c.pidfile)+'.descendant')
  deadline=time.monotonic()+3
  while not descendant.exists() and time.monotonic()<deadline: time.sleep(.01)
  assert descendant.exists()
  started=time.monotonic();c.send({'action':'cancel'});c.wait('status','cancelled')
  assert time.monotonic()-started<5
  c.close()
  ps=subprocess.run(['ps','-o','stat=','-p',descendant.read_text()],capture_output=True,text=True)
  assert not ps.stdout.strip() or ps.stdout.strip().startswith('Z'), ps.stdout
  print('ok - ACP task lifecycle, explicit decisions, unsupported capabilities, resume, private results, auth redaction and cancellation')
PY
