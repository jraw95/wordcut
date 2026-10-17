'use strict';
const $=id=>document.getElementById(id), clamp=(x,a,b)=>Math.max(a,Math.min(b,x));
let job=null, peaks=null, audioBuffer=null, audioContext=null, source=null, audioClock=0, audioOffset=0, audioSpeed=1;
let cutIn=0,cutOut=0,viewStart=0,viewSpan=1,focusEdge='in',loadingId=null,activeId=null,polling=false,raf=0;
let video=$('video'),uploadRequest=null;
function format(t){const ms=Math.round(Math.max(0,t)*1000),h=Math.floor(ms/3600000),m=Math.floor(ms/60000)%60,s=Math.floor(ms/1000)%60;return (h?String(h).padStart(2,'0')+':':'')+String(m).padStart(2,'0')+':'+String(s).padStart(2,'0')+'.'+String(ms%1000).padStart(3,'0')}
function parse(t){if(!/^\d+(?::\d{1,2}){0,2}(?:\.\d{1,3})?$/.test(t.trim()))throw Error('Use M:SS.mmm or H:MM:SS.mmm.');const p=t.trim().split(':').map(Number);if(p.slice(1).some(x=>x>=60))throw Error('Seconds and minutes after a colon must be below 60.');return p.reduce((a,b)=>a*60+b,0)}
function message(text=''){$('message').textContent=text}
function node(tag,text,cls){const n=document.createElement(tag);n.textContent=text;if(cls)n.className=cls;return n}
async function api(path,data){const options=data===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json','X-Wordcut':'1'},body:JSON.stringify(data)};const r=await fetch(path,options),d=await r.json();if(!r.ok)throw Error(d.error||'Request failed.');return d}
function setBusy(active){activeId=active;$('prepare').disabled=!!active;$('upload-button').disabled=!!active;$('export').disabled=!!active||!audioBuffer;transcriptButtons();}
function progress(j){const running=['uploading','preparing','exporting'].includes(j.state)||j.transcript_state==='loading';$('progress-card').hidden=!running;$('phase').textContent=j.transcript_state==='loading'?'Extracting YouTube captions…':j.phase;$('progress').value=j.progress||0;$('cancel').dataset.id=j.id;if(j.error)message(j.error)}
async function poll(){if(polling)return;polling=true;try{const d=await api('/api/jobs');setBusy(d.active);renderRecent(d.jobs);if(uploadRequest){setBusy('upload');return}const selected=job?.id||localStorage.getItem('wordcut-job')||d.jobs[0]?.id;const current=d.jobs.find(j=>j.id===selected);if(current){progress(current);if(current.state==='ready'&&current.duration){if(!job||job.id!==current.id||!audioBuffer){await load(current)}else{job=current;downloadState();refreshTranscript(current)}}else if(!job){job=current;localStorage.setItem('wordcut-job',current.id)}}}catch(e){message('Connection interrupted. '+e.message)}finally{polling=false;setTimeout(poll,2000)}}
function renderRecent(jobs){$('recent').replaceChildren();if(!jobs.length){$('recent').append(node('p','Paste a YouTube link or upload a file above.','muted'));return}for(const j of jobs.slice(0,8)){const row=node('div','','recent'),info=node('div'),button=node('button',j.state==='ready'?'Open':'View');info.append(node('strong',j.title),node('small',format(j.requested_start)+' → '+format(j.requested_end)+' · '+j.phase));button.onclick=async()=>{stop();job=null;audioBuffer=null;localStorage.setItem('wordcut-job',j.id);$('editor').hidden=true;progress(j);message(j.error||'');if(j.state==='ready')await load(j);else job=j};row.append(info,button);$('recent').append(row)}}
async function load(j){if(loadingId===j.id)return;loadingId=j.id;stop();job=j;audioBuffer=null;peaks=null;$('editor').hidden=false;document.body.classList.add('editing');$('source-details').open=false;$('title').textContent=j.title;$('context-info').textContent='Editing window '+format(j.origin)+' – '+format(j.origin+j.duration)+' · source duration '+format(j.source_duration);$('url').value=j.url||'';$('start').value=shortTime(j.requested_start);$('end').value=shortTime(j.requested_end);sourceSpan.refresh();video=j.audio_only?$('audio-player'):$('video');$('video').hidden=!!j.audio_only;$('audio-source').hidden=!j.audio_only;bindPlayer();video.src=`/media/${j.id}/${j.preview||'preview.mp4'}`;video.load();cutIn=j.cut_start;cutOut=j.cut_end;viewStart=0;viewSpan=j.duration;$('zoom').value=0;$('fade').checked=j.fade!==false;applyOptions(j);refreshTranscript(j);message('Loading waveform and audio preview…');
 try{const [waveResponse,audioResponse]=await Promise.all([fetch(`/media/${j.id}/waveform.bin`),fetch(`/media/${j.id}/audio.wav`)]);if(!waveResponse.ok||!audioResponse.ok)throw Error('Preview files are unavailable. Prepare the clip again.');const [raw,audio]=await Promise.all([waveResponse.arrayBuffer(),audioResponse.arrayBuffer()]);audioContext ||= new (window.AudioContext||window.webkitAudioContext)();const decoded=await audioContext.decodeAudioData(audio);if(job?.id!==j.id)return;peaks=new Int16Array(raw);audioBuffer=decoded;localStorage.setItem('wordcut-job',j.id);message(j.error||'');sync();setBusy(activeId);downloadState();}catch(e){message(e.message)}finally{loadingId=null}}

function getOptions(){
 const mode=$('output-mode').value,fmt=$(mode==='video'?'video-format':'audio-format').value;
 let name=$('output-name').value.replace(/[^A-Za-z0-9 _.-]/g,'_').replace(/^[ .]+|[ .]+$/g,'').slice(0,80).replace(/\.(mp4|mov|mkv|webm|mp3|m4a|wav|flac|ogg|opus)$/i,'')||'wordcut-clip';
 return {mode,format:fmt,quality:$('quality').value,height:$('height').value,bitrate:Number($('bitrate').value),sample_rate:Number($('sample-rate').value),channels:$('channels').value,volume_db:Number($('volume-db').value),fade_in_ms:$('fade').checked?Number($('fade-in').value):0,fade_out_ms:$('fade').checked?Number($('fade-out').value):0,include_audio:mode==='audio'||$('include-audio').value==='yes',name};
}
function applyOptions(j){
 const o=j.export_options||{};
 $('output-mode').value=o.mode||(j.audio_only?'audio':'video');$('output-mode').querySelector('[value="video"]').disabled=!!j.audio_only;
 $('video-format').value=o.mode==='video'?o.format:'mp4';$('audio-format').value=o.mode==='audio'?o.format:'mp3';
 for(const [id,key,fallback] of [['quality','quality','high'],['height','height','source'],['bitrate','bitrate',192],['sample-rate','sample_rate',48000],['channels','channels','source'],['volume-db','volume_db',0],['fade-in','fade_in_ms',3],['fade-out','fade_out_ms',3]])$(id).value=o[key]??fallback;
 $('include-audio').value=o.include_audio===false?'no':'yes';$('output-name').value=o.name||(j.title||'wordcut-clip').replace(/\.[a-z0-9]{2,4}$/i,'').replace(/[^A-Za-z0-9 _.-]/g,'_').slice(0,80);
 if(j.export_options)$('fade').checked=!!(o.fade_in_ms||o.fade_out_ms);formatUI();
}
function formatUI(){
 const isVideo=$('output-mode').value==='video',fmt=$(isVideo?'video-format':'audio-format').value;
 $('video-format-label').hidden=!isVideo;$('audio-format-label').hidden=isVideo;
 document.querySelectorAll('[data-video-setting]').forEach(e=>e.hidden=!isVideo);
 document.querySelector('[data-quality-setting]').hidden=!isVideo&&fmt!=='ogg';
 const audioEnabled=!isVideo||$('include-audio').value==='yes';
 document.querySelectorAll('[data-audio-setting]').forEach(e=>e.querySelectorAll('input,select').forEach(i=>i.disabled=!audioEnabled));
 const lossless=!isVideo&&['wav','flac','ogg'].includes(fmt);$('bitrate').disabled=!audioEnabled||lossless;$('bitrate-note').textContent=fmt==='ogg'?'Vorbis uses variable bitrate selected by encoding quality.':lossless?'Lossless / PCM: bitrate is determined by the audio.':'';
 for(const option of $('sample-rate').options)option.disabled=(fmt==='opus'&&!['16000','48000'].includes(option.value))||(isVideo&&fmt==='webm'&&option.value!=='48000');
 if($('sample-rate').selectedOptions[0]?.disabled)$('sample-rate').value='48000';
 for(const option of $('bitrate').options)option.disabled=fmt==='mp3'&&Number($('sample-rate').value)<32000&&Number(option.value)>128;
 if($('bitrate').selectedOptions[0]?.disabled)$('bitrate').value='128';
 $('fade-in').disabled=!audioEnabled||!$('fade').checked;$('fade-out').disabled=!audioEnabled||!$('fade').checked;
 $('export').textContent='Export '+fmt.toUpperCase()+' ↓';
 const descriptions={mp4:'MP4 · H.264 + AAC',mov:'QuickTime MOV · H.264 + AAC',mkv:'Matroska MKV · H.264 + AAC',webm:'WebM · VP9 + Opus',mp3:'MP3 · MPEG audio',m4a:'M4A · AAC audio',wav:'WAV · uncompressed 16-bit PCM',flac:'FLAC · lossless audio',ogg:'Ogg · Vorbis audio',opus:'Opus · efficient audio'};
 $('format-description').textContent=descriptions[fmt]+(isVideo&&!audioEnabled?' · audio omitted':'');
 $('export-summary').textContent=isVideo?'Video keeps the source aspect ratio and is never upscaled. Maximum resolution is the prepared preview (720p).':'Audio-only export uses the same IN/OUT selection. Choose WAV or FLAC for further editing; MP3 or M4A for easy sharing.';
 downloadState();
}
function downloadState(){
 const current=getOptions();let sameSettings=false;
 if(job?.export_options)sameSettings=JSON.stringify(current)===JSON.stringify(job.export_options);
 else sameSettings=current.mode==='video'&&current.format==='mp4'&&current.volume_db===0&&current.height==='source'&&current.quality==='high'&&current.include_audio&&current.fade_in_ms===(job?.fade?3:0)&&current.fade_out_ms===(job?.fade?3:0);
 const match=job?.export&&Math.abs(job.cut_start-cutIn)<.0005&&Math.abs(job.cut_end-cutOut)<.0005&&sameSettings;
 $('download-box').hidden=!match;
 if(match){$('download').href=`/media/${job.id}/${job.export}`;$('download').download=job.download_name||'';$('download').textContent='Download '+job.export.split('.').pop().toUpperCase()+' ↓';$('export-info').textContent=(job.download_name||'Your clip')+' · '+(cutOut-cutIn).toFixed(3)+' seconds'+(job.export_bytes?' · '+(job.export_bytes/1024/1024).toFixed(2)+' MiB':'')}
}


function sync(){if(!job?.duration)return;$('cut-in').value=format(job.origin+cutIn);$('cut-out').value=format(job.origin+cutOut);$('length').textContent=(cutOut-cutIn).toFixed(3)+' seconds';for(const [id,value] of [['in-marker',cutIn],['out-marker',cutOut]]){const m=$(id);m.setAttribute('aria-valuemin',job.origin.toFixed(3));m.setAttribute('aria-valuemax',(job.origin+job.duration).toFixed(3));m.setAttribute('aria-valuenow',(job.origin+value).toFixed(3));m.setAttribute('aria-valuetext',format(job.origin+value))}downloadState();renderTranscript();draw();editSpan.refresh()}
function position(edge,value){if(!job?.duration)return;stop();focusEdge=edge;value=Math.round(value*1000)/1000;if(edge==='in')cutIn=clamp(value,0,cutOut-.02);else cutOut=clamp(value,cutIn+.02,job.duration);sync()}
function draw(){if(!job?.duration)return;viewSpan=clamp(viewSpan,Math.min(.1,job.duration),job.duration);viewStart=clamp(viewStart,0,Math.max(0,job.duration-viewSpan));const canvas=$('wave'),w=$('wave-wrap').clientWidth,h=226,dpr=window.devicePixelRatio||1;canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);const y=112,amp=76,gain=Number($('gain').value);ctx.strokeStyle='#262a31';ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(w,y);ctx.stroke();
 const target=viewSpan/6,steps=[.001,.002,.005,.01,.02,.05,.1,.2,.5,1,2,5,10,20,30,60,120,300],step=steps.find(v=>v>=target)||600;ctx.font='10px ui-monospace,Menlo,monospace';for(let t=Math.ceil((job.origin+viewStart)/step)*step;t<=job.origin+viewStart+viewSpan;t+=step){const x=(t-job.origin-viewStart)/viewSpan*w;ctx.strokeStyle='#1a1d22';ctx.beginPath();ctx.moveTo(x,34);ctx.lineTo(x,195);ctx.stroke();ctx.fillStyle='#6b717b';ctx.fillText(format(t),clamp(x+3,0,w-72),216)}
 if(peaks){ctx.lineWidth=1;for(let x=0;x<w;x++){const a=Math.max(0,Math.floor((viewStart+x/w*viewSpan)*1000)),b=Math.min(peaks.length/2,Math.max(a+1,Math.ceil((viewStart+(x+1)/w*viewSpan)*1000)));let low=0,high=0;for(let k=a;k<b;k++){low=Math.min(low,peaks[k*2]);high=Math.max(high,peaks[k*2+1])}const t=viewStart+x/w*viewSpan;ctx.strokeStyle=t>=cutIn&&t<=cutOut?'#6fe3b4':'#3a4049';ctx.beginPath();ctx.moveTo(x,y-clamp(high/32768*gain,0,1)*amp);ctx.lineTo(x,y-clamp(low/32768*gain,-1,0)*amp);ctx.stroke()}}
 const left=(cutIn-viewStart)/viewSpan*w,right=(cutOut-viewStart)/viewSpan*w;for(const [id,x]of[['in-marker',left],['out-marker',right]]){$(id).style.left=clamp(x,0,w)+'px';$(id).hidden=x<-.1||x>w+.1}$('selected').style.left=clamp(left,0,w)+'px';const selWidth=Math.max(0,clamp(right,0,w)-clamp(left,0,w));$('selected').style.width=selWidth+'px';$('selected').classList.toggle('narrow',selWidth<84);$('selected-len').textContent=spanText(cutOut-cutIn,true);$('pan').max=Math.max(0,job.duration-viewSpan);$('pan').value=viewStart;$('pan').disabled=viewSpan>=job.duration-.001;$('window-label').textContent=format(job.origin+viewStart)+' – '+format(job.origin+viewStart+viewSpan)+' · '+viewSpan.toFixed(3)+'s visible';}
function zoom(level,center){if(!job?.duration)return;const oldCenter=center??(viewStart+viewSpan/2);$('zoom').value=clamp(level,0,100);const maxZoom=Math.max(1,job.duration/.1);viewSpan=job.duration/Math.pow(maxZoom,Number($('zoom').value)/100);viewStart=oldCenter-viewSpan/2;draw()}
function focus(edge){focusEdge=edge;const t=edge==='in'?cutIn:cutOut;viewSpan=Math.min(job.duration,2);$('zoom').value=100*Math.log(job.duration/viewSpan)/Math.log(Math.max(1.001,job.duration/.1));viewStart=t-viewSpan/2;draw();$(edge==='in'?'in-marker':'out-marker').focus()}
function stop(){if(source){source.onended=null;try{source.stop()}catch{}source.disconnect();source=null}video.pause();video.muted=false;if(raf)cancelAnimationFrame(raf);raf=0;$('playhead').style.display='none';}
function animate(){let t=source?audioOffset+(audioContext.currentTime-audioClock)*audioSpeed:video.currentTime;if(job?.duration){const x=(t-viewStart)/viewSpan*$('wave-wrap').clientWidth;$('playhead').style.display=x>=0&&x<=$('wave-wrap').clientWidth?'block':'none';$('playhead').style.left=x+'px'}if(source||!video.paused)raf=requestAnimationFrame(animate);else raf=0}
async function play(a,b,fade=false){if(!audioBuffer)return;stop();try{await audioContext.resume();audioSpeed=Number($('speed').value);source=audioContext.createBufferSource();source.buffer=audioBuffer;source.playbackRate.value=audioSpeed;const gain=audioContext.createGain();source.connect(gain);gain.connect(audioContext.destination);audioClock=audioContext.currentTime+.05;audioOffset=a;const length=Math.min(b,audioBuffer.duration)-a;if(length<=0){stop();return}const level=Math.pow(10,Number($('volume-db').value||0)/20),opts=getOptions();gain.gain.setValueAtTime(level,audioClock);if(fade){const fi=Math.min(opts.fade_in_ms/1000,length/2),fo=Math.min(opts.fade_out_ms/1000,length/2);if(fi){gain.gain.setValueAtTime(0,audioClock);gain.gain.linearRampToValueAtTime(level,audioClock+fi/audioSpeed)}if(fo){gain.gain.setValueAtTime(level,audioClock+(length-fo)/audioSpeed);gain.gain.linearRampToValueAtTime(0,audioClock+length/audioSpeed)}}source.onended=()=>{source=null;gain.disconnect();video.pause();video.muted=false;cancelAnimationFrame(raf);raf=0};video.currentTime=a;video.playbackRate=audioSpeed;video.muted=true;video.play().catch(()=>{});source.start(audioClock,a,length);animate()}catch(e){stop();message('Audio preview could not start: '+e.message)}}
for(const [id,edge]of[['in-marker','in'],['out-marker','out']]){const marker=$(id);marker.addEventListener('pointerdown',e=>{e.preventDefault();focusEdge=edge;marker.focus();marker.setPointerCapture(e.pointerId);marker.dataset.drag='1';stop()});marker.addEventListener('pointermove',e=>{if(marker.dataset.drag!=='1')return;const r=$('wave-wrap').getBoundingClientRect(),x=e.clientX-r.left;if(x<0)viewStart-=viewSpan*.015;if(x>r.width)viewStart+=viewSpan*.015;viewStart=clamp(viewStart,0,job.duration-viewSpan);position(edge,viewStart+clamp(x/r.width,0,1)*viewSpan)});for(const event of['pointerup','pointercancel','lostpointercapture'])marker.addEventListener(event,()=>delete marker.dataset.drag);marker.addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight'].includes(e.key))return;e.preventDefault();const delta=(e.shiftKey ? .01 : .001)*(e.key==='ArrowLeft'?-1:1);position(edge,(edge==='in'?cutIn:cutOut)+delta)})}
$('wave').onclick=e=>{if(!job)return;stop();const r=$('wave').getBoundingClientRect();video.currentTime=clamp(viewStart+(e.clientX-r.left)/r.width*viewSpan,0,job.duration);animate()};
for(const edge of['in','out']){$('cut-'+edge).onchange=()=>{try{const value=parse($('cut-'+edge).value)-job.origin;if(value<0||value>job.duration)throw Error('Keep the marker inside the downloaded editing window.');position(edge,value);message()}catch(e){message(e.message);sync()}};$('focus-'+edge).onclick=()=>focus(edge);$('listen-'+edge).onclick=()=>{const t=edge==='in'?cutIn:cutOut;focus(edge);play(Math.max(0,t-.7),Math.min(job.duration,t+.7))}}
document.querySelectorAll('[data-delta]').forEach(b=>b.onclick=()=>position(b.dataset.edge,(b.dataset.edge==='in'?cutIn:cutOut)+Number(b.dataset.delta)));
$('zoom').oninput=()=>zoom(Number($('zoom').value));$('zoom-in').onclick=()=>zoom(Number($('zoom').value)+10);$('zoom-out').onclick=()=>zoom(Number($('zoom').value)-10);$('fit').onclick=()=>{viewStart=0;zoom(0,job.duration/2)};$('pan').oninput=()=>{viewStart=Number($('pan').value);draw()};$('gain').onchange=draw;$('play').onclick=()=>play(cutIn,cutOut,true);$('stop').onclick=stop;$('speed').onchange=stop;$('fade').onchange=()=>{stop();formatUI()};
function bindPlayer(){video.onplay=()=>{if(!raf)animate()};video.onseeking=()=>{if(!source)animate()};}bindPlayer();
$('source-form').onsubmit=async e=>{e.preventDefault();try{parse($('start').value);parse($('end').value);stop();message();$('prepare').disabled=true;const j=await api('/api/prepare',{url:$('url').value,start:$('start').value,end:$('end').value});job=j;audioBuffer=null;peaks=null;localStorage.setItem('wordcut-job',j.id);$('editor').hidden=true;setBusy(j.id);progress(j)}catch(e){message(e.message);setBusy(activeId)}};
$('cancel').onclick=async()=>{if(uploadRequest){uploadRequest.abort();return}try{await api('/api/cancel',{id:$('cancel').dataset.id});message('Cancelling the current operation…')}catch(e){message(e.message)}};
$('export').onclick=async()=>{try{for(const input of document.querySelectorAll('.export-settings input')){if(!input.disabled&&!input.reportValidity())return}stop();message();$('export').disabled=true;const j=await api('/api/export',{id:job.id,start:cutIn,end:cutOut,fade:$('fade').checked,options:getOptions()});job=j;setBusy(j.id);progress(j);downloadState()}catch(e){message(e.message);setBusy(activeId)}};
document.querySelectorAll('.export-settings select,.export-settings input').forEach(input=>input.addEventListener('change',()=>{stop();formatUI()}));
$('upload-button').onclick=()=>{
 const file=$('upload-file').files[0];if(!file){message('Choose an audio or video file first.');return}if(file.size>250*1024*1024){message('Choose a file no larger than 250 MiB.');return}
 stop();message();setBusy('upload');$('progress-card').hidden=false;$('phase').textContent='Uploading '+file.name;$('progress').value=0;
 const xhr=new XMLHttpRequest();uploadRequest=xhr;xhr.open('POST','/api/upload');xhr.setRequestHeader('Content-Type','application/octet-stream');xhr.setRequestHeader('X-Wordcut','1');xhr.setRequestHeader('X-Filename',encodeURIComponent(file.name));
 xhr.upload.onprogress=e=>{if(e.lengthComputable)$('progress').value=100*e.loaded/e.total};
 xhr.onload=()=>{uploadRequest=null;try{const j=JSON.parse(xhr.responseText);if(xhr.status!==202)throw Error(j.error||'Upload failed.');job=j;audioBuffer=null;peaks=null;$('editor').hidden=true;localStorage.setItem('wordcut-job',j.id);setBusy(j.id);progress(j)}catch(e){message(e.message);setBusy(null);$('progress-card').hidden=true}};
 xhr.onerror=xhr.onabort=()=>{uploadRequest=null;setBusy(null);$('progress-card').hidden=true;message('Upload interrupted or cancelled.');};xhr.send(file);
};
let transcript=null, transcriptJob=null, transcriptAttempt=null, transcriptRenderKey=null;
function transcriptButtons(){
 $('transcript-fetch').disabled=!!activeId||!job?.url||job?.state!=='ready';
 $('transcript-language').disabled=!!activeId;
 const box=$('transcript-text');
 $('transcript-copy-selection').disabled=!box.value||box.selectionStart===box.selectionEnd;
 $('transcript-copy-all').disabled=!box.value;$('transcript-download').disabled=!box.value;
}
async function refreshTranscript(j){
 if(transcriptJob!==j.id){transcriptJob=j.id;transcript=null;transcriptAttempt=null;transcriptRenderKey=null;$('transcript-controls').hidden=true;$('transcript-text').value='';$('transcript-search').value='';$('transcript-copy-status').textContent='';$('transcript-language').replaceChildren();}
 transcriptButtons();
 if(transcript&&j.transcript_state!=='loading')$('transcript-language').value=transcript.track.id;
 $('transcript-fetch').textContent=j.transcript_state==='loading'?'Extracting…':transcript?'Reload transcript':'Extract transcript';
 $('transcript-status').textContent=!j.url?'Transcript extraction uses YouTube captions. Audio and video uploads currently support trimming and conversion only.':j.transcript_state==='loading'?'Reading captions from YouTube. Your clip remains available.':j.transcript_error||(transcript?`${transcript.track.name} · ${transcript.track.automatic?'Automatic captions — may contain recognition or translation errors.':'Creator-provided captions.'}`:'Extract the video’s captions, then highlight any passage to copy.');
 const attempt=j.id+':'+j.transcript_revision;
 if(!j.transcript_revision||transcriptAttempt===attempt)return;
 transcriptAttempt=attempt;
 try{
  const result=await api(`/api/jobs/${j.id}/transcript`);
  if(job?.id!==j.id)return;
  transcript=result;transcriptRenderKey=null;
  $('transcript-language').replaceChildren(...result.tracks.map(t=>{const option=node('option',t.name+' · '+(t.automatic?'automatic':'creator'));option.value=t.id;return option}));
  $('transcript-language').value=result.track.id;$('transcript-controls').hidden=false;
  renderTranscript();refreshTranscript(job);
 }catch(e){if(job?.id===j.id)$('transcript-status').textContent='Could not open the saved transcript. '+e.message}
}
function renderTranscript(){
 if(!transcript||transcriptJob!==job?.id)return;
 const scope=$('transcript-scope').value,search=$('transcript-search').value.trim().toLocaleLowerCase(),timestamps=$('transcript-timestamps').checked;
 const key=JSON.stringify([transcript.revision,scope,search,timestamps,scope==='clip'?cutIn:null,scope==='clip'?cutOut:null]);
 if(key===transcriptRenderKey)return;transcriptRenderKey=key;
 const cues=transcript.cues.filter(c=>(scope==='full'||(c.end>job.origin+cutIn&&c.start<job.origin+cutOut))&&(!search||c.text.toLocaleLowerCase().includes(search)));
 const text=cues.map(c=>(timestamps?'['+format(c.start)+'] ':'')+c.text).join('\n\n');
 $('transcript-text').value=text;$('transcript-text').scrollTop=0;
 $('transcript-count').textContent=cues.length+' caption lines · '+(text.trim()?cues.map(c=>c.text).join(' ').trim().split(/\s+/).length:0)+' words'+(search?' · search results':'');
 $('transcript-copy-status').textContent='';transcriptButtons();
}
async function extractTranscript(language=''){
 try{const j=await api('/api/transcript',{id:job.id,language});if(job?.id!==j.id)return;job=j;setBusy(j.id);progress(j);refreshTranscript(j)}catch(e){$('transcript-status').textContent=e.message}
}
async function copyTranscript(selected){
 const box=$('transcript-text'),start=box.selectionStart,end=box.selectionEnd;
 const text=selected?box.value.slice(start,end):box.value;if(!text)return;
 try{
  if(navigator.clipboard&&window.isSecureContext)await navigator.clipboard.writeText(text);
  else{
   // Plain HTTP on the LAN has no Clipboard API; a user-initiated copy still works.
   const helper=document.createElement('textarea');helper.value=text;helper.className='clipboard-helper';document.body.append(helper);helper.select();
   const copied=document.execCommand('copy');helper.remove();box.focus({preventScroll:true});box.setSelectionRange(start,end);
   if(!copied)throw Error('Use Cmd+C on Mac or Ctrl+C to copy the highlighted text.');
  }
  $('transcript-copy-status').textContent=selected?'Highlighted text copied.':'Shown text copied.';
 }catch(e){$('transcript-copy-status').textContent=e.message||'Use Cmd+C on Mac or Ctrl+C to copy.'}
}
$('transcript-fetch').onclick=()=>extractTranscript($('transcript-language').value||'');
$('transcript-language').onchange=()=>extractTranscript($('transcript-language').value);
$('transcript-scope').onchange=renderTranscript;$('transcript-search').oninput=renderTranscript;$('transcript-timestamps').onchange=renderTranscript;
for(const event of ['select','keyup','mouseup','touchend'])$('transcript-text').addEventListener(event,transcriptButtons);
$('transcript-copy-selection').onclick=()=>copyTranscript(true);$('transcript-copy-all').onclick=()=>copyTranscript(false);
$('transcript-download').onclick=()=>{const url=URL.createObjectURL(new Blob([$('transcript-text').value+'\n'],{type:'text/plain;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=(job.title||'wordcut').replace(/[^A-Za-z0-9 _.-]/g,'_').slice(0,80)+'-transcript.txt';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};

// Linked Start · Duration · End. The anchored edge stays put; duration edits move the other edge.
function shortTime(t){const ms=Math.round(Math.max(0,t)*1000),h=Math.floor(ms/3600000),m=Math.floor(ms/60000)%60,s=Math.floor(ms/1000)%60,f=ms%1000;return (h?h+':'+String(m).padStart(2,'0'):m)+':'+String(s).padStart(2,'0')+(f?'.'+String(f).padStart(3,'0').replace(/0+$/,''):'')}
function spanText(t,precise){t=Math.round(Math.max(0,t)*1000)/1000;const m=Math.floor(t/60),s=t-m*60,sec=precise?s.toFixed(3):String(+s.toFixed(3));return m?m+'m'+(precise?' '+sec.padStart(6,'0')+'s':s>0?' '+sec+'s':''):sec+'s'}
function parseSpan(text){
 const t=text.trim().toLowerCase().replace(',','.');
 if(t.includes(':'))return parse(t);
 if(/^\d+(\.\d+)?$/.test(t))return Number(t);
 const m=t.match(/^(?:(\d+(?:\.\d+)?)\s*h)?\s*(?:(\d+(?:\.\d+)?)\s*m(?!s))?\s*(?:(\d+(?:\.\d+)?)\s*s)?\s*(?:(\d+)\s*ms)?$/);
 if(!t||!m||!m.slice(1).some(Boolean))throw Error('Enter a duration such as 30, 45s, 1m 30s or 1:30.');
 return (+m[1]||0)*3600+(+m[2]||0)*60+(+m[3]||0)+(+m[4]||0)/1000;
}
class Span{
 constructor(root,o){
  this.root=root;this.o=o;this.input=root.querySelector('.span-dur');this.note=root.querySelector('.span-note');this.rail=root.querySelector('.span-rail');this.last=null;
  root.querySelectorAll('[data-anchor]').forEach(b=>b.onclick=()=>this.setAnchor(b.dataset.anchor));
  root.querySelectorAll('[data-dur]').forEach(b=>b.addEventListener('click',e=>{e.stopPropagation();this.set(Number(b.dataset.dur))}));
  this.input.onchange=()=>{try{this.set(parseSpan(this.input.value))}catch(e){this.say(e.message);this.refresh(true)}};
  this.input.onkeydown=e=>{if(e.key!=='ArrowUp'&&e.key!=='ArrowDown')return;e.preventDefault();this.step(e,e.key==='ArrowUp'?1:-1)};
  this.input.onblur=()=>this.refresh(true);
  this.input.addEventListener('wheel',e=>{if(document.activeElement!==this.input)return;e.preventDefault();this.step(e,e.deltaY<0?1:-1)},{passive:false});
  // Scrub: drag the ⟷ handle sideways, like a number field in a design tool.
  const grip=root.querySelector('.scrub');
  grip.addEventListener('pointerdown',e=>{const d=this.dur();if(d===null)return;e.preventDefault();grip.setPointerCapture(e.pointerId);this.drag={x:e.clientX,d};document.body.classList.add('scrubbing')});
  grip.addEventListener('pointermove',e=>{if(!this.drag)return;const per=e.shiftKey?.01:e.altKey?1:.1;this.set(this.drag.d+(e.clientX-this.drag.x)*per,{round:e.shiftKey?.01:per})});
  for(const ev of['pointerup','pointercancel','lostpointercapture'])grip.addEventListener(ev,()=>{this.drag=null;document.body.classList.remove('scrubbing')});
  if(this.rail){
   const at=e=>{const r=this.rail.querySelector('.rail-track').getBoundingClientRect();let f=clamp((e.clientX-r.left)/r.width,0,1);if(this.anchor==='end')f=1-f;const d=this.o.limits().maxDur*f*f;this.set(e.shiftKey?d:Span.snap(d),{round:.01})};
   this.rail.addEventListener('pointerdown',e=>{if(e.target.closest('[data-dur]')||this.dur()===null)return;e.preventDefault();this.rail.focus();this.rail.setPointerCapture(e.pointerId);this.rail.classList.add('dragging');at(e)});
   this.rail.addEventListener('pointermove',e=>{if(this.rail.classList.contains('dragging'))at(e)});
   for(const ev of['pointerup','pointercancel','lostpointercapture'])this.rail.addEventListener(ev,()=>this.rail.classList.remove('dragging'));
   this.rail.addEventListener('keydown',e=>{const dir={ArrowRight:1,ArrowUp:1,ArrowLeft:-1,ArrowDown:-1}[e.key];if(!dir)return;e.preventDefault();const horizontal=e.key==='ArrowLeft'||e.key==='ArrowRight';this.step(e,this.anchor==='end'&&horizontal?-dir:dir)});
  }
  this.setAnchor(localStorage.getItem('wordcut-anchor-'+root.id)==='end'?'end':'start');
 }
 static snap(d){return d<10?Math.max(.5,Math.round(d*2)/2):d<60?Math.round(d):d<180?Math.round(d/5)*5:Math.round(d/10)*10}
 dur(){const r=this.o.read();return r&&r.end>r.start?r.end-r.start:null}
 step(e,dir){const d=this.dur();if(d===null)return;const size=e.altKey?10:e.shiftKey?.1:1;this.set(d+dir*size,{round:e.shiftKey?.1:.001})}
 say(text){this.note.textContent=text||''}
 set(want,opts={}){
  const r=this.o.read();if(!r){this.say(this.o.invalid);return}
  const L=this.o.limits(),q=opts.round||.001;let d=clamp(Math.round(want/q)*q,L.minDur,L.maxDur),note='';
  if(want>L.maxDur+.0005)note=`Capped at ${spanText(L.maxDur)} — ${L.maxLabel}.`;
  else if(want<L.minDur-.0005)note=`Minimum length is ${spanText(L.minDur)}.`;
  let s=r.start,e=r.end;
  if(this.anchor==='start'){e=s+d;if(e>L.max+.0005){e=L.max;note=`Only ${spanText(e-s)} fits after ${this.o.names[0]} — ${this.o.names[1]} moved to ${this.o.endLabel}.`}}
  else{s=e-d;if(s<L.min-.0005){s=L.min;note=`Only ${spanText(e-s)} fits before ${this.o.names[1]} — ${this.o.names[0]} moved to ${this.o.startLabel}.`}}
  s=Math.round(s*1000)/1000;e=Math.round(e*1000)/1000;
  this.o.write(s,e,this.anchor==='start'?'end':'start');this.say(note);this.refresh(true);
 }
 // Called when the user types into a time field: keep the duration if the anchor moved.
 edgeChanged(edge){if(edge===this.anchor&&this.last){const keep=this.last;this.set(keep)}else{this.say('');this.refresh()}}
 setAnchor(a){
  this.anchor=a;this.root.dataset.anchor=a;localStorage.setItem('wordcut-anchor-'+this.root.id,a);
  this.root.querySelectorAll('[data-anchor]').forEach(b=>b.setAttribute('aria-checked',String(b.dataset.anchor===a)));this.say('');this.refresh();
 }
 refresh(force){
  const d=this.dur(),L=this.o.limits();if(d!==null)this.last=d;
  if(force||document.activeElement!==this.input)this.input.value=d===null?'':spanText(d,this.o.precise);
  this.root.querySelectorAll('[data-dur]').forEach(b=>{const v=Number(b.dataset.dur);b.classList.toggle('on',d!==null&&Math.abs(v-d)<.0005);if(!this.rail)b.disabled=d===null||v>L.maxDur+.0005});
  for(const el of this.root.querySelectorAll('[data-derive]')){const edge=el.dataset.derive;el.textContent=d===null?'':edge===this.anchor?'anchored':edge==='end'?'= start + '+spanText(d):'= end − '+spanText(d)}
  if(this.rail){
   const pos=v=>Math.sqrt(clamp(v/L.maxDur,0,1))*100,p=d===null?0:pos(d),fromEnd=this.anchor==='end';
   const fill=this.rail.querySelector('.rail-fill');fill.style.left=(fromEnd?100-p:0)+'%';fill.style.width=p+'%';
   this.rail.querySelector('.rail-knob').style.left=(fromEnd?100-p:p)+'%';
   this.rail.querySelectorAll('[data-dur]').forEach(b=>b.style.left=(fromEnd?100-pos(+b.dataset.dur):pos(+b.dataset.dur))+'%');
   this.rail.setAttribute('aria-valuenow',d===null?0:d.toFixed(3));this.rail.setAttribute('aria-valuetext',d===null?'No valid range':spanText(d)+(fromEnd?' ending at ':' starting at ')+(fromEnd?$('end').value:$('start').value));
  }
 }
}
const sourceSpan=new Span($('source-span'),{
 names:['start','end'],startLabel:'0:00',endLabel:'the end',invalid:'Enter a valid start and end time first.',
 read(){try{return {start:parse($('start').value),end:parse($('end').value)}}catch{return null}},
 limits:()=>({min:0,max:Infinity,minDur:.1,maxDur:600,maxLabel:'one selection covers up to 10 minutes'}),
 write(s,e,moved){$('start').value=shortTime(s);$('end').value=shortTime(e);const f=document.querySelector(`.time-field[data-edge="${moved}"]`);f.classList.remove('flash');void f.offsetWidth;f.classList.add('flash')}
});
for(const edge of['start','end'])$(edge).addEventListener('change',()=>sourceSpan.edgeChanged(edge));
const editSpan=new Span($('edit-span'),{
 names:['IN','OUT'],startLabel:'the window start',endLabel:'the window end',precise:true,invalid:'Open a prepared clip first.',
 read:()=>job?.duration&&audioBuffer?{start:cutIn,end:cutOut}:null,
 limits:()=>({min:0,max:job?.duration||0,minDur:.02,maxDur:job?.duration||0,maxLabel:'the length of the prepared editing window'}),
 write(s,e,moved){stop();cutIn=s;cutOut=e;focusEdge=moved==='start'?'in':'out';sync()}
});
formatUI();new ResizeObserver(draw).observe($('wave-wrap'));window.addEventListener('pagehide',stop);poll();
