import json, html
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
Q=ROOT/"data/queue.json"

def esc(x): return html.escape(str(x or ""))

def build():
    reviews_path=ROOT/"data/reviews.json"
    reviews=json.loads(reviews_path.read_text()).get("reviews",{}) if reviews_path.exists() else {}
    if Q.exists():
        data=json.loads(Q.read_text())
        data["reviews"]=reviews
    else:
        data={"generated_at":"No run yet","count":0,"held_count":0,"stories":[],"held":[]}

    cards=[]
    for s in data.get("stories",[]):
        body=""
        if s.get("format")=="single":
            body=f"<pre>{esc(s.get('post'))}</pre>"
        else:
            body="<ol>"+ "".join(f"<li><pre>{esc(p)}</pre></li>" for p in s.get("thread",[]))+"</ol>"
        cards.append(f"""
        <section class='card'>
          <div class='meta'>ID: {esc(s.get('id'))} · {esc(s.get('priority_level'))} · {esc(s.get('event_status'))} ·
          confidence {esc(s.get('confidence'))} · score {esc(s.get('priority_score'))}</div>
          <h2>{esc(s.get('title'))}</h2>
          {body}
          <p><b>Quality:</b> {'PASS' if s.get('quality_pass') else 'FAIL'} · <b>Review:</b> {esc(data.get('reviews',{}).get(s.get('id'),{}).get('decision','PENDING'))}</p>
          <p><b>Source:</b> <a href='{esc(s.get('url'))}' target='_blank'>{esc(s.get('source'))}</a></p>
        </section>""")

    held=[]
    for s in data.get("held",[])[:30]:
        held.append(f"""
        <section class='held'>
          <b>{esc(s.get('hold_reason','Held'))}</b><br>
          {esc(s.get('title'))}<br>
          <small>{esc(s.get('source'))} · score {esc(s.get('priority_score',s.get('score')))}</small>
        </section>""")

    return f"""<!doctype html>
<html>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>PulseX Dashboard</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:1100px;margin:0 auto;padding:24px;background:#f5f5f5;color:#171717}}
header{{background:white;padding:22px;border-radius:16px;margin-bottom:18px}}
.card,.held{{background:white;border-radius:14px;padding:18px;margin:12px 0;box-shadow:0 1px 5px #0001}}
.meta{{font-size:13px;font-weight:700;letter-spacing:.03em}}
pre{{white-space:pre-wrap;font-family:inherit;font-size:16px}}
.held{{border-left:5px solid #888}}
a{{color:inherit}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}
.stat{{background:#eee;border-radius:10px;padding:12px}}
</style>
</head>
<body>
<header>
<h1>PulseX</h1>
<p>Dry-run dashboard — <b>nothing is posted to X from this page.</b></p>
<div class='grid'>
<div class='stat'><b>{data.get('count',0)}</b><br>Ready</div>
<div class='stat'><b>{data.get('held_count',0)}</b><br>Held</div>
<div class='stat'><b>{esc(data.get('generated_at'))}</b><br>Last run</div>
</div>
</header>
<h2>Ready for X</h2>
{''.join(cards) or '<p>No stories ready.</p>'}
<h2>Held / Needs Attention</h2>
{''.join(held) or '<p>Nothing held.</p>'}
</body></html>"""

(ROOT/"data").mkdir(exist_ok=True)
(ROOT/"data/dashboard.html").write_text(build(),encoding="utf-8")
print("Dashboard generated: data/dashboard.html")
