"""Carte interactive du board pour le rapport HTML (SVG + JavaScript intégrés, sans dépendance).

Échelles de couleur au choix (sélecteur au-dessus de la carte) :
  * « Impédance (Ω) centrée sur la cible » : une cible à la fois (ex. « cible 90 Ω »), dégradé
    bleu (trop bas) → gris (= cible) → rouge (trop haut), graduations en Ω, demi-plage réglable ;
  * « Impédance (Ω) libre » : min / max en Ω saisis (défaut : étendue des valeurs affichées) ;
  * « Écart à la cible (%) » : toutes les cibles ensemble, graduations en %.
Infobulle au survol (net, s, Z, statut), zoom à la molette, déplacement à la souris,
double-clic pour réinitialiser. Sans JavaScript, la carte s'affiche avec les couleurs de l'échelle
« centrée sur la cible » (précalculées).
"""

from __future__ import annotations

import html
import json
import math
from typing import Dict

STOPS = ["#2a78d6", "#b7d3f6", "#c9c8c2", "#f4b6b2", "#e34948"]


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


def board_svg(run, bm=None) -> str:
    spans = []      # (d, target index, z, zt, tol, label, s_mm, layer, kind)
    markers = []
    xs, ys = [], []
    for ti, tr in enumerate(run.targets):
        zt, tol, kind = tr.target["z_target"], tr.target["tol"], tr.target["kind"]
        by: Dict = {}
        for s in tr.samples:
            by.setdefault((s.net, s.path_id), []).append(s)
        for smp in by.values():
            smp.sort(key=lambda s: s.s)
            pts = [((s.cx if math.isfinite(s.cx) else s.x) * 1e3, (s.cy if math.isfinite(s.cy) else s.y) * 1e3)
                   for s in smp]
            for i, s in enumerate(smp):
                p = pts[i]
                a = pts[i - 1] if i > 0 else p
                b = pts[i + 1] if i < len(smp) - 1 else p
                a = ((a[0] + p[0]) / 2, (a[1] + p[1]) / 2)
                b = ((b[0] + p[0]) / 2, (b[1] + p[1]) / 2)
                xs += [a[0], b[0]]
                ys += [a[1], b[1]]
                if s.valid:
                    d = f"M{_f(a[0])} {_f(a[1])}L{_f(p[0])} {_f(p[1])}L{_f(b[0])} {_f(b[1])}"
                    spans.append((d, ti, s.z, zt, tol, tr.label, s.s * 1e3, s.layer, kind))
                else:
                    markers.append((p, s.status, tr.label, s.s * 1e3, s.reason, ti))
    if not spans and not markers:
        return ""
    pad = 3.0
    x0, x1 = min(xs) - pad, max(xs) + pad
    y0, y1 = min(ys) - pad, max(ys) + pad
    ctx = []
    if bm is not None:
        for sg in bm.segs:
            pts = sg.points()
            px = [p[0] * 1e3 for p in pts]
            py = [p[1] * 1e3 for p in pts]
            if max(px) < x0 or min(px) > x1 or max(py) < y0 or min(py) > y1:
                continue
            d = "M" + "L".join(f"{_f(x)} {_f(y)}" for x, y in zip(px, py))
            ctx.append(f'<path d="{d}" stroke-width="{_f(sg.width * 1e3)}"/>')
    parts = []
    for d, ti, z, zt, tol, lbl, s_mm, layer, kind in spans:
        col = color_at(0.5 + (z / zt - 1) / (4 * tol))
        parts.append(f'<path class="zs" d="{d}" stroke="{col}" data-t="{ti}" data-z="{z:.3f}" data-zt="{zt:g}" '
                     f'data-tol="{tol:g}" data-l="{html.escape(lbl)}" data-s="{s_mm:.2f}" data-y="{layer}" '
                     f'data-k="{kind}"/>')
    for (px, py), st, lbl, s_mm, why, ti in markers:
        tip = html.escape(f"{lbl} — s = {s_mm:.2f} mm — {('perte de référence' if st == 'no_reference' else 'non calculé')} : {why}")
        if st == "no_reference":
            parts.append(f'<path class="mk nr" data-t="{ti}" d="M{_f(px)} {_f(py - 0.7)}L{_f(px + 0.6)} {_f(py + 0.4)}'
                         f'L{_f(px - 0.6)} {_f(py + 0.4)}Z" data-tip="{tip}"/>')
        else:
            parts.append(f'<path class="mk dc" data-t="{ti}" d="M{_f(px - 0.35)} {_f(py - 0.35)}L{_f(px + 0.35)} {_f(py + 0.35)}'
                         f'M{_f(px - 0.35)} {_f(py + 0.35)}L{_f(px + 0.35)} {_f(py - 0.35)}" data-tip="{tip}"/>')
    labels = []
    for ti, tr in enumerate(run.targets):
        if tr.samples:
            s0 = tr.samples[0]
            labels.append(f'<text data-t="{ti}" x="{_f(s0.x * 1e3 + 0.4)}" y="{_f(s0.y * 1e3 + 1.4)}">'
                          f'{html.escape(tr.label)}</text>')
    groups = sorted({(tr.target["z_target"], tr.target["kind"]) for tr in run.targets if tr.samples})
    gopts = "".join(f'<option value="{zt:g}">Cible {zt:g} Ω ({"paires" if k == "pair" else "pistes"})</option>'
                    for zt, k in groups)
    vb = f"{_f(x0)} {_f(y0)} {_f(x1 - x0)} {_f(y1 - y0)}"
    return TEMPLATE.replace("{{VB}}", vb).replace("{{CTX}}", "".join(ctx)).replace("{{ITEMS}}", "".join(parts)) \
        .replace("{{LABELS}}", "".join(labels)).replace("{{GROUPS}}", gopts) \
        .replace("{{STOPS}}", json.dumps(STOPS))


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
 </div>
 <div class="bm-legend"><div class="bm-bar"></div><div class="bm-ticks" id="bm-ticks"></div>
  <div class="bm-key"><span class="k-dc">✕</span> non calculé (discontinuité) &nbsp; <span class="k-nr">▲</span> perte de référence
  &nbsp; · molette : zoom, glisser : déplacer, double-clic : réinitialiser</div></div>
 <div class="bm-wrap"><svg id="bm-svg" viewBox="{{VB}}" preserveAspectRatio="xMidYMid meet" role="img"
   aria-label="Carte des impédances le long des pistes">
  <g class="ctx">{{CTX}}</g><g class="items">{{ITEMS}}</g><g class="lbl">{{LABELS}}</g></svg>
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
#bm-svg .ctx path{fill:none;stroke:#e1e0dc;stroke-linecap:round}
#bm-svg .zs{fill:none;stroke-width:3.2px;vector-effect:non-scaling-stroke;stroke-linecap:butt}
#bm-svg .zs.off{stroke:#d9d8d3 !important;stroke-width:2px}
#bm-svg .zs:hover{stroke-width:6px}
#bm-svg .mk{vector-effect:non-scaling-stroke}
#bm-svg .dc{fill:none;stroke:#52514e;stroke-width:1.4px}
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
 spans.forEach(function(sp){ sp.addEventListener('mousemove',function(ev){ var d=sp.dataset, z=+d.z, zt=+d.zt;
   place(ev,'<b>'+d.l+'</b><br>'+(d.k==='pair'?'Zdiff ':'Z0 ')+z.toFixed(1)+' Ω (cible '+zt+' Ω, '+(z>=zt?'+':'')+(100*(z/zt-1)).toFixed(1)+' %)<br>s = '+d.s+' mm · '+d.y)});
  sp.addEventListener('mouseleave',function(){tip.hidden=true})});
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
