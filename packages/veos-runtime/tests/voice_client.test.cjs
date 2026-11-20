const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/veos_runtime/web/voice.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

function fixture({ mint, media } = {}) {
  const requests = [], peers = [], messages = [], statuses = [], tasks = [], timers = new Set();
  const track = {stopped: false, stop() { this.stopped = true; }};
  class Peer {
    constructor() { peers.push(this); this.connectionState = 'new'; }
    addTrack() {}
    createDataChannel() {
      this.channel = {readyState:'open', send: data => messages.push(JSON.parse(data)), close() {this.readyState='closed';}};
      return this.channel;
    }
    async createOffer() { return {sdp:'offer'}; }
    async setLocalDescription() {}
    async setRemoteDescription() {this.connectionState='connected';this.channel.onopen();}
    close() {this.connectionState='closed';this.onconnectionstatechange?.();}
  }
  const session = () => ({connectUrl:'https://api.openai.com/v1/realtime/calls', clientSecret:{value:'ephemeral-fixture'},maxSessionSeconds:600,
    harness:{token:'scoped-fixture', tools:[{name:'run_local_agent',inputSchema:{type:'object'}}]}});
  const context = vm.createContext({window:{},navigator:{mediaDevices:{getUserMedia: media || (async()=>({getTracks:()=>[track]}))}},
    Audio:class {play(){return Promise.resolve();}},RTCPeerConnection:Peer,AbortSignal,
    setTimeout: callback=>{timers.add(callback);return callback;},clearTimeout: callback=>timers.delete(callback),
    fetch:async(url, options)=>{
      requests.push({url,options});
      if(url.startsWith('https:'))return {ok:true,text:async()=>'answer'};
      return {ok:true,json:async()=>url.endsWith('/tools')?{task_id:'task-1',state:'queued',external_action_confirmed:false}:{disconnected:true}};
    }});
  vm.runInContext(source, context);
  const client = context.window.createVeOSVoice({api:mint || (async()=>session()),onStatus:(...args)=>statuses.push(args),onTask:id=>tasks.push(id),onTranscript:()=>{}});
  return {client,peers,messages,requests,statuses,tasks,track,timers,session};
}

test('one dispatch and one correlated output for duplicate voice tool events', async()=>{
  const f=fixture(); await f.client.start('project','codex');
  const channel=f.peers[0].channel;
  const event={data:JSON.stringify({type:'response.function_call_arguments.done',name:'run_local_agent',call_id:'call-1',arguments:'{"task":"Read calendar","request_key":"one"}'})};
  await Promise.all([channel.onmessage(event),channel.onmessage(event)]);
  assert.equal(f.requests.filter(r=>r.url==='/api/harness/tools').length,1);
  const output=f.messages.filter(m=>m.type==='conversation.item.create');
  assert.equal(output.length,1);assert.equal(output[0].item.call_id,'call-1');
  assert.equal(JSON.parse(output[0].item.output).state,'queued');
  assert.equal(JSON.parse(output[0].item.output).external_action_confirmed,false);
  await channel.onmessage({data:event.data.replace('Read calendar','Delete calendar')});
  assert.equal(f.requests.filter(r=>r.url==='/api/harness/tools').length,1);
  assert.ok(f.statuses.some(([text])=>text.includes('different arguments')));
  assert.equal(f.requests.find(r=>r.url.startsWith('https:')).options.headers.Authorization,'Bearer ephemeral-fixture');
  assert.equal(f.requests.find(r=>r.url==='/api/harness/tools').options.headers.Authorization,'Bearer scoped-fixture');
});

test('stop speaking clears audio without cancelling local work; disconnect closes microphone', async()=>{
  const f=fixture();await f.client.start('project','claude');f.client.stopSpeaking();
  assert.ok(f.messages.some(m=>m.type==='response.cancel'));
  assert.ok(f.messages.some(m=>m.type==='output_audio_buffer.clear'));
  assert.equal(f.requests.filter(r=>r.url==='/api/harness/tools').length,0);
  await f.client.stop();
  assert.equal(f.track.stopped,true);assert.equal(f.peers[0].connectionState,'closed');
  assert.equal(f.requests.filter(r=>r.url==='/api/harness/disconnect').length,1);
  assert.equal(f.timers.size,0);assert.equal(f.client.projectId,null);
});

test('disconnect while minting revokes late grant without opening WebRTC', async()=>{
  let resolve;const f=fixture({mint:()=>new Promise(r=>resolve=r)});
  const start=f.client.start('project','codex');await tick();
  await f.client.stop();resolve(f.session());await start;
  assert.equal(f.peers.length,0);assert.equal(f.track.stopped,true);
  assert.equal(f.requests.filter(r=>r.url==='/api/harness/disconnect').length,1);
});

test('disconnect while microphone prompt is pending releases late tracks without minting', async()=>{
  let resolve, minted=false;
  const f=fixture({media:()=>new Promise(r=>resolve=r),mint:async()=>{minted=true;}});
  const start=f.client.start('project','codex');await tick();await f.client.stop();
  resolve({getTracks:()=>[f.track]});await start;
  assert.equal(f.track.stopped,true);assert.equal(minted,false);assert.equal(f.requests.length,0);
});
