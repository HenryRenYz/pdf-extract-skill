#!/usr/bin/env python3
"""Publish a reviewed native source crop at its frozen display size; no upsampling."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path
from PIL import Image
from figure_fallback import validate_fallback


def digest(path):return hashlib.file_digest(Path(path).open('rb'),'sha256').hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--spec',type=Path,required=True);p.add_argument('--source-crop',type=Path,required=True);p.add_argument('--original-crop',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--reason',required=True);p.add_argument('--assessment',type=Path,required=True,help='source-checked feasibility decision with evidence hashes');args=p.parse_args()
    spec=json.loads(args.spec.read_text());src=args.source_crop.resolve(strict=True);out=args.output.resolve()
    assessment=json.loads(args.assessment.read_text())
    validate_fallback({'fallback_assessment':assessment},src)
    if out.exists():p.error('Use a new output directory')
    if spec['source'].get('crop_sha256')!=digest(src):p.error('Source crop hash differs from frozen preparation')
    with Image.open(src) as image:w,h=image.size
    box=spec['source'].get('view_bbox_px',spec['source']['bbox_px']);expected=[box[2]-box[0],box[3]-box[1]]
    if [w,h]!=expected:p.error('Retained crop size does not match frozen source view')
    width,height=spec['canvas']['width_mm'],spec['canvas']['height_mm'];dpi=[w/(width/25.4),h/(height/25.4)];out.mkdir(parents=True)
    shutil.copyfile(src,out/'figure.png');files={'png':{'file':'figure.png','sha256':digest(out/'figure.png')}}
    if args.original_crop:
        original=args.original_crop.resolve(strict=True);shutil.copyfile(original,out/'source-original.png')
    meta={'schema_version':'figure-render-v1','id':spec['id'],'figure_id':spec['id'],'source':spec['source'],'canvas':spec['canvas'],'style_version':spec['style_version'],'spec_sha256':digest(args.spec),'style_sha256':spec.get('style_sha256'),'export_mode':'retain_source','vector_kind':'source_raster','png_size_px':[w,h],'png_dpi':dpi,'effective_dpi':dpi,'files':files,'artifacts':[{'path':'figure.png','format':'png','sha256':files['png']['sha256'],'declared_content':'raster'}],'reason':args.reason,'fallback_assessment':assessment,'pixel_operation':'Byte-identical copy of prepared native/rectified crop; no upsampling or retagging','source_original_copy':'source-original.png' if args.original_crop else None,'acceptance':'unreviewed','script_sha256':digest(__file__),'uncertainties':['Source crop and any page rectification still require review']}
    (out/'render-metadata.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2));print(json.dumps({'id':spec['id'],'native_pixels':[w,h],'display_mm':[width,height],'effective_dpi':dpi,'model_calls':0}))


if __name__=='__main__':main()
