"use strict";

// Voice owns audio and delegates work to the existing runtime. It never executes
// model-provided code or carries the runtime administrator token to OpenAI.
window.createVeOSVoice = function ({ api, onStatus, onTask, onTranscript, audioOutput }) {
  let pc, channel, stream, connection, timer, projectId;
  let generation = 0;
  let responding = false;
  const audio = audioOutput || new Audio();
  audio.autoplay = true;
  const calls = new Map();
  const send = value => {
    if (channel?.readyState === "open") channel.send(JSON.stringify(value));
  };
  async function harness(name, args, binding) {
    const response = await fetch("/api/harness/tools", {
      method: "POST", headers: {Authorization: `Bearer ${binding.token}`, "Content-Type": "application/json"},
      body: JSON.stringify({name, arguments: args}),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || "Local task was not accepted");
    return body;
  }
  async function stop() {
    generation++;
    clearTimeout(timer);
    stream?.getTracks().forEach(track => track.stop());
    stream = null;
    channel?.close();
    pc?.close();
    pc = channel = null;
    audio.srcObject = null;
    audio.hidden = true;
    projectId = null;
    responding = false;
    const previous = connection;
    connection = null;
    calls.clear();
    onStatus("Voice disconnected. Existing tasks remain in the work queue.", false);
    if (previous) {
      try {
        await fetch("/api/harness/disconnect", {method: "POST", headers: {Authorization: `Bearer ${previous.token}`, "Content-Type":"application/json"}, body:"{}"});
      } catch { /* The connection also expires server-side. */ }
    }
  }
  async function start(pid, provider) {
    await stop();
    const current = ++generation;
    projectId = pid;
    onStatus("Requesting microphone access…", true);
    try {
      const media = await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true}});
      if (current !== generation) {
        media.getTracks().forEach(track => track.stop());
        return;
      }
      stream = media;
      onStatus("Connecting configured API voice…", true);
      const session = await api("/api/voice/start", {pid, provider});
      if (current !== generation) {
        await fetch("/api/harness/disconnect", {method:"POST",headers:{Authorization:`Bearer ${session.harness.token}`,"Content-Type":"application/json"},body:"{}"});
        return;
      }
      connection = session.harness;
      const peer = new RTCPeerConnection();
      pc = peer;
      peer.ontrack = event => {
        if (current !== generation) return;
        audio.hidden = false;
        audio.srcObject = event.streams[0];
        audio.play().catch(() => onStatus("Press play below to hear the connected voice.", true));
      };
      stream.getTracks().forEach(track => peer.addTrack(track, stream));
      channel = peer.createDataChannel("oai-events");
      channel.onopen = () => {
        if (current !== generation) return;
        send({type:"session.update",session:{type:"realtime",tools:connection.tools.map(t=>({type:"function",name:t.name,description:t.description,parameters:t.inputSchema})),
          instructions:"Help the user through the local harness tools. Queued, running, blocked or awaiting review is not completion. Keep the same request key when retrying the same task. Use returned status and verified receipts to describe progress. The user can stop speech independently of cancelling work."}});
        onStatus("Listening · tasks use this project's selected local harness", true);
      };
      channel.onmessage = async event => {
        if (current !== generation || event.data.length > 100000) return;
        let item;
        try { item = JSON.parse(event.data); } catch { return; }
        if (!item || typeof item !== "object" || Array.isArray(item)) return;
        if (item.type === "response.created") responding = true;
        if (item.type === "response.done") responding = false;
        if (item.type === "conversation.item.input_audio_transcription.completed") onTranscript("You", item.transcript || "");
        if (item.type === "response.output_audio_transcript.done") onTranscript("Ve", item.transcript || "");
        if (item.type === "error") onStatus("Voice reported an error. Check the local task before trying again.", true);
        if (item.type !== "response.function_call_arguments.done") return;
        const binding = connection;
        if (!binding || !binding.tools.some(tool=>tool.name===item.name) || typeof item.call_id !== "string" || typeof item.arguments !== "string" || item.arguments.length>12000) return;
        const signature = JSON.stringify([item.name,item.arguments]);
        let pending = calls.get(item.call_id);
        if (pending && pending.signature !== signature) {
          onStatus("Voice reused a call ID with different arguments; no new task was dispatched.", true);
          return;
        }
        if (!pending) {
          if (calls.size >= 100) { await stop(); return; }
          pending = {signature, result:Promise.resolve().then(()=>harness(item.name,JSON.parse(item.arguments),binding))
            .catch(error=>({error:error.message,completed:false}))};
          calls.set(item.call_id,pending);
        }
        const result = await pending.result;
        if (current !== generation) return;
        if (pending.delivered) return;
        pending.delivered = true;
        if (result.task_id) onTask(result.task_id);
        send({type:"conversation.item.create",item:{type:"function_call_output",call_id:item.call_id,output:JSON.stringify(result)}});
        send({type:"response.create"});
      };
      peer.onconnectionstatechange = () => {
        if (current !== generation) return;
        if (["failed","disconnected","closed"].includes(peer.connectionState)) stop();
      };
      const offer = await peer.createOffer();
      if (current !== generation) return;
      await peer.setLocalDescription(offer);
      if (current !== generation) return;
      const response = await fetch(session.connectUrl, {
        method:"POST",headers:{Authorization:`Bearer ${session.clientSecret.value}`,"Content-Type":"application/sdp"},
        body:offer.sdp,signal:AbortSignal.timeout(15000),redirect:"error",
      });
      session.clientSecret = null;
      if (!response.ok) throw new Error("Voice connection failed; no automatic retry was made");
      const sdp = await response.text();
      if (sdp.length>100000) throw new Error("Voice answer exceeded its limit");
      if (current !== generation) return;
      await peer.setRemoteDescription({type:"answer",sdp});
      if (current !== generation) return;
      timer = setTimeout(stop, Math.min(session.maxSessionSeconds,600)*1000);
    } catch (error) {
      if (current !== generation) return;
      await stop();
      onStatus(error.message || "Voice could not connect", false);
    }
  }
  function stopSpeaking() {
    if (responding) send({type:"response.cancel"});
    send({type:"output_audio_buffer.clear"});
    onStatus("Speech stopped. Local tasks continue until you cancel or pause them.", true);
  }
  return {start,stop,stopSpeaking,get projectId(){return projectId;}};
};
