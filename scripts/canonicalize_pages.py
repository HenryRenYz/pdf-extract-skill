#!/usr/bin/env python3
"""Normalize representation metadata, with immutable inputs and explicit source-reviewed patches."""
import argparse,copy,json,re,time
from pathlib import Path
from prepare_images import atomic_json,digest,manifest_errors,select_pages
from assemble_book import check_page,verify_files
from semantic_contract import SEMANTIC_FIELDS,validate_semantic_block
from source_identity import source_digest,manifest_source_hash
from style_patches import apply_style_patches,validate_rules
from validate_outputs import validate

def reviewed_recovery(receipt,job,checkpoint,raw,page,source_hash):
 """Admit a complete stopped attempt without rewriting its failure history."""
 if (checkpoint.get('status')!='needs_review' or not checkpoint.get('ended_unix')
     or checkpoint.get('fingerprint') is None):raise ValueError('Recovery requires a completed needs_review attempt')
 required={'page':page['page'],'source_sha256':source_hash,
           'source_image_sha256':page['original']['sha256'],
           'checkpoint_sha256':digest(job/'checkpoint.json'),'output_sha256':digest(raw),
           'decision':'accept_complete_output'}
 if any(receipt.get(key)!=value for key,value in required.items()):raise ValueError('Recovery receipt identity or decision differs')
 if not receipt.get('reviewer') or not receipt.get('reason'):raise ValueError('Recovery requires independent reviewer and reason')
 evidence=Path(receipt.get('evidence_image','')).expanduser().resolve()
 if not evidence.is_file() or digest(evidence)!=receipt.get('evidence_image_sha256'):
  raise ValueError('Recovery source review evidence is missing or changed')
 report=validate(job/'input/manifest.json',job/'output',require_markdown=not checkpoint.get('json_only',False))
 if not report['structure_ok']:raise ValueError('Incomplete output cannot be recovered')
 return {'kind':'reviewed_failed_call_recovery',**receipt,'original_exit_code':checkpoint.get('exit_code'),
         'original_timed_out':checkpoint.get('timed_out'),'content_evidence_unchanged':True}

def validate_job_scope(contract, source_hash, requested_pages, page):
 """Allow context pages without accepting an unowned or foreign page."""
 if contract.get('source_pdf_sha256') != source_hash:
  raise ValueError('Job source differs')
 producer=contract.get('expected_pages');owned=contract.get('owned_pages',producer)
 for values in (producer,owned):
  if (not isinstance(values,list) or not values or
      any(isinstance(n,bool) or not isinstance(n,int) or n<1 for n in values) or
      values!=sorted(set(values))):raise ValueError('Invalid job page scope')
 if (not set(requested_pages).issubset(producer) or
     not set(owned).issubset(producer) or page not in owned):
  raise ValueError('Job source/scope differs')
 return {'expected_pages':producer,'owned_pages':owned}

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--manifest',type=Path,required=True);p.add_argument('--source','--source-pdf',dest='source_pdf',type=Path,required=True);p.add_argument('--expected-pages',required=True)
 p.add_argument('--jobs-dir',type=Path,action='append',required=True);p.add_argument('--output',type=Path,required=True)
 p.add_argument('--patches',type=Path);p.add_argument('--rules',type=Path);p.add_argument('--style-patches',type=Path)
 p.add_argument('--recoveries',type=Path,help='Explicit independent review receipts for complete outputs from stopped failed calls')
 a=p.parse_args();prepared=a.manifest.resolve();manifest=json.loads(prepared.read_text());expected=select_pages(a.expected_pages);source_hash=source_digest(a.source_pdf.expanduser().resolve(strict=True))
 if manifest_errors(manifest,expected) or manifest_source_hash(manifest)!=source_hash:raise ValueError('Image scope/source differs')
 out=a.output.resolve()
 if out.exists():p.error('Canonical output exists; use a new revision')
 out.mkdir(parents=True);(out/'pages').mkdir();page_meta={x['page']:x for x in manifest['pages']}
 patches=json.loads(a.patches.read_text()) if a.patches else [];rules=json.loads(a.rules.read_text()) if a.rules else None
 recoveries=json.loads(a.recoveries.read_text()) if a.recoveries else []
 if not isinstance(recoveries,list) or any(not isinstance(r,dict) or r.get('page') not in expected for r in recoveries) or len({r['page'] for r in recoveries})!=len(recoveries):raise ValueError('Recovery receipts require unique requested pages')
 recovery_by_page={r['page']:r for r in recoveries}
 if rules:validate_rules(rules,source_hash)
 style_docs=json.loads(a.style_patches.read_text()) if a.style_patches else []
 if isinstance(style_docs,dict):style_docs=[style_docs]
 if not isinstance(style_docs,list) or any(not isinstance(d,dict) or d.get('page') not in expected for d in style_docs):raise ValueError('Style patch pages must belong to requested range')
 if a.style_patches and not rules:p.error('--style-patches requires --rules')
 records=[];changes=[];seen=set();t=time.perf_counter()
 for n in expected:
  matches=[]
  for jr in a.jobs_dir:
   dirs=sorted((jr/'pages'/f'{n:04d}').glob('attempt-*'))
   if dirs:
    contract=json.loads((jr/'contract.json').read_text())
    validate_job_scope(contract,source_hash,expected,n)
    matches.append(dirs[-1].resolve())
  if len(matches)!=1:raise ValueError(f'Page {n}: expected one producer, found {len(matches)}')
  job=matches[0];checkpoint=json.loads((job/'checkpoint.json').read_text())
  verify_files(job,checkpoint['file_hashes']);raw=job/'output'/f'{n:04d}.json';data=json.loads(raw.read_text());original=copy.deepcopy(data);log=[];page=page_meta[n]
  if checkpoint.get('status')!='validated' or checkpoint.get('exit_code')!=0 or checkpoint.get('timed_out'):
   if n not in recovery_by_page:raise ValueError('Failed/timeout model call needs explicit independent recovery review')
   log.append(reviewed_recovery(recovery_by_page[n],job,checkpoint,raw,page,source_hash))
  elif n in recovery_by_page:raise ValueError('Recovery receipt supplied for an already validated call')
  local_manifest=json.loads((job/'input/manifest.json').read_text())
  if manifest_source_hash(local_manifest)!=source_hash or local_manifest['pages'][0]['original']['sha256']!=page['original']['sha256']:raise ValueError('Producer source image differs')
  image_paths={Path(x['path']).name:x['path'] for x in [page['original']]+page['images']}
  viewed=[]
  for value in data.get('viewed_images',[]):
   name=Path(value).name;candidate=Path(value)
   aliases={f'{n:04d}/{name}',f'input/{n:04d}/{name}'}
   if name not in image_paths or (value not in aliases and value!=image_paths[name] and not(candidate.is_absolute() and candidate.resolve()==(job/'input'/f'{n:04d}'/name).resolve())):raise ValueError('Reported unassigned source image')
   viewed.append(image_paths[name])
  if viewed!=data['viewed_images']:log.append({'field':'viewed_images','kind':'verified_job_to_prepared_namespace','before':data['viewed_images'],'after':viewed})
  data['viewed_images']=viewed
  ids={b['id'] for b in data['blocks']};valid=[];notes=[]
  for change in data['changes']:
   if (change.get('block_id') in ids and all(isinstance(change.get(k),str) for k in ['before','after','reason'])
       and change['reason'].strip()):valid.append(change)
   else:notes.append(change)
  if notes:
   data['unstructured_change_notes']=notes;data['changes']=valid;log.append({'field':'changes','kind':'deferred_noncontract_change_notes','count':len(notes),'note':'Raw records preserved; no before/after or identity invented.'})
  for patch in patches:
   if patch['page']!=n:continue
   if patch.get('source_sha256')!=source_hash or patch.get('source_image_sha256')!=page['original']['sha256'] or patch.get('evidence')!='source_checked' or not patch.get('reason') or not Path(patch['evidence_image']).is_file():raise ValueError('Patch lacks explicit current-source review evidence')
   if digest(Path(patch['evidence_image']))!=patch.get('evidence_image_sha256',page['original']['sha256']):raise ValueError('Patch evidence image hash differs; crops require evidence_image_sha256')
   block=next(b for b in data['blocks'] if b['id']==patch['block_id']);field=patch['field']
   if field not in ('text','type','bbox','styles',*SEMANTIC_FIELDS) or 'before' not in patch or block.get(field)!=patch['before']:raise ValueError('Source patch before mismatch')
   if field=='text' and block['styles'] and len(patch['after'])!=len(patch['before']) and 'styles_after' not in patch:raise ValueError('Text patch changes Unicode indices; styles_after required')
   block[field]=copy.deepcopy(patch['after'])
   if 'styles_after' in patch:block['styles']=copy.deepcopy(patch['styles_after'])
   if 'text_sha256' in block:
    from semantic_contract import text_hash
    block['text_sha256']=text_hash(block['text'])
   block.setdefault('coordinator_patches',[]).append(patch);log.append({'kind':'source_reviewed_patch',**patch})
  if style_docs:
   data,style_log=apply_style_patches(data,style_docs,rules,source_hash);log.extend(style_log)
  data.update(source_sha256=source_hash,source_image_sha256=page['original']['sha256'],normalization=log)
  check_page(data,n,page,seen)
  target=out/'pages'/f'{n:04d}.json';atomic_json(target,data)
  # Optional working derivative is deterministic; final delivery uses JSON -> TeX.
  (target.with_suffix('.md')).write_text('\n\n'.join(b['text'] for b in data['blocks'])+'\n')
  records.append({'page':n,'json_path':f'pages/{n:04d}.json','sha256':digest(target),'input_json_path':str(raw),'input_json_sha256':digest(raw),'producer_checkpoint':str(job/'checkpoint.json'),'producer_checkpoint_sha256':digest(job/'checkpoint.json'),'normalization':log})
  changes.extend({'page':n,**entry} for entry in log)
 result={'source_sha256':source_hash,'expected_pages':expected,'pages':records,'normalizer_sha256':digest(Path(__file__)),'patches_sha256':digest(a.patches) if a.patches else None,'rules_sha256':digest(a.rules) if a.rules else None,'recoveries_sha256':digest(a.recoveries) if a.recoveries else None,'wall_seconds':time.perf_counter()-t,'note':'Representation normalization, explicit source patches and reviewed style inference; no full-page accuracy claim.'}
 atomic_json(out/'manifest.json',result);atomic_json(out/'normalization-report.json',{'changes':changes,'pages':len(records)})
 print(json.dumps({'pages':len(records),'normalizations':len(changes),'seconds':result['wall_seconds']}))
if __name__=='__main__':main()
