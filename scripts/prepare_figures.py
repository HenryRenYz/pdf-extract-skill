#!/usr/bin/env python3
"""Freeze figure sizes, source crops, and budgeted page context before dispatch."""
import argparse
import hashlib
import json
import math
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

from prepare_images import select_pages, starts


def digest(path):
    return hashlib.file_digest(Path(path).open('rb'), 'sha256').hexdigest()


def mapped(points, matrix):
    p=np.column_stack([np.asarray(points,dtype=float),np.ones(len(points))]) @ matrix.T
    if np.any(np.abs(p[:,2])<1e-9):raise ValueError('Transform maps a source point to infinity')
    return p[:,:2]/p[:,2,None]


def rectify(image, options):
    """Page-only homography/deskew; never infer skew from diagram axes."""
    if not options:return image,np.eye(3),image.width,[]
    import cv2
    array=np.asarray(image.convert('RGB'));matrix=np.eye(3);notes=[];nominal_width=image.width
    quad=options.get('page_quad_px')
    if quad is not None:
        q=np.asarray(quad,dtype=np.float32)
        if q.shape!=(4,2) or not np.isfinite(q).all():raise ValueError('Quad must be TL,TR,BR,BL')
        if not ((q[:,0]>=0).all() and (q[:,0]<=image.width).all() and (q[:,1]>=0).all() and (q[:,1]<=image.height).all()):raise ValueError('Page quad outside image')
        if not cv2.isContourConvex(q.astype(np.float32)) or abs(cv2.contourArea(q))<100:raise ValueError('Page quad must be convex and nondegenerate')
        size=options.get('rectified_size_px')
        if not size or len(size)!=2 or any(type(v)!=int or v<2 for v in size):raise ValueError('A reviewed rectified_size_px is required for perspective correction')
        w,h=size;target=np.array([[0,0],[w-1,0],[w-1,h-1],[0,h-1]],dtype=np.float32)
        transform=cv2.getPerspectiveTransform(q,target);array=cv2.warpPerspective(array,transform,(w,h),flags=cv2.INTER_CUBIC,borderValue=(255,255,255));matrix=transform @ matrix;nominal_width=w;notes.append('reviewed page-border perspective transform')
    angle=options.get('deskew_degrees',0)
    if type(angle) not in (int,float) or not math.isfinite(angle) or abs(angle)>15:raise ValueError('Review page-level deskew; supported absolute angle <=15 degrees')
    if angle:
        h,w=array.shape[:2];rot=cv2.getRotationMatrix2D((w/2,h/2),angle,1);hom=np.vstack([rot,[0,0,1]])
        corners=mapped([[0,0],[w,0],[w,h],[0,h]],hom);lo=np.floor(corners.min(axis=0));hi=np.ceil(corners.max(axis=0));hom[:2,2]-=lo;size=tuple((hi-lo).astype(int));array=cv2.warpPerspective(array,hom,size,flags=cv2.INTER_CUBIC,borderValue=(255,255,255));matrix=hom @ matrix;notes.append('reviewed page text/border deskew, expanded canvas')
    unknown=set(options)-{'page_quad_px','rectified_size_px','deskew_degrees','reviewed','evidence'}
    if unknown:raise ValueError(f'Unsupported transforms: {sorted(unknown)}; nonlinear page curvature needs separately reviewed dewarping')
    if not options.get('reviewed'):raise ValueError('Page transform must have reviewed=true; do not straighten intrinsic graph slopes')
    return Image.fromarray(array),matrix,nominal_width,notes


def region_box(box, width, height):
    if not isinstance(box,list) or len(box)!=4 or not all(type(v)==int for v in box) or not (0<=box[0]<box[2]<=width and 0<=box[1]<box[3]<=height):raise ValueError('bbox_px must be integer source pixel bounds inside the page')
    return box


def transformed_box(box,matrix,width,height):
    x0,y0,x1,y1=box;corners=mapped([[x0,y0],[x1,y0],[x1,y1],[x0,y1]],matrix)
    lo=np.floor(corners.min(axis=0)).astype(int);hi=np.ceil(corners.max(axis=0)).astype(int)
    return [int(max(0,lo[0])),int(max(0,lo[1])),int(min(width,hi[0])),int(min(height,hi[1]))]


def record(path,root,kind,bbox,scale=1):
    with Image.open(path) as image:w,h=image.size
    return {'path':path.relative_to(root).as_posix(),'kind':kind,'width':w,'height':h,'sha256':digest(path),'view_bbox_px':bbox,'view_scale':scale}


def budget_images(image,folder,root,max_pixels,max_edge,overlap):
    folder.mkdir(parents=True,exist_ok=True);w,h=image.size;factor=min(1,1024/max(w,h),max_edge/max(w,h),math.sqrt(max_pixels/(w*h)))
    overview=image.resize((max(1,int(w*factor)),max(1,int(h*factor))),Image.Resampling.LANCZOS);p=folder/'overview.png';overview.save(p);records=[record(p,root,'overview',[0,0,w,h],factor)]
    tw=min(w,max_edge,max_pixels);th=min(h,max_edge,max_pixels//tw)
    if overlap>=min(tw,th) and (w>tw or h>th):raise ValueError('Overlap too large for the model image budget')
    for y in starts(h,th,overlap):
        for x in starts(w,tw,overlap):
            b=[x,y,min(w,x+tw),min(h,y+th)];p=folder/f'tile-{len(records):03d}.png';image.crop(b).save(p);records.append(record(p,root,'tile',b))
    return records


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf',type=Path,required=True)
    parser.add_argument('--regions',type=Path,required=True,help='JSON list of id/page/bbox_px proposals in native scan coordinates')
    parser.add_argument('--style',type=Path,required=True,help='Coordinator-reviewed frozen book profile')
    parser.add_argument('--output',type=Path,required=True,help='New work directory')
    parser.add_argument('--page-width-mm',type=float,required=True,help='One canonical page width for the book; PDF points may be unreliable for scans')
    parser.add_argument('--context-pages',help='Readonly pages available to workers, e.g. 1-339; defaults to figure pages')
    parser.add_argument('--max-pixels',type=int,required=True,help='Verified model image-area limit')
    parser.add_argument('--max-edge',type=int,default=2048)
    parser.add_argument('--overlap',type=int,default=128)
    parser.add_argument('--page-transforms',type=Path,help='Reviewed page-level transforms keyed by physical page')
    parser.add_argument('--dpi',type=float,default=96,help='Fallback render resolution for PDFs without one full-page native scan')
    args=parser.parse_args()
    if min(args.page_width_mm,args.max_pixels,args.max_edge,args.dpi)<=0 or args.overlap<0:parser.error('Positive dimensions/dpi required; overlap nonnegative')
    root=args.output.expanduser().resolve()
    if root.exists():parser.error('Output exists; use a new run directory')
    regions=json.loads(args.regions.read_text());style=json.loads(args.style.read_text())
    if not isinstance(regions,list) or not regions:parser.error('Provide nonempty figure proposals; detector coverage is a separate audit')
    ids=[r['id'] for r in regions]
    if len(ids)!=len(set(ids)) or any(not isinstance(i,str) or not i or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in i) for i in ids):parser.error('Unique safe figure IDs required')
    pages=select_pages(args.context_pages) if args.context_pages else sorted({r['page'] for r in regions})
    if not {r['page'] for r in regions}.issubset(pages):parser.error('Context pages must include all figure pages')
    transforms=json.loads(args.page_transforms.read_text()) if args.page_transforms else {}
    import pymupdf as fitz
    pdf_path=args.pdf.expanduser().resolve(strict=True);doc=fitz.open(pdf_path)
    if max(pages)>len(doc):parser.error('Requested page exceeds PDF')
    root.mkdir(parents=True);(root/'shared').mkdir()
    # Freeze a portable font bundle before workers are dispatched. Relative
    # paths are rebased once, then job copies preserve these exact bytes.
    font_paths=[];font_manifest=[]
    for value in style.get('font',{}).get('paths',[]):
        src=Path(value).expanduser()
        if not src.is_absolute():src=args.style.resolve().parent/src
        src=src.resolve(strict=True);hashvalue=digest(src)
        name=hashvalue[:16]+'-'+src.name;dst=root/'shared/fonts'/name
        dst.parent.mkdir(exist_ok=True)
        if not dst.exists():shutil.copyfile(src,dst)
        font_paths.append('fonts/'+name);font_manifest.append({'path':'fonts/'+name,'sha256':hashvalue})
    if 'font' in style:style['font']['paths']=font_paths
    style['font_file_manifest']=font_manifest
    (root/'shared/style.json').write_text(json.dumps(style,ensure_ascii=False,indent=2))
    (root/'shared/page-transforms.json').write_text(json.dumps(transforms,ensure_ascii=False,indent=2))
    pdfhash=digest(pdf_path);inventory=[];page_records=[]
    for number in pages:
        page=doc[number-1];infos=page.get_image_info(xrefs=True);pdir=root/'source/pages'/f'{number:04d}';pdir.mkdir(parents=True)
        full=next((info for info in infos if info.get('xref') and abs(fitz.Rect(info['bbox']).width/page.rect.width-1)<.01 and abs(fitz.Rect(info['bbox']).height/page.rect.height-1)<.01 and page.rotation==0 and info['transform'][1]==0 and info['transform'][2]==0 and info['transform'][0]>0 and info['transform'][3]>0),None)
        if full:
            pix=fitz.Pixmap(doc,full['xref']);pix=fitz.Pixmap(fitz.csRGB,pix) if pix.n!=3 else pix;image=Image.frombytes('RGB',[pix.width,pix.height],pix.samples);native=True
        else:
            pix=page.get_pixmap(matrix=fitz.Matrix(args.dpi/72,args.dpi/72),alpha=False);image=Image.frombytes('RGB',[pix.width,pix.height],pix.samples);native=False
        raw=pdir/'original.png';image.save(raw);view,matrix,nominal_width,notes=rectify(image,transforms.get(str(number),{}));vp=pdir/'view.png';view.save(vp)
        context=budget_images(view,root/'shared/pages'/f'{number:04d}',root,args.max_pixels,args.max_edge,args.overlap)
        page_record={'page':number,'source_size_px':list(image.size),'view_size_px':list(view.size),'page_size_pt':[page.rect.width,page.rect.height],'native_scan_extraction':native,'source_sha256':digest(raw),'view_sha256':digest(vp),'source_to_view_homography':matrix.tolist(),'view_to_source_homography':np.linalg.inv(matrix).tolist(),'nominal_page_width_px':nominal_width,'transforms':notes,'transform_config':transforms.get(str(number),{}),'transform_config_sha256':hashlib.sha256(json.dumps(transforms.get(str(number),{}),sort_keys=True,ensure_ascii=False).encode()).hexdigest(),'images':context};page_records.append(page_record)
        for region in [r for r in regions if r['page']==number]:
            bbox=region_box(region['bbox_px'],*image.size);vb=transformed_box(bbox,matrix,*view.size);crop=view.crop(vb);folder=root/'figures'/region['id'];(folder/'reference').mkdir(parents=True)
            original=folder/'reference/source-original.png';image.crop(bbox).save(original);reference=folder/'reference/source.png';crop.save(reference)
            inputs=budget_images(crop,folder/'input',root,args.max_pixels,args.max_edge,args.overlap)
            width_mm=(vb[2]-vb[0])/nominal_width*args.page_width_mm;height_mm=(vb[3]-vb[1])/nominal_width*args.page_width_mm
            spec={'id':region['id'],'source':{'pdf_sha256':pdfhash,'page':number,'bbox_px':bbox,'page_size_px':list(image.size),'page_size_pt':[page.rect.width,page.rect.height],'crop_sha256':digest(reference),'view_bbox_px':vb,'page_transform':page_record},'canvas':{'width_mm':width_mm,'height_mm':height_mm,'canonical_page_width_mm':args.page_width_mm,'scale_mode':'book-wide normalized page width','caption_in_canvas':False},'style_version':style['version'],'style_sha256':digest(root/'shared/style.json'),'route':region.get('route','redraw_candidate'),'caption':region.get('caption'),'inputs':inputs,'context_root':'shared/pages','context_manifest':'shared/pages.json','scene':region.get('scene',{}),'notes':region.get('notes',[]),'status':'prepared_not_semantically_verified'}
            if spec['route'] not in ['redraw_candidate','retain']:raise ValueError('route must be redraw_candidate or retain')
            (folder/'spec.json').write_text(json.dumps(spec,ensure_ascii=False,indent=2));inventory.append({'id':region['id'],'page':number,'spec':(folder/'spec.json').relative_to(root).as_posix(),'reference':reference.relative_to(root).as_posix(),'width_mm':width_mm,'height_mm':height_mm,'route':spec['route']})
    (root/'shared/pages.json').write_text(json.dumps({'pdf_sha256':pdfhash,'coordinate_note':'Images are views; map to source with each page inverse homography. Figure inputs are relative to crop view_bbox origin.','image_budget':{'max_pixels':args.max_pixels,'max_edge':args.max_edge,'overlap':args.overlap},'pages':page_records},ensure_ascii=False,indent=2))
    (root/'inventory.json').write_text(json.dumps({'source_pdf_sha256':pdfhash,'canonical_page_width_mm':args.page_width_mm,'style_sha256':digest(root/'shared/style.json'),'figures':inventory,'coverage':'selected proposals only; not proof of all figures detected'},ensure_ascii=False,indent=2))
    print(json.dumps({'figures':len(inventory),'context_pages':len(pages),'output':str(root),'model_calls':0}))


if __name__=='__main__':main()
