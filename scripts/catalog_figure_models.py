#!/usr/bin/env python3
"""Snapshot mainstream OpenRouter models with image input and tool calling."""
import argparse
import datetime
import json
import urllib.request
from pathlib import Path




def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--candidate',action='append',default=[],help='Exact OpenRouter model ID, repeatable');p.add_argument('--max-input-price',type=float,default=.5,help='USD per million input token listing floor');p.add_argument('--max-output-price',type=float,default=3,help='USD per million output token listing floor');p.add_argument('--include-deepseek',action='store_true',help='Explicitly authorized optional comparison family');p.add_argument('--family',action='append',default=[],help='Optional provider-family filter; unrestricted by default');args=p.parse_args()
    if args.output.exists():p.error('Keep dated snapshots; output already exists')
    if min(args.max_input_price,args.max_output_price)<0:p.error('Prices must be nonnegative')
    url='https://openrouter.ai/api/v1/models'
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'pdf-extract-skill/figure-model-catalog'}),timeout=40) as response:data=json.load(response)
    models=[];seen=set()
    for m in data['data']:
        model=m['id'];a=m.get('architecture',{});price=m.get('pricing',{})
        if args.family and model.split('/')[0] not in args.family:continue
        if 'image' not in a.get('input_modalities',[]) or 'text' not in a.get('output_modalities',[]) or 'image' in a.get('output_modalities',[]) or 'tools' not in m.get('supported_parameters',[]):continue
        if args.candidate and model not in args.candidate:continue
        if float(price.get('prompt','inf'))*1e6>args.max_input_price or float(price.get('completion','inf'))*1e6>args.max_output_price:continue
        models.append({k:m.get(k) for k in ['id','name','canonical_slug','context_length','architecture','pricing','supported_parameters','top_provider','reasoning']});seen.add(model)
    missing=set(args.candidate)-seen
    if missing:p.error(f'Candidates missing, not image/tool-capable, or above budget: {sorted(missing)}')
    models.sort(key=lambda m:float(m['pricing']['prompt'])+float(m['pricing']['completion']));args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({'fetched_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'source':url,'family_filter':sorted(args.family),'price_note':'Listing floor; actual provider, context tiers, images, reasoning, cache, tool fees and retry costs require request accounting. Quality not evaluated.','models':models},ensure_ascii=False,indent=2))
    print(json.dumps({'models':len(models),'output':str(args.output),'ids':[m['id'] for m in models]},ensure_ascii=False))


if __name__=='__main__':main()
