/* shared helpers */
async function getJSON(path){
  const r = await fetch(path);
  if(!r.ok) throw new Error("failed to load "+path);
  return r.json();
}
const esc = s => String(s==null?"":s).replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const STNAME = {confirmed:"Confirmed",partly:"Partly proven",inference:"Inferred",open:"Open",
  unchecked:"Unchecked",unsourced:"Unsourced",contradicted:"Contradicted",computed:"Computed"};
const st = s => s ? `<span class="st ${esc(s)}">${esc(STNAME[s]||s)}</span>` : "";
const rejBadge = r => r ? `<span class="st rejected">Rejected</span>` : "";
const BR = {rosenblum:"Rosenblum",finkelstein:"Finkelstein",maidanek:"Maidanek",jurgrau:"Jurgrau",
  besner:"Besner",kurzberg:"Kurzberg",laufer:"Laufer",rafoul:"Rafoul",horer:"Horer / Herer",herer:"Herer"};
const brKey = b => b==="herer" ? "horer" : (b||"");
const brName = b => BR[b] || b || "Unknown";
/* branch highlight chips (pedigree pages) */
function initChips(container){
  const box = container.querySelector("[data-chips]");
  if(!box) return;
  const branches = [...new Set([...container.querySelectorAll("[data-br]")].map(el=>el.dataset.br))].filter(Boolean);
  box.innerHTML = branches.map(b=>
    `<button class="chip" aria-pressed="false" data-br="${esc(b)}" style="--c:var(--b-${esc(b)})"><i class="sw"></i>${esc(brName(b))}</button>`
  ).join("") + `<button class="chip" data-br="*">Show all</button>`;
  box.addEventListener("click", e=>{
    const btn = e.target.closest("[data-br]"); if(!btn) return;
    const k = btn.dataset.br;
    const chips = [...box.querySelectorAll("[data-br]")];
    if(k === "*"){ chips.forEach(c=>c.setAttribute("aria-pressed","false")); }
    else {
      const on = btn.getAttribute("aria-pressed")==="true";
      btn.setAttribute("aria-pressed", String(!on));
      box.querySelector('[data-br="*"]').setAttribute("aria-pressed","false");
    }
    const active = chips.filter(c=>c.dataset.br!=="*" && c.getAttribute("aria-pressed")==="true").map(c=>c.dataset.br);
    container.querySelectorAll("[data-br]").forEach(el=>{
      if(el.closest("[data-chips]")) return;
      el.classList.toggle("dim", active.length>0 && !active.includes(el.dataset.br));
    });
  });
}
