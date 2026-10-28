from pathlib import Path
import json,shutil,sys
HERE=Path(__file__).resolve().parent
BASE=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else HERE.parent
CORE=BASE/'core'; MEDIA=BASE/'media'/'media'
assert CORE.is_dir() and MEDIA.is_dir(), 'Extract core-data.zip into core/ and media.zip into media/ beside platform/'
web=HERE/'web';web.mkdir(exist_ok=True)
qs=json.loads((CORE/'questions.json').read_text(encoding='utf-8'))
for q in qs:q.pop('bank_version',None)
refs={r['question_id']:r['reference'] for r in json.loads((CORE/'reference-answers.json').read_text(encoding='utf-8'))}
assets=json.loads((CORE/'assets.json').read_text(encoding='utf-8'))
for a in assets:a['file']=a['file'].removeprefix('media/')
for name,data in [('questions.json',qs),('answer-key.demo.json',refs),('assets.json',assets)]:
 (web/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
shutil.copy2(CORE/'characters.json',web/'characters.json');shutil.copy2(HERE/'bank-manifest.json',web/'manifest.json')
(web/'contestants.private.json').write_text(json.dumps({'version':1,'contestants':[{'roster_key':'example-agent','subject_type':'ai_agent','model_name':'Example Agent','agent_host':'Example application','include_in_statistics':True}]},indent=2),encoding='utf-8')
shutil.copytree(MEDIA,HERE/'service/media',dirs_exist_ok=True)
(HERE/'service/media/media-manifest.json').write_text(json.dumps({'version':json.loads((HERE/'bank-manifest.json').read_text(encoding='utf-8'))['version'],'assets':assets},ensure_ascii=False,indent=2),encoding='utf-8')
print('Prepared example runtime. Start: python -m service.app')
