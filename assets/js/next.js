/* next steps */
async function init(){
  const leads=await getJSON("data/leads.json");
  const pris=[...new Set(leads.map(l=>l.pri))].sort((a,b)=>a-b);
  document.getElementById("leads").innerHTML = pris.map(p=>{
    const ls=leads.filter(l=>l.pri===p);
    return `<h3>Priority ${esc(p)} (${ls.length})</h3>`+ls.map(l=>`
      <div class="card"><div style="display:flex;gap:10px;align-items:baseline;flex-wrap:wrap">
        <span class="mono" style="color:var(--muted)">${esc(l.id)}</span><b>${esc(l.t)}</b></div>
        ${l.d?`<p style="margin:.4rem 0 0">${esc(l.d)}</p>`:""}
        ${l.where?`<p style="margin:.3rem 0 0;font-size:.9rem;color:var(--muted)">Where to look: ${esc(l.where)}</p>`:""}
      </div>`).join("");
  }).join("");
  document.getElementById("counts").textContent = `${leads.length} open research leads`;
}
init().catch(e=>{document.getElementById("leads").innerHTML=`<p class="noimg">Could not load data: ${esc(e.message)}</p>`;});
