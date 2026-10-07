"use strict";
const data = JSON.parse(document.getElementById("viewer-data").textContent);
const providers = {spotify:["Spotify","S","#88d8a5"], applemusic:["Apple Music","A","#f0a0ae"], soundcloud:["SoundCloud","SC","#efb078"], tiddl:["TIDAL","T","#b4c8e2"], tidal:["TIDAL","T","#b4c8e2"], beatport:["Beatport","B","#c5dd88"], qobuz:["Qobuz","Q","#97cce7"], manual:["Saisie manuelle","M","#c5bddb"]};
const kinds = {playlist:"Playlists",album:"Albums / Releases",track:"Morceaux"};
const icons = {Search, Download, ExternalLink};
const $ = id => document.getElementById(id);
let selected = 0, filter = "all", sort = "number", direction = 1;
function el(tag, text, className){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;}
function svgNode([tag, attrs, children=[]]){const node=document.createElementNS("http://www.w3.org/2000/svg",tag);for(const [key,value] of Object.entries(attrs))node.setAttribute(key,value);children.forEach(child=>node.append(svgNode(child)));return node;}
function icon(name){const node=svgNode(icons[name]);node.setAttribute("aria-hidden","true");return node;}
document.querySelectorAll("[data-icon]").forEach(node=>node.replaceChildren(icon(node.dataset.icon)));
function safeURL(value){try{const url=new URL(value);return url.protocol==="https:"&&!url.username&&!url.password?url.href:null;}catch{return null;}}
function provider(source){return providers[source]||[source||"Source","?","#aab0ba"];}
function mark(source){const [name,initial,color]=provider(source);const node=el("span",initial,"source-mark");node.style.setProperty("--source-color",color);node.title=name;return node;}
function state(track){return track.unavailable?"unavailable":track.target&&!track.requires_matching?"direct":"pending";}
function duration(seconds){return Number.isFinite(seconds)&&seconds>0?`${Math.floor(seconds/60)}:${String(Math.floor(seconds%60)).padStart(2,"0")}`:"\u2014";}
function folded(value){return String(value||"").normalize("NFD").replace(/[\u0300-\u036f]/g,"").toLocaleLowerCase("fr");}
function drawNav(){
  const query=folded($("scenario-search").value);const nav=$("scenarios");nav.replaceChildren();let visible=0;
  for(const [kind,label] of Object.entries(kinds)){
    const matches=data.scenarios.map((item,index)=>({item,index})).filter(({item})=>item.result.kind===kind&&folded(`${provider(item.result.source)[0]} ${item.result.name||""} ${item.result.url}`).includes(query));
    if(!matches.length)continue;
    const heading=el("div",undefined,"group-heading");heading.append(el("span",label),el("span",String(matches.length)));nav.append(heading);
    for(const {item,index} of matches){const r=item.result;const button=el("button",undefined,`scenario${index===selected?" active":""}`);button.type="button";button.dataset.index=index;button.setAttribute("aria-current",String(index===selected));button.title=r.name||r.url;
      const label=el("span",undefined,"scenario-label");label.append(el("strong",provider(r.source)[0]),el("small",r.name||r.url));button.append(mark(r.source),label,el("span",r.status==="ok"?String(item.payload.tracks.length):"!",`scenario-total ${r.status==="ok"?"":"failed"}`));
      button.addEventListener("click",()=>select(index));nav.append(button);visible++;
    }
  }
  if(!visible)nav.append(el("p","Aucun scenario","empty"));
}
function select(index){selected=index;filter="all";sort="number";direction=1;$("track-search").value="";drawNav();drawHeader();drawTracks();$("table-region").scrollTop=0;}
function drawHeader(){
  const {result:r,payload}=data.scenarios[selected];const tracks=payload?.tracks||[];
  $("source-mark").replaceWith(Object.assign(mark(r.source),{id:"source-mark"}));
  $("collection-type").textContent=`${provider(r.source)[0]} / ${kinds[r.kind]||r.kind}`;
  $("result-status").textContent=r.status==="ok"?"Lecture validee":"Echec de lecture";
  $("result-status").style.color=r.status==="ok"?"#8bd1b3":"#ed959b";
  $("collection-title").textContent=payload?.name||r.name||provider(r.source)[0];
  const direct=tracks.filter(t=>state(t)==="direct").length;
  const meta=$("collection-meta");meta.replaceChildren();for(const [n,label] of [[tracks.length,"titres"],[direct,"liens directs"],[tracks.filter(t=>state(t)==="pending").length,"a apparier"]]){const span=el("span");span.append(el("strong",String(n)),document.createTextNode(` ${label}`));meta.append(span);}
  if(Number.isFinite(r.seconds))meta.append(el("span",`${r.seconds.toLocaleString("fr")} s`));
  const link=safeURL(r.url);$("source-link").hidden=!link;if(link)$("source-link").href=link;
  $("export").disabled=!payload;$("source-note").textContent="Aucun audio telecharge";
}
function drawTracks(){
  if(!data.scenarios[selected])return;
  const {result:r,payload}=data.scenarios[selected];const all=payload?.tracks||[];const query=folded($("track-search").value);
  const rows=all.filter(t=>(filter==="all"||state(t)===filter)&&folded(`${t.title} ${t.artist} ${t.album} ${t.mix}`).includes(query));
  rows.sort((a,b)=>direction*(typeof a[sort]==="number"||sort==="duration_seconds"?(a[sort]||0)-(b[sort]||0):String(a[sort]||"").localeCompare(String(b[sort]||""),"fr",{numeric:true})));
  document.querySelectorAll("[data-sort]").forEach(button=>button.parentElement.setAttribute("aria-sort",button.dataset.sort===sort?(direction===1?"ascending":"descending"):"none"));
  document.querySelectorAll("[data-filter]").forEach(button=>{button.setAttribute("aria-pressed",String(button.dataset.filter===filter));button.querySelector("span").textContent=button.dataset.filter==="all"?all.length:all.filter(t=>state(t)===button.dataset.filter).length;});
  const fragment=document.createDocumentFragment();for(const t of rows){const row=el("tr");row.append(el("td",String(t.number),"number"),el("td",t.title,"title"),el("td",t.artist||"\u2014","artist"),el("td",duration(t.duration_seconds),"duration"));
    const status=state(t);const cell=el("td",undefined,"state");cell.append(el("span",status==="direct"?provider(t.target.provider)[0]:status==="pending"?"A apparier":"Indisponible",`track-state ${status}`));row.append(cell);
    const target=safeURL(t.target?.url||t.url);const action=el("td",undefined,"link-column");if(target){const a=el("a",undefined,"icon-button");a.href=target;a.target="_blank";a.rel="noopener noreferrer";a.title=`Ouvrir ${t.title}`;a.setAttribute("aria-label",a.title);a.append(icon("ExternalLink"));action.append(a);}row.append(action);fragment.append(row);
  }$("tracks").replaceChildren(fragment);
  $("empty").hidden=rows.length>0;$("empty").textContent=r.status!=="ok"?(r.error||"Lecture impossible."):all.length?"Aucun morceau ne correspond aux filtres.":"Aucun morceau dans ce scenario.";
  $("visible-count").textContent=`${rows.length} / ${all.length} titres`;
}
$("scenario-search").addEventListener("input",drawNav);$("track-search").addEventListener("input",drawTracks);
document.querySelectorAll("[data-filter]").forEach(button=>button.addEventListener("click",()=>{filter=button.dataset.filter;drawTracks();$("table-region").scrollTop=0;}));
document.querySelectorAll("[data-sort]").forEach(button=>button.addEventListener("click",()=>{if(sort===button.dataset.sort)direction*=-1;else{sort=button.dataset.sort;direction=1;}drawTracks();$("table-region").scrollTop=0;}));
$("export").addEventListener("click",()=>{const item=data.scenarios[selected];if(!item.payload)return;const blob=new Blob([JSON.stringify(item.payload,null,2)],{type:"application/json"});const url=URL.createObjectURL(blob);const a=el("a");a.href=url;a.download=`${item.result.source}-${item.result.kind}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
$("run-status").textContent=`${data.scenarios.filter(s=>s.result.status==="ok").length}/${data.scenarios.length} valides`;
$("run-date").textContent=new Date(data.created_at).toLocaleDateString("fr-FR",{day:"2-digit",month:"short"});
$("scenario-count").textContent=`${data.scenarios.length} scenarios`;
if(data.scenarios.length)select(0);else{$("collection-title").textContent="Aucun scenario";$("export").disabled=true;$("source-link").hidden=true;}
