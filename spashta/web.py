"""
The two HTML pages: the home page (paste a link or text) and the
permanent report page at /r/<id>.

The report page is rendered on the server with no script, so it opens
fast on any phone, can be read by link previews, and still works years
later. Every label shows in Nepali and English. All text from articles
and reports is HTML-escaped.
"""

from __future__ import annotations

import json
from html import escape

from . import __version__
from . import i18n as T

CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a65;--line:#e4e1d8;--card:#fff;--good:#2f7d4f;--mid:#b07d1a;--bad:#b23b2e}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--fg:#ecebe6;--muted:#a3a19a;--line:#33322e;--card:#1f1f1c;--good:#5fb383;--mid:#d4a54a;--bad:#e07a6c}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 system-ui,"Noto Sans Devanagari",sans-serif}
main{max-width:760px;margin:0 auto;padding:24px 16px 64px}h1{margin:0 0 4px;font-size:28px}h1 a{text-decoration:none}
p.sub{margin:0 0 20px;color:var(--muted)}
textarea,input[type=url],input[type=text]{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);font:inherit}
textarea{min-height:140px}button,.button{display:inline-block;margin-top:10px;padding:10px 18px;border:0;border-radius:8px;background:var(--fg);color:var(--bg);font:inherit;cursor:pointer;text-decoration:none}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-top:16px}
.score{font-size:40px;font-weight:700;line-height:1.2}.label{font-size:18px}
.sig h3{margin:0 0 4px;font-size:16px}.sig h3 small{font-weight:400}.sig{border-top:1px solid var(--line);padding-top:12px;margin-top:12px}
.bar{height:6px;border-radius:3px;background:var(--line);overflow:hidden;margin:6px 0}.bar span{display:block;height:100%}
ul{margin:6px 0 0 18px;padding:0;color:var(--muted)}li{overflow-wrap:anywhere}small,.muted{color:var(--muted)}a{color:inherit;overflow-wrap:anywhere}
.ne{display:block}.en{display:block;color:var(--muted)}.share{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.share input{flex:1;min-width:0}label.opt{display:flex;gap:8px;align-items:center;margin-top:10px;color:var(--muted)}
"""


def _color(concern: float) -> str:
    return "var(--good)" if concern < 0.25 else "var(--mid)" if concern < 0.6 else "var(--bad)"


def _two(ne: str, en: str) -> str:
    return f'<span class="ne">{escape(ne)}</span><span class="en">{escape(en)}</span>' if ne else f"<span>{escape(en)}</span>"


def report_page(r: dict, permalink: str) -> str:
    """Server-rendered, script-free page for one stored report."""
    title = r.get("title") or ""
    label = r.get("label", "")
    signals = []
    for s in r.get("signals", []):
        name = s["name"]
        evidence = "".join(f"<li>{escape(str(e))}</li>" for e in s.get("evidence", []))
        conf = "" if s.get("confidence", 1) >= 0.8 else f' · <small>confidence {s["confidence"]:.2f}</small>'
        signals.append(
            f'<div class="sig"><h3>{escape(T.SIGNALS.get(name, name))} · {escape(T.SIGNALS_EN.get(name, name))}'
            f' <small>{s["concern"]:.2f}</small>{conf}</h3>'
            f'<div class="bar"><span style="width:{round(s["concern"] * 100)}%;background:{_color(s["concern"])}"></span></div>'
            f'{_two(T.summary_ne(s["summary"]), s["summary"])}<ul>{evidence}</ul></div>')
    related = ""
    if r.get("related"):
        items = "".join(
            f'<li><a href="{escape(x["url"]) if x["url"].startswith(("http://", "https://")) else "#"}" rel="noopener nofollow">'
            f'{escape(x["title"])}</a> · {escape(x["source"])} · {escape(T.ROLES.get(x["role"], x["role"]))} / {escape(x["role"])}</li>'
            for x in r["related"])
        related = f'<div class="sig"><h3>{_two(*T.UI["other_coverage"])}</h3><ul>{items}</ul></div>'
    url = r.get("url", "")
    source = (f'<a href="{escape(url)}" rel="noopener nofollow">{escape(r.get("source") or url)}</a>'
              if url.startswith(("http://", "https://")) else escape(r.get("source") or ""))
    created = escape(str(r.get("created_at", ""))[:16])
    desc = f'{r.get("score")}/100 · {T.LABELS.get(label, "")} · {label}'
    return f"""<!doctype html>
<html lang="ne"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(f"{r.get('score')}/100 · {title}")} · Spashta</title>
<meta name="description" content="{escape(desc)}">
<meta property="og:title" content="{escape(f"Spashta {r.get('score')}/100: {title}")}">
<meta property="og:description" content="{escape(desc)}">
<meta property="og:url" content="{escape(permalink)}"><meta property="og:type" content="article">
<meta name="robots" content="noindex">
<style>{CSS}</style></head><body><main>
<h1><a href="/">स्पष्ट · Spashta</a></h1>
<div class="card">
<div class="score">{escape(str(r.get("score")))}/100</div>
<div class="label">{_two(T.LABELS.get(label, ""), label)}</div>
<p><strong>{escape(title)}</strong><br><small>{source} · {escape(r.get("lang", ""))}</small></p>
{"".join(signals)}{related}
<p><small>{escape(T.UI["created"][0])} · {escape(T.UI["created"][1])}: {created} UTC · Spashta {escape(str(r.get("version", "")))}<br>
{escape(T.UI["snapshot"][0])}<br>{escape(T.UI["snapshot"][1])}</small></p>
<p><small>{escape(r.get("disclaimer_ne", ""))}<br>{escape(r.get("disclaimer", ""))}</small></p>
<div class="share"><input type="text" readonly value="{escape(permalink)}" aria-label="permalink"></div>
<a class="button" href="/">{escape(T.UI["check_another"][0])} · {escape(T.UI["check_another"][1])}</a>
</div></main></body></html>
"""


def home_page() -> str:
    labels = json.dumps(T.bundle(), ensure_ascii=False).replace("</", "<\\/")
    return HOME.replace("/*__I18N__*/", labels).replace("__VERSION__", escape(__version__))


HOME = """<!doctype html>
<html lang="ne"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spashta · स्पष्ट</title>
<meta name="description" content="समाचार कसरी लेखिएको छ, जाँच्नुहोस्। Check how a news story is told.">
<style>""" + CSS + """</style></head><body><main>
<h1>स्पष्ट · Spashta</h1>
<p class="sub">समाचार कसरी लेखिएको छ, जाँच्नुहोस्। Check how a news story is told.</p>
<input id="url" type="url" inputmode="url" placeholder="समाचारको लिङ्क / article link">
<p class="sub" style="margin:8px 0">वा / or</p>
<textarea id="text" placeholder="शीर्षक पहिलो लाइनमा, त्यसपछि समाचार / Headline on the first line, then the story"></textarea>
<label class="opt"><input id="share" type="checkbox"> सेयर गर्ने लिङ्क बनाउनुहोस् (टाँसेको पाठ सर्भरमा राखिन्छ) · Make a shareable link (stores pasted text)</label>
<button id="go">जाँच्नुहोस् · Check</button>
<div id="out"></div>
<p><small>Spashta measures presentation, sourcing and corroboration. It does not decide whether a story is true. · v__VERSION__</small></p>
</main><script id="i18n" type="application/json">/*__I18N__*/</script><script>
const T=JSON.parse(document.getElementById("i18n").textContent);
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const color=c=>c<0.25?"var(--good)":c<0.6?"var(--mid)":"var(--bad)";
const two=(ne,en)=>ne?`<span class="ne">${esc(ne)}</span><span class="en">${esc(en)}</span>`:`<span>${esc(en)}</span>`;
const sumNe=s=>{const low=s.endsWith(T.low[0]);const b=low?s.slice(0,-T.low[0].length):s;const n=T.summaries[b]||"";return n&&low?n+T.low[1]:n};
document.getElementById("go").onclick=async()=>{
  const url=document.getElementById("url").value.trim(), text=document.getElementById("text").value.trim();
  const out=document.getElementById("out"); out.innerHTML='<div class="card">…</div>';
  const [title,...rest]=text.split("\\n");
  const body=url?{url}:{title,body:rest.join("\\n"),share:document.getElementById("share").checked};
  try{
    const r=await fetch("/api/analyze",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const d=await r.json(); if(!r.ok) throw new Error(d.detail||r.status);
    let h=`<div class="card"><div class="score">${d.score}/100</div><div class="label">${two(T.labels[d.label],d.label)}</div><p><strong>${esc(d.title)}</strong></p>`;
    for(const s of d.signals){h+=`<div class="sig"><h3>${esc(T.signals[s.name]||s.name)} · ${esc(T.signals_en[s.name]||s.name)} <small>${s.concern.toFixed(2)}</small></h3><div class="bar"><span style="width:${Math.round(s.concern*100)}%;background:${color(s.concern)}"></span></div>${two(sumNe(s.summary),s.summary)}<ul>${s.evidence.map(e=>`<li>${esc(e)}</li>`).join("")}</ul></div>`}
    if(d.related.length){h+=`<div class="sig"><h3>${two(...T.ui.other_coverage)}</h3><ul>${d.related.map(x=>`<li><a href="${/^https?:\\/\\//.test(x.url)?esc(x.url):"#"}" rel="noopener nofollow">${esc(x.title)}</a> · ${esc(x.source)} · ${esc(T.roles[x.role]||x.role)} / ${esc(x.role)}</li>`).join("")}</ul></div>`}
    if(d.permalink){const link=new URL(d.permalink,location.href).href;h+=`<div class="sig"><h3>${two(...T.ui.share)}</h3><div class="share"><input type="text" readonly value="${esc(link)}" id="plink"><button type="button" id="copy">${esc(T.ui.copy[0])} · ${esc(T.ui.copy[1])}</button></div><p><a href="${esc(d.permalink)}">${esc(link)}</a></p></div>`}
    out.innerHTML=h+`<p><small>${esc(d.disclaimer)}</small></p></div>`;
    const c=document.getElementById("copy"); if(c) c.onclick=()=>{const i=document.getElementById("plink");i.select();navigator.clipboard&&navigator.clipboard.writeText(i.value)};
  }catch(e){out.innerHTML=`<div class="card">${esc(e.message)}</div>`}
};
</script></body></html>
"""
