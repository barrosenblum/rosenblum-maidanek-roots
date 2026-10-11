/* full report */
async function init(){
  const [rep, branches] = await Promise.all([getJSON("data/report.json"), getJSON("data/branches.json")]);
  document.getElementById("findings").innerHTML = rep.solved.map(f=>`
    <div class="card"><h3 style="margin-top:0">${esc(f.t)}</h3><p>${esc(f.x)}</p>
    ${f.imgs&&f.imgs.length?`<div class="thumbs">${f.imgs.map(id=>
      `<button class="thumb" data-doc="${esc(id)}"><img loading="lazy" src="assets/images/t/${esc(id)}.jpg" alt="${esc(id)}" onerror="this.closest('.thumb').style.display='none'"><span>${esc(id)}</span></button>`
    ).join("")}</div>`:""}</div>`).join("");
  document.querySelectorAll("#findings [data-doc]").forEach(b=>b.onclick=()=>{
    const ov=document.createElement("div"); ov.className="lightbox";
    ov.innerHTML=`<img src="assets/images/${esc(b.dataset.doc)}.jpg" alt=""><button class="btn">Close ✕</button>`;
    ov.querySelector("button").onclick=()=>ov.remove(); ov.onclick=e=>{if(e.target===ov)ov.remove();};
    document.body.appendChild(ov);
  });
  const bk=Object.keys(branches);
  document.getElementById("families").innerHTML = bk.map(k=>{const b=branches[k];return `
    <div class="card branch-card" style="--bc:var(--b-${esc(k)})">
      <h3 style="margin-top:0">${esc(b.n)} <span style="color:var(--muted);font-weight:400;font-size:.9rem">· ${esc(b.place||"")}</span></h3>
      <p>${esc(b.x)}</p>
    </div>`;}).join("");
  const vc={site:"Earlier site is right",kb:"Research notes are right",neither:"Neither — see records",
    both_ok:"Both fine",split:"Split",unresolved:"Open"};
  document.getElementById("conflicts").innerHTML = rep.conflicts.length ? `
    <div class="tbl"><table><thead><tr><th>Question</th><th>Earlier site</th><th>What the records show</th><th>Decision</th></tr></thead>
    <tbody>${rep.conflicts.map(c=>`<tr><td><b>${esc(c.topic)}</b></td><td>${esc(c.site||"")}</td>
      <td>${esc(c.ev||"")}</td><td>${c.st==="resolved"&&c.verdict!=="unresolved"?esc(vc[c.verdict]||c.verdict||"")+' <span class="badge">settled</span>':st("open")}</td></tr>`).join("")}
    </tbody></table></div>` : `<p class="noimg">No recorded disagreements.</p>`;
  document.getElementById("corrections").innerHTML = rep.corrections.map(c=>`
    <div class="card"><p style="margin:0 0 .3rem"><b>Earlier:</b> ${esc(c.site)}</p>
    <p style="margin:0"><b>Records show:</b> ${esc(c.rec)} <span style="color:var(--muted)">(${esc(c.src)})</span></p></div>`).join("");
}
init().catch(e=>{document.getElementById("findings").innerHTML=`<p class="noimg">Could not load data: ${esc(e.message)}</p>`;});
