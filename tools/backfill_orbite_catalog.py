"""Import existing downloaded Orbite metadata into the metadata-only outbox."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import catalog_sync
parser=argparse.ArgumentParser();parser.add_argument('collections',type=Path);parser.add_argument('config',type=Path);args=parser.parse_args()
rows=[];seen=set();skipped=0
for path in args.collections.glob('*/tracks/*.json'):
    try:
        data=json.loads(path.read_text());audio=Path(data.get('audio_file',''))
        if not data.get('audio_file'):continue
        if not audio.is_absolute():audio=path.parent.parent/audio
        if not audio.is_file():continue
        match=data.get('match') or {};provider=data.get('provider');identifier=str(match.get('id') or '')
        if not identifier.isdigit() or provider not in ('tiddl','beatport','qobuz'):
            skipped+=1;continue
        key=(provider,identifier)
        if key in seen:continue
        seen.add(key)
        title=match.get('title') or data.get('title','')
        if match.get('version'):title+=' ('+match['version']+')'
        artists=match.get('artists') or data.get('artists','')
        if isinstance(artists,list):artists=', '.join(a.get('name','') if isinstance(a,dict) else str(a) for a in artists)
        url={'tiddl':'https://tidal.com/track/','beatport':'https://www.beatport.com/track/-/','qobuz':'https://open.qobuz.com/track/'}[provider]+identifier
        rows.append(catalog_sync.clean({'title':title,'artist':artists,'url':url,'provider':provider}))
    except (ValueError,OSError,TypeError):skipped+=1
catalog_sync.enqueue(args.config.parent,rows)
config=json.loads(args.config.read_text())
while True:
    state=catalog_sync.flush(args.config.parent,config)
    if not state.get('pending'):break
print(json.dumps({'metadata_imported':len(rows),'without_source_id':skipped,'pending':state.get('pending',0)}))
