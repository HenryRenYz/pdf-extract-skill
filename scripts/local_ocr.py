#!/usr/bin/env python3
"""One resident local MinerU model; immutable per-page drafts with hash-gated resume."""
import argparse,dataclasses,json,os,time,traceback
from pathlib import Path
from prepare_images import atomic_json,digest,manifest_errors,select_pages
from source_identity import source_digest,manifest_source_hash
from model_identity import model_identity

def serial(value):
 if dataclasses.is_dataclass(value):return dataclasses.asdict(value)
 if hasattr(value,'model_dump'):return value.model_dump()
 if hasattr(value,'tolist'):return value.tolist()
 raise TypeError(type(value).__name__)

def write_json(path,value):
 atomic_json(path,json.loads(json.dumps(value,ensure_ascii=False,default=serial)))

def load_engine(model_path,device,threads,batch_size):
 import torch
 from PIL import Image
 from transformers import AutoProcessor,Qwen2VLForConditionalGeneration
 from mineru_vl_utils import MinerUClient
 from mineru_vl_utils.post_process import json2md
 torch.set_num_threads(threads)
 model=Qwen2VLForConditionalGeneration.from_pretrained(str(model_path),dtype=torch.bfloat16,device_map=device,attn_implementation='sdpa').eval()
 processor=AutoProcessor.from_pretrained(str(model_path),use_fast=True)
 client=MinerUClient(backend='transformers',model=model,processor=processor,image_analysis=False,batch_size=batch_size)
 def infer(image_path,target):
  with Image.open(image_path) as source:image=source.convert('RGB')
  blocks=client.two_step_extract(image)
  converted=json.loads(json.dumps(blocks,ensure_ascii=False,default=serial))
  write_json(target/'raw-blocks.json',converted)
  for i,b in enumerate(converted):
   if b['type'] in ('image','chart') and not b.get('content'):
    box=b['bbox'];asset=target/'images'/f'block-{i:03d}.png';asset.parent.mkdir(exist_ok=True)
    image.crop((box[0]*image.width,box[1]*image.height,box[2]*image.width,box[3]*image.height)).save(asset)
    b['content']=f'![](images/{asset.name})'
  return json2md(converted),len(blocks)
 return infer

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--prepared-dir',type=Path,required=True);p.add_argument('--source','--source-pdf',dest='source_pdf',type=Path,required=True)
 p.add_argument('--pages',required=True);p.add_argument('--model-path',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
 p.add_argument('--device',default='cuda:0');p.add_argument('--threads',type=int,default=8);p.add_argument('--batch-size',type=int,default=4)
 p.add_argument('--wait-seconds',type=float,default=300);p.add_argument('--resume',action='store_true')
 a=p.parse_args();prepared=a.prepared_dir.expanduser().resolve();output=a.output.expanduser().resolve();source=a.source_pdf.expanduser().resolve(strict=True);model=a.model_path.expanduser().resolve(strict=True)
 output.mkdir(parents=True,exist_ok=True);pages=select_pages(a.pages);source_hash=source_digest(source)
 identity=model_identity(model)
 contract={'source_sha256':source_hash,'model_path':str(model),'model_sha256':identity['sha256'],'model_identity':identity,'device':a.device,'threads':a.threads,'batch_size':a.batch_size,'adapter_sha256':digest(Path(__file__))}
 existing=output/'contract.json'
 if existing.exists() and json.loads(existing.read_text())!=contract:p.error('Existing OCR run contract differs; use a new output directory')
 atomic_json(existing,contract);started=time.perf_counter();records=[];engine=None;load_seconds=0
 for n in pages:
  deadline=time.monotonic()+a.wait_seconds;manifest=None
  while time.monotonic()<deadline:
   single=prepared/f'page-{n:04d}.json';full=prepared/'manifest.json'
   path=single if single.is_file() else full
   if path.is_file():
    candidate=json.loads(path.read_text())
    if not manifest_errors(candidate) and any(x['page']==n for x in candidate.get('pages',[])):
     manifest=candidate;break
   failure=prepared/'progress.json'
   if failure.exists() and json.loads(failure.read_text()).get('preparation_status')=='failed':raise RuntimeError('Image producer failed')
   time.sleep(.2)
  if manifest is None:raise TimeoutError(f'Page {n} preparation not ready')
  if manifest_source_hash(manifest)!=source_hash:raise ValueError('Prepared source differs from current document')
  page=next(x for x in manifest['pages'] if x['page']==n);image=(prepared/page['original']['path']).resolve()
  if not image.is_relative_to(prepared) or digest(image)!=page['original']['sha256']:raise ValueError('Original image hash/path changed')
  target=output/f'{n:04d}';ready=target/'page-ready.json'
  if ready.exists():
   r=json.loads(ready.read_text())
   if not a.resume:p.error('OCR output exists; use --resume after confirming the contract')
   if r.get('contract')==contract and r.get('image_sha256')==digest(image) and all((target/k).is_file() and digest(target/k)==v for k,v in r['outputs'].items()):
    records.append({**r,'reused':True});continue
   raise ValueError('OCR existing page or files changed; new attempt required')
  if target.exists():raise ValueError(f'Partial output {target}; inspect before new attempt')
  target.mkdir()
  if engine is None:
   t=time.perf_counter();engine=load_engine(model,a.device,a.threads,a.batch_size);load_seconds=time.perf_counter()-t;print('MODEL_LOADED',load_seconds,flush=True)
  t=time.perf_counter()
  try:
   text,count=engine(image,target);tmp=target/'original.md.tmp';tmp.write_text(text);tmp.replace(target/'original.md')
   r={'page':n,'status':'produced','source_sha256':source_hash,'image_sha256':digest(image),'blocks':count,'elapsed_seconds':time.perf_counter()-t,'contract':contract,'outputs':{str(f.relative_to(target)):digest(f) for f in target.rglob('*') if f.is_file()}}
   atomic_json(ready,r)
  except Exception as e:
   r={'page':n,'status':'failed','elapsed_seconds':time.perf_counter()-t,'error':str(e)};atomic_json(target/'failure.json',r);(target/'error.txt').write_text(traceback.format_exc())
  records.append(r);atomic_json(output/'progress.json',{'requested_pages':pages,'records':records,'loading_seconds':load_seconds,'wall_seconds':time.perf_counter()-started});print(n,r['status'],r['elapsed_seconds'],flush=True)
 final=prepared/'manifest.json'
 if not final.is_file():raise RuntimeError('Whole preparation terminal manifest is absent')
 if manifest_errors(json.loads(final.read_text())):raise RuntimeError('Whole preparation did not complete')
 if source_digest(source)!=source_hash or model_identity(model)!=identity:raise RuntimeError('Source or local model changed during OCR')
 report={'requested_pages':pages,'records':records,'loading_seconds':load_seconds,'wall_seconds':time.perf_counter()-started,'all_produced':len(records)==len(pages) and all(x['status']=='produced' for x in records)}
 atomic_json(output/'report.json',report)
 if not report['all_produced']:raise SystemExit(1)
if __name__=='__main__':main()
