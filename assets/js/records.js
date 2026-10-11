/* records gallery + sources */
function openLightbox(did, cap){
  const ov=document.createElement("div");
  ov.className="lightbox"; ov.setAttribute("role","dialog"); ov.setAttribute("aria-label","Record image");
  ov.innerHTML=`<img src="assets/images/${esc(did)}.jpg" alt="${esc(cap||did)}"><button class="btn">Close ✕</button><div class="cap">${esc(cap||did)}</div>`;
  const close=()=>ov.remove();
  ov.querySelector("button").onclick=close;
  ov.onclick=e=>{if(e.target===ov)close();};
  document.addEventListener("keydown",function esc2(e){if(e.key==="Escape"){close();document.removeEventListener("keydown",esc2);}});
  document.body.appendChild(ov);
}
async function init(){
  const [docs, sources] = await Promise.all([getJSON("data/documents.json"), getJSON("data/sources.json")]);
  const ids=Object.keys(docs).sort();
  const withImg=ids.filter(id=>docs[id].img), noImg=ids.filter(id=>!docs[id].img);
  const withImgOk=withImg.filter(id=>!docs[id].rej), withImgRej=withImg.filter(id=>docs[id].rej);
  const thumbHTML=id=>{const d=docs[id];return `
    <button class="thumb" data-doc="${esc(id)}" title="${esc(d.d||id)}">
      <img loading="lazy" src="assets/images/t/${esc(id)}.jpg" alt="${esc(d.d||id)}" onerror="this.closest('.thumb').style.display='none'">
      <span>${rejBadge(d.rej)}<b>${esc(id)}</b> · ${esc((d.d||"").slice(0,80))}</span>
    </button>`;};
  document.getElementById("gallery").innerHTML = withImgOk.map(thumbHTML).join("")
    +(withImgRej.length?`<details class="rejsec"><summary>Rejected / ruled out (${withImgRej.length})</summary><div class="thumbs">${withImgRej.map(thumbHTML).join("")}</div></details>`:"");
  document.querySelectorAll("[data-doc]").forEach(b=>b.onclick=()=>openLightbox(b.dataset.doc, docs[b.dataset.doc].d));
  const noImgOk=noImg.filter(id=>!docs[id].rej), noImgRej=noImg.filter(id=>docs[id].rej);
  const noImgItem=id=>`<li>${rejBadge(docs[id].rej)}<b>${esc(id)}</b> — ${esc(docs[id].d||"")}</li>`;
  document.getElementById("noimg-list").innerHTML = (noImgOk.length||noImgRej.length)
    ? `<h3>Described, image not yet in hand (${noImgOk.length})</h3><ul>`+
      noImgOk.map(noImgItem).join("")+`</ul>`
      +(noImgRej.length?`<details class="rejsec"><summary>Rejected / ruled out (${noImgRej.length})</summary><ul>${noImgRej.map(noImgItem).join("")}</ul></details>`:"") : "";
  const sids=Object.keys(sources).sort();
  const rows=sids.map(id=>{const s=sources[id];return `<tr id="src-${esc(id)}" data-t="${esc(((s.t||"")+" "+(s.repo||"")+" "+(s.ref||"")).toLowerCase())}">
    <td class="mono">${esc(id)}</td><td><b>${esc(s.t||"")}</b>${s.repo?`<br><span style="color:var(--muted)">${esc(s.repo)}</span>`:""}${s.ref?`<br><span class="mono">${esc(s.ref)}</span>`:""}</td>
    <td>${esc(s.kind||"")}</td>
    <td>${s.url?`<a href="${esc(s.url)}" target="_blank" rel="noopener">link</a>`:"—"}</td></tr>`;}).join("");
  document.getElementById("sources").innerHTML =
    `<div class="toolbar"><input type="search" id="sq" placeholder="Filter sources…" aria-label="Filter sources"></div>
     <div class="tbl"><table><thead><tr><th>ID</th><th>Title / repository</th><th>Kind</th><th>Link</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  document.getElementById("sq").addEventListener("input",e=>{
    const q=e.target.value.toLowerCase();
    document.querySelectorAll("#sources tbody tr").forEach(tr=>{
      tr.style.display = tr.dataset.t.includes(q)?"":"none";
    });
  });
  if(location.hash.startsWith("#src-")){const tr=document.getElementById(location.hash.slice(1));if(tr){tr.classList.add("hilite");tr.scrollIntoView({block:"center"});}}
  document.getElementById("counts").textContent = `${withImg.length} record images · ${sids.length} sources`;
}
init().catch(e=>{document.getElementById("gallery").innerHTML=`<p class="noimg">Could not load data: ${esc(e.message)}</p>`;});
