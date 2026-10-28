"""Render a validated lesson JSON file as a self-contained browser preview."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]

PAGE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Blu Visual Teacher — Lesson Preview</title>
<style>
:root{color-scheme:dark;--bg:#08111f;--panel:#111e30;--line:#263953;--cyan:#60dddc;--text:#edf5ff;--muted:#9eb0c7}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 50% 0,#152b40,var(--bg) 68%);color:var(--text);font:16px/1.5 system-ui,"Noto Sans",sans-serif;min-height:100vh;display:grid;place-items:center;padding:24px}
main{width:min(100%,1000px)}@media(min-width:1000px) and (min-height:700px){main{width:min(100%,1500px,calc((100vh - 160px)*16/9))}}header{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:16px}.brand{font-size:14px;color:var(--cyan);font-weight:750;letter-spacing:.12em;text-transform:uppercase}select,button{background:var(--panel);color:var(--text);border:1px solid var(--line);padding:10px 14px;border-radius:10px;font:inherit}button{cursor:pointer}button:hover{border-color:var(--cyan)}button:disabled{opacity:.4;cursor:default}
.player{aspect-ratio:16/9;background:linear-gradient(135deg,#12243a,#0d1727);border:1px solid var(--line);border-radius:20px;overflow:hidden;display:grid;place-items:center;position:relative;padding:5%}.scene{width:100%;height:100%;display:flex;flex-direction:column;justify-content:center;align-items:center;text-align:center}.eyebrow{color:var(--cyan);font-weight:700;letter-spacing:.12em;text-transform:uppercase;font-size:clamp(12px,1.5vw,16px)}h1{font-size:clamp(30px,6vw,64px);line-height:1.08;margin:12px 0}h2{font-size:clamp(22px,3.4vw,38px);margin:0 0 20px}.subtitle{font-size:clamp(16px,2.3vw,24px);color:var(--muted)}.diagram{width:min(100%,850px);height:66%;overflow:visible}.node rect{fill:#162a40;stroke:#60dddc;stroke-width:2}.node text{fill:#edf5ff;font-size:18px;text-anchor:middle;dominant-baseline:middle}.arrow{stroke:#93b8d4;stroke-width:3;fill:none;marker-end:url(#arrow)}.arrow-label{fill:#60dddc;font-size:15px;text-anchor:middle}.code-layout{display:grid;grid-template-columns:1fr .55fr;gap:24px;width:min(100%,850px);text-align:left}.code,.output{background:#08111f;border:1px solid var(--line);border-radius:14px;padding:18px;font:clamp(14px,2vw,21px)/1.8 ui-monospace,Consolas,monospace;white-space:pre}.code div{padding:0 8px;border-radius:5px}.code .active{background:#17384a;color:#8df3e4}.output{color:#9ff0bd}.caption{position:absolute;bottom:18px;left:8%;right:8%;background:#050a12d9;border-radius:9px;padding:8px 14px;font-size:clamp(12px,1.8vw,18px)}
.scene{animation:scene-in .45s ease both}.arrow{stroke-dasharray:1200;stroke-dashoffset:1200;animation:draw-arrow 1.1s ease forwards}.arrow:nth-of-type(2){animation-delay:.35s}.arrow:nth-of-type(3){animation-delay:.7s}.node{transform-box:fill-box;transform-origin:center;animation:node-in .45s ease both}.node:nth-of-type(2){animation-delay:.12s}.node:nth-of-type(3){animation-delay:.24s}.node:nth-of-type(4){animation-delay:.36s}.code .active{animation:line-pulse .65s ease both}.output{animation:scene-in .4s ease both}.controls{display:flex;align-items:center;gap:8px;margin-top:14px}.controls .spacer{flex:1}.controls button[aria-pressed="true"]{border-color:var(--cyan);color:var(--cyan)}.progress{height:6px;background:#1b2a3e;border-radius:5px;overflow:hidden;margin-top:14px}.progress i{display:block;height:100%;background:var(--cyan);transition:width .2s}.meta{font-size:13px;color:var(--muted);margin-top:14px}.error{color:#ffadad;padding:24px}@keyframes scene-in{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}@keyframes draw-arrow{to{stroke-dashoffset:0}}@keyframes node-in{from{opacity:0;transform:scale(.86)}to{opacity:1;transform:scale(1)}}@keyframes line-pulse{0%{background:#245c69}100%{background:#17384a}}
@media(max-width:600px){body{padding:12px}.code-layout{grid-template-columns:1fr;gap:8px}.player{aspect-ratio:4/3}.diagram{height:56%}.controls{gap:5px;flex-wrap:wrap}.controls button{padding:8px}}@media(prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.01ms!important;scroll-behavior:auto!important}}
</style></head><body><main><header><div><div class="brand">Blu Visual Teacher</div><div id="lesson-title"></div></div><label>Language <select id="language"></select></label></header><section class="player" id="player" aria-live="polite"></section><div class="progress"><i id="progress"></i></div><div class="controls"><button id="replay" title="Replay lesson">↺ Replay</button><button id="play" aria-pressed="false">▶ Play</button><button id="prev" aria-label="Previous scene">← Previous</button><span id="counter"></span><span class="spacer"></span><button id="next">Next →</button><button id="text-size" aria-pressed="false" title="Increase lesson text size">A+ Text</button></div><div class="meta" id="concept"></div></main>
<script>
const lesson = __LESSON__;
const audioMap = __AUDIO_MAP__;
const labels={"en-IN":"English (India)","hi-IN":"हिन्दी","ta-IN":"தமிழ்","te-IN":"తెలుగు","bn-IN":"বাংলা","mr-IN":"मराठी"};
let index=0,language=lesson.languages[0];
const player=document.querySelector('#player'), lang=document.querySelector('#language');
for(const code of lesson.languages){const o=document.createElement('option');o.value=code;o.textContent=labels[code]||code;lang.append(o)}
lang.addEventListener('change',()=>{language=lang.value;render()});
document.querySelector('#lesson-title').textContent=lesson.title;
document.querySelector('#concept').textContent=`${lesson.level} · ${lesson.targetDurationSeconds}s target · ${lesson.concept}`;
document.querySelector('#text-size').onclick=()=>{const main=document.querySelector('main'),large=main.classList.toggle('large-text'),button=document.querySelector('#text-size');button.textContent=large?'A− Text':'A+ Text';button.setAttribute('aria-pressed',String(large))};
document.querySelector('#prev').onclick=()=>{index=Math.max(0,index-1);render()};
document.querySelector('#next').onclick=()=>{index=Math.min(lesson.scenes.length-1,index+1);render()};
function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n}
function objectIcon(name){const art=el('div','subject-art '+name);const shapes={
 sun:'<g stroke="#ffbe4a" stroke-width="6"><path d="M50 3v17M50 80v17M3 50h17M80 50h17M17 17l12 12M71 71l12 12M83 17L71 29M29 71L17 83"/></g><circle cx="50" cy="50" r="29" fill="#ffd447" stroke="#fff0a0" stroke-width="3"/>',
 moon:'<circle cx="50" cy="50" r="34" fill="#f0eed6"/><circle cx="67" cy="35" r="31" fill="#12243a"/>',
 cloud:'<g fill="#dceefa"><circle cx="32" cy="56" r="20"/><circle cx="53" cy="43" r="27"/><circle cx="75" cy="57" r="18"/><rect x="18" y="52" width="72" height="23" rx="11"/></g>',
 plant:'<path d="M50 85V31" stroke="#65bf73" stroke-width="7"/><ellipse cx="34" cy="55" rx="21" ry="11" fill="#69b867" transform="rotate(25 34 55)"/><ellipse cx="67" cy="39" rx="21" ry="11" fill="#8ad47d" transform="rotate(-25 67 39)"/>',
 flower:'<path d="M50 90V50" stroke="#65bf73" stroke-width="6"/><g fill="#ff93bd"><circle cx="50" cy="19" r="14"/><circle cx="25" cy="39" r="14"/><circle cx="75" cy="39" r="14"/><circle cx="35" cy="62" r="14"/><circle cx="65" cy="62" r="14"/></g><circle cx="50" cy="42" r="13" fill="#ffd65d"/>',
 water:'<path d="M50 5C36 30 20 51 20 67a30 30 0 0 0 60 0C80 51 64 30 50 5Z" fill="#4bb2ef"/>',
 earth:'<circle cx="50" cy="50" r="37" fill="#4896dc"/><path d="M23 33 37 16l22 7 8 17-20 6-9 19-18-6Z M61 61l19-9-6 24-17 8Z" fill="#69bb77"/>',
 ball:'<circle cx="50" cy="50" r="36" fill="#ff885a" stroke="#ffd4ad" stroke-width="3"/><path d="M28 24c23 6 37 28 42 50M18 55c25-5 47-2 65 8" fill="none" stroke="#ffe0bf" stroke-width="3"/>',
 insect:'<ellipse cx="50" cy="56" rx="18" ry="30" fill="#f0b24e"/><circle cx="50" cy="25" r="15" fill="#313d4b"/><g stroke="#f0b24e" stroke-width="4"><path d="M35 43 15 28M35 60 12 61M35 75 17 90M65 43 85 28M65 60 88 61M65 75 83 90"/></g>'
};if(!shapes[name])return art;const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 100 100');svg.setAttribute('role','img');svg.setAttribute('aria-label',name);svg.innerHTML=shapes[name];art.append(svg);return art}

function render(){const s=lesson.scenes[index],v=s.visual;player.replaceChildren();const wrap=el('div','scene');
if(s.type==='title-card'){wrap.append(el('div','eyebrow','A short visual lesson'),el('h1','',v.heading),el('div','subtitle',''));wrap.lastChild.textContent=v.subtitle}
if(s.type==='diagram'){wrap.append(el('h2','',s.captions[language]||''));const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 1000 400');svg.setAttribute('class','diagram');const defs=document.createElementNS(svg.namespaceURI,'defs'),marker=document.createElementNS(svg.namespaceURI,'marker');marker.setAttribute('id','arrow');marker.setAttribute('markerWidth','10');marker.setAttribute('markerHeight','10');marker.setAttribute('refX','8');marker.setAttribute('refY','3');marker.setAttribute('orient','auto');marker.setAttribute('markerUnits','strokeWidth');const tip=document.createElementNS(svg.namespaceURI,'path');tip.setAttribute('d','M0,0 L0,6 L9,3 z');tip.setAttribute('fill','#93b8d4');marker.append(tip);defs.append(marker);svg.append(defs);const map=new Map();for(const n of v.nodes){const x=n.x*1000,y=n.y*400;map.set(n.id,{x,y});for(const a of v.arrows.filter(a=>a.from===n.id)){const t=v.nodes.find(q=>q.id===a.to);if(!t)continue;const line=document.createElementNS(svg.namespaceURI,'line');line.setAttribute('x1',x);line.setAttribute('y1',y);line.setAttribute('x2',t.x*1000);line.setAttribute('y2',t.y*400);line.setAttribute('class','arrow');svg.append(line);if(a.label){const tx=document.createElementNS(svg.namespaceURI,'text');tx.setAttribute('x',(x+t.x*1000)/2);tx.setAttribute('y',(y+t.y*400)/2-10);tx.setAttribute('class','arrow-label');tx.textContent=a.label;svg.append(tx)}}}for(const n of v.nodes){const g=document.createElementNS(svg.namespaceURI,'g');g.setAttribute('class','node');const r=document.createElementNS(svg.namespaceURI,'rect');r.setAttribute('x',n.x*1000-100);r.setAttribute('y',n.y*400-42);r.setAttribute('width','200');r.setAttribute('height','84');r.setAttribute('rx','16');const t=document.createElementNS(svg.namespaceURI,'text');t.setAttribute('x',n.x*1000);t.setAttribute('y',n.y*400);const words=String(n.label).split(/\s+/),lines=[''];for(const word of words){const last=lines.length-1,joined=(lines[last]+' '+word).trim();if(joined.length>18&&lines[last]&&lines.length<3)lines.push(word);else lines[last]=joined}lines.forEach((line,i)=>{const span=document.createElementNS(svg.namespaceURI,'tspan');span.setAttribute('x',n.x*1000);span.setAttribute('dy',i===0?`${-(lines.length-1)*10}px`:'20px');span.textContent=line;t.append(span)});g.append(r,t);svg.append(g)}if(lesson.visualObjects?.includes('sun')&&lesson.visualObjects?.includes('earth')){const sun=v.nodes.find(n=>n.id==='sun'),earth=v.nodes.find(n=>n.id==='earth');if(sun&&earth){const beam=document.createElementNS(svg.namespaceURI,'line');beam.setAttribute('x1',sun.x*1000+60);beam.setAttribute('y1',sun.y*400-105);beam.setAttribute('x2',earth.x*1000-60);beam.setAttribute('y2',earth.y*400-105);beam.setAttribute('class','sunlight-path');svg.insertBefore(beam,svg.querySelector('.node'));for(const [name,node] of [['sun',sun],['earth',earth]]){const icon=objectIcon(name).querySelector('svg');icon.setAttribute('x',node.x*1000-50);icon.setAttribute('y',node.y*400-155);icon.setAttribute('width','100');icon.setAttribute('height','100');icon.setAttribute('class','diagram-object '+name);svg.append(icon)}}}wrap.append(svg)}
if(s.type==='code-walkthrough'){wrap.append(el('h2','',v.heading?.[language]||v.heading||'Trace the example step by step'));const grid=el('div','code-layout'),code=el('div','code'),out=el('div','output');v.code.forEach((line,i)=>code.append(el('div',v.highlightLines.includes(i+1)?'active':'',line)));out.textContent='Output\n'+v.output.join('\n');grid.append(code,out);wrap.append(grid)}
if(s.type==='concept-card'){wrap.append(el('h2','',v.heading));const points=el('div','code-layout');points.style.gridTemplateColumns='1fr';for(const point of v.points)points.append(el('div','output',point));wrap.append(points)}
if(lesson.visualObjects?.length&&!(s.type==='diagram'&&v.nodes?.some(n=>n.id==='sun')&&v.nodes?.some(n=>n.id==='earth'))){if(s.type==='title-card')wrap.classList.add('title-scene');const arts=el('div','subject-arts');for(const name of lesson.visualObjects.slice(0,3))arts.append(objectIcon(name));if(s.type==='title-card'&&lesson.visualObjects.includes('sun')&&lesson.visualObjects.includes('earth'))arts.append(el('div','sunlight-stream'));wrap.prepend(arts)}wrap.append(el('div','caption',''));wrap.lastChild.textContent=s.captions[language]||s.narration[language]||'';player.append(wrap);document.querySelector('#counter').textContent=`Scene ${index+1} of ${lesson.scenes.length}`;document.querySelector('#progress').style.width=`${(index+1)/lesson.scenes.length*100}%`;document.querySelector('#prev').disabled=index===0;document.querySelector('#next').disabled=index===lesson.scenes.length-1}
render();
const playButton=document.querySelector('#play'),replayButton=document.querySelector('#replay'),audio=new Audio();
let playing=false,timer=null,timerStarted=0,timerRemaining=0;
function currentTiming(){return audioMap?.[language]?.[lesson.scenes[index].id]||audioMap?.[lesson.scenes[index].id]||null}
function setPlayState(value){playing=value;playButton.textContent=playing?'Ⅱ Pause':'▶ Play';playButton.setAttribute('aria-pressed',String(playing))}
function stopTimer(){if(timer){clearTimeout(timer);timer=null;timerRemaining=Math.max(0,timerRemaining-(performance.now()-timerStarted))}}
function updatePlaybackProgress(){const timing=currentTiming(),seconds=timing?.duration||lesson.targetDurationSeconds/lesson.scenes.length;const fraction=timing&&audio.src?Math.min(1,audio.currentTime/seconds):Math.max(0,1-timerRemaining/(seconds*1000));document.querySelector('#progress').style.width=`${(index+fraction)/lesson.scenes.length*100}%`}
function queueNext(ms){timerRemaining=ms;timerStarted=performance.now();timer=setTimeout(()=>{timer=null;timerRemaining=0;nextScene()},ms)}
function startScene(reset=false){stopTimer();if(reset)timerRemaining=0;const timing=currentTiming();if(timing?.url){if(reset||!audio.src.endsWith(timing.url)){audio.src=timing.url;audio.currentTime=0}audio.onended=()=>queueNext((timing.pause||0)*1000);audio.play().catch(()=>setPlayState(false));updatePlaybackProgress();return}audio.pause();const ms=reset||!timerRemaining?lesson.targetDurationSeconds*1000/lesson.scenes.length:timerRemaining;queueNext(ms)}
function nextScene(){if(!playing)return;if(index>=lesson.scenes.length-1){setPlayState(false);audio.pause();document.querySelector('#progress').style.width='100%';return}index++;render();startScene(true)}
function navigate(delta){stopTimer();timerRemaining=0;audio.pause();index=Math.max(0,Math.min(lesson.scenes.length-1,index+delta));render();if(playing)startScene(true)}
playButton.onclick=()=>{if(playing){setPlayState(false);audio.pause();stopTimer();return}setPlayState(true);startScene(false)};
replayButton.onclick=()=>{stopTimer();timerRemaining=0;audio.pause();index=0;render();setPlayState(true);startScene(true)};
document.querySelector('#prev').onclick=()=>navigate(-1);document.querySelector('#next').onclick=()=>navigate(1);
audio.ontimeupdate=updatePlaybackProgress;
lang.addEventListener('change',()=>{if(playing)startScene(true)});
</script></body></html>'''

PAGE = PAGE.replace(
    "</style></head>",
    ".diagram-object.sun{animation:object-pulse 2.4s ease-in-out infinite;transform-box:fill-box;transform-origin:center}.diagram-object.earth{animation:object-float 3s ease-in-out infinite;transform-box:fill-box;transform-origin:center}.subject-art.sun{animation:object-pulse 2.4s ease-in-out infinite}.subject-art.earth,.subject-art.cloud,.subject-art.moon{animation:object-float 3s ease-in-out infinite}.subject-art.ball{animation:object-bounce 1.5s ease-in-out infinite}.subject-art.insect{animation:object-flutter 1.2s ease-in-out infinite}.subject-art.plant,.subject-art.flower{animation:object-sway 2.8s ease-in-out infinite;transform-origin:bottom center}.sunlight-stream{position:absolute;left:23%;right:23%;top:43%;height:8px;background:repeating-linear-gradient(90deg,#ffd65f 0 10px,transparent 10px 26px);filter:drop-shadow(0 0 7px #ffd65f);animation:light-flow 1.1s linear infinite;pointer-events:none}.title-scene .subject-arts{position:relative}.sunlight-path{stroke:#ffd65f;stroke-width:6;stroke-dasharray:14 15;animation:dash-flow .9s linear infinite;filter:drop-shadow(0 0 5px #ffd65f)}@keyframes object-pulse{50%{transform:scale(1.1) rotate(8deg)}}@keyframes object-float{50%{transform:translateY(-12px) rotate(5deg)}}@keyframes object-bounce{50%{transform:translateY(-15px)}}@keyframes object-flutter{50%{transform:translate(5px,-12px) rotate(8deg)}}@keyframes object-sway{50%{transform:rotate(8deg)}}@keyframes light-flow{to{background-position:26px 0}}@keyframes dash-flow{to{stroke-dashoffset:-29}}.subject-arts{position:absolute;right:7%;top:13%;display:flex;gap:6px;pointer-events:none}.subject-art{position:relative;z-index:1;width:76px;height:76px}.subject-art svg{width:100%;height:100%}.title-scene .subject-arts{position:static;justify-content:center;margin-bottom:10px}.title-scene .subject-art{width:110px;height:110px}.caption{max-height:27%;overflow:auto;line-height:1.35}.large-text h1{font-size:clamp(36px,6.5vw,72px)}.large-text h2{font-size:clamp(27px,4vw,46px)}.large-text .subtitle{font-size:clamp(20px,2.8vw,30px)}.large-text .code,.large-text .output{font-size:clamp(17px,2.3vw,26px)}.large-text .caption{font-size:clamp(16px,2.2vw,24px);max-height:36%}.code-layout{max-height:72%;min-height:0;overflow:auto;align-content:start}.code,.output{white-space:pre-wrap;overflow-wrap:anywhere;min-width:0;max-height:100%;overflow:auto}.scene{min-height:0;overflow:hidden}.scene h1,.scene h2,.subtitle{max-width:100%;overflow-wrap:anywhere}.diagram{max-height:70%}.node text{font-size:15px}.caption{overflow-wrap:anywhere}@media(max-width:600px){.code-layout{max-height:62%}.code,.output{max-height:140px}}"
    "</style></head>",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a browser preview from a lesson JSON")
    parser.add_argument("lesson", nargs="?", type=Path, default=ROOT / "data" / "lessons" / "sample-lesson.json")
    parser.add_argument("-o", "--output", type=Path, default=ROOT / "lesson-preview.html")
    parser.add_argument("--narration-manifest", type=Path, action="append", default=[], help="local voice_manifest.json; repeat once per language to synchronize all available preview audio")
    parser.add_argument("--audio-base-url", default="", help="local route prefix serving the manifest audio files")
    args = parser.parse_args()
    try:
        lesson = json.loads(args.lesson.read_text(encoding="utf-8"))
        schema = json.loads((ROOT / "schemas" / "lesson.schema.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    problems = list(Draft202012Validator(schema).iter_errors(lesson))
    if problems:
        for problem in problems:
            path = ".".join(map(str, problem.absolute_path)) or "<root>"
            print(f"{path}: {problem.message}")
        return 1
    audio_map: dict[str, dict[str, dict[str, Any]]] = {}
    if args.narration_manifest:
        for manifest_path in args.narration_manifest:
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                parser.error(f"Could not read narration manifest {manifest_path}: {exc}")
            language = manifest.get("language", "")
            audio_base_url = args.audio_base_url.rstrip("/") or f"/audio/{quote(language)}"
            audio_map[language] = {
                item["sceneId"]: {
                    "url": f"{audio_base_url}/{quote(item['audioFile'])}",
                    "duration": float(item["audioDurationSeconds"]),
                    "pause": float(item.get("pauseSeconds", 0.5)),
                }
                for item in manifest.get("scenes", [])
                if item.get("sceneId") and item.get("audioFile")
            }
    output = PAGE.replace("__LESSON__", json.dumps(lesson, ensure_ascii=False).replace("</", "<\\/"))
    output = output.replace("__AUDIO_MAP__", json.dumps(audio_map, ensure_ascii=False).replace("</", "<\\/"))
    args.output.write_text(output, encoding="utf-8")
    print(f"Preview created: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
