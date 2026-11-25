#!/usr/bin/env python3
"""Measure source-density and layer-count memory scaling in fresh workers.

Fixtures are streamed, shared-index, watertight box meshes. Increasing source
subdivision changes triangulation only; increasing height changes layer count
while keeping cross-section complexity fixed. No STL intermediates are used.
"""
from __future__ import annotations
import argparse
import io
import json
import os
from pathlib import Path
import platform
import sys
import time
import zipfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'src'))
from layerloom.contour_benchmark import benchmark, artifact_metrics


def write_grid_box(path, subdivisions, *, width=10., depth=10., height=.8, recipe='cmy'):
    """Write a cube surface grid without retaining vertices/faces in memory."""
    from layerloom.export import _content_types_xml, _rels_root_xml
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    n=int(subdivisions)
    if n<1 or n!=subdivisions:raise ValueError('subdivisions must be a positive integer')
    if min(width,depth,height)<=0:raise ValueError('dimensions must be positive')
    if any(c not in 'cmykw' for c in recipe) or not recipe:raise ValueError('invalid recipe')
    bottom=(n+1)**2;ring=4*n
    def idx(x,y,z):
        if z==0:return y*(n+1)+x
        if z==n:return bottom+(n-1)*ring+y*(n+1)+x
        if y==0:offset=x
        elif y==n:offset=3*n-1+x
        elif x in (0,n):offset=n+1+2*(y-1)+(x==n)
        else:raise ValueError('interior vertices are not part of the surface')
        return bottom+(z-1)*ring+offset
    vertices=6*n*n+2;triangles=12*n*n
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as zf:
        zf.writestr('[Content_Types].xml',_content_types_xml())
        zf.writestr('_rels/.rels',_rels_root_xml())
        with zf.open('3D/3dmodel.model','w',force_zip64=True) as raw:
            with io.TextIOWrapper(io.BufferedWriter(raw,1024**2),encoding='utf8') as out:
                out.write('<?xml version="1.0"?><model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" unit="millimeter"><resources>')
                out.write(f'<object id="1" type="model" name="grid_box__PAT_{recipe}__"><mesh><vertices>')
                written=0
                for iz in range(n+1):
                    for iy in range(n+1):
                        xs=range(n+1) if iz in (0,n) or iy in (0,n) else (0,n)
                        for ix in xs:
                            assert idx(ix,iy,iz)==written
                            out.write(f'<vertex x="{width*ix/n:.12g}" y="{depth*iy/n:.12g}" z="{height*iz/n:.12g}"/>')
                            written+=1
                assert written==vertices
                out.write('</vertices><triangles>')
                count=0
                def quad(coords,flip=False):
                    nonlocal count
                    a,b,c,d=[idx(*xyz) for xyz in coords]
                    fs=((a,c,b),(a,d,c)) if flip else ((a,b,c),(a,c,d))
                    for u,v,w in fs:out.write(f'<triangle v1="{u}" v2="{v}" v3="{w}"/>')
                    count+=2
                for a in range(n):
                    for b in range(n):
                        quad([(a,b,0),(a+1,b,0),(a+1,b+1,0),(a,b+1,0)],True)
                        quad([(a,b,n),(a+1,b,n),(a+1,b+1,n),(a,b+1,n)])
                        quad([(a,0,b),(a+1,0,b),(a+1,0,b+1),(a,0,b+1)])
                        quad([(a,n,b),(a+1,n,b),(a+1,n,b+1),(a,n,b+1)],True)
                        quad([(0,a,b),(0,a+1,b),(0,a+1,b+1),(0,a,b+1)],True)
                        quad([(n,a,b),(n,a+1,b),(n,a+1,b+1),(n,a,b+1)])
                assert count==triangles
                out.write('</triangles></mesh></object></resources><build><item objectid="1"/></build></model>')
    return dict(path=str(path.resolve()),subdivisions=n,vertices=vertices,triangles=triangles,
                dimensions_mm=[width,depth,height],recipe=recipe)


def run(output_dir,*,repeats=3,base_subdivisions=64,base_layers=100,
        step=.08,memory_budget_mib=1024,timeout_s=1800,generate_only=False):
    output_dir=Path(output_dir).resolve();output_dir.mkdir(parents=True,exist_ok=True)
    for name in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']:
        os.environ[name]='1'
    cases=[]
    report=dict(schema_version=1,created_unix_s=time.time(),python=sys.version,platform=platform.platform(),
        repeats=repeats,layer_height_mm=step,first_layer_height_mm=step,xy_tolerance_mm=.02,
        memory_budget_mib=memory_budget_mib,peak_rss_acceptance_bytes=2*1024**3,
        series={'source_density':'1x/4x/16x triangles on exactly the same box boundary',
                'layer_count':'1x/4x/16x layers, 12 source triangles and identical active cross-section'},
        notes=['No native slicer or wall-path comparison is run by this stress suite.',
               'RSS is sampled every 50 ms for the complete weave subprocess tree.',
               'Fixture generation is streamed, but its RSS is not part of the weave measurements.',
               'Fresh workers and fixed one-thread math libraries reduce warm-state and concurrency noise.'],cases=cases)
    def save():
        (output_dir/'stress_summary.json').write_text(json.dumps(report,indent=2)+'\n')
    for series in ['source_density','layer_count']:
        for scale in [1,4,16]:
            stem=f'{series}-{scale}x'
            n=base_subdivisions*int(scale**.5) if series=='source_density' else 1
            layers=10 if series=='source_density' else base_layers*scale
            fixture=write_grid_box(output_dir/'fixtures'/(stem+'.3mf'),n,height=layers*step)
            fixture['expected_layers']=layers
            row=dict(series=series,scale=scale,fixture=fixture)
            cases.append(row);save()
            print(f'[stress] {stem}: {fixture["triangles"]:,} source triangles / {layers} layers',flush=True)
            if generate_only:continue
            results=benchmark(fixture['path'],output_dir/stem,repeats=repeats,step=step,first_height=step,
                modes=('contour-0.02',),memory_budget_mib=memory_budget_mib,timeout_s=timeout_s,
                visuals=False,keep_repeats=False)
            row['benchmark_report']=str(output_dir/stem/'benchmark.json')
            row['aggregate']=results['aggregate']['contour-0.02']
            runs=results['runs'];good=[r for r in runs if r.get('core_report')]
            row['all_runs_succeeded']=len(good)==repeats and all(r['weave']['exit_code']==0 for r in runs)
            row['all_layer_counts_match']=bool(good) and all(r['core_report']['layers']==layers for r in good)
            row['max_core_rss_bytes']=max((r['core_report'].get('peak_rss_bytes',0) for r in good),default=0)
            row['max_peak_scratch_bytes']=max((r['core_report'].get('peak_scratch_bytes',0) for r in good),default=0)
            row['output_triangles']=[r['core_report']['output_triangles'] for r in good]
            row['output_bytes']=[r['artifact']['size_bytes'] for r in good]
            row['all_contour_checks_passed']=bool(good) and all(r['core_report'].get('contour_validated',False) for r in good)
            row['under_rss_acceptance']=row['aggregate']['max_observed_weave_rss_bytes']<=report['peak_rss_acceptance_bytes']
            save()
    if not generate_only:
        report['scaling']={}
        for series in ['source_density','layer_count']:
            group=[r for r in cases if r['series']==series]
            baseline=group[0]['aggregate']['max_observed_weave_rss_bytes']
            report['scaling'][series]=dict(
                rss_growth_16x_over_1x=group[-1]['aggregate']['max_observed_weave_rss_bytes']/baseline if baseline else None,
                rss_by_scale_bytes={str(r['scale']):r['aggregate']['max_observed_weave_rss_bytes'] for r in group},
                median_time_by_scale_s={str(r['scale']):r['aggregate']['median_weave_time_s'] for r in group},
                source_triangles_by_scale={str(r['scale']):r['fixture']['triangles'] for r in group},
                layers_by_scale={str(r['scale']):r['fixture']['expected_layers'] for r in group})
        report['passed']=all(r['all_runs_succeeded'] and r['all_layer_counts_match'] and r['all_contour_checks_passed']
            and r['under_rss_acceptance'] and r['aggregate']['rss_measurement_complete'] for r in cases)
        save()
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',default=str(REPO/'experiments/layer_contour/stress'))
    p.add_argument('--repeats',type=int,default=3)
    p.add_argument('--base-subdivisions',type=int,default=64)
    p.add_argument('--base-layers',type=int,default=100)
    p.add_argument('--step',type=float,default=.08)
    p.add_argument('--memory-budget-mib',type=int,default=1024)
    p.add_argument('--timeout',type=float,default=1800)
    p.add_argument('--generate-only',action='store_true')
    args=p.parse_args()
    if args.repeats<1 or args.base_layers<1 or args.base_subdivisions<1 or args.step<=0:
        p.error('counts, subdivisions, and layer height must be positive')
    result=run(args.output_dir,repeats=args.repeats,base_subdivisions=args.base_subdivisions,
        base_layers=args.base_layers,step=args.step,memory_budget_mib=args.memory_budget_mib,
        timeout_s=args.timeout,generate_only=args.generate_only)
    print(json.dumps({'report':str(Path(args.output_dir).resolve()/'stress_summary.json'),
                      'passed':result.get('passed'),'scaling':result.get('scaling')},indent=2))
    return 0 if result.get('passed',args.generate_only) else 1


if __name__=='__main__':raise SystemExit(main())
