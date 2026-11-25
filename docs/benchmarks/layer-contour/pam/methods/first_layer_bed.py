"""Stream actual first-layer deposition and inspect Pam's U1 bed clearances.

Normally requires a successful, completed local slice report. An explicit
--allow-partial-diagnostic --gcode PATH override may inspect the complete first
layer of an aborted export, clearly marked as an incomplete-export diagnostic.
Custom purge macros and pre-layer preparation are excluded; no printer action.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import zipfile

import numpy as np
from shapely.geometry import LineString, MultiLineString, GeometryCollection
from shapely.ops import unary_union

WORD = re.compile(r'([A-Za-z])\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))')
COLORS = {0:'#0086D6', 2:'#FFF800', 3:'#FF00E1'}
GROUPS = ['model', 'model brim', 'supports', 'prime tower', 'prime tower brim', 'other adhesion']


def first_layer(path):
    xyz=[0.,0.,0.];absolute_xyz=True;absolute_e=True;es={0:0.};tool=0
    feature='Custom';width=None;inside=False;brim=False;layer_count=0
    rows=[];stats=dict(lines_read=0,custom_macros_excluded=Counter(),excluded_positive_xy_moves=0,
                       first_layer_z_mm=None,object_starts=0,object_stops=0)
    prefix_digest=hashlib.sha256();prefix_bytes=0
    with Path(path).open(newline='') as stream:
        for number,raw in enumerate(stream,1):
            encoded=raw.encode('utf-8');prefix_digest.update(encoded);prefix_bytes+=len(encoded)
            stats['lines_read']=number
            code,_,comment=raw.partition(';');c=comment.strip();lower=c.lower()
            if c=='LAYER_CHANGE':
                layer_count+=1
                if layer_count>1:break
            if layer_count==1 and c.startswith('Z:'):
                stats['first_layer_z_mm']=float(c.split(':',1)[1])
            if lower.startswith('printing object '):inside=True;stats['object_starts']+=1
            elif lower.startswith('stop printing object '):inside=False;stats['object_stops']+=1
            if c=='WIPE_TOWER_BRIM_START':brim=True
            elif c=='WIPE_TOWER_BRIM_END':brim=False
            if lower.startswith(('type:','feature:')):feature=c.split(':',1)[1].strip()
            if c.upper().startswith(('WIDTH:','LINE_WIDTH:')):
                width=float(c.split(':',1)[1]);assert math.isfinite(width) and width>0
            code=re.sub(r'\([^)]*\)','',code).strip().upper()
            if not code:continue
            if code.startswith('SM_PRINT_PREEXTRUDE_FILAMENT'):
                stats['custom_macros_excluded'][code.split()[0]]+=1
            code=re.sub(r'^N\d+\s*','',code).split('*',1)[0]
            match=re.match(r'([GMT])(\d+)',code)
            if not match:continue
            command=match.group(1)+str(int(match.group(2)))
            args={k:float(v) for k,v in WORD.findall(code[match.end():])}
            if not all(math.isfinite(v) for v in args.values()):raise ValueError('Nonfinite G-code')
            if command.startswith('T'):tool=int(match.group(2));es.setdefault(tool,0.)
            elif command=='G90':absolute_xyz=True
            elif command=='G91':absolute_xyz=False
            elif command=='M82':absolute_e=True
            elif command=='M83':absolute_e=False
            elif command=='G20':raise ValueError('Inch G-code is unsupported')
            elif command=='M200' and args.get('D',0)>0:raise ValueError('Volumetric E is unsupported')
            elif command=='G92':
                for i,axis in enumerate('XYZ'):
                    if axis in args:xyz[i]=args[axis]
                if 'E' in args:es[tool]=args['E']
            elif command in {'G0','G1','G2','G3'}:
                nxt=[args.get(axis,xyz[i]) if absolute_xyz else xyz[i]+args.get(axis,0.) for i,axis in enumerate('XYZ')]
                e=args.get('E',es[tool] if absolute_e else 0.);delta=e-es[tool] if absolute_e else e
                if 'E' in args:es[tool]=e if absolute_e else es[tool]+e
                positive=delta>1e-10 and (math.dist(xyz[:2],nxt[:2])>1e-12 or command in {'G2','G3'})
                group=None
                if layer_count==1:
                    if feature.lower()=='prime tower':group='prime tower brim' if brim else 'prime tower'
                    elif inside and feature.lower().startswith('support'):group='supports'
                    elif feature.lower()=='brim':group='model brim' if inside else 'other adhesion'
                    elif feature.lower()=='skirt':group='other adhesion'
                    elif inside and feature.lower()!='custom':group='model'
                if positive and group:
                    if command in {'G2','G3'}:raise ValueError(f'Extruding arc at line {number}')
                    if abs(nxt[2]-xyz[2])>1e-6:raise ValueError(f'Nonplanar extrusion at line {number}')
                    if width is None:raise ValueError(f'Missing line width at line {number}')
                    if abs(nxt[2]-stats['first_layer_z_mm'])>1e-6:raise ValueError(f'Unexpected first-layer Z at line {number}')
                    rows.append(dict(start=tuple(xyz[:2]),end=tuple(nxt[:2]),tool=tool,width=width,feature=feature,group=group,extrusion=delta))
                elif positive:stats['excluded_positive_xy_moves']+=1
                xyz=nxt
    if layer_count<2:raise ValueError('Could not find complete first layer and start of second layer')
    if stats['object_starts']!=stats['object_stops']:raise ValueError('Unbalanced model object markers')
    if not rows:raise ValueError('No first-layer deposition found')
    stats['custom_macros_excluded']=dict(stats['custom_macros_excluded'])
    stats['complete_first_layer_found']=True
    stats['prefix_utf8_bytes_read']=prefix_bytes
    stats['prefix_utf8_sha256']=prefix_digest.hexdigest()
    stats['hash_scope']='Exact UTF-8 prefix through the second LAYER_CHANGE marker; not a whole-file hash'
    return rows,stats


def footprint(rows):
    bywidth=defaultdict(list)
    for row in rows:bywidth[row['width']].append((row['start'],row['end']))
    return unary_union([MultiLineString(lines).buffer(width/2,quad_segs=16) for width,lines in bywidth.items()]) if bywidth else GeometryCollection()


def render(rows,metrics,output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib.patches import Rectangle
    from matplotlib.lines import Line2D
    fig,axes=plt.subplots(1,3,figsize=(18,6),gridspec_kw={'width_ratios':[1.25,1,1]})
    for ax in axes:
        ax.add_patch(Rectangle((0,0),270,270,facecolor='#f7f7f7',edgecolor='#444444',lw=1,zorder=0))
        # Black underlay keeps yellow distinguishable from the pale plate.
        yellow=[(r['start'],r['end']) for r in rows if r['tool']==2]
        if yellow:ax.add_collection(LineCollection(yellow,colors='#5d5700',linewidths=1.15,alpha=.9))
        for tool in sorted({r['tool'] for r in rows}):
            rr=[r for r in rows if r['tool']==tool]
            ax.add_collection(LineCollection([(r['start'],r['end']) for r in rr],colors=COLORS.get(tool,'#777777'),linewidths=.65,alpha=.95))
        ax.set_aspect('equal');ax.grid(alpha=.15);ax.set_xlabel('X (mm)');ax.set_ylabel('Y (mm)')
    axes[0].set_xlim(-5,275);axes[0].set_ylim(-5,275)
    axes[0].set_title('Actual first layer on 270 × 270 mm U1 plate')
    tower=metrics['tower_including_brim']['nominal_bead_bounds_mm']
    tx0,ty0,tx1,ty1=tower
    settings=metrics['saved_profile_settings']
    brim_width=settings.get('prime_tower_brim_width','?')
    axes[0].add_patch(Rectangle((tx0,ty0),tx1-tx0,ty1-ty0,fill=False,edgecolor='#a53383',lw=1.2,ls='--'))
    axes[0].annotate(f'Tower + {brim_width} mm brim',xy=((tx0+tx1)/2,ty1),xytext=(12,264),fontsize=9,arrowprops={'arrowstyle':'->','color':'#a53383'},color='#8d236b')
    mx0,my0,mx1,my1=metrics['groups']['model']['nominal_bead_bounds_mm']
    axes[0].annotate('Yellow model contact',xy=((mx0+mx1)/2,(my0+my1)/2),xytext=(165,80),fontsize=9,arrowprops={'arrowstyle':'->','color':'#555500'},color='#555500')
    axes[0].text(12,10,'Cyan front/middle paths:\nsupports',fontsize=8,color='#00689e')
    axes[1].set_xlim(tx0-5,tx1+5);axes[1].set_ylim(ty0-9,ty1+5)
    axes[1].set_title(f'Tower brim and nearest supports\nNominal bead clearance: {metrics["tower_to_support_clearance_mm"]:.3f} mm')
    if 'model brim' in metrics['groups']:
        bx0,by0,bx1,by1=metrics['groups']['model brim']['nominal_bead_bounds_mm']
        axes[2].set_xlim(min(mx0,bx0)-1,max(mx1,bx1)+1);axes[2].set_ylim(min(my0,by0)-1,max(my1,by1)+1)
        axes[2].set_title('Model contact and brim at Z = 0.16 mm\nYellow brim is included in this reference slice')
    else:
        axes[2].set_xlim(mx0-3,mx1+3);axes[2].set_ylim(my0-3,my1+3)
        axes[2].set_title('Model contact at Z = 0.16 mm\nOther model geometry is carried by supports')
    legend=[Line2D([0],[0],color=COLORS[t],lw=3,label={0:'Cyan · slot 1',2:'Yellow · slot 3',3:'Magenta · slot 4'}[t]) for t in [0,2,3]]
    axes[0].legend(handles=legend,loc='lower right',fontsize=8)
    source_label='completed native G-code' if metrics['full_slice_completed'] else 'PARTIAL EXPORT DIAGNOSTIC — complete first-layer paths only'
    fig.suptitle(f'Pam first-layer deposition: {source_label}\nCustom purge macros and pre-layer preparation excluded; plotted filament colors are the saved U1 mapping',fontsize=13)
    fig.text(.5,.02,f'Saved tower settings: {settings.get("prime_tower_width","?")} mm width · {brim_width} mm brim · {settings.get("wipe_tower_wall_type","?")} walls · {settings.get("wipe_tower_extra_rib_length","?")} mm extra rib length · {settings.get("wipe_tower_rib_width","?")} mm rib width · {settings.get("wipe_tower_max_purge_speed","?")} mm/s tower speed cap (excludes ramming)',ha='center',fontsize=10)
    fig.tight_layout(rect=[0,.05,1,.92]);fig.savefig(output,dpi=180);plt.close(fig)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--slice-report',type=Path,required=True)
    parser.add_argument('--output-prefix',type=Path,required=True)
    parser.add_argument('--allow-partial-diagnostic',action='store_true',help='Explicitly inspect a complete first layer from an unfinished/aborted export; never counts as full slicing validation')
    parser.add_argument('--gcode',type=Path,help='Explicit partial G-code path, only with --allow-partial-diagnostic')
    args=parser.parse_args();slice_report=json.loads(args.slice_report.read_text())
    if args.allow_partial_diagnostic:
        if args.gcode is None:raise SystemExit('--allow-partial-diagnostic requires an explicit --gcode path')
        gcode=args.gcode.resolve()
        identity=dict(path=str(gcode),size_bytes_snapshot=gcode.stat().st_size,partial_export=True,sha256=None)
    elif args.gcode is not None:
        raise SystemExit('--gcode is only accepted with --allow-partial-diagnostic')
    elif not slice_report.get('full_slice_completed') or 'gcode' not in slice_report:
        raise SystemExit('A completed successful native slice report with a G-code artifact is required')
    else:
        gcode=Path(slice_report['gcode']['path']);identity=slice_report['gcode']
    rows,stats=first_layer(gcode)
    groups={}
    for name in GROUPS:
        rr=[r for r in rows if r['group']==name]
        if not rr:continue
        shape=footprint(rr)
        groups[name]=dict(segments=len(rr),tools=sorted({r['tool'] for r in rr}),nominal_bead_bounds_mm=list(shape.bounds),nominal_bead_area_mm2=shape.area,centerline_length_mm=sum(math.dist(r['start'],r['end']) for r in rr))
    tower_rows=[r for r in rows if r['group'].startswith('prime tower')]
    tower=footprint(tower_rows);support=footprint([r for r in rows if r['group']=='supports']);model=footprint([r for r in rows if r['group'] in {'model','model brim'}]);all_shape=footprint(rows)
    model_brim=footprint([r for r in rows if r['group']=='model brim'])
    model_body=footprint([r for r in rows if r['group']=='model'])
    with zipfile.ZipFile(slice_report['project']['path']) as z:profile=json.loads(z.read('Metadata/project_settings.config'))
    keys=['prime_tower_width','prime_tower_brim_width','wipe_tower_extra_rib_length','wipe_tower_rib_width','wipe_tower_max_purge_speed','wipe_tower_wall_type','wipe_tower_cone_angle','wipe_tower_x','wipe_tower_y','enable_support','enable_prime_tower','initial_layer_print_height','layer_height','filament_colour']
    bounds=list(all_shape.bounds)
    result=dict(gcode=identity,slice_report=str(args.slice_report.resolve()),first_layer=stats,groups=groups,tools={str(t):sum(r['tool']==t for r in rows) for t in sorted({r['tool'] for r in rows})},
        full_slice_completed=bool(slice_report.get('full_slice_completed') and not args.allow_partial_diagnostic),diagnostic_from_incomplete_gcode=args.allow_partial_diagnostic,source_slice_stop_reason=slice_report.get('stop_reason'),
        tower_including_brim=dict(nominal_bead_bounds_mm=list(tower.bounds),nominal_bead_area_mm2=tower.area),
        all_nominal_bead_bounds_mm=bounds,bed_edge_clearances_mm=dict(left=bounds[0],front=bounds[1],right=270-bounds[2],rear=270-bounds[3]),
        all_nominal_deposition_inside_plate=min(bounds[:2])>=0 and max(bounds[2:])<=270,
        tower_to_support_clearance_mm=tower.distance(support),tower_to_model_clearance_mm=tower.distance(model),tower_support_overlap_area_mm2=tower.intersection(support).area,
        model_including_brim_to_support_clearance_mm=model.distance(support),
        model_brim_support_overlap_area_mm2=model_brim.intersection(support).area,
        model_body_support_overlap_area_mm2=model_body.intersection(support).area,
        saved_profile_settings={key:profile.get(key) for key in keys},
        method='Read only through the second layer marker. Positive planar G0/G1 extrusion at first-layer Z is classified by native feature/object/brim markers. Nominal bead footprints are round buffers of annotated line width/2, unioned per class.',
        limitations=['This is nominal first-layer geometry, not a guarantee of bed adhesion or tower stability.', 'Printer custom purge macros are opaque and intentionally excluded, as are startup movements before the first layer marker.', 'Reported clearance does not include nozzle-body, carriage or later-layer collision envelopes.'],
        print_started=False,printer_contacted=False)
    if args.allow_partial_diagnostic:
        result['limitations'].insert(0,'This is a diagnostic from an incomplete/aborted G-code export. Only the first layer is complete and inspected; full slicing, all-layer bed fit and wall comparisons are NOT validated.')
    out=args.output_prefix;out.parent.mkdir(parents=True,exist_ok=True)
    result['image']=str(Path(str(out)+'.png').resolve())
    render(rows,result,result['image'])
    Path(str(out)+'.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':main()
