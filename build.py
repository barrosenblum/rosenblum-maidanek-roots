#!/usr/bin/env python3
"""Build the multi-file Rosenblum-Maidanek Roots website from kb/roots.sqlite.

Regenerates everything from scratch; safe to re-run every 12 hours (idempotent).
Usage:  python3 build.py   (run from ~/workspace/roots-project/website/)

Output layout (all under this directory):
  index.html, people.html, records.html, report.html, next-steps.html, plan.html
  assets/css/style.css, assets/js/*.js, assets/images/*.jpg (+ t/ thumbnails)
  data/*.json
"""
import ast
import html as _html
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)                       # ~/workspace/roots-project
KB_PATH = os.path.join(PROJ, "roots", "kb", "roots.sqlite")
IMG_SRC = os.path.join(PROJ, "roots", "site", "img")
META_SRC = os.path.join(PROJ, "roots", "site_src", "img_meta.json")
DOCP_SRC = os.path.join(PROJ, "roots", "site_src", "doc_persons.json")
BUILDDATA_SRC = os.path.join(PROJ, "roots", "site_src", "build_data.py")
sys.path.insert(0, os.path.join(PROJ, "roots", "site_src"))
from plan_links import build_plan

OUT_DATA = os.path.join(HERE, "data")
OUT_IMG = os.path.join(HERE, "assets", "images")
OUT_CSS = os.path.join(HERE, "assets", "css")
OUT_JS = os.path.join(HERE, "assets", "js")

# ---------------------------------------------------------------- text utils

def clean(s):
    if s is None:
        return None
    s = str(s)
    return (s.replace("\u2014", " - ").replace("\u2013", "-")
             .replace(" || ", ". ").replace(".. ", ". "))

# privacy scrub: never publish living-person locations (same rule as the old site)
SCRUB_CITIES = (" (Herzliya)", " (Ramat Gan)", "Kiryat Haim, 1999. ",
                " (Herzliya, 1999)", " (Ramat Gan, 1999)", "Herzliya", "Ramat Gan", "Kiryat Haim")

def scrub(s):
    if not s:
        return s
    for c in SCRUB_CITIES:
        s = s.replace(c, "")
    return s

# neutralize "Dad/family-personal" framing -> professional public tone
NEUTRAL = [
    ("Dad's Geni tree", "the family tree"),
    ("Dad's tree", "the family tree"),
    ("Dad's direct line", "the direct line"),
    ("Dad's question was whether", "The question was whether"),
    ("Dad's family knowledge", "family knowledge"),
    ("Saba Yehuda's", "Yehuda's"),
    ("Saba Yehuda", "Yehuda"),
    ("Saba's", "Yehuda's"),
    ("Saba ", "Yehuda "),
    ("Savta Rosa's", "Rosa's"),
    ("Savta Rosa", "Rosa"),
    ("your Geni tree", "the family tree"),
    ("in 'For Dad'", "in the report"),
    ("Edo's site", "the earlier family site"),
    ("Edo's notes", "earlier research notes"),
    ("Root of Edo's site and of this tree.", "Son of Yehuda Rosenblum and Miriam (Miza) Maidanek."),
]

def neutral(s):
    if not s:
        return s
    for a, b in NEUTRAL:
        s = s.replace(a, b)
    return s

def E(s):
    return _html.escape("" if s is None else str(s), quote=True)

ALIAS = {"n_yehudit_gafni": "n_jetti_m", "n_klara": "chaje", "n_moses_lm": "n_moses_marie"}
HIDE_REL_KINDS = {"same_as?"}
RANK = {"confirmed": 0, "partly": 1, "inference": 2, "open": 3, "unchecked": 4,
        "unsourced": 5, "contradicted": 6, "computed": 1}

# ---- rejected/refuted findings: mark unmistakably, don't present as live ----
_DOC_REJ = re.compile(
    r"checked and NOT\b|\brejected\b|\bruled out\b|"
    r"\bneither\b.{0,40}\bis ours\b|\bnot one of the siblings\b|"
    r"\ba lead, not a sibling\b", re.I)

def _doc_rejected(desc):
    return bool(desc and _DOC_REJ.search(desc))

_CLAIM_REJ_HEAD = re.compile(
    r"checked and rejected\b|HYPOTHESIS REJECTED|"
    r"REJECTED\s*\(do not reopen\)", re.I)

def _claim_rejected(cid, c):
    if c["st"] == "contradicted":
        return True
    if re.search(r"_rejected\b|_withdrawn\b", cid or ""):
        return True
    s = c["s"] or ""
    if "WITHDRAWN" in s:  # uppercase = deliberate editorial marker
        return True
    head = s[:120]
    return bool(_CLAIM_REJ_HEAD.search(head))

# ---- life-event dedup: one row per distinct fact, not one per KB stage ----
_YR = re.compile(r"\b(1[5-9]\d{2}|20\d{2})\b")

def _ev_years(date_text, year_min):
    yrs = set()
    if date_text:
        for m in _YR.finditer(date_text):
            yrs.add(m.group(1))
        # expand 2-digit range ends: "1941-42" -> {1941, 1942}
        for m in re.finditer(r"\b((?:1[5-9]|20)\d{2})-(\d{2})\b", date_text):
            base = int(m.group(1))
            century = (base // 100) * 100
            end = century + int(m.group(2))
            if end < base:
                end += 100
            for y in range(base, end + 1):
                yrs.add(str(y))
    if year_min:
        yrs.add(str(year_min))
    return yrs

def _ev_place_norm(place):
    p = (place or "").lower()
    p = re.sub(r"[^a-z\u00e0-\u024f\u0370-\u03ff\u0590-\u05ff0-9 ]", " ", p)
    return re.sub(r"\s+", " ", p).strip()

def _ev_compatible(a, b):
    """Two raw events describe the same fact: same kind, overlapping
    year(s) and compatible place. Genuine disagreements (different year or
    different place) are NOT compatible."""
    if a["k"] != b["k"]:
        return False
    ya, yb = _ev_years(a["d"], a["y"]), _ev_years(b["d"], b["y"])
    if ya and yb and not (ya & yb):
        return False
    pa, pb = _ev_place_norm(a["pl"]), _ev_place_norm(b["pl"])
    if pa and pb and pa != pb and pa not in pb and pb not in pa:
        return False
    if not ya and not yb:
        na = re.sub(r"\s+", " ", (a["d"] or "").lower()).strip()
        nb = re.sub(r"\s+", " ", (b["d"] or "").lower()).strip()
        if na != nb and na not in nb and nb not in na:
            return False
    return True

def _dedup_events(raw):
    # 1. drop exact dupes: same claim recorded twice for the same kind
    seen_cid, uniq = set(), []
    for e in raw:
        if e["c"]:
            key = (e["k"], e["c"])
            if key in seen_cid:
                continue
            seen_cid.add(key)
        uniq.append(e)
    # 2. group compatible (same-fact) events
    groups = []
    for e in uniq:
        for g in groups:
            if _ev_compatible(g[0], e):
                g.append(e)
                break
        else:
            groups.append([e])
    # 3. merge each group: most detailed text wins, citations union
    def _detail(e):
        d = e["d"] or ""
        digits = sum(ch.isdigit() for ch in d)
        return (digits, len(d))
    out = []
    for g in groups:
        best_d = max(g, key=_detail)
        best_pl = max(g, key=lambda e: len(e["pl"] or ""))
        cids = []
        for e in g:
            if e["c"] and e["c"] not in cids:
                cids.append(e["c"])
        sts = [e["st"] for e in g if e["st"]]
        worst = max(sts, key=lambda s: RANK.get(s, 9)) if sts else None
        ys = [e["y"] for e in g if e["y"]]
        out.append(dict(k=g[0]["k"], d=best_d["d"],
                        y=min(ys) if ys else None, pl=best_pl["pl"],
                        cids=cids, st=worst))
    return out
BR = {"rosenblum": "Rosenblum", "finkelstein": "Finkelstein", "maidanek": "Maidanek",
      "jurgrau": "Jurgrau", "besner": "Besner", "kurzberg": "Kurzberg",
      "laufer": "Laufer", "rafoul": "Rafoul", "horer": "Horer / Herer", "herer": "Herer"}
BRS = ["rosenblum", "finkelstein", "maidanek", "jurgrau", "besner",
       "kurzberg", "laufer", "rafoul", "horer"]

def br_key(b):
    return "horer" if b == "herer" else (b or "")

# ------------------------------------------------- curated literals (AST, no exec)

def curated_literals():
    tree = ast.parse(open(BUILDDATA_SRC, encoding="utf-8").read())
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in ("BRANCHES", "SOLVED", "CORRECTIONS"):
            out[node.targets[0].id] = ast.literal_eval(node.value)
    assert set(out) == {"BRANCHES", "SOLVED", "CORRECTIONS"}, "curated literals not found"
    return out

# ---------------------------------------------------------------- load the KB

def load_kb():
    c = sqlite3.connect(KB_PATH)
    c.row_factory = sqlite3.Row

    persons = {}
    for r in c.execute("SELECT * FROM persons"):
        if r["id"] in ALIAS:
            continue
        persons[r["id"]] = dict(
            id=r["id"], en=scrub(clean(r["name_en"])), he=r["name_he"],
            sur=r["surname"], sex=r["sex"], br=r["branch"],
            ahn=r["ahnentafel"], gen=r["generation"], by=r["birth_year_est"],
            byn=scrub(clean(r["birth_year_note"])), on=r["on_page"],
            notes=scrub(neutral(clean(r["notes"]))), ev=[], cl=[], docs=[])

    # person-specific display fixes carried over from the old site
    if "n_jetti_m" in persons:
        persons["n_jetti_m"].update(
            en="Yehudit (Yeti / Jetti) Gafni, n\u00e9e Maidanek",
            he="\u05d9\u05d4\u05d5\u05d3\u05d9\u05ea (\u05d9\u05d8\u05d9) \u05d2\u05e4\u05e0\u05d9 \u05dc\u05d1\u05d9\u05ea \u05de\u05d9\u05d9\u05d3\u05e0\u05e7",
            notes=("Daughter of Emil (Mendel) and Rosa; Miriam's younger sister, born c.1931. "
                   "Listed as 'Jetti Maidanek' on the Cyprus Camp 63 list (Jan 1948). As Yehudit Gafni "
                   "she filed the Yad Vashem pages for her father, grandparents and uncle, and the "
                   "survivor forms for Rosa and Miriam (1999-2007)."))
    if "chaje" in persons and persons["chaje"]["notes"]:
        persons["chaje"]["notes"] += (" Recorded as 'Klara Jurgrau' (daughter of Salomon & Chane) "
                                      "on the 1900 birth of her son Israel Mortko.")
    if "n_fani" in persons:
        persons["n_fani"]["en"] = "Fani Rosenblum (later Volman)"
    if "n_dona_jeni" in persons:
        persons["n_dona_jeni"]["en"] = "Dona Jeni (Dontzia) Maidanek"

    rels = []
    seen = set()
    for r in c.execute("SELECT * FROM relationships"):
        if r["kind"] in HIDE_REL_KINDS:
            continue
        a, b = ALIAS.get(r["a"], r["a"]), ALIAS.get(r["b"], r["b"])
        if r["a"] == "n_klara":
            continue
        key = (r["kind"], a, b)
        if key in seen or a == b or a not in persons or b not in persons:
            continue
        seen.add(key)
        rels.append(dict(k=r["kind"], a=a, b=b, st=r["status"], basis=scrub(clean(r["basis"]))))

    sources = {}
    for r in c.execute("SELECT * FROM sources"):
        sources[r["id"]] = dict(t=scrub(clean(r["title"])), kind=r["kind"],
                                repo=scrub(clean(r["repository"])),
                                ref=scrub(clean(r["reference"])),
                                url=r["url"], date=r["record_date"],
                                acc=r["access"], lang=r["language"])

    claims = {}
    for r in c.execute("SELECT * FROM claims"):
        pid = ALIAS.get(r["person_id"], r["person_id"])
        cit = [[x["source_id"], scrub(clean(x["locator"])), scrub(clean(x["quote"]))]
               for x in c.execute("SELECT * FROM citations WHERE claim_id=?", (r["id"],))]
        val = [[x["stage"], x["method"], x["result"], scrub(clean(x["detail"]))]
               for x in c.execute("SELECT * FROM validations WHERE claim_id=? ORDER BY id", (r["id"],))]
        claims[r["id"]] = dict(p=pid, k=r["kind"], s=scrub(neutral(clean(r["statement_en"]))),
                               st=r["status"], conf=r["confidence"], o=r["origin"],
                               pt=r["page_text"], note=scrub(neutral(clean(r["note"]))),
                               cit=cit, val=val)
        claims[r["id"]]["rej"] = _claim_rejected(r["id"], claims[r["id"]])
        if pid in persons:
            persons[pid]["cl"].append(r["id"])

    raw_ev = {}
    for r in c.execute("SELECT * FROM events ORDER BY year_min"):
        pid = ALIAS.get(r["person_id"], r["person_id"])
        if pid not in persons:
            continue
        st = claims.get(r["claim_id"], {}).get("st") if r["claim_id"] else None
        raw_ev.setdefault(pid, []).append(
            dict(k=r["kind"], d=scrub(clean(r["date_text"])),
                 y=r["year_min"], pl=scrub(clean(r["place"])),
                 c=r["claim_id"], st=st))
    for pid, raw in raw_ev.items():
        persons[pid]["ev"] = _dedup_events(raw)

    meta = json.load(open(META_SRC, encoding="utf-8"))
    try:
        doc_persons = {k: v["pp"] for k, v in
                       json.load(open(DOCP_SRC, encoding="utf-8")).items()}
    except Exception:
        doc_persons = {}
    docs = {}
    for r in c.execute("SELECT * FROM documents"):
        did = r["id"]
        m = meta.get(did)
        pp = [x for x in doc_persons.get(did, []) if x in persons]
        docs[did] = dict(src=r["source_id"], d=scrub(clean(r["description"])),
                         img=bool(m), w=m and m["w"], h=m and m["h"],
                         red=bool(m and m["redacted"]), pp=pp,
                         web=did.startswith("W"),
                         rej=_doc_rejected(scrub(clean(r["description"]))))
    for did, d in docs.items():
        for pid in d["pp"]:
            persons[pid]["docs"].append(did)

    conflicts = [dict(id=r["id"], topic=scrub(clean(r["topic"])), persons=r["persons"],
                      site=scrub(neutral(clean(r["site_says"]))),
                      kb=scrub(clean(r["kb_says"])),
                      ev=scrub(clean(r["evidence_says"])), verdict=r["verdict"],
                      sev=r["severity"], next=scrub(clean(r["next_step"])),
                      st=r["status"])
                 for r in c.execute("SELECT * FROM conflicts ORDER BY id")]
    leads = [dict(id=r["id"], pri=r["priority"], kind=r["kind"],
                  t=scrub(clean(r["title"])), d=scrub(clean(r["detail"])),
                  persons=r["persons"], where=scrub(clean(r["where_to_look"])),
                  acc=r["access"], st=r["status"])
             for r in c.execute("SELECT * FROM leads ORDER BY priority, id")]

    plan = build_plan(c)
    c.close()
    return persons, rels, sources, claims, docs, conflicts, leads, plan

# ------------------------------------------------------- relationship helpers

class RelIndex:
    def __init__(self, persons, rels):
        self.persons = persons
        self._parents = {}   # child -> [(parent_id, status)]
        self._children = {}  # parent -> [(child_id, status)]
        self._spouses = {}   # id -> [(spouse_id, status)]
        self._sib_rel = {}   # id -> [(sib_id, status)]
        for r in rels:
            if r["k"] == "parent":
                self._parents.setdefault(r["b"], []).append((r["a"], r["st"]))
                self._children.setdefault(r["a"], []).append((r["b"], r["st"]))
            elif r["k"] == "spouse":
                self._spouses.setdefault(r["a"], []).append((r["b"], r["st"]))
                self._spouses.setdefault(r["b"], []).append((r["a"], r["st"]))
            elif r["k"] == "sibling":
                self._sib_rel.setdefault(r["a"], []).append((r["b"], r["st"]))
                self._sib_rel.setdefault(r["b"], []).append((r["a"], r["st"]))

    def best_parents(self, pid):
        pick = {}
        for a, st in self._parents.get(pid, []):
            s = (self.persons[a]["sex"] or "?")
            if s not in pick or RANK.get(st, 9) < RANK.get(pick[s][1], 9):
                pick[s] = (a, st)
        return [dict(id=pick[s][0], st=pick[s][1]) for s in ("M", "F", "?") if s in pick]

    def siblings(self, pid):
        out = {}
        for p, _ in self._parents.get(pid, []):
            for c, st in self._children.get(p, []):
                if c != pid and c not in out:
                    out[c] = None
        for o, st in self._sib_rel.get(pid, []):
            out[o] = st
        return [dict(id=k, st=v) for k, v in out.items()]

    def spouses(self, pid):
        return [dict(id=o, st=st) for o, st in self._spouses.get(pid, [])]

    def children(self, pid):
        return [dict(id=o, st=st) for o, st in self._children.get(pid, [])]

def year_of(p, kind):
    evs = [e for e in p["ev"] if e["k"] == kind and e["y"] and e["st"] != "contradicted"]
    evs.sort(key=lambda e: RANK.get(e["st"], 3))
    return evs[0]["y"] if evs else None

def life(p):
    b = year_of(p, "birth")
    d = year_of(p, "death")
    if not b and p["by"]:
        b = "c.%d" % p["by"]
    if not b and not d:
        return ""
    return "%s%s" % (b or "?", ("-" + str(d)) if d else "")

def direct_set(ri, roots):
    s = set()
    def up(pid):
        if pid in s or pid not in ri.persons:
            return
        s.add(pid)
        for x in ri.best_parents(pid):
            up(x["id"])
    for r in roots:
        up(r)
    return s

# ------------------------------------------------------- pedigree layout (server-side)

def pedigree_layout(ri, root_id):
    """Port of the old site's drawPedigree: tidy ancestor tree, returns nodes/edges/size."""
    colW, rowH, cw, ch, pad = 230, 66, 188, 54, 14
    nodes, edges = [], []
    leaf, maxD = [0], [0]
    def place(pid, depth, seen):
        seen.add(pid)
        maxD[0] = max(maxD[0], depth)
        ys = []
        for x in ri.best_parents(pid):
            if x["id"] in seen or x["id"] not in ri.persons:
                continue
            y = place(x["id"], depth + 1, seen)
            ys.append(y)
            edges.append(dict(c=pid, p=x["id"], st=x["st"]))
        y = (min(ys) + max(ys)) / 2 if ys else leaf[0] * rowH
        if not ys:
            leaf[0] += 1
        nodes.append(dict(id=pid, depth=depth, y=y))
        return y
    if root_id in ri.persons:
        place(root_id, 0, set())
    W = pad * 2 + maxD[0] * colW + cw
    H = pad * 2 + max(leaf[0] - 1, 0) * rowH + ch
    return dict(nodes=nodes, edges=edges, W=W, H=H,
                colW=colW, rowH=rowH, cw=cw, ch=ch, pad=pad)

def pedigree_html(ri, persons, layout, root_id, title, lede, gen_labels):
    """Static HTML for one ancestor pedigree (cards absolutely positioned over SVG)."""
    pos = {n["id"]: (layout["pad"] + n["depth"] * layout["colW"],
                     layout["pad"] + n["y"]) for n in layout["nodes"]}
    cw, ch = layout["cw"], layout["ch"]
    def sty(s):
        if s == "confirmed":
            return 'stroke="var(--ok)"'
        if s in ("partly", "inference"):
            return 'stroke="var(--warn)" stroke-dasharray="7 4"'
        if s == "contradicted":
            return 'stroke="var(--bad)" stroke-dasharray="3 3"'
        return 'stroke="var(--grey)" stroke-dasharray="2 4"'
    paths = []
    for e in layout["edges"]:
        a, b = pos[e["c"]], pos[e["p"]]
        x1, y1, x2, y2 = a[0] + cw, a[1] + ch / 2, b[0], b[1] + ch / 2
        m = x1 + (x2 - x1) / 2
        paths.append('<path d="M%s %sH%sV%sH%s" fill="none" stroke-width="2" %s/>'
                     % (x1, y1, m, y2, x2, sty(e["st"])))
    cards = []
    for n in layout["nodes"]:
        p = persons[n["id"]]
        q = pos[n["id"]]
        sib = len(ri.siblings(n["id"]))
        lv = life(p)
        cards.append(
            '<a class="node" data-br="%s" href="people.html#p=%s" '
            'style="left:%spx;top:%spx;--bc:var(--b-%s)" title="%s%s">'
            '<span class="nm">%s</span>'
            '<span class="yr">%s%s</span></a>' % (
                E(br_key(p["br"])), E(n["id"]), q[0], q[1], E(br_key(p["br"])),
                E(p["en"]), E(" (" + lv + ")" if lv else ""),
                E(p["en"] or "?"), E(lv or "dates unknown"),
                ('<span class="sib" title="%d siblings on record">+%d</span>' % (sib, sib)) if sib else ""))
    labels = "".join("<div>%s</div>" % E(gen_labels[i] if i < len(gen_labels) else "")
                     for i in range(layout["nodes"] and max(n["depth"] for n in layout["nodes"]) + 1 or 0))
    return (
        '<div class="ped"><h3>%s</h3><p class="lede">%s</p>'
        '<div class="legend">'
        '<span><svg width="34" height="8"><line x1="0" y1="4" x2="34" y2="4" stroke="var(--ok)" stroke-width="2.5"/></svg> Confirmed by a record</span>'
        '<span><svg width="34" height="8"><line x1="0" y1="4" x2="34" y2="4" stroke="var(--warn)" stroke-width="2.5" stroke-dasharray="7 4"/></svg> Partly proven or inferred</span>'
        '<span><svg width="34" height="8"><line x1="0" y1="4" x2="34" y2="4" stroke="var(--grey)" stroke-width="2.5" stroke-dasharray="2 4"/></svg> From earlier research, not yet checked</span>'
        '</div>'
        '<div class="chips" data-chips></div>'
        '<div class="treebox"><div class="gen-labels" style="width:%spx">%s</div>'
        '<div class="canvas" style="width:%spx;height:%spx">'
        '<svg width="%s" height="%s" aria-hidden="true">%s</svg>%s'
        "</div></div></div>" % (
            E(title), E(lede), layout["W"], labels, layout["W"], layout["H"],
            layout["W"], layout["H"], "".join(paths), "".join(cards)))

# ------------------------------------------------------- data files

def write_json(name, obj):
    p = os.path.join(OUT_DATA, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    return os.path.getsize(p)

def build_data_files(persons, rels, sources, claims, docs, conflicts, leads, plan, ri):
    os.makedirs(OUT_DATA, exist_ok=True)
    today = date.today().strftime("%-d %b %Y")

    meta = dict(generated=today, people=len(persons),
                claims=len(claims), sources=len(sources),
                docs=len(docs), doc_images=sum(1 for d in docs.values() if d["img"]),
                confirmed=sum(1 for c in claims.values() if c["st"] == "confirmed"),
                open_leads=sum(1 for l in leads if l["st"] == "open"))
    write_json("meta.json", meta)

    # compact directory index
    index = [dict(id=p["id"], en=p["en"], he=p["he"], sur=p["sur"], sex=p["sex"],
                  br=p["br"], gen=p["gen"], by=p["by"])
             for p in persons.values()]
    index.sort(key=lambda x: (x["en"] or "").lower())
    write_json("people_index.json", index)

    # full person records (self-contained: claims embedded with citation text)
    full = {}
    for pid, p in persons.items():
        cl = []
        for cid in p["cl"]:
            c = claims.get(cid)
            if not c:
                continue
            cit = []
            for sid, loc, q in c["cit"]:
                s = sources.get(sid, {})
                cit.append(dict(sid=sid, t=s.get("t") or sid, loc=loc, q=q))
            cl.append(dict(id=cid, k=c["k"], s=c["s"], st=c["st"], conf=c["conf"],
                           note=c["note"], cit=cit, rej=c.get("rej", False)))
        cl.sort(key=lambda x: RANK.get(x["st"], 9))
        # attach deduped source refs to each (merged) life event for evidence links
        # (resolve via global claims: an event may cite another person's claim)
        ev = []
        for e in p["ev"]:
            srcs, seen_sid = [], set()
            for cid in e.get("cids", []):
                gc = claims.get(cid)
                if not gc:
                    continue
                for sid, loc, q in gc["cit"]:
                    if sid and sid not in seen_sid:
                        seen_sid.add(sid)
                        srcs.append([sid, sources.get(sid, {}).get("t") or sid])
            ev.append(dict(k=e["k"], d=e["d"], y=e["y"], pl=e["pl"],
                           st=e["st"], src=srcs,
                           rej=any(_claim_rejected(cid, claims[cid])
                                   for cid in e.get("cids", [])
                                   if cid in claims)))
        fam = dict(
            parents=[dict(id=x["id"], en=persons[x["id"]]["en"], st=x["st"])
                     for x in ri.best_parents(pid)],
            spouses=[dict(id=x["id"], en=persons[x["id"]]["en"], st=x["st"])
                     for x in ri.spouses(pid) if x["id"] in persons],
            children=[dict(id=x["id"], en=persons[x["id"]]["en"], st=x["st"])
                      for x in ri.children(pid) if x["id"] in persons],
            siblings=[dict(id=x["id"], en=persons[x["id"]]["en"])
                      for x in ri.siblings(pid) if x["id"] in persons],
        )
        fam["children"].sort(key=lambda x: persons[x["id"]]["by"] or 9999)
        dlist = []
        for did in p["docs"]:
            d = docs.get(did)
            if d:
                dlist.append(dict(id=did, d=d["d"], img=d["img"], red=d["red"],
                                  rej=d.get("rej", False)))
        full[pid] = dict(id=pid, en=p["en"], he=p["he"], sur=p["sur"], sex=p["sex"],
                         br=p["br"], by=p["by"], byn=p["byn"], life=life(p),
                         notes=p["notes"], ev=ev, fam=fam, cl=cl, docs=dlist)
    write_json("people.json", full)

    write_json("claims.json", claims)
    write_json("sources.json", sources)
    write_json("documents.json",
               {did: dict(src=d["src"], d=d["d"], img=d["img"], red=d["red"],
                          web=d["web"], pp=d["pp"], rej=d.get("rej", False))
                for did, d in docs.items()})
    write_json("leads.json", [l for l in leads if l["st"] == "open"])

    cur = curated_literals()
    branches = {k: dict(n=neutral(v[0]), place=v[1], x=neutral(scrub(clean(v[2]))))
                for k, v in cur["BRANCHES"].items()}
    write_json("branches.json", branches)
    write_json("report.json", dict(
        solved=[dict(t=neutral(scrub(clean(a))), x=neutral(scrub(clean(b))), imgs=d)
                for a, b, d in cur["SOLVED"]],
        corrections=[dict(site=neutral(scrub(clean(a))), rec=neutral(scrub(clean(b))),
                          src=neutral(scrub(clean(s))), imgs=d)
                     for a, b, s, d in cur["CORRECTIONS"]],
        conflicts=conflicts))
    write_json("plan.json", plan)
    return meta

# ------------------------------------------------------- CSS

CSS = """/* Rosenblum-Maidanek Roots - archival reading-room theme */
:root{
  --paper:#f4f2ec; --card:#fdfcf9; --ink:#1d2630; --muted:#5f6b75; --rule:#d5d9d4;
  --stamp:#5a3e9e; --stamp-soft:#e9e2f6;
  --ok:#2e7a4d; --ok-soft:#dcefe2; --warn:#9a6400; --warn-soft:#f6ead2;
  --bad:#b0281f; --bad-soft:#f7dedb; --open:#2a5fa3; --open-soft:#dce7f6;
  --grey:#7f8a94; --grey-soft:#e6eaec; --inf:#2b7a84; --inf-soft:#d9eef0;
  --b-rosenblum:#3d6ea8; --b-finkelstein:#6a8fc4; --b-maidanek:#8b5a2b; --b-jurgrau:#b07a1e;
  --b-besner:#c4964a; --b-kurzberg:#2d7c6b; --b-laufer:#5f9282; --b-rafoul:#7b4e8c;
  --b-horer:#9b6bab; --b-herer:#9b6bab; --b-:#aab3ba;
  --f-display:"Alegreya","Iowan Old Style",Georgia,serif;
  --f-body:"Alegreya Sans","Segoe UI",system-ui,sans-serif;
  --f-he:"Frank Ruhl Libre",David,"Times New Roman",serif;
  --f-mono:"IBM Plex Mono",ui-monospace,Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{background:var(--paper);color:var(--ink);font:16px/1.55 var(--f-body);margin:0}
a{color:var(--stamp)}
.wrap{max-width:1180px;margin:0 auto;padding-inline:16px}
.he{font-family:var(--f-he);direction:rtl;unicode-bidi:isolate}
.mono{font-family:var(--f-mono);font-size:.82em}

/* header + nav */
header.site{border-bottom:1px solid var(--rule);background:var(--card)}
header.site .wrap{padding-block:18px 0}
.eyebrow{font-size:.74rem;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);font-weight:700}
header.site h1{font:700 clamp(28px,4.6vw,42px)/1.05 var(--f-display);margin:.25rem 0;letter-spacing:-.01em}
header.site h1 .dash{color:var(--stamp)}
header.site .sub{color:var(--muted);margin:0 0 .4rem;max-width:72ch}
.stats{display:flex;flex-wrap:wrap;gap:6px 20px;margin:.6rem 0 .4rem;font-size:.92rem;color:var(--muted);font-variant-numeric:tabular-nums}
.stats b{color:var(--ink)}
nav.main{position:sticky;top:0;z-index:30;background:var(--card);border-bottom:1px solid var(--rule)}
nav.main .wrap{display:flex;gap:2px;overflow-x:auto;scrollbar-width:none;padding-block:0}
nav.main a{display:block;padding:12px 14px 10px;white-space:nowrap;color:var(--muted);font-weight:500;
  text-decoration:none;border-bottom:3px solid transparent}
nav.main a[aria-current="page"]{color:var(--ink);border-bottom-color:var(--stamp)}
main{padding-block:26px 70px}
h2{font:700 1.7rem/1.15 var(--f-display);margin:0 0 .35rem}
h3{font:700 1.25rem/1.2 var(--f-display);margin:1.4rem 0 .3rem}
.lede{color:var(--muted);max-width:74ch;margin:0 0 18px}
footer.site{border-top:1px solid var(--rule);color:var(--muted);font-size:.88rem;padding-block:22px}
footer.site .wrap{display:flex;flex-wrap:wrap;gap:6px 24px;justify-content:space-between}

/* badges, chips, buttons */
.st{display:inline-block;font-size:.72rem;font-weight:700;letter-spacing:.03em;padding:1px 7px;border-radius:4px;white-space:nowrap}
.st.confirmed{background:var(--ok-soft);color:var(--ok)} .st.partly{background:var(--warn-soft);color:var(--warn)}
.st.contradicted{background:var(--bad-soft);color:var(--bad)} .st.open{background:var(--open-soft);color:var(--open)}
.st.unchecked,.st.computed{background:var(--grey-soft);color:var(--grey)} .st.inference{background:var(--inf-soft);color:var(--inf)}
.badge{display:inline-block;font-size:.72rem;font-weight:700;padding:1px 7px;border-radius:4px;border:1px solid var(--rule);color:var(--muted)}
.btn{border:1px solid var(--rule);background:var(--card);border-radius:6px;padding:6px 14px;cursor:pointer;font:inherit}
.btn:hover{border-color:var(--stamp)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}
.chip{border:1px solid var(--rule);background:var(--card);border-radius:999px;padding:3px 11px;font-size:.86rem;
  cursor:pointer;display:inline-flex;align-items:center;gap:6px;font:inherit}
.chip[aria-pressed="true"]{background:var(--stamp-soft);border-color:var(--stamp)}
.chip .sw{width:10px;height:10px;border-radius:2px;background:var(--c,#aab3ba)}
.card{background:var(--card);border:1px solid var(--rule);border-radius:10px;padding:16px 18px;margin:0 0 14px}
.toolbar{display:flex;flex-wrap:wrap;gap:10px 16px;align-items:center;margin:0 0 14px}
input[type=search],select{font:inherit;padding:7px 12px;border:1px solid var(--rule);border-radius:7px;background:var(--card);color:var(--ink)}
input[type=search]{min-width:min(320px,100%)}

/* pedigree */
.ped{margin:0 0 34px}
.legend{display:flex;flex-wrap:wrap;gap:6px 20px;font-size:.85rem;color:var(--muted);margin:8px 0}
.legend span{display:inline-flex;align-items:center;gap:7px}
.treebox{overflow:auto;border:1px solid var(--rule);border-radius:10px;background:var(--card);max-height:70vh}
.gen-labels{display:flex;position:sticky;top:0;background:var(--card);border-bottom:1px solid var(--rule);z-index:2;font-size:.75rem;color:var(--muted)}
.gen-labels div{width:230px;flex:none;padding:6px 8px;font-weight:600}
.canvas{position:relative}
.canvas svg{position:absolute;inset:0}
a.node{position:absolute;width:188px;min-height:54px;background:var(--card);border:1px solid var(--rule);
  border-left:5px solid var(--bc,#aab3ba);border-radius:8px;padding:7px 10px;text-decoration:none;color:var(--ink);
  display:flex;flex-direction:column;justify-content:center;box-shadow:0 1px 3px rgba(20,25,30,.08)}
a.node:hover{border-color:var(--stamp)}
a.node .nm{font-weight:600;font-size:.92rem;line-height:1.25}
a.node .yr{font-size:.78rem;color:var(--muted)}
a.node .sib{font-size:.68rem;border:1px solid var(--rule);border-radius:8px;padding:0 5px;margin-left:6px;color:var(--muted)}
.treebox .dim{opacity:.22}
.branch-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px;margin-top:8px}
.branch-card{border-top:4px solid var(--bc,#aab3ba)}

/* people */
.person-row{display:flex;gap:12px;align-items:baseline;padding:9px 4px;border-bottom:1px solid var(--rule)}
.person-row a{font-weight:600;text-decoration:none;color:var(--ink)}
.person-row a:hover{color:var(--stamp)}
.person-row .meta{color:var(--muted);font-size:.88rem}
.dot{display:inline-block;width:10px;height:10px;border-radius:2px;background:var(--c,#aab3ba);flex:none;align-self:center}
.pager{display:flex;gap:8px;align-items:center;margin:16px 0;flex-wrap:wrap}
.detail-head{display:flex;flex-wrap:wrap;gap:8px 18px;align-items:baseline}
.detail-head h2{margin:0}
.facts{display:grid;grid-template-columns:130px 1fr;gap:4px 14px;margin:.4rem 0}
.facts dt{font-weight:600;color:var(--muted);font-size:.88rem}
.facts dd{margin:0}
.claim{border:1px solid var(--rule);border-radius:8px;padding:10px 14px;margin:0 0 10px;background:var(--card)}
.claim .cit{font-size:.86rem;color:var(--muted);margin-top:6px}
.claim summary{cursor:pointer;color:var(--stamp);font-size:.88rem}
.thumbs{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin-top:8px}
.thumb{border:1px solid var(--rule);border-radius:8px;overflow:hidden;background:var(--card);cursor:pointer;padding:0;font:inherit;color:inherit;text-align:left}
.thumb img{display:block;width:100%;height:110px;object-fit:cover;background:#e8e6df}
.thumb span{display:block;font-size:.76rem;color:var(--muted);padding:6px 8px;line-height:1.3}
.noimg{color:var(--muted);font-size:.9rem;font-style:italic}

/* records */
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px}
table{border-collapse:collapse;width:100%;font-size:.92rem}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--rule);vertical-align:top}
th{font-size:.78rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
.tbl{overflow-x:auto;background:var(--card);border:1px solid var(--rule);border-radius:10px}
.lightbox{position:fixed;inset:0;background:rgba(10,14,18,.92);z-index:100;display:flex;align-items:center;justify-content:center;padding:20px}
.lightbox img{max-width:100%;max-height:92vh;border-radius:6px;background:#fff}
.lightbox .cap{position:absolute;bottom:14px;left:0;right:0;text-align:center;color:#cfd6da;font-size:.9rem;padding:0 20px}
.lightbox button{position:absolute;top:14px;right:16px}

/* report / misc */
.stack{display:grid;gap:14px}
.md h4{font:700 1.05rem var(--f-display);margin:1rem 0 .3rem}
.md ul{margin:.3rem 0 .8rem;padding-left:1.3rem}
.md li{margin:.25rem 0}
.md p{margin:.4rem 0;max-width:78ch}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:760px){.grid2{grid-template-columns:1fr}.facts{grid-template-columns:1fr}}
.skip{position:absolute;left:-9999px}
.skip:focus{left:8px;top:8px;background:var(--card);padding:8px 14px;z-index:200;border:1px solid var(--stamp)}
</style>
""".replace("</style>\n", "")

# ------------------------------------------------------- JS

COMMON_JS = """/* shared helpers */
async function getJSON(path){
  const r = await fetch(path);
  if(!r.ok) throw new Error("failed to load "+path);
  return r.json();
}
const esc = s => String(s==null?"":s).replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const STNAME = {confirmed:"Confirmed",partly:"Partly proven",inference:"Inferred",open:"Open",
  unchecked:"Unchecked",unsourced:"Unsourced",contradicted:"Contradicted",computed:"Computed"};
const st = s => s ? `<span class="st ${esc(s)}">${esc(STNAME[s]||s)}</span>` : "";
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
"""

PEOPLE_JS = """/* people directory + person detail */
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
"""

RECORDS_JS = """/* records gallery + sources */
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
  document.getElementById("gallery").innerHTML = withImg.map(id=>{const d=docs[id];return `
    <button class="thumb" data-doc="${esc(id)}" title="${esc(d.d||id)}">
      <img loading="lazy" src="assets/images/t/${esc(id)}.jpg" alt="${esc(d.d||id)}" onerror="this.closest('.thumb').style.display='none'">
      <span><b>${esc(id)}</b> · ${esc((d.d||"").slice(0,80))}</span>
    </button>`;}).join("");
  document.querySelectorAll("[data-doc]").forEach(b=>b.onclick=()=>openLightbox(b.dataset.doc, docs[b.dataset.doc].d));
  document.getElementById("noimg-list").innerHTML = noImg.length
    ? `<h3>Described, image not yet in hand (${noImg.length})</h3><ul>`+
      noImg.map(id=>`<li><b>${esc(id)}</b> — ${esc(docs[id].d||"")}</li>`).join("")+`</ul>` : "";
  const sids=Object.keys(sources).sort();
  const rows=sids.map(id=>{const s=sources[id];return `<tr data-t="${esc(((s.t||"")+" "+(s.repo||"")+" "+(s.ref||"")).toLowerCase())}">
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
  document.getElementById("counts").textContent = `${withImg.length} record images · ${sids.length} sources`;
}
init().catch(e=>{document.getElementById("gallery").innerHTML=`<p class="noimg">Could not load data: ${esc(e.message)}</p>`;});
"""

REPORT_JS = """/* full report */
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
"""

NEXT_JS = """/* next steps */
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
"""

PLAN_JS = """/* plan & status */
async function init(){
  const plan=await getJSON("data/plan.json");
  const s=plan.status||{};
  document.getElementById("plan").innerHTML = `
    <p class="lede">${esc(s.title||"Where we stand")}${s.updated?" — updated "+esc(s.updated):""}.</p>
    <div class="stack">${(s.sections||[]).map(x=>`<div class="card md"><h3 style="margin-top:0">${esc(x.h)}</h3>${x.html}</div>`).join("")}</div>
    ${(plan.changelog&&plan.changelog.length)?`<h3>Change log</h3><div class="card md">${plan.changelog.map(c=>`<p>${esc(c)}</p>`).join("")}</div>`:""}`;
}
init().catch(e=>{document.getElementById("plan").innerHTML=`<p class="noimg">Could not load data: ${esc(e.message)}</p>`;});
"""

# ------------------------------------------------------- page assembly

PAGES = [
    ("index.html", "Family Tree"),
    ("people.html", "People"),
    ("records.html", "Records"),
    ("report.html", "Full Report"),
    ("next-steps.html", "Next Steps"),
    ("plan.html", "Plan & Status"),
]

FONTS = ("https://fonts.googleapis.com/css2?family=Alegreya:wght@500;700"
         "&family=Alegreya+Sans:ital,wght@0,400;0,500;0,700;1,400"
         "&family=Frank+Ruhl+Libre:wght@400;500&family=IBM+Plex+Mono:wght@400;500&display=swap")

def page_shell(active, title, body, scripts, meta, lede_sub):
    nav = "".join(
        '<a href="%s"%s>%s</a>' % (href, ' aria-current="page"' if href == active else "", label)
        for href, label in PAGES)
    stats = ('<span><b>%s</b> people</span><span><b>%s</b> record images</span>'
             '<span><b>%s</b> of %s facts confirmed</span><span>Updated %s</span>' % (
                 f"{meta['people']:,}", f"{meta['doc_images']:,}",
                 f"{meta['confirmed']:,}", f"{meta['claims']:,}", E(meta["generated"])))
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="description" content="Rosenblum-Maidanek Roots - a family research archive for the Rosenblum, Maidanek, Kurzberg, Jurgrau and related families of Bukovina, Moldavia and Israel. Every fact tied to its record.">
<title>%s &middot; Rosenblum-Maidanek Roots</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="%s">
<link rel="stylesheet" href="assets/css/style.css">
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="site"><div class="wrap">
<div class="eyebrow">Family Archive &middot; Bukovina, Moldavia, Israel</div>
<h1>Rosenblum<span class="dash">-</span>Maidanek Roots</h1>
<p class="sub">%s</p>
<div class="stats">%s</div>
</div></header>
<nav class="main" aria-label="Sections"><div class="wrap">%s</div></nav>
<main id="main"><div class="wrap">
%s
</div></main>
<footer class="site"><div class="wrap">
<span>Rosenblum-Maidanek Roots &middot; a family research archive</span>
<span>Every fact tied to the record it comes from. Living people appear by name and relationship only.</span>
<span>Updated %s</span>
</div></footer>
%s
</body>
</html>
""" % (E(title), FONTS, lede_sub, stats, nav, body, E(meta["generated"]),
       "\n".join('<script src="%s"></script>' % s for s in scripts))

LEDE = ("The Rosenblum, Maidanek, Kurzberg, Jurgrau and related families - "
        "researched fact by fact from civil records, archives and family knowledge.")

def build_pages(persons, rels, docs, meta, branches, ri, direct):
    ped_y = pedigree_layout(ri, "yehuda") if "yehuda" in persons else None
    ped_m = pedigree_layout(ri, "miriam") if "miriam" in persons else None
    gen_labels = (lambda root: [root, "Parents", "Grandparents", "Great-grandparents",
                                "2x great-grandparents", "3x great-grandparents",
                                "4x great-grandparents", "5x great-grandparents"])
    tree_html = ""
    if ped_y and ped_y["nodes"]:
        tree_html += pedigree_html(
            ri, persons, ped_y, "yehuda", "Yehuda Rosenblum's line",
            "Yehuda (1925-2007) on the left, his ancestors to the right. "
            "Line style shows how well each parent link is proven; select a family to highlight it.",
            gen_labels("Yehuda"))
    if ped_m and ped_m["nodes"]:
        tree_html += pedigree_html(
            ri, persons, ped_m, "miriam", "Miriam Maidanek's line",
            "Miriam 'Miza' (1926-1999) on the left, her ancestors to the right.",
            gen_labels("Miriam"))
    branch_cards = "".join(
        '<div class="card branch-card" style="--bc:var(--b-%s)"><h3 style="margin-top:0">%s</h3>'
        '<p style="color:var(--muted);font-size:.88rem;margin:.1rem 0 .5rem">%s</p><p>%s</p></div>' % (
            E(k), E(v["n"]), E(v["place"] or ""), E(v["x"]))
        for k, v in branches.items())
    index_body = """
<h2>Family tree</h2>
<p class="lede">Two family lines - the Rosenblums of Galati and the Maidaneks of Bukovina -
brought together by Yehuda Rosenblum and Miriam Maidanek. Click any person for their facts, family and records.</p>
%s
<h2>The families</h2>
<p class="lede">Nine family lines researched so far, each with its home town and story.</p>
<div class="branch-grid">%s</div>
""" % (tree_html, branch_cards)

    people_body = """
<h2>People</h2>
<p class="lede">Everyone in the archive - %s people. Search by any spelling, in English or Hebrew, or filter by family.</p>
<div id="directory">
<div class="toolbar">
<input type="search" id="q" placeholder="Search names…" aria-label="Search names">
<select id="branch" aria-label="Filter by family"></select>
</div>
<p id="count" class="lede"></p>
<div id="rows"></div>
<div class="pager" id="pager"></div>
</div>
<section id="detail" hidden aria-live="polite"></section>
""" % f"{meta['people']:,}"
    records_body = """
<h2>Records</h2>
<p class="lede">Every record image in the archive, then the full list of sources with references. Select an image to read it.</p>
<p id="counts" class="lede"></p>
<div class="gallery" id="gallery"></div>
<div id="noimg-list"></div>
<h3>Sources</h3>
<div id="sources"></div>
"""
    report_body = """
<h2>Full report</h2>
<p class="lede">The research so far: key findings, the families, where records disagree, and corrections
to earlier research. Each person's full evidence - every fact with its source - is on their
<a href="people.html">People</a> page.</p>
<h3>Key findings</h3>
<div id="findings" class="stack"></div>
<h3>The families</h3>
<div id="families" class="stack"></div>
<h3>Where the records disagree</h3>
<div id="conflicts"></div>
<h3>Corrections to earlier research</h3>
<div id="corrections" class="stack"></div>
"""
    next_body = """
<h2>Next steps</h2>
<p class="lede">What is still open and where to look next. The archive grows as each lead is worked.</p>
<p id="counts" class="lede"></p>
<div id="leads"></div>
"""
    plan_body = """
<h2>Plan &amp; status</h2>
<p class="lede">Where the research stands. Rebuilt from the project's status log every time the archive is updated.</p>
<div id="plan"></div>
"""
    pages = {
        "index.html": ("Family Tree", index_body,
                       ["assets/js/common.js"],
                       "<script>document.querySelectorAll('.ped').forEach(initChips);</script>"),
        "people.html": ("People", people_body,
                        ["assets/js/common.js", "assets/js/people.js"], ""),
        "records.html": ("Records", records_body,
                         ["assets/js/common.js", "assets/js/records.js"], ""),
        "report.html": ("Full Report", report_body,
                        ["assets/js/common.js", "assets/js/report.js"], ""),
        "next-steps.html": ("Next Steps", next_body,
                            ["assets/js/common.js", "assets/js/next.js"], ""),
        "plan.html": ("Plan & Status", plan_body,
                      ["assets/js/common.js", "assets/js/plan.js"], ""),
    }
    for href, (title, body, scripts, extra) in pages.items():
        out = page_shell(href, title, body, scripts, meta, LEDE)
        if extra:
            out = out.replace("</body>", extra + "\n</body>")
        with open(os.path.join(HERE, href), "w", encoding="utf-8") as f:
            f.write(out)

# ------------------------------------------------------- images

def copy_images(docs):
    os.makedirs(os.path.join(OUT_IMG, "t"), exist_ok=True)
    n_full = n_th = 0
    for f in sorted(os.listdir(IMG_SRC)):
        if not f.endswith(".jpg"):
            continue
        did = f[:-4]
        if did not in docs or not docs[did]["img"]:
            continue
        shutil.copy2(os.path.join(IMG_SRC, f), os.path.join(OUT_IMG, f))
        n_full += 1
    tsrc = os.path.join(IMG_SRC, "t")
    if os.path.isdir(tsrc):
        for f in sorted(os.listdir(tsrc)):
            if not f.endswith(".jpg"):
                continue
            if f[:-4] not in docs or not docs[f[:-4]]["img"]:
                continue
            shutil.copy2(os.path.join(tsrc, f), os.path.join(OUT_IMG, "t", f))
            n_th += 1
    return n_full, n_th

# ------------------------------------------------------- validation

def validate(meta):
    errs = []
    # 1. pages exist and have basic structure
    for href, label in PAGES:
        p = os.path.join(HERE, href)
        if not os.path.exists(p):
            errs.append("missing page " + href)
            continue
        s = open(p, encoding="utf-8").read()
        for needle in ("<!doctype html>", "<main", "</main>", "Rosenblum-Maidanek Roots",
                       'href="assets/css/style.css"'):
            if needle not in s:
                errs.append("%s missing %r" % (href, needle))
    # 2. JSON parses
    for name in ("meta.json", "branches.json", "people_index.json", "people.json",
                 "claims.json", "sources.json", "documents.json", "leads.json",
                 "report.json", "plan.json"):
        p = os.path.join(OUT_DATA, name)
        try:
            json.load(open(p, encoding="utf-8"))
        except Exception as e:
            errs.append("bad JSON %s: %s" % (name, e))
    # 3. images exist and are referenced correctly
    docs = json.load(open(os.path.join(OUT_DATA, "documents.json"), encoding="utf-8"))
    missing = [did for did, d in docs.items() if d["img"]
               and not os.path.exists(os.path.join(OUT_IMG, did + ".jpg"))]
    if missing:
        errs.append("missing full images: %s" % missing[:5])
    missing_t = [did for did, d in docs.items() if d["img"]
                 and not os.path.exists(os.path.join(OUT_IMG, "t", did + ".jpg"))]
    if missing_t:
        errs.append("missing thumbnails: %s" % missing_t[:5])
    # 4. people.json cross-refs resolve
    people = json.load(open(os.path.join(OUT_DATA, "people.json"), encoding="utf-8"))
    bad = 0
    for pid, p in people.items():
        for sec in ("parents", "spouses", "children", "siblings"):
            for x in p["fam"][sec]:
                if x["id"] not in people:
                    bad += 1
    if bad:
        errs.append("dangling family refs: %d" % bad)
    # 5. no em/en dashes anywhere user-facing
    for href, _ in PAGES:
        s = open(os.path.join(HERE, href), encoding="utf-8").read()
        if "\u2014" in s or "\u2013" in s:
            errs.append(href + " contains em/en dash")
    return errs

# ------------------------------------------------------- main

def main():
    # fresh output dirs (idempotent)
    for d in (OUT_DATA,):
        shutil.rmtree(d, ignore_errors=True)
    os.makedirs(OUT_IMG, exist_ok=True)
    for f in os.listdir(OUT_IMG):
        fp = os.path.join(OUT_IMG, f)
        if os.path.isdir(fp):
            shutil.rmtree(fp)
        else:
            os.remove(fp)
    os.makedirs(OUT_DATA, exist_ok=True)
    os.makedirs(OUT_CSS, exist_ok=True)
    os.makedirs(OUT_JS, exist_ok=True)
    os.makedirs(os.path.join(OUT_IMG, "t"), exist_ok=True)

    persons, rels, sources, claims, docs, conflicts, leads, plan = load_kb()
    ri = RelIndex(persons, rels)
    direct = direct_set(ri, ["yehuda", "miriam"])

    meta = build_data_files(persons, rels, sources, claims, docs, conflicts, leads, plan, ri)
    branches = json.load(open(os.path.join(OUT_DATA, "branches.json"), encoding="utf-8"))

    # assets
    with open(os.path.join(OUT_CSS, "style.css"), "w", encoding="utf-8") as f:
        f.write(CSS)
    js_files = {"common.js": COMMON_JS, "people.js": PEOPLE_JS, "records.js": RECORDS_JS,
                "report.js": REPORT_JS, "next.js": NEXT_JS, "plan.js": PLAN_JS}
    for name, src in js_files.items():
        with open(os.path.join(OUT_JS, name), "w", encoding="utf-8") as f:
            f.write(src)

    n_full, n_th = copy_images(docs)
    build_pages(persons, rels, docs, meta, branches, ri, direct)

    errs = validate(meta)
    print("people %d claims %d sources %d docs %d (%d images)"
          % (meta["people"], meta["claims"], meta["sources"],
             len(docs), meta["doc_images"]))
    print("images copied: %d full + %d thumbs" % (n_full, n_th))
    if errs:
        print("VALIDATION ERRORS:")
        for e in errs:
            print(" -", e)
        sys.exit(1)
    print("build OK")

if __name__ == "__main__":
    main()
