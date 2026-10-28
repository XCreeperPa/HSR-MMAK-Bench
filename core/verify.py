from pathlib import Path
import json,csv,hashlib,sqlite3
from scoring.original_scoring import evaluate_points,inside_head
P=Path(__file__).resolve().parent
qs={q['id']:q for q in json.loads((P/'questions.json').read_text(encoding='utf-8'))}
refs={r['question_id']:r['reference'] for r in json.loads((P/'reference-answers.json').read_text(encoding='utf-8'))}
rows=[json.loads(l) for l in (P/'answers.jsonl').read_text(encoding='utf-8').splitlines()]
assert len(rows)==525 and len({(r['experiment_id'],r['question_id']) for r in rows})==525
for r in rows:
 q=qs[r['question_id']];ref=refs[r['question_id']];ann=r['annotations'] or []
 if r['status'] not in ['valid_answer','explicit_abstention']:original=revised=0
 elif q['module']==1:
  original=evaluate_points(ref['targets'],ann)['score'];revised=0
  for t in ref['targets']:
   best=0;x0,y0,x1,y1=t['body_region_rectangle']
   for a in ann:
    if a['character_id']!=t['character_id']:continue
    hit=inside_head(a['x'],a['y'],t['head_region'])
    best=max(best,1 if hit else .5 if x0<=a['x']<=x1 and y0<=a['y']<=y1 else 0)
   revised+=best
 else:
  selected=(r['answer'] or {}).get('selected_options',[])
  original=revised=q['score'] if selected==ref['correct_options'] else 0
 assert original==r['original_score'],(r['experiment_id'],r['question_id'],'original',original,r['original_score'])
 assert revised==r['analysis_score'],(r['experiment_id'],r['question_id'],'revised')
with (P/'scores.csv').open(encoding='utf-8-sig') as f:
 for s in csv.DictReader(f):
  rr=[r for r in rows if r['experiment_id']==s['experiment_id']]
  assert sum(r['analysis_score'] for r in rr)==float(s['analysis_total'])
  assert sum(r['original_score'] for r in rr)==float(s['original_total'])
  for m in range(1,5):assert sum(r['analysis_score'] for r in rr if r['module']==m)==float(s[f'module_{m}'])
c=sqlite3.connect(P/'results.sqlite3');assert c.execute('pragma integrity_check').fetchone()[0]=='ok';assert c.execute('select count(*) from answers').fetchone()[0]==525
assets=json.loads((P/'assets.json').read_text(encoding='utf-8'))
for a in assets:
 f=P.parent/'media'/a['file']
 if f.exists():assert hashlib.sha256(f.read_bytes()).hexdigest()==a['sha256']
print('PASS: 525 original/revised scores; 25 totals/module sums; SQLite; available media hashes.')
