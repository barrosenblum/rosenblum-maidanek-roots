/* people directory + person detail */
let INDEX=[], FULL=null, FULL_LOADING=null;
const PER_PAGE=60;
let query="", page=0, branchFilter="";
async function full(){
  if(FULL) return FULL;
  if(!FULL_LOADING) FULL_LOADING = getJSON("data/people.json").then(d=>FULL=d);
  return FULL_LOADING;
}
function filtered(){
  const q=query.trim().toLowerCase();
  return INDEX.filter(p=>{
    if(branchFilter && brKey(p.br)!==branchFilter) return false;
    if(!q) return true;
    return ((p.en||"")+" "+(p.he||"")+" "+(p.sur||"")).toLowerCase().includes(q);
  });
}
function renderList(){
  const list=filtered(), pages=Math.max(1,Math.ceil(list.length/PER_PAGE));
  page=Math.min(page,pages-1);
  const slice=list.slice(page*PER_PAGE,(page+1)*PER_PAGE);
  document.getElementById("count").textContent =
    list.length.toLocaleString()+" people"+(query||branchFilter?" (filtered)":"");
  document.getElementById("rows").innerHTML = slice.map(p=>
    `<div class="person-row"><span class="dot" style="--c:var(--b-${esc(brKey(p.br))})" title="${esc(brName(p.br))}"></span>
     <div><a href="#p=${esc(p.id)}">${esc(p.en||"(name unknown)")}</a>
     ${p.he?`<span class="he" lang="he"> ${esc(p.he)}</span>`:""}
     <div class="meta">${esc(brName(p.br))}${p.by?" · b. "+esc(p.by):""}${p.gen!=null?" · gen "+esc(p.gen):""}</div></div></div>`
  ).join("") || `<p class="noimg">No matches.</p>`;
  document.getElementById("pager").innerHTML =
    `<button class="btn" id="prev" ${page===0?"disabled":""}>← Prev</button>
     <span>Page ${page+1} of ${pages}</span>
     <button class="btn" id="nxt" ${page>=pages-1?"disabled":""}>Next →</button>`;
  const pv=document.getElementById("prev"), nx=document.getElementById("nxt");
  if(pv) pv.onclick=()=>{page--;renderList();window.scrollTo({top:0,behavior:"smooth"});};
  if(nx) nx.onclick=()=>{page++;renderList();window.scrollTo({top:0,behavior:"smooth"});};
}
function claimHTML(c){
  const cit=(c.cit||[]).map(x=>`${esc(x.t)}${x.loc?` <span class="mono">${esc(x.loc)}</span>`:""}${x.q?` — “${esc(x.q)}”`:""}`).join("<br>");
  return `<div class="claim">${st(c.st)} ${esc(c.s)}
    ${cit?`<div class="cit">Source: ${cit}</div>`:""}
    ${c.note?`<details><summary>Research note</summary><div>${esc(c.note)}</div></details>`:""}</div>`;
}
async function renderDetail(id){
  const data=await full(), p=data[id];
  const el=document.getElementById("detail");
  if(!p){ el.innerHTML=`<p class="noimg">Person not found.</p>`; el.hidden=false; return; }
  const F=p.fam, link=(x)=>`<a href="#p=${esc(x.id)}">${esc(x.en||x.id)}</a>`;
  const famRows=[];
  if(F.parents.length) famRows.push(["Parents",F.parents.map(x=>link(x)+" "+st(x.st)).join(" · ")]);
  if(F.spouses.length) famRows.push([F.spouses.length>1?"Spouses":"Spouse",F.spouses.map(x=>link(x)+" "+st(x.st)).join(" · ")]);
  if(F.children.length) famRows.push(["Children",F.children.map(x=>link(x)).join(" · ")]);
  if(F.siblings.length) famRows.push(["Siblings",F.siblings.map(x=>link(x)).join(" · ")]);
  const evs=(p.ev||[]).slice().sort((a,b)=>(a.y||0)-(b.y||0));
  const KIND={birth:"Born",death:"Died",marriage:"Married",migration:"Moved",burial:"Buried",deportation:"Deported",residence:"Lived",event:"Event"};
  el.innerHTML=`
    <p><a href="#" id="back">← Back to directory</a></p>
    <div class="card">
      <div class="detail-head"><span class="dot" style="--c:var(--b-${esc(brKey(p.br))})"></span>
        <h2>${esc(p.en||"(name unknown)")}</h2></div>
      <p class="lede" style="margin-bottom:6px">${p.he?`<span class="he" lang="he">${esc(p.he)}</span> · `:""}${esc(p.life||"")}${p.br?` · ${esc(brName(p.br))} family`:""}</p>
      ${p.notes?`<p>${esc(p.notes)}</p>`:""}
      ${evs.length?`<h3>Life events</h3><dl class="facts">${evs.map(e=>`<dt>${esc(KIND[e.k]||e.k)}</dt><dd>${esc(e.d||"")}${e.pl?", "+esc(e.pl):""} ${e.st&&e.st!=="confirmed"?st(e.st):""}</dd>`).join("")}</dl>`:""}
      ${famRows.length?`<h3>Family</h3><dl class="facts">${famRows.map(([k,v])=>`<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>`:""}
      <h3>Records (${p.docs.filter(d=>d.img).length})</h3>
      ${p.docs.length?`<div class="thumbs">${p.docs.filter(d=>d.img).map(d=>
        `<button class="thumb" data-doc="${esc(d.id)}"><img loading="lazy" src="assets/images/t/${esc(d.id)}.jpg" alt="${esc(d.d)}" onerror="this.closest('.thumb').style.display='none'"><span>${esc(d.d)}</span></button>`
      ).join("")}</div>`:`<p class="noimg">No record images in hand yet.</p>`}
      <h3>Evidence (${p.cl.length} facts)</h3>
      ${p.cl.length?p.cl.map(claimHTML).join(""):`<p class="noimg">No facts recorded.</p>`}
    </div>`;
  el.hidden=false;
  document.getElementById("directory").hidden=true;
  document.getElementById("back").onclick=e=>{e.preventDefault();location.hash="";};
  el.querySelectorAll("[data-doc]").forEach(b=>b.onclick=()=>openLightbox(b.dataset.doc));
  window.scrollTo({top:0});
}
function openLightbox(did){
  const ov=document.createElement("div");
  ov.className="lightbox"; ov.setAttribute("role","dialog"); ov.setAttribute("aria-label","Record image");
  ov.innerHTML=`<img src="assets/images/${esc(did)}.jpg" alt="Record ${esc(did)}"><button class="btn">Close ✕</button><div class="cap">${esc(did)}</div>`;
  const close=()=>ov.remove();
  ov.querySelector("button").onclick=close;
  ov.onclick=e=>{if(e.target===ov)close();};
  document.addEventListener("keydown",function esc2(e){if(e.key==="Escape"){close();document.removeEventListener("keydown",esc2);}});
  document.body.appendChild(ov);
}
function route(){
  const m=location.hash.match(/^#p=(.+)$/);
  if(m){ renderDetail(decodeURIComponent(m[1])); }
  else{
    document.getElementById("detail").hidden=true;
    document.getElementById("directory").hidden=false;
    renderList();
  }
}
async function init(){
  INDEX=await getJSON("data/people_index.json");
  const sel=document.getElementById("branch");
  const used=[...new Set(INDEX.map(p=>brKey(p.br)).filter(Boolean))].sort();
  sel.innerHTML=`<option value="">All families</option>`+used.map(b=>`<option value="${esc(b)}">${esc(brName(b))}</option>`).join("");
  document.getElementById("q").addEventListener("input",e=>{query=e.target.value;page=0;renderList();});
  sel.addEventListener("change",e=>{branchFilter=e.target.value;page=0;renderList();});
  window.addEventListener("hashchange",route);
  route();
}
init().catch(e=>{document.getElementById("rows").innerHTML=`<p class="noimg">Could not load data: ${esc(e.message)}</p>`;});
