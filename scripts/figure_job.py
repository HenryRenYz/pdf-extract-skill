#!/usr/bin/env python3
"""Prepare a bounded figure-worker job; --execute explicitly starts a model call."""
import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path
from PIL import Image

ROOT=Path(__file__).resolve().parent.parent
from opencode_runtime import invocation, safe_diagnostic, session_metadata
from backend_capabilities import load_capabilities, check_images
from runtime_contract import stage_identity


def digest(path):
    return hashlib.file_digest(Path(path).open('rb'),'sha256').hexdigest()


def scoped_paths(path,job):
    path=Path(path).resolve();forms={str(path)}
    for parent in [job,*job.parents]:
        if path.is_relative_to(parent):forms.add(str(path.relative_to(parent)))
    return {p:'allow' for form in forms for p in [form,form+'/*']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec',type=Path,required=True)
    parser.add_argument('--style',type=Path,required=True)
    parser.add_argument('--context-index',type=Path,required=True)
    parser.add_argument('--prepared-root',type=Path,required=True)
    parser.add_argument('--job-dir',type=Path,required=True)
    parser.add_argument('--model',help='Optional for OpenCode; use its configured default when omitted')
    parser.add_argument('--backend',choices=['opencode','codex'],default='opencode')
    parser.add_argument('--allow-deepseek',action='store_true',help='Deprecated compatibility flag; providers are selected by capability, not family')
    parser.add_argument('--capabilities',type=Path,help='Provider-neutral declared image/tool limits and token semantics')
    parser.add_argument('--catalog',type=Path,help='Official OpenRouter models snapshot with models/data array and image modalities')
    parser.add_argument('--reasoning-effort',choices=['none','minimal','low','medium','high'],default='low')
    parser.add_argument('--disable-mcp',action='append',default=[])
    parser.add_argument('--steps',type=int,default=18)
    parser.add_argument('--timeout',type=float,default=900)
    parser.add_argument('--previous',type=Path,help='Previous attempt output with draw.py/scene.json')
    parser.add_argument('--feedback',type=Path,help='Specific source-grounded correction requests')
    parser.add_argument('--render-preview',type=Path,action='append',default=[],help='Budgeted PNG of the previous actual render for visual self-check; repeat for tiles/PDF previews')
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    if args.steps<1 or args.timeout<=0:parser.error('Positive steps and timeout required')
    if bool(args.previous)!=bool(args.feedback):parser.error('Revisions require both previous output and feedback')
    if args.render_preview and not args.previous:parser.error('Rendered self-check requires previous output and feedback')
    entry=None
    if args.backend=='codex' and not args.model:parser.error('Codex requires an explicit model')
    if args.backend=='opencode' and args.model and '/' not in args.model:parser.error('Use provider/model or omit --model for the configured default')
    if args.catalog:
        if not args.model:parser.error('An explicit catalogue check requires --model')
        catalog=json.loads(args.catalog.read_text());entry=next((m for m in catalog.get('models',catalog.get('data',[])) if m['id']==args.model.removeprefix('openrouter/')),None)
        if not entry or 'image' not in entry.get('architecture',{}).get('input_modalities',[]) or 'text' not in entry.get('architecture',{}).get('output_modalities',[]) or 'tools' not in entry.get('supported_parameters',[]):parser.error('Catalogue model lacks image/text/tool capabilities')
    prepared=args.prepared_root.expanduser().resolve(strict=True);specpath=args.spec.expanduser().resolve(strict=True);spec=json.loads(specpath.read_text());stylepath=args.style.expanduser().resolve(strict=True);style=json.loads(stylepath.read_text());contextpath=args.context_index.expanduser().resolve(strict=True);context=json.loads(contextpath.read_text())
    if spec['style_version']!=style['version'] or spec.get('style_sha256')!=digest(stylepath):parser.error('Frozen style version/hash mismatch')
    if context.get('pdf_sha256')!=spec['source']['pdf_sha256']:parser.error('Context belongs to a different source book')
    images=[]
    for row in spec['inputs']:
        p=(prepared/row['path']).resolve()
        if not p.is_relative_to(prepared) or not p.is_file() or digest(p)!=row['sha256']:parser.error('Missing/changed figure image')
        with Image.open(p) as image:width,height=image.size
        if [width,height]!=[row['width'],row['height']]:parser.error('Figure image dimensions differ from frozen metadata')
        if width*height>context['image_budget']['max_pixels'] or max(width,height)>context['image_budget']['max_edge']:parser.error('Figure image exceeds verified image budget')
        images.append(p)
    context_images=[]
    for page in context['pages']:
        for row in page['images']:
            p=(prepared/row['path']).resolve()
            if not p.is_relative_to(prepared) or not p.is_file() or digest(p)!=row['sha256']:parser.error('Missing/changed context image')
            with Image.open(p) as image:width,height=image.size
            if [width,height]!=[row['width'],row['height']]:parser.error('Context image dimensions differ from frozen metadata')
            if width*height>context['image_budget']['max_pixels'] or max(width,height)>context['image_budget']['max_edge']:parser.error('Context image exceeds verified budget')
            context_images.append(p)
    preview_sources=[]
    for value in args.render_preview:
        p=value.expanduser().resolve(strict=True)
        with Image.open(p) as im:
            width,height=im.size
            if im.format!='PNG':parser.error('Rendered self-check previews must be real PNG images')
        if width*height>context['image_budget']['max_pixels'] or max(width,height)>context['image_budget']['max_edge']:parser.error('Rendered preview exceeds verified image budget; provide overview/tiles, not an oversized full render')
        preview_sources.append(p)
    try:
        capabilities=load_capabilities(args.capabilities,backend=args.backend,model=args.model,required=args.execute)
    except ValueError as error:
        parser.error(str(error))
    budget=check_images(capabilities,images+preview_sources,context_images)
    job=args.job_dir.expanduser().resolve()
    if job.exists():parser.error('Use a new job directory for every model/figure/attempt')
    (job/'input').mkdir(parents=True);(job/'output').mkdir()
    for source,name in [(specpath,'spec.json'),(stylepath,'style.json'),(ROOT/'scripts/figure_render.py','figure_render.py'),(ROOT/'assets/figure-template.py','figure-template.py')]:shutil.copyfile(source,job/'input'/name)
    for value in style.get('font',{}).get('paths',[]):
        relative=Path(value)
        if relative.is_absolute() or '..' in relative.parts:
            parser.error('Use the portable font bundle produced by prepare_figures')
        source=(stylepath.parent/relative).resolve(strict=True)
        if not source.is_relative_to(stylepath.parent):parser.error('Font bundle path escapes shared style directory')
        expected=next((r['sha256'] for r in style.get('font_file_manifest',[]) if r['path']==value),None)
        if expected is None or digest(source)!=expected:parser.error('Frozen font bundle missing or hash changed')
        target=job/'input'/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    contextcopy={**context,'prepared_root':str(prepared),'pages_directory':str(prepared/'shared/pages')}
    (job/'input/context.json').write_text(json.dumps(contextcopy,ensure_ascii=False,indent=2))
    prompt=(ROOT/'assets/figure-prompt.txt').read_text()+'\nShared pages root: '+str(prepared/'shared/pages')+'. Read the overview/tiles you need from input/context.json; do not read other models\' results.'
    if args.previous:
        for source,name in [(args.previous/'draw.py','previous-draw.py'),(args.previous/'scene.json','previous-scene.json'),(args.feedback,'feedback.json')]:shutil.copyfile(source,job/'input'/name)
        if (args.previous/'data.json').is_file():
            shutil.copyfile(args.previous/'data.json',job/'input/previous-data.json')
        prompt+='\nThe previous round\'s script and scene are in input/previous-draw.py and input/previous-scene.json; if previous-data.json exists, keep or revise its data as well. Feedback is in input/feedback.json.'
    previews=[]
    for n,source in enumerate(preview_sources,1):
        target=job/'input'/f'render-preview-{n:02d}.png'
        shutil.copyfile(source,target);images.append(target)
        with Image.open(target) as im:dimensions=list(im.size)
        previews.append({'path':str(target),'sha256':digest(target),'size_px':dimensions,'source_path':str(source)})
    if previews:
        (job/'input/render-previews.json').write_text(json.dumps(previews,ensure_ascii=False,indent=2))
        prompt+='\nThis is the post-render self-check/revision round. input/render-previews.json lists the actual rendered images; these are not source scans. Inspect every preview, compare details against the source images and body-text data, revise draw.py, and write output/self-check.json. Fields: reviewed_images=[{path,sha256}] (previews actually viewed), checks=[{category,status,action}], label_placements=[{label_id,attempted_positions,selected_position,background_reason}], source_deviations=[...]. Use pass/fixed/uncertain/needs_revision for status; do not claim final acceptance yourself. If you changed the script without rendering again, state that these checks cover this round\'s input previews and the revised version still awaits render review.'
    # Per-model/per-attempt jobs are nested; explicit absolute paths avoid
    # models resolving input/output against a parent figure folder.
    prompt=prompt.replace('input/',str(job/'input')+'/').replace('output/',str(job/'output')+'/')
    prompt+='\nThe only writable output directory is '+str(job/'output')+'. All assigned file paths are absolute; do not omit the model or attempt directory.'
    (job/'prompt.txt').write_text(prompt)
    # These shared folders contain only prevalidated budgeted images, never
    # raw full-page scans. Folder scopes also avoid oversized config/env values
    # when workers can consult hundreds of context pages.
    context_dir=prepared/'shared/pages'
    figure_input=prepared/'figures'/spec['id']/'input'
    if any(not path.is_relative_to(context_dir) for path in context_images):
        parser.error('Context index must expose only shared budgeted page views')
    if any(not path.is_relative_to(figure_input) for path in images if path not in [Path(p['path']) for p in previews]):
        parser.error('Figure inputs must be inside the budgeted figure input folder')
    read={'*':'deny',**scoped_paths(job/'input',job),**scoped_paths(job/'output',job),
          **scoped_paths(context_dir,job),**scoped_paths(figure_input,job)}
    permission={'*':'deny','read':read,'edit':{'*':'deny',**scoped_paths(job/'output',job)},
                'external_directory':{'*':'deny',**scoped_paths(context_dir,job),**scoped_paths(figure_input,job)}}
    options={'reasoning':{'effort':args.reasoning_effort}}
    if args.backend=='opencode':
        worker={'description':'Source-grounded figure script worker','mode':'primary','steps':args.steps,'permission':permission,'prompt':'Only read assigned inputs and page images, and write requested source-grounded figure files. Never change frozen style/canvas. Shell, network and subagent tools are disabled.'}
        config={'mcp':{n:{'enabled':False} for n in args.disable_mcp},'permission':permission,'agent':{'figure-worker':worker}}
        if args.model:
            config['model']=args.model;worker['model']=args.model
            config['provider']={args.model.split('/',1)[0]:{'models':{args.model.split('/',1)[1]:{'options':options}}}}
        (job/'config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2))
        if args.execute:
            command,env,runtime=invocation('opencode',job,config,prompt,images,'figure-worker',job.name)
    else:
        # Codex uses local image attachments and view_image for on-demand context.
        # Its workspace-write sandbox is broader than OpenCode per-tool rules;
        # keep each working root isolated and review its recorded operations.
        env=os.environ.copy();command=['codex','exec','--skip-git-repo-check','--json','--cd',str(job),'--sandbox','workspace-write','--model',args.model,'-c','model_reasoning_effort='+json.dumps(args.reasoning_effort)]
        for image in images:command+=['--image',str(image)]
        command+=['--',prompt]
    run={'id':spec['id'],'model':args.model,'backend':args.backend,'status':'prepared','paid_call_started':False,'phase':'rendered_self_check' if previews else 'draw_script','render_previews':previews,'image_delivery':'CLI paths and hashes only; runtime tool/server input needs separate auditing','attachments':[{'path':str(p),'sha256':digest(p)} for p in images],'context_available_pages':[p['page'] for p in context['pages']],'source_spec_sha256':digest(specpath),'style_sha256':digest(stylepath),'catalogue_entry':entry,'deepseek_explicit_exception':args.allow_deepseek,'reasoning_effort':args.reasoning_effort,'timeout_seconds':args.timeout,'attempts':0}
    (job/'run.json').write_text(json.dumps(run,ensure_ascii=False,indent=2))
    run.update(capabilities=capabilities,image_budget=budget,stage_runtime=stage_identity([Path(__file__),ROOT/'scripts/figure_render.py'],('Pillow','PyMuPDF','numpy','matplotlib','fonttools','scipy','CairoSVG')))
    (job/'run.json').write_text(json.dumps(run,ensure_ascii=False,indent=2))
    if not args.execute:print(json.dumps({'status':'prepared','job':str(job),'model_calls':0}));return
    if args.backend=='opencode':run['runtime']=runtime
    started=time.perf_counter();run.update(status='running',paid_call_started=True,attempts=1,started_unix=time.time())
    (job/'run.json').write_text(json.dumps(run,ensure_ascii=False,indent=2))
    try:process=subprocess.Popen(command,cwd=job,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
    except OSError as exc:run.update(status='failed_to_launch',paid_call_started=False,error=type(exc).__name__);(job/'run.json').write_text(json.dumps(run,indent=2));raise SystemExit(1)
    timed_out=False
    try:stdout,stderr=process.communicate(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        timed_out=True
        try:os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError:pass
        try:stdout,stderr=process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            stdout,stderr=process.communicate()
    sessions=set();usages=[];tools=[];events=[];generation_ids=set()
    for line in stdout.splitlines():
        try:event=json.loads(line)
        except ValueError:continue
        if not isinstance(event,dict):continue
        generation_ids.update(re.findall(r'gen-[a-zA-Z0-9_-]+',json.dumps(event)))
        if isinstance(event.get('sessionID'),str):sessions.add(event['sessionID'])
        summary={'type':event.get('type'),'sessionID':event.get('sessionID')}
        if event.get('type')=='step_finish':
            part=event.get('part',{});item={'part_id':part.get('id'),'tokens_as_returned':part.get('tokens'),'catalogue_cost':part.get('cost')};usages.append(item);summary.update(item)
        part=event.get('part',{});tool=part.get('tool')
        if tool:
            state=part.get('state',{});params=state.get('input',{});item={'tool':tool,'status':state.get('status'),'file_path':params.get('filePath'),'error_type':'tool_error' if state.get('error') else None};tools.append(item);summary['tool']=item
        if event.get('type')=='turn.completed':usages.append({'codex_usage_as_returned':event.get('usage')})
        if event.get('type')=='thread.started':sessions.add(event.get('thread_id',''))
        events.append(summary)
    (job/'events-metadata.jsonl').write_text('\n'.join(json.dumps(e,ensure_ascii=False) for e in events)+'\n')
    if args.backend=='opencode':run.update(session_metadata('opencode',job,sorted(sessions),runtime))
    scene_path=job/'output/scene.json';valid=False;error=None
    try:
        scene=json.loads(scene_path.read_text());assert scene['id']==spec['id'] and scene['route'] in ['redraw','retain']
        assert all(isinstance(scene.get(k),list) for k in ['labels','objects','viewed_context_images','uncertainties'])
        if scene['route']=='redraw':ast.parse((job/'output/draw.py').read_text())
        if previews:
            self_check=json.loads((job/'output/self-check.json').read_text())
            assert isinstance(self_check,dict)
            assert all(isinstance(self_check.get(k),list) for k in ['reviewed_images','checks','label_placements','source_deviations'])
            allowed={(p['path'],p['sha256']) for p in previews}
            reported={(p['path'],p['sha256']) for p in self_check['reviewed_images']}
            assert reported==allowed
        valid=True
    except (OSError,ValueError,KeyError,TypeError,AssertionError,SyntaxError) as exc:error=type(exc).__name__
    run.update(status='produced_not_accepted' if valid and process.returncode==0 and not timed_out else 'needs_review',exit_code=process.returncode,timed_out=timed_out,diagnostic_types=safe_diagnostic(stderr),wall_seconds=time.perf_counter()-started,ended_unix=time.time(),session_ids=sorted(sessions),usage_as_returned=usages,generation_ids=sorted(generation_ids),tool_calls=tools,usage_partial=timed_out or process.returncode!=0 or not usages,usage_completeness='not_reconciled_with_provider',output_structure_valid=valid,output_error_type=error)
    (job/'run.json').write_text(json.dumps(run,ensure_ascii=False,indent=2));print(json.dumps({'id':spec['id'],'status':run['status'],'seconds':run['wall_seconds'],'usage_records':len(usages)}));raise SystemExit(0 if run['status']=='produced_not_accepted' else 1)


if __name__=='__main__':main()
