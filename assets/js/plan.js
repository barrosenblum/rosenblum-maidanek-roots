/* plan & status */
async function init(){
  const plan=await getJSON("data/plan.json");
  const s=plan.status||{};
  document.getElementById("plan").innerHTML = `
    <p class="lede">${esc(s.title||"Where we stand")}${s.updated?" — updated "+esc(s.updated):""}.</p>
    <div class="stack">${(s.sections||[]).map(x=>`<div class="card md"><h3 style="margin-top:0">${esc(x.h)}</h3>${x.html}</div>`).join("")}</div>
    ${(plan.changelog&&plan.changelog.length)?`<h3>Change log</h3><div class="card md">${plan.changelog.map(c=>`<p>${esc(c)}</p>`).join("")}</div>`:""}`;
}
init().catch(e=>{document.getElementById("plan").innerHTML=`<p class="noimg">Could not load data: ${esc(e.message)}</p>`;});
