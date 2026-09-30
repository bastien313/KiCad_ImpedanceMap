"""Carte interactive du board pour le rapport HTML (SVG + JavaScript intégrés, sans dépendance).

La carte montre ce que le calcul a réellement vu :
  * le **cuivre final** de chaque couche (pistes, pads, pastilles de vias, remplissages de zones), net
    analysé en gris foncé, autres nets en gris clair, cuivre sans net en pointillés ; perçages en blanc ;
    une case à cocher par couche (couches des cibles cochées par défaut) ;
  * chaque point calculé est une **bande de la largeur de cuivre mesurée dans sa coupe** (et non la
    largeur nominale de la piste), colorée selon Z ; au survol, la **ligne de coupe** (fenêtre du
    solveur 2D) est tracée : tout le cuivre qu'elle traverse, sur toutes les couches, est dans le calcul ;
  * les points non calculés consécutifs de même cause forment **un** tronçon hachuré avec un seul
    repère (✕ discontinuité, ▲ perte de référence).

Échelles de couleur au choix : Ω centrée sur la cible (une cible à la fois), Ω libre (min / max), écart
en %. Infobulles, zoom à la molette, déplacement à la souris, double-clic pour réinitialiser. Sans
JavaScript, la carte s'affiche avec les couleurs « centrée sur la cible » (précalculées).
"""

from __future__ import annotations

import html
import json
import math
from typing import Dict

STOPS = ["#2a78d6", "#b7d3f6", "#c9c8c2", "#f4b6b2", "#e34948"]
SIMPLIFY = 3e-6          # tolérance de simplification des contours de cuivre (m)


def _hex(c):
    return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))


def color_at(t: float) -> str:
    """Couleur du dégradé pour t ∈ [0, 1] (0 = bas, 0,5 = cible, 1 = haut)."""
    t = min(1.0, max(0.0, t))
    k = min(int(t * 4), 3)
    u = t * 4 - k
    a, b = _hex(STOPS[k]), _hex(STOPS[k + 1])
    return "#%02x%02x%02x" % tuple(round(a[i] + (b[i] - a[i]) * u) for i in range(3))


def _f(v, n=3):
    return f"{v:.{n}f}"


def _poly_d(geom) -> str:
    """Chemin SVG (mm) d'un (Multi)Polygon shapely en mètres, trous compris (fill-rule evenodd)."""
    out = []
    polys = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    for pg in polys:
        if pg.geom_type != "Polygon" or pg.is_empty:
            continue
        for ring in [pg.exterior, *pg.interiors]:
            c = list(ring.coords)
            if len(c) < 3:
                continue
            out.append("M" + "L".join(f"{_f(x * 1e3)} {_f(y * 1e3)}" for x, y in c[:-1]) + "Z")
    return "".join(out)


def _line_d(pts) -> str:
    return "M" + "L".join(f"{_f(x)} {_f(y)}" for x, y in pts)


def _copper_layers(bm, box_m, target_nets, active_layers):
    """(cuivre, contours de pads) : groupes SVG par couche, découpés à la vue. Les contours des pads du
    net analysé sont rendus à part pour rester visibles au-dessus des bandes colorées."""
    from shapely.geometry import Point, box

    view = box(*box_m)
    out, pads_out = [], []
    for L in bm.copper_names:
        hidden = "" if L in active_layers else ' style="display:none"'
        pads = []
        lc = bm.layer_copper(L)
        items = []
        for part, net in zip(lc.parts, lc.nets):
            if not part.intersects(view):
                continue
            g = part.intersection(view).simplify(SIMPLIFY, preserve_topology=True)
            d = _poly_d(g)
            if d:
                cls = "t" if net in target_nets else ("f" if net == "" else "g")
                items.append(f'<path class="{cls}" d="{d}"><title>{html.escape(net or "sans net")} · {L}</title></path>')
        for p in bm.pads:
            sh = p.shapes.get(L)
            if p.net in target_nets and sh is not None and sh.intersects(view):
                d = _poly_d(sh.simplify(SIMPLIFY))
                if d:
                    tip = f"pad {p.ref} · {p.net} · {L}".replace("pad  ·", "pad ·")
                    pads.append(f'<path class="pd" d="{d}"><title>{html.escape(tip)}</title></path>')
        holes = [(v.pos, v.drill) for v in bm.vias if L in v.layers] + \
                [(p.pos, p.drill) for p in bm.pads if p.is_through]
        for pos, dr in holes:
            if dr > 0 and view.intersects(Point(pos)):
                items.append(f'<circle class="dr" cx="{_f(pos[0] * 1e3)}" cy="{_f(pos[1] * 1e3)}" r="{_f(dr * 5e2, 4)}"/>')
        if items:
            out.append(f'<g class="cu" data-L="{html.escape(L)}"{hidden}>{"".join(items)}</g>')
        if pads:
            pads_out.append(f'<g class="cu" data-L="{html.escape(L)}"{hidden}>{"".join(pads)}</g>')
    return "".join(reversed(out)), "".join(reversed(pads_out))     # couche du dessus dessinée en dernier


def _span_pts(pts, i):
    p = pts[i]
    a = pts[i - 1] if i > 0 and pts[i - 1] is not None else p
    b = pts[i + 1] if i < len(pts) - 1 and pts[i + 1] is not None else p
    return ((a[0] + p[0]) / 2, (a[1] + p[1]) / 2), p, ((b[0] + p[0]) / 2, (b[1] + p[1]) / 2)


def board_svg(run, bm=None) -> str:
    spans = []      # (d, largeur mm, target index, z, zt, tol, label, s_mm, layer, kind, cut, w_mm, half_mm)
    runs = []       # tronçons non calculés : (d, largeur mm, marqueur (x, y), status, label, s0, s1, n, why, ti, z)
    xs, ys, halves = [], [], []
    target_nets = set()
    active_layers = set()
    for ti, tr in enumerate(run.targets):
        zt, tol, kind = tr.target["z_target"], tr.target["tol"], tr.target["kind"]
        target_nets.update(tr.target["nets"])
        by: Dict = {}
        for s in tr.samples:
            by.setdefault((s.net, s.path_id), []).append(s)
            active_layers.add(s.layer)
        for smp in by.values():
            smp.sort(key=lambda s: s.s)
            p_pts = [(s.x * 1e3, s.y * 1e3) for s in smp]
            c_pts = [((s.cx if math.isfinite(s.cx) else s.x) * 1e3, (s.cy if math.isfinite(s.cy) else s.y) * 1e3)
                     for s in smp]
            # brin N : point réellement en face (relevé par le moteur), jamais une symétrie du brin P
            n_pts = [(s.nx * 1e3, s.ny * 1e3) if math.isfinite(getattr(s, "nx", float("nan"))) else None
                     for s in smp]
            strands = [(p_pts, "p")] + ([(n_pts, "n")] if kind == "pair" else [])
            known = p_pts + [q for q in n_pts if q is not None]
            xs += [q[0] for q in known]
            ys += [q[1] for q in known]
            i = 0
            while i < len(smp):
                s = smp[i]
                if s.valid:
                    half = s.half * 1e3 if math.isfinite(s.half) else float("nan")
                    if math.isfinite(half):
                        halves.append(half)
                    nx, ny = -s.ty, s.tx
                    c = c_pts[i]
                    cut = (f"{_f(c[0] - nx * half)} {_f(c[1] - ny * half)} {_f(c[0] + nx * half)} "
                           f"{_f(c[1] + ny * half)}") if math.isfinite(half) else ""
                    for pts, which in strands:
                        if pts[i] is None:
                            continue
                        a, p, b = _span_pts(pts, i)
                        w = s.wn if (which == "n" and math.isfinite(s.wn)) else s.width
                        spans.append((_line_d([a, p, b]), w * 1e3, ti, s.z, zt, tol, tr.label, s.s * 1e3, s.layer,
                                      kind, cut, w * 1e3, half))
                    i += 1
                    continue
                j = i
                while j + 1 < len(smp) and not smp[j + 1].valid and \
                        (smp[j + 1].status == "no_reference") == (s.status == "no_reference"):
                    j += 1
                why = " ; ".join(dict.fromkeys(x.reason for x in smp[i:j + 1] if x.reason))
                for pts, which in strands:
                    seg_pts = [q for q in pts[i:j + 1] if q is not None]
                    if not seg_pts:
                        continue
                    a = _span_pts(pts, i)[0] if pts[i] is not None else seg_pts[0]
                    b = _span_pts(pts, j)[2] if pts[j] is not None else seg_pts[-1]
                    line = [a] + seg_pts + [b]
                    w = max(x.width for x in smp[i:j + 1]) * 1e3
                    mid = seg_pts[len(seg_pts) // 2]
                    z = s.z if (s.status == "no_reference" and math.isfinite(s.z)) else float("nan")
                    runs.append((_line_d(line), w, mid, s.status, tr.label, smp[i].s * 1e3, smp[j].s * 1e3,
                                 j - i + 1, why, ti, z, which == "p"))
                i = j + 1
    if not spans and not runs:
        return ""
    margin = max(3.0, min(max(halves, default=0.0) + 0.5, 10.0))
    x0, x1 = min(xs) - margin, max(xs) + margin
    y0, y1 = min(ys) - margin, max(ys) + margin
    ctx, pad_lines = _copper_layers(bm, (x0 * 1e-3, y0 * 1e-3, x1 * 1e-3, y1 * 1e-3), target_nets, active_layers) \
        if bm is not None else ("", "")
    parts = []
    for d, w, *_rest in runs:
        parts.append(f'<path class="nc" d="{d}" stroke-width="{_f(w)}"/>')
    for d, w, ti, z, zt, tol, lbl, s_mm, layer, kind, cut, w_mm, half in spans:
        col = color_at(0.5 + (z / zt - 1) / (4 * tol))
        parts.append(f'<path class="zs" d="{d}" stroke="{col}" stroke-width="{_f(w)}" data-t="{ti}" data-z="{z:.3f}" '
                     f'data-zt="{zt:g}" data-tol="{tol:g}" data-l="{html.escape(lbl)}" data-s="{s_mm:.2f}" '
                     f'data-y="{layer}" data-k="{kind}" data-c="{cut}" data-w="{w_mm:.3f}" '
                     f'data-h="{half:.2f}"/>')
    for d, w, (px, py), st, lbl, s0, s1, n, why, ti, z, main in runs:
        if not main:
            continue
        what = "perte de référence" if st == "no_reference" else "non calculé"
        extra = f" — Z calculée {z:.1f} Ω (dépend de la boîte de calcul, exclue)" if math.isfinite(z) else ""
        rng = f"s = {s0:.2f} mm" if n == 1 else f"s = {s0:.2f} → {s1:.2f} mm ({n} points)"
        tip = html.escape(f"{lbl} — {rng} — {what} : {why}{extra}")
        if st == "no_reference":
            parts.append(f'<path class="mk nr" data-t="{ti}" d="M{_f(px)} {_f(py - 0.7)}L{_f(px + 0.6)} {_f(py + 0.4)}'
                         f'L{_f(px - 0.6)} {_f(py + 0.4)}Z" data-tip="{tip}"/>')
        else:
            parts.append(f'<path class="mk dc" data-t="{ti}" d="M{_f(px - 0.35)} {_f(py - 0.35)}L{_f(px + 0.35)} '
                         f'{_f(py + 0.35)}M{_f(px - 0.35)} {_f(py + 0.35)}L{_f(px + 0.35)} {_f(py - 0.35)}" '
                         f'data-tip="{tip}"/>')
    labels = []
    for ti, tr in enumerate(run.targets):
        if tr.samples:
            s0 = tr.samples[0]
            labels.append(f'<text data-t="{ti}" x="{_f(s0.x * 1e3 + 0.4)}" y="{_f(s0.y * 1e3 + 1.4)}">'
                          f'{html.escape(tr.label)}</text>')
    groups = sorted({(tr.target["z_target"], tr.target["kind"]) for tr in run.targets if tr.samples})
    gopts = "".join(f'<option value="{zt:g}">Cible {zt:g} Ω ({"paires" if k == "pair" else "pistes"})</option>'
                    for zt, k in groups)
    layer_boxes = ""
    if bm is not None:
        layer_boxes = "".join(
            f'<label><input type="checkbox" class="bm-L" value="{html.escape(L)}"'
            f'{" checked" if L in active_layers else ""}> {html.escape(L)}</label>'
            for L in bm.copper_names)
        layer_boxes = f'<span class="bm-layers">Cuivre : {layer_boxes}</span>'
    vb = f"{_f(x0)} {_f(y0)} {_f(x1 - x0)} {_f(y1 - y0)}"
    return TEMPLATE.replace("{{VB}}", vb).replace("{{CTX}}", ctx).replace("{{ITEMS}}", "".join(parts) + pad_lines) \
        .replace("{{LABELS}}", "".join(labels)).replace("{{GROUPS}}", gopts) \
        .replace("{{LAYERS}}", layer_boxes).replace("{{STOPS}}", json.dumps(STOPS))


TEMPLATE = r"""
<div class="bm">
 <div class="bm-ctl">
  <label>Échelle
   <select id="bm-mode">
    <option value="target">Impédance (Ω) centrée sur la cible</option>
    <option value="free">Impédance (Ω) libre (min / max)</option>
    <option value="dev">Écart à la cible (%)</option>
   </select></label>
  <label id="bm-gl">Afficher <select id="bm-group">{{GROUPS}}<option value="all">Toutes les cibles</option></select></label>
  <label id="bm-hl">± <input id="bm-half" type="number" step="any" min="0" size="5"> <span id="bm-hu">Ω</span></label>
  <label id="bm-ml">min <input id="bm-min" type="number" step="any" size="5"> max
   <input id="bm-max" type="number" step="any" size="5"> Ω</label>
  {{LAYERS}}
 </div>
 <div class="bm-legend"><div class="bm-bar"></div><div class="bm-ticks" id="bm-ticks"></div>
  <div class="bm-key">Bandes colorées : largeur de cuivre <b>mesurée dans chaque coupe</b> ; survol : ligne de coupe
  (fenêtre du solveur 2D, tout le cuivre qu'elle traverse est dans le calcul). Gris foncé : net analysé ; gris clair :
  autres nets (0 V) ; pointillés clairs : cuivre sans net (flottant) ; pointillés noirs : pads du net ; blanc : perçages.<br>
  <span class="k-dc">✕</span> tronçon hachuré non calculé (discontinuité) &nbsp; <span class="k-nr">▲</span> perte de référence
  &nbsp; · molette : zoom, glisser : déplacer, double-clic : réinitialiser</div></div>
 <div class="bm-wrap"><svg id="bm-svg" viewBox="{{VB}}" preserveAspectRatio="xMidYMid meet" role="img"
   aria-label="Carte des impédances le long des pistes">
  <defs><pattern id="bm-hatch" width="0.25" height="0.25" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
   <rect width="0.25" height="0.25" fill="#f1f0ec"/><rect width="0.1" height="0.25" fill="#8c8b86"/></pattern></defs>
  <g class="ctx">{{CTX}}</g><g class="items">{{ITEMS}}</g>
  <g id="bm-cutg" style="display:none"><line id="bm-cut"/><circle id="bm-c0" r="0.12"/><circle id="bm-c1" r="0.12"/></g>
  <g class="lbl">{{LABELS}}</g></svg>
  <div id="bm-tip" class="bm-tip" hidden></div></div>
</div>
<style>
.bm-ctl{display:flex;flex-wrap:wrap;gap:6px 18px;align-items:center;margin-bottom:8px;color:var(--ink2)}
.bm-ctl select,.bm-ctl input{font:inherit;color:var(--ink);background:var(--card);border:1px solid var(--line);border-radius:4px;padding:2px 4px}
.bm-ctl input{width:5.5em}
.bm-legend{margin:4px 0 8px}.bm-bar{height:12px;border-radius:3px;background:linear-gradient(90deg,#2a78d6,#b7d3f6 25%,#c9c8c2 50%,#f4b6b2 75%,#e34948)}
.bm-ticks{position:relative;height:30px;font-size:12px;color:var(--ink2)}
.bm-ticks span{position:absolute;transform:translateX(-50%);white-space:nowrap;top:2px}
.bm-ticks span.tol{color:var(--ink);font-weight:600}
.bm-ticks span:before{content:"";position:absolute;left:50%;top:-4px;height:5px;border-left:1px solid var(--ink2)}
.bm-key{font-size:12px;color:var(--ink2)}.k-dc{color:var(--ink2)}.k-nr{color:#eda100}
.bm-wrap{position:relative;border:1px solid var(--line);border-radius:6px;overflow:hidden;background:#fff}
#bm-svg{display:block;width:100%;height:min(70vh,640px);cursor:grab;touch-action:none}
#bm-svg .cu path{fill-rule:evenodd}
#bm-svg .cu .g{fill:#dddcd6}
#bm-svg .cu .t{fill:#9d9c96}
#bm-svg .cu .f{fill:#efeee9;stroke:#a8a7a1;stroke-width:0.8px;stroke-dasharray:3 2;vector-effect:non-scaling-stroke}
#bm-svg .cu .pd{fill:none;stroke:#0b0b0b;stroke-width:1px;stroke-dasharray:3 2;vector-effect:non-scaling-stroke;pointer-events:none}
#bm-svg .cu .dr{fill:#fff;stroke:#b5b4ae;stroke-width:0.5px;vector-effect:non-scaling-stroke}
#bm-svg .zs{fill:none;stroke-linecap:butt;stroke-linejoin:round}
#bm-svg .zs.off{stroke:#c9c8c2 !important;opacity:.5}
#bm-svg .zs.hl{filter:drop-shadow(0 0 1.5px #0b0b0b)}
#bm-svg .nc{fill:none;stroke:url(#bm-hatch);stroke-linecap:butt;stroke-linejoin:round}
#bm-cut{stroke:#0b0b0b;stroke-width:1.2px;stroke-dasharray:5 3;vector-effect:non-scaling-stroke}
#bm-cutg circle{fill:#0b0b0b}
.bm-layers{display:inline-flex;gap:10px;flex-wrap:wrap}.bm-layers label{white-space:nowrap}
#bm-svg .mk{vector-effect:non-scaling-stroke}
#bm-svg .dc{fill:none;stroke:#0b0b0b;stroke-width:1.6px}
#bm-svg .nr{fill:#eda100;stroke:#0b0b0b;stroke-width:1px}
#bm-svg .off{opacity:.25}
#bm-svg text{font-size:1.1px;fill:#52514e;font-family:system-ui,sans-serif}
.bm-tip{position:absolute;pointer-events:none;background:var(--card);color:var(--ink);border:1px solid var(--line);
 border-radius:6px;padding:6px 8px;font-size:12px;box-shadow:0 2px 8px rgba(0,0,0,.15);max-width:320px}
</style>
<script>
(function(){
 var S={{STOPS}}, svg=document.getElementById('bm-svg'); if(!svg) return;
 var spans=[].slice.call(svg.querySelectorAll('.zs')), mk=[].slice.call(svg.querySelectorAll('.mk')),
     lb=[].slice.call(svg.querySelectorAll('.lbl text')),
     mode=document.getElementById('bm-mode'), grp=document.getElementById('bm-group'),
     half=document.getElementById('bm-half'), mn=document.getElementById('bm-min'), mx=document.getElementById('bm-max'),
     ticks=document.getElementById('bm-ticks'), tip=document.getElementById('bm-tip');
 function hx(c){return [1,3,5].map(function(i){return parseInt(c.substr(i,2),16)})}
 function col(t){t=Math.max(0,Math.min(1,t));var k=Math.min(Math.floor(t*4),3),u=t*4-k,a=hx(S[k]),b=hx(S[k+1]);
  return 'rgb('+[0,1,2].map(function(i){return Math.round(a[i]+(b[i]-a[i])*u)}).join(',')+')'}
 function fmt(v){var t=Math.abs(v)>=100?v.toFixed(0):v.toFixed(1); return t.replace('-','−')}
 function sel(sp){return grp.value==='all'||Math.abs(+sp.dataset.zt-(+grp.value))<1e-9}
 function show(id,on){document.getElementById(id).style.display=on?'':'none'}
 var lastGroup=null;
 function defaults(){
  var g=grp.value;
  if(mode.value==='target'){ if(g==='all'){grp.selectedIndex=0; g=grp.value;}
   var sp=spans.filter(sel); var zt=+g, tol=sp.length?+sp[0].dataset.tol:0.1;
   if(!half.value||lastGroup!==g+mode.value) half.value=(2*tol*zt).toFixed(1); }
  if(mode.value==='dev'){ var t0=spans.length?+spans[0].dataset.tol:0.1;
   if(!half.value||lastGroup!==g+mode.value) half.value=(200*t0).toFixed(0); }
  if(mode.value==='free'){ var v=spans.filter(sel).map(function(s){return +s.dataset.z});
   if(v.length&&(lastGroup!==g+mode.value)){mn.value=Math.floor(Math.min.apply(0,v));mx.value=Math.ceil(Math.max.apply(0,v));} }
  lastGroup=g+mode.value;
 }
 function paint(){
  defaults();
  show('bm-hl',mode.value!=='free'); show('bm-ml',mode.value==='free');
  document.getElementById('bm-hu').textContent=mode.value==='dev'?'%':'Ω';
  var h=Math.max(1e-9,+half.value||1), lo=+mn.value, hi=+mx.value; if(!(hi>lo)) hi=lo+1;
  spans.forEach(function(sp){ var on=sel(sp), z=+sp.dataset.z, zt=+sp.dataset.zt, t;
   if(mode.value==='target') t=0.5+(z-zt)/(2*h); else if(mode.value==='dev') t=0.5+100*(z/zt-1)/(2*h); else t=(z-lo)/(hi-lo);
   sp.style.stroke=col(t); sp.classList.toggle('off',!on); });
  var tgs={}; spans.forEach(function(sp){ if(sel(sp)) tgs[sp.dataset.t]=1; });
  mk.concat(lb).forEach(function(e){ e.classList.toggle('off', grp.value!=='all'&&!tgs[e.dataset.t]); });
  var out=[], n=5, i;
  if(mode.value==='free'){ for(i=0;i<n;i++){var v=lo+(hi-lo)*i/(n-1); out.push([i/(n-1),fmt(v)+' Ω',''])} }
  else { var zt=+grp.value, tol=(spans.filter(sel)[0]||{dataset:{tol:0.1}}).dataset.tol*1;
   for(i=0;i<n;i++){var f=-1+2*i/(n-1); out.push([i/(n-1), mode.value==='dev'?(f*h>0?'+':'')+fmt(f*h)+' %':fmt(zt+f*h)+' Ω', i===2?'tol':''])}
   var tw=mode.value==='dev'?100*tol:tol*zt;
   if(tw<h){ [[-1,'−'],[1,'+']].forEach(function(sg){ var pos=0.5+sg[0]*tw/(2*h),
      txt=(mode.value==='dev'?sg[1]+fmt(100*tol)+' %':fmt(zt+sg[0]*tw)+' Ω')+' (tol.)', hit=null;
      out.forEach(function(o){ if(Math.abs(o[0]-pos)<0.04) hit=o; });
      if(hit){ hit[1]=txt; hit[2]='tol'; } else out.push([pos,txt,'tol']); }); }
   if(mode.value==='target') out[2][1]='cible '+fmt(zt)+' Ω'; else out[2][1]='cible';
  }
  ticks.innerHTML=out.map(function(o){return '<span class="'+o[2]+'" style="left:'+(o[0]*100)+'%">'+o[1]+'</span>'}).join('');
 }
 [mode,grp].forEach(function(e){e.addEventListener('change',function(){half.value='';paint()})});
 [half,mn,mx].forEach(function(e){e.addEventListener('input',paint)});
 grp.value=grp.options[0].value; paint();
 // infobulle
 function place(ev,txt){ var r=svg.parentNode.getBoundingClientRect(); tip.innerHTML=txt; tip.hidden=false;
  var x=ev.clientX-r.left+12, y=ev.clientY-r.top+12; if(x>r.width-240) x-=260; tip.style.left=x+'px'; tip.style.top=y+'px'; }
 var cutg=document.getElementById('bm-cutg'), cut=document.getElementById('bm-cut'),
     c0=document.getElementById('bm-c0'), c1=document.getElementById('bm-c1'), hl=null;
 function showCut(sp){ var c=(sp.dataset.c||'').split(' ').map(Number); if(hl) hl.classList.remove('hl'); hl=sp; sp.classList.add('hl');
  if(c.length!==4||c.some(isNaN)){cutg.style.display='none';return}
  cut.setAttribute('x1',c[0]);cut.setAttribute('y1',c[1]);cut.setAttribute('x2',c[2]);cut.setAttribute('y2',c[3]);
  c0.setAttribute('cx',c[0]);c0.setAttribute('cy',c[1]);c1.setAttribute('cx',c[2]);c1.setAttribute('cy',c[3]); cutg.style.display=''; }
 spans.forEach(function(sp){ sp.addEventListener('mousemove',function(ev){ var d=sp.dataset, z=+d.z, zt=+d.zt; showCut(sp);
   place(ev,'<b>'+d.l+'</b><br>'+(d.k==='pair'?'Zdiff ':'Z0 ')+z.toFixed(1)+' Ω (cible '+zt+' Ω, '+(z>=zt?'+':'')+(100*(z/zt-1)).toFixed(1)+' %)<br>s = '+d.s+' mm · '+d.y+
     '<br>cuivre mesuré dans la coupe : '+(+d.w).toFixed(2)+' mm'+(isNaN(+d.h)?'':'<br>ligne de coupe : ± '+(+d.h).toFixed(2)+' mm'))});
  sp.addEventListener('mouseleave',function(){tip.hidden=true; cutg.style.display='none'; if(hl){hl.classList.remove('hl'); hl=null}})});
 [].slice.call(document.querySelectorAll('.bm-L')).forEach(function(cb){ cb.addEventListener('change',function(){
  [].slice.call(svg.querySelectorAll('.cu')).forEach(function(g){ if(g.dataset.L===cb.value) g.style.display=cb.checked?'':'none'; }); }); });
 mk.forEach(function(m){ m.addEventListener('mousemove',function(ev){place(ev,m.dataset.tip)}); m.addEventListener('mouseleave',function(){tip.hidden=true})});
 // zoom / déplacement
 var vb0=svg.getAttribute('viewBox').split(' ').map(Number), vb=vb0.slice(), drag=null;
 function setvb(){svg.setAttribute('viewBox',vb.join(' '))}
 svg.addEventListener('wheel',function(ev){ev.preventDefault(); var r=svg.getBoundingClientRect(),
  k=ev.deltaY>0?1.2:1/1.2, s=Math.max(vb[2]/r.width,vb[3]/r.height),
  cx=vb[0]+(ev.clientX-r.left-(r.width-vb[2]/s)/2)*s, cy=vb[1]+(ev.clientY-r.top-(r.height-vb[3]/s)/2)*s;
  vb=[cx-(cx-vb[0])*k, cy-(cy-vb[1])*k, vb[2]*k, vb[3]*k]; setvb();},{passive:false});
 svg.addEventListener('pointerdown',function(ev){drag=[ev.clientX,ev.clientY,vb[0],vb[1]]; svg.setPointerCapture(ev.pointerId)});
 svg.addEventListener('pointermove',function(ev){ if(!drag) return; var r=svg.getBoundingClientRect(),
  s=Math.max(vb[2]/r.width,vb[3]/r.height); vb[0]=drag[2]-(ev.clientX-drag[0])*s; vb[1]=drag[3]-(ev.clientY-drag[1])*s; setvb();});
 svg.addEventListener('pointerup',function(){drag=null});
 svg.addEventListener('dblclick',function(){vb=vb0.slice(); setvb();});
})();
</script>
"""
