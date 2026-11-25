"""Reproducible layer-contour benchmarks, bounded previews, and wall comparisons.

Run each weave/slice in a fresh subprocess. Process-tree RSS is sampled, not
misrepresented as an exact allocator high-water mark. Full slicing is optional
and requires an explicit command template and printer/process configuration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import statistics
import subprocess
import sys
import tempfile
import time
import zipfile

import numpy as np
from lxml import etree


def artifact_metrics(path):
    path=Path(path)
    report={'path':str(path),'size_bytes':path.stat().st_size}
    digest=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):digest.update(block)
    report['sha256']=digest.hexdigest()
    if zipfile.is_zipfile(path):
        report.update(vertices=0,triangles=0,uncompressed_geometry_bytes=0)
        with zipfile.ZipFile(path) as z:
            report['zip_valid']=z.testzip() is None
            for entry in z.infolist():
                if not entry.filename.lower().endswith('.model'):continue
                report['uncompressed_geometry_bytes']+=entry.file_size
                with z.open(entry) as f:
                    for _,element in etree.iterparse(f,events=('end',),huge_tree=True,resolve_entities=False,no_network=True):
                        name=etree.QName(element).localname
                        if name=='vertex':report['vertices']+=1
                        elif name=='triangle':report['triangles']+=1
                        element.clear()
                        while element.getprevious() is not None:del element.getparent()[0]
    elif path.suffix.lower()=='.gcode':
        import re
        motion=0; extrusion_arguments=0
        with path.open(errors='replace') as f:
            for line in f:
                code=line.split(';',1)[0].strip()
                if re.match(r'G(?:0|1|2|3)(?:\s|[XYZEFIJKR]|$)',code,re.I):
                    motion+=1
                    if re.search(r'E[-+0-9.]',code,re.I):extrusion_arguments+=1
        report.update(motion_commands=motion,commands_with_extrusion_parameter=extrusion_arguments)
    return report


def run_measured(command,log_path,*,timeout_s=3600,env=None):
    try:import psutil
    except ImportError as e:raise RuntimeError('Benchmarks require the bench extra: psutil') from e
    start=time.monotonic(); peak=0; samples=0; timed_out=False; rss_errors=set()
    with Path(log_path).open('w') as log:
        process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=env,start_new_session=True)
        try:
            monitored=psutil.Process(process.pid)
            while process.poll() is None:
                try:
                    family=[monitored]
                    try:family+=monitored.children(recursive=True)
                    except psutil.NoSuchProcess:pass
                    except (psutil.Error,PermissionError) as exc:rss_errors.add(type(exc).__name__+': cannot enumerate descendants')
                    rss=0
                    for member in family:
                        try:rss+=member.memory_info().rss
                        except psutil.NoSuchProcess:pass
                        except (psutil.Error,PermissionError) as exc:rss_errors.add(type(exc).__name__)
                    peak=max(peak,rss);samples+=1
                except psutil.NoSuchProcess:pass
                except (psutil.Error,PermissionError) as exc:rss_errors.add(type(exc).__name__)
                if time.monotonic()-start>timeout_s:
                    timed_out=True;os.killpg(process.pid,signal.SIGTERM)
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL)
                    break
                time.sleep(.05)
            code=process.wait()
        except BaseException:
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                process.wait()
            raise
    return {'command':command,'exit_code':code,'elapsed_s':time.monotonic()-start,
            'peak_process_tree_rss_bytes_observed':peak,'rss_poll_interval_s':.05,
            'rss_samples':samples,'rss_measurement_complete':bool(samples and peak>0 and not rss_errors),'rss_measurement_errors':sorted(rss_errors),'timed_out':timed_out,'log':str(log_path)}


def runtime_metadata():
    import platform
    import importlib.metadata
    result=dict(python=sys.version,python_executable=sys.executable,platform=platform.platform(),machine=platform.machine())
    for name in ['numpy','shapely','trimesh','psutil','lxml']:
        try:result[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:result[name]=None
    try:
        import shapely
        result['geos']=shapely.geos_version_string
    except (ImportError,AttributeError):pass
    import plistlib
    info=Path('/Applications/Snapmaker Orca.app/Contents/Info.plist')
    if info.exists():
        with info.open('rb') as f:result['installed_snapmaker_orca_version']=plistlib.load(f).get('CFBundleShortVersionString')
    package=Path(__file__).parent
    result['implementation_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(package.glob('contour_*.py'))}
    result['implementation_sha256']['weave.py']=hashlib.sha256((package/'weave.py').read_bytes()).hexdigest()
    return result


def _source_environment():
    env=os.environ.copy();src=str(Path(__file__).resolve().parents[1])
    env['PYTHONPATH']=src+os.pathsep+env.get('PYTHONPATH','')
    env['PYTHONDONTWRITEBYTECODE']='1'
    return env


def _report_for(output):
    for p in [output.with_suffix('.report.json'),Path(str(output)+'.report.json')]:
        if p.exists():return json.loads(p.read_text())
    return None


def _exclusive_benchmark(function):
    """Prevent two benchmark managers from writing the same evidence directory."""
    from functools import wraps
    @wraps(function)
    def guarded(input_path,output_dir,*args,**kwargs):
        directory=Path(output_dir).resolve();directory.mkdir(parents=True,exist_ok=True)
        lock=directory/'.benchmark.lock'
        try:
            with lock.open('x') as f:f.write(str(os.getpid())+'\n')
        except FileExistsError as exc:
            raise RuntimeError(f'Benchmark directory is already locked: {lock}. Use a new directory; inspect the recorded PID before removing a stale lock.') from exc
        try:return function(input_path,output_dir,*args,**kwargs)
        finally:lock.unlink(missing_ok=True)
    return guarded

@_exclusive_benchmark
def benchmark(input_path,output_dir,*,repeats=3,step=.08,first_height=.16,
              modes=('legacy','contour-0','contour-0.01','contour-0.02'),
              memory_budget_mib=1024,timeout_s=3600,slicer_command=None,visuals=False,keep_repeats=False):
    input_path=Path(input_path).resolve();output_dir=Path(output_dir).resolve();output_dir.mkdir(parents=True,exist_ok=True)
    if repeats<1:raise ValueError('repeats must be positive')
    cases=[];env=_source_environment()
    summary={'runtime':runtime_metadata(),'input':artifact_metrics(input_path),'step_mm':step,'first_layer_height_mm':first_height,
             'repeats':repeats,'runs':cases,'slicer_command_template':slicer_command,
             'note':'Weaving and slicer RSS are measured separately. Absence of a full-slice command means wall paths are NOT validated.'}
    for mode in modes:
        if mode!='legacy' and not mode.startswith('contour-'):raise ValueError('Unknown benchmark mode '+mode)
        for rep in range(repeats):
            stem=f'{mode}-r{rep+1}';output=output_dir/(stem+'.3mf');scratch=output_dir/(stem+'-scratch');scratch.mkdir(exist_ok=True)
            command=[sys.executable,'-m','layerloom.weave','-i',str(input_path),'-o',str(output),'--step',str(step),'--no-export-stls']
            if mode!='legacy':command+=['--weave-mode','layer-contour','--xy-tolerance-mm',mode.split('-',1)[1],
                '--first-layer-height',str(first_height),'--memory-budget-mib',str(memory_budget_mib),'--scratch-dir',str(scratch)]
            row={'mode':mode,'repeat':rep+1,'weave':run_measured(command,output_dir/(stem+'.log'),timeout_s=timeout_s,env=env),
                 'wall_paths_validated':False}
            if mode=='legacy':row['legacy_first_layer_geometry_height_mm']=step
            if row['weave']['exit_code']==0 and output.exists():
                row['artifact']=artifact_metrics(output);row['core_report']=_report_for(output)
                if slicer_command:
                    gcode=output_dir/(stem+'.gcode')
                    command=[arg.format(input=str(output),output=str(gcode),output_dir=str(output_dir)) for arg in slicer_command]
                    row['slice']=run_measured(command,output_dir/(stem+'-slice.log'),timeout_s=timeout_s,env=env)
                    row['full_slice_completed']=row['slice']['exit_code']==0 and gcode.exists() and gcode.stat().st_size>0
                    if row['full_slice_completed']:row['gcode']=artifact_metrics(gcode)
                if visuals and rep==0 and mode!='legacy':
                    try:row['visual_report']=render_comparison(input_path,output,output_dir/(stem+'-visual'),step=step,first_height=first_height)
                    except Exception as e:row['visual_error']=str(e)
            cases.append(row)
            (output_dir/'benchmark.json').write_text(json.dumps(summary,indent=2)+'\n')
    # Zero-simplification new-mode output is the controlled reference for unequal
    # first layers. Legacy's historical first-band semantics are reported separately.
    from layerloom.contour_gcode import compare_gcode
    for row in cases:
        ref=next((r for r in cases if r['mode']=='contour-0' and r['repeat']==row['repeat']),None)
        if ref and row.get('full_slice_completed') and ref.get('full_slice_completed'):
            try:
                comparison=compare_gcode(ref['gcode']['path'],row['gcode']['path'],
                    report_path=output_dir/(f"{row['mode']}-r{row['repeat']}-walls.jsonl"))
                row['wall_comparison']=comparison;row['wall_paths_validated']=comparison['wall_paths_validated']
            except Exception as exc:
                row['wall_comparison_error']=str(exc);row['wall_paths_validated']=False
    summary['aggregate']={}
    for mode in modes:
        runs=[r for r in cases if r['mode']==mode]
        summary['aggregate'][mode]={
            'successful_runs':sum(r['weave']['exit_code']==0 for r in runs),
            'median_weave_time_s':statistics.median(r['weave']['elapsed_s'] for r in runs),
            'max_observed_weave_rss_bytes':max(r['weave']['peak_process_tree_rss_bytes_observed'] for r in runs),
            'rss_measurement_complete':all(r['weave']['rss_measurement_complete'] for r in runs),
            'wall_comparisons_performed':sum('wall_comparison' in r for r in runs),
            'all_wall_comparisons_passed':all(r['wall_paths_validated'] for r in runs),
        }
    if not keep_repeats:
        for row in cases:
            if row['repeat']<=1 or row['weave']['exit_code']!=0:continue
            for key in ['artifact','gcode']:
                if key in row:
                    Path(row[key]['path']).unlink(missing_ok=True);row[key]['removed_redundant_repeat']=True
    (output_dir/'benchmark.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary


def render_comparison(source,candidate,output_prefix,*,step=.08,first_height=.16,zs=None):
    """Disk-indexed contours and streamed triangle silhouettes; no full mesh load."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from PIL import Image, ImageDraw
    from .contour_store import TriangleStore
    from .contour_weave import layer_regions
    output_prefix=Path(output_prefix);output_prefix.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='layerloom-visual-') as tmp:
        with TriangleStore(str(source),Path(tmp)/'source') as a, TriangleStore(str(candidate),Path(tmp)/'candidate') as b:
            lo=float(a.bounds[0,2]);hi=float(a.bounds[1,2])
            if zs is None:
                zs=[lo+first_height/2]
                for frac in [.15,.33,.5,.67,.85]:
                    k=max(0,int((frac*(hi-lo)-first_height)/step))
                    zs.append(lo+first_height+(k+.5)*step)
                count=1+max(0,int(math.ceil((hi-lo-first_height-step*.5)/step-1e-10)))
                zs.append(lo+first_height*.5 if count==1 else lo+first_height+(count-1.5)*step)
                zs=sorted({round(z,12) for z in zs if z<hi})
            audit=Path(candidate).with_suffix('.layers.jsonl')
            if audit.exists():
                import heapq
                worst=[];actual_first=actual_last=None
                with audit.open() as f:
                    for line in f:
                        row=json.loads(line);item=(float(row.get('max_boundary_distance_mm',0)),float(row['slice_z']))
                        actual_last=item[1]
                        if actual_first is None:actual_first=actual_last
                        heapq.heappush(worst,item)
                        if len(worst)>3:heapq.heappop(worst)
                zs=sorted({round(z,12) for z in list(zs)+[z for _,z in worst]+[actual_first,actual_last] if z is not None})
            fig,axes=plt.subplots(2,math.ceil(len(zs)/2),figsize=(15,8),squeeze=False)
            tokens={'c':'#0086d6','m':'#ff00e1','y':'#c9b900','k':'#000000','w':'#999999'}
            core=_report_for(Path(candidate)) or {};first_token=core.get('first_layer_token',core.get('first_layer_color'))
            for ax,z in zip(axes.flat,zs):
                phase=max(0,int(math.floor((z-lo)/step+1e-9)))
                reference=layer_regions(a,z,phase,first_layer_token=first_token if z<lo+first_height else None)
                current=layer_regions(b,z,phase)
                for region,style,alpha in [(reference,'-',.5),(current,'--',1)]:
                    for token,g in region.items():
                        polys=list(g.geoms) if hasattr(g,'geoms') else [g]
                        for poly in polys:
                            if poly.is_empty:continue
                            for ring in [poly.exterior,*poly.interiors]:
                                xy=np.asarray(ring.coords);ax.plot(xy[:,0],xy[:,1],style,color=tokens.get(token,'#555555'),alpha=alpha,lw=.7)
                ax.set_title(f'Z = {z:.4f} mm');ax.set_aspect('equal');ax.grid(alpha=.15)
            for ax in list(axes.flat)[len(zs):]:ax.axis('off')
            fig.suptitle('Solid = original contours; dashed = serialized candidate contours')
            fig.tight_layout();overlay=Path(str(output_prefix)+'-layers.png');fig.savefig(overlay,dpi=160);plt.close(fig)
            # Projection silhouettes union all streamed triangles into fixed-size
            # raster buffers. Depth sorting is unnecessary for silhouettes.
            views={'front':np.array([[1,0,0],[0,0,1]]),'side':np.array([[0,1,0],[0,0,1]]),
                   'top':np.array([[1,0,0],[0,1,0]]),'iso':np.array([[.7071,-.7071,0],[.4082,.4082,.8165]])}
            sheet=Image.new('RGB',(1200,4*430),'white');draw=ImageDraw.Draw(sheet)
            for row,(name,matrix) in enumerate(views.items()):
                bounds=np.array([np.minimum(a.bounds[0],b.bounds[0]),np.maximum(a.bounds[1],b.bounds[1])])
                corners=np.array([[x,y,z] for x in bounds[:,0] for y in bounds[:,1] for z in bounds[:,2]])@matrix.T
                mn,mx=corners.min(0),corners.max(0);scale=360/max(mx-mn)
                pictures=[]
                for store in [a,b]:
                    im=Image.new('L',(400,400),0);d=ImageDraw.Draw(im)
                    for part in store.parts:
                        for tri in store.iter_triangles(part.id):
                            xy=(tri@matrix.T-mn)*scale+20;xy[:,:,1]=400-xy[:,:,1]
                            for face in xy:d.polygon([tuple(p) for p in face],fill=255)
                    pictures.append(np.asarray(im))
                for col,pic in enumerate(pictures):sheet.paste(Image.fromarray(255-pic).convert('RGB'),(col*400,row*430+25))
                diff=np.full((400,400,3),255,dtype=np.uint8)
                diff[(pictures[0]>0)&(pictures[1]>0)]=[180,180,180]
                diff[(pictures[0]>0)&(pictures[1]==0)]=[230,60,40]
                diff[(pictures[0]==0)&(pictures[1]>0)]=[30,100,230]
                sheet.paste(Image.fromarray(diff),(800,row*430+25))
                draw.text((10,row*430+5),name+' - original / candidate / difference',fill='black')
            silhouette=Path(str(output_prefix)+'-silhouettes.png');sheet.save(silhouette)
    return {'layer_overlay':str(overlay),'silhouettes':str(silhouette),'visual_signoff':'pending human/model inspection','sample_z_mm':zs}


def slice_orca(input_path,output_path,profile_json,*,step=None,first_height=None,source_reference=None,thin_wall_fixture=False,executable='/Applications/Snapmaker Orca.app/Contents/MacOS/Snapmaker_Orca',timeout_s=3600):
    """Stage a contour 3MF with native Orca slots and a fixed U1 test profile.

    Geometry is copied in bounded chunks; only a build translation and native
    metadata are added. Printer custom G-code and filament settings are copied
    unchanged. This validation helper never contacts a printer.
    """
    import re
    import shutil
    import xml.etree.ElementTree as ET
    input_path=Path(input_path).resolve();output_path=Path(output_path).resolve()
    output_path.parent.mkdir(parents=True,exist_ok=True)
    staging_started=time.monotonic();runtime=runtime_metadata()
    profile=json.loads(Path(profile_json).read_text())
    with zipfile.ZipFile(input_path) as zin:
        if 'Metadata/layerloom_contour.json' in zin.namelist():
            manifest=json.loads(zin.read('Metadata/layerloom_contour.json'))
        else:
            if step is None or first_height is None or source_reference is None:
                raise ValueError('Legacy input requires --step, --first-layer-height and --source-reference for a fixed comparison')
            from .contour_store import TriangleStore
            with tempfile.TemporaryDirectory(prefix='orca-reference-') as td:
                with TriangleStore(source_reference,td) as store:bounds=store.bounds.tolist()
            manifest=dict(layer_height=step,first_layer_height=first_height,source_bounds=bounds)
        metadata=[];build=[];resource_ids=[];current_object=None
        with zin.open('3D/3dmodel.model') as raw:
            for event,element in etree.iterparse(raw,events=('start','end'),huge_tree=True,resolve_entities=False,no_network=True):
                name=etree.QName(element).localname
                if event=='start' and element.get('id') is not None:resource_ids.append(int(element.get('id')))
                if event=='start' and name=='object':
                    current_object=dict(element.attrib);metadata.append(current_object)
                if event=='start' and name=='mesh' and current_object is not None:current_object['_mesh']=True
                if event=='start' and name=='item':build.append(dict(element.attrib))
                if event=='end':
                    element.clear()
                    while element.getprevious() is not None:del element.getparent()[0]
        if not build:raise ValueError('Missing build items')
        existing_assembly=len(build)==1 and not next((m.get('_mesh') for m in metadata if m['id']==build[0]['objectid']),False)
        assembly=build[0]['objectid'] if existing_assembly else str(max(resource_ids)+1)
        slot_map={'c':1,'m':4,'y':3}
        config=ET.Element('config');obj=ET.SubElement(config,'object',id=assembly)
        ET.SubElement(obj,'metadata',key='name',value='LayerLoom wall validation')
        for part in metadata:
            if not part.get('_mesh'):continue
            match=re.search(r'__PAT_([a-z])__',part.get('name',''))
            token=match[1] if match else {'all_cyan':'c','all_magenta':'m','all_yellow':'y'}.get(part.get('name',''))
            if token not in slot_map:raise ValueError('U1 validation helper supports CMY parts only: '+str(part.get('name')))
            child=ET.SubElement(obj,'part',id=part['id'],subtype='normal_part')
            ET.SubElement(child,'metadata',key='name',value=part['name'])
            ET.SubElement(child,'metadata',key='extruder',value=str(slot_map[token]))
        profile.update(layer_height=str(manifest['layer_height']),
            initial_layer_print_height=str(manifest['first_layer_height']),first_layer_height=str(manifest['first_layer_height']),
            adaptive_layer_height='0',enable_support='0',support_enable='0',enable_prime_tower='0',
            seam_position='back',resolution='0.012',wall_generator='classic',wall_loops='3')
        if thin_wall_fixture:
            profile.update(elefant_foot_compensation='0',detect_thin_wall='1',initial_layer_line_width='0.42')
        bounds=np.asarray(manifest['source_bounds']);offset=[135-float(bounds[:,0].mean()),135-float(bounds[:,1].mean()),-float(bounds[0,2])]
        original_tf=[float(x) for x in build[0].get('transform','1 0 0 0 1 0 0 0 1 0 0 0').split()] if existing_assembly else [1,0,0,0,1,0,0,0,1,0,0,0]
        original_tf[9:12]=[x+y for x,y in zip(original_tf[9:12],offset)]
        transform=' '.join(f'{x:.9f}' for x in original_tf)
        assembly_xml=b''
        if not existing_assembly:
            assembly_element=ET.Element('object',id=assembly,type='model',name='LayerLoom benchmark assembly')
            components=ET.SubElement(assembly_element,'components')
            for item in build:
                attrs=dict(objectid=item['objectid'])
                if 'transform' in item:attrs['transform']=item['transform']
                ET.SubElement(components,'component',**attrs)
            assembly_xml=ET.tostring(assembly_element,encoding='utf-8')
        xml_build=f'<build><item objectid="{assembly}" transform="{transform}" printable="1"/></build>'.encode()
        metadata_xml=b'<metadata name="Application">BambuStudio-1.10.1.50</metadata><metadata name="BambuStudio:3mfVersion">1</metadata>'
        with tempfile.TemporaryDirectory(prefix='orca-contour-',dir=output_path.parent) as scratch:
            scratch=Path(scratch);staged=scratch/'validation.3mf';sliced=scratch/'sliced';sliced.mkdir()
            with zipfile.ZipFile(staged,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as zout:
                for info in zin.infolist():
                    if info.filename in ['Metadata/model_settings.config','Metadata/project_settings.config']:continue
                    with zin.open(info) as src,zout.open(info.filename,'w',force_zip64=True) as dst:
                        if info.filename=='3D/3dmodel.model':
                            first=True;carry=b''
                            while True:
                                block=src.read(65536)
                                if not block:break
                                carry+=block
                                if first and b'<resources>' in carry:
                                    carry=carry.replace(b'<resources>',metadata_xml+b'<resources>',1);first=False
                                if len(carry)>131072:dst.write(carry[:-65536]);carry=carry[-65536:]
                            if assembly_xml:carry=carry.replace(b'</resources>',assembly_xml+b'</resources>')
                            carry,n=re.subn(rb'<build(?:\s[^>]*)?>.*?</build>',lambda _:xml_build,carry,flags=re.S)
                            if n!=1:raise ValueError('Cannot locate final single build record')
                            dst.write(carry)
                        elif info.filename=='[Content_Types].xml':
                            text=src.read(1024*1024)
                            if b'Extension="config"' not in text:text=text.replace(b'</Types>',b'<Default Extension="config" ContentType="application/xml"/></Types>')
                            dst.write(text)
                        else:shutil.copyfileobj(src,dst,1024*1024)
                zout.writestr('Metadata/model_settings.config',ET.tostring(config,encoding='utf-8',xml_declaration=True))
                zout.writestr('Metadata/project_settings.config',json.dumps(profile))
            command=[executable,'--debug','4','--orient','0','--arrange','0','--slice','1','--outputdir',str(sliced),str(staged)]
            log=output_path.with_suffix('.orca.log')
            staging_elapsed=time.monotonic()-staging_started
            result=run_measured(command,log,timeout_s=timeout_s)
            result['staging_elapsed_s']=staging_elapsed
            result['runtime']=runtime
            result.update(input=artifact_metrics(input_path),profile=artifact_metrics(profile_json),
                placement_translation_mm=offset,slot_map=slot_map,
                overrides={k:profile.get(k) for k in ['layer_height','first_layer_height','enable_support','enable_prime_tower','seam_position','resolution','wall_generator','wall_loops','elefant_foot_compensation','detect_thin_wall','initial_layer_line_width']},
                printer_custom_gcode_changed=False,thin_wall_fixture=thin_wall_fixture)
            files=list(sliced.glob('*.gcode'))
            if result['exit_code']!=0 or len(files)!=1:
                diagnostics={}
                for diagnostic in sliced.glob('*.json'):
                    if diagnostic.stat().st_size<10*1024*1024:
                        try:diagnostics[diagnostic.name]=json.loads(diagnostic.read_text())
                        except (ValueError,UnicodeError):pass
                failure_stage=output_path.with_suffix('.failed-stage.3mf');shutil.copy2(staged,failure_stage)
                result.update(full_slice_completed=False,files_found=[p.name for p in files],slicer_diagnostics=diagnostics,failure_stage=str(failure_stage))
                output_path.with_suffix('.slice.json').write_text(json.dumps(result,indent=2)+'\n')
                raise RuntimeError(f'Orca full slicing failed; see {log}')
            shutil.copy2(files[0],output_path)
            output_path.with_suffix('.failed-stage.3mf').unlink(missing_ok=True)
    result.update(full_slice_completed=True,gcode=artifact_metrics(output_path))
    with output_path.open(errors='replace') as generated:
        for _,line in zip(range(100),generated):
            if line.startswith('; generated by '):
                result['slicer_generator']=line.strip('; \n');break
    output_path.with_suffix('.slice.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def summarize_evidence(benchmark_paths, slice_paths, wall_paths, output_dir, *, visual_paths=(), visual_review_note=None, measurement_note=None, contour_audit_paths=()):
    """Write a compact evidence bundle, preserving failed and untested gates."""
    output_dir=Path(output_dir);output_dir.mkdir(parents=True,exist_ok=True)
    evidence=dict(schema_version=1,summary_runtime=runtime_metadata(),benchmarks=[],full_slices=[],wall_comparisons=[],contour_audits=[],visuals=[],
                  visual_review_note=visual_review_note,measurement_note=measurement_note,
                  scope="Local file generation and slicing only; no physical print, upload, or printer action.")
    def source_record(path):
        path=Path(path);record=artifact_metrics(path)
        return record,json.loads(path.read_text())
    for path in benchmark_paths:
        record,data=source_record(path)
        item=dict(evidence_file=record,runtime=data.get('runtime'),interruption_note=data.get('interruption_note'),input=data['input'],step_mm=data['step_mm'],
                  first_layer_height_mm=data['first_layer_height_mm'],repeats=data['repeats'],modes={})
        for mode in dict.fromkeys(row['mode'] for row in data['runs']):
            runs=[r for r in data['runs'] if r['mode']==mode]
            artifacts=[r['artifact'] for r in runs if 'artifact' in r]
            cores=[r['core_report'] for r in runs if r.get('core_report')]
            aggregate=data.get('aggregate',{}).get(mode) or dict(
                median_weave_time_s=statistics.median(r['weave']['elapsed_s'] for r in runs),
                max_observed_weave_rss_bytes=max(r['weave']['peak_process_tree_rss_bytes_observed'] for r in runs),
                rss_measurement_complete=all(r['weave']['rss_measurement_complete'] for r in runs),
                derived_from_completed_runs=True)
            item['modes'][mode]=dict(aggregate=aggregate,
                successful_runs=sum(r['weave']['exit_code']==0 for r in runs),
                triangles=[r['triangles'] for r in artifacts],compressed_bytes=[r['size_bytes'] for r in artifacts],
                uncompressed_geometry_bytes=[r.get('uncompressed_geometry_bytes') for r in artifacts],
                output_sha256=[r['sha256'] for r in artifacts],
                contour_validated=bool(cores) and all(r.get('contour_validated',False) for r in cores),
                max_contour_distance_mm=max((r.get('max_contour_distance_mm',0) for r in cores),default=None),
                peak_scratch_bytes=max((r.get('peak_scratch_bytes',0) for r in cores),default=None),
                stage_timings=[{k:v for k,v in r.items() if 'seconds' in k or k=='stage_times'} for r in cores],
                logs=[r['weave']['log'] for r in runs])
        evidence['benchmarks'].append(item)
    for path in slice_paths:
        record,data=source_record(path);evidence['full_slices'].append(dict(evidence_file=record,result=data))
    for path in wall_paths:
        record,data=source_record(path);evidence['wall_comparisons'].append(dict(evidence_file=record,result=data))
    for path in contour_audit_paths:
        record,data=source_record(path);evidence['contour_audits'].append(dict(evidence_file=record,result=data))
    for path in visual_paths:
        evidence['visuals'].append(artifact_metrics(path))
    slices=evidence['full_slices'];walls=evidence['wall_comparisons']
    audits=evidence['contour_audits']
    evidence['gates']=dict(
        serialized_contours='untested' if not audits else ('passed' if all(x['result'].get('serialized_contours_validated',False) for x in audits) else 'failed'),
        full_slicing='untested' if not slices else ('passed' if all(x['result'].get('full_slice_completed',False) for x in slices) else 'failed'),
        wall_preservation='untested' if not walls else ('passed' if all(x['result'].get('wall_paths_validated',False) for x in walls) else 'failed'),
        visual_inspection='recorded' if visual_review_note else 'pending',physical_print='untested')
    (output_dir/'report.json').write_text(json.dumps(evidence,indent=2)+'\n')
    lines=['# Layer-contour benchmark evidence','',evidence['scope'],'',
           '| Case / mode | Runs | Triangles | 3MF MiB | Median weave s | Peak observed RSS MiB | Builder contour checks |',
           '| --- | ---: | ---: | ---: | ---: | ---: | --- |']
    for case in evidence['benchmarks']:
        label=Path(case['evidence_file']['path']).parent.name
        for mode,data in case['modes'].items():
            ag=data['aggregate'];tri=data['triangles'][0] if data['triangles'] else 0
            size=data['compressed_bytes'][0]/1024**2 if data['compressed_bytes'] else 0
            timing=ag.get('median_weave_time_s',0);rss=ag.get('max_observed_weave_rss_bytes',0)/1024**2
            contour='passed' if data['contour_validated'] else 'not certified'
            lines.append(f"| {label} / {mode} | {data['successful_runs']}/{case['repeats']} | {tri:,} | {size:.2f} | {timing:.2f} | {rss:.1f} | {contour} |")
    lines+=['','RSS is a 50 ms sampled process-tree peak. Geometry counts describe stored mesh resources. Full slicing and wall comparison are separate gates.',measurement_note or '', '',
            '## Recorded gates','']
    lines += [f"- {k.replace('_',' ')}: **{v}**" for k,v in evidence['gates'].items()]
    lines += ['','## Independent serialized-contour audit','']
    for item in audits:
        row=item['result'];lines.append(f"- {Path(item['evidence_file']['path']).name}: {'passed' if row.get('serialized_contours_validated') else 'FAILED'}; {row.get('failed_layers',0)}/{row.get('layers',0)} layers failed. Builder checks alone do not certify serialized output.")
    lines += ['','## Full slicing','']
    for item in slices:
        row=item['result'];name=Path(item['evidence_file']['path']).name
        lines.append(f"- {name}: {'completed' if row.get('full_slice_completed') else 'FAILED'}, {row.get('elapsed_s',0):.2f} s; profile/input hashes, placement, overrides and command recorded in report.json.")
    lines += ['','## Actual wall comparison','']
    for item in walls:
        row=item['result'];name=Path(item['evidence_file']['path']).name
        lines.append(f"- {name}: {'passed' if row.get('wall_paths_validated') else 'FAILED'}; {row.get('failed_layers',0)}/{row.get('layers',0)} layers failed; maximum strict displacement upper bound {row.get('max_displacement_upper_mm',0):.6f} mm; combined-feature per-filament upper bound {row.get('max_combined_feature_displacement_upper_mm',0):.6f} mm.")
    lines += ['', '## Visual review','',visual_review_note or 'Pending inspection. Generated images alone do not constitute sign-off.','',
              'This bundle includes failures and untested gates deliberately. Contour validation and resource savings do not certify downstream wall placement. Source evidence, profile hashes and output hashes are retained in [report.json](report.json).','']
    (output_dir/'report.md').write_text('\n'.join(lines))
    return dict(json=str(output_dir/'report.json'),markdown=str(output_dir/'report.md'),gates=evidence['gates'])


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    run=sub.add_parser('run');run.add_argument('input');run.add_argument('--output-dir',required=True)
    run.add_argument('--repeats',type=int,default=3);run.add_argument('--step',type=float,default=.08)
    run.add_argument('--first-layer-height',type=float,default=.16);run.add_argument('--memory-budget-mib',type=int,default=1024)
    run.add_argument('--modes',nargs='+',default=['legacy','contour-0','contour-0.01','contour-0.02'])
    run.add_argument('--timeout-s',type=float,default=3600);run.add_argument('--slicer-command-json',help='JSON argv array with {input}, {output}, {output_dir}; must perform full slicing and write G-code')
    run.add_argument('--visuals',action='store_true');run.add_argument('--keep-repeats',action='store_true')
    visual=sub.add_parser('visual');visual.add_argument('source');visual.add_argument('candidate');visual.add_argument('--output-prefix',required=True)
    visual.add_argument('--step',type=float,default=.08);visual.add_argument('--first-layer-height',type=float,default=.16)
    sl=sub.add_parser('slice-orca');sl.add_argument('input');sl.add_argument('--output',required=True);sl.add_argument('--profile-json',required=True)
    sl.add_argument('--executable',default='/Applications/Snapmaker Orca.app/Contents/MacOS/Snapmaker_Orca');sl.add_argument('--timeout-s',type=float,default=3600);sl.add_argument('--step',type=float);sl.add_argument('--first-layer-height',type=float);sl.add_argument('--source-reference');sl.add_argument('--thin-wall-fixture',action='store_true',help='Explicit tilted-fixture diagnostic: thin walls on, first width .42, elephant foot zero')
    summary=sub.add_parser('summarize');summary.add_argument('--benchmark-json',nargs='*',default=[]);summary.add_argument('--slice-json',nargs='*',default=[]);summary.add_argument('--wall-json',nargs='*',default=[]);summary.add_argument('--contour-audit-json',nargs='*',default=[]);summary.add_argument('--visual',nargs='*',default=[]);summary.add_argument('--visual-review-note');summary.add_argument('--measurement-note');summary.add_argument('--output-dir',required=True)
    a=p.parse_args(argv)
    if a.action=='summarize':
        result=summarize_evidence(a.benchmark_json,a.slice_json,a.wall_json,a.output_dir,visual_paths=a.visual,visual_review_note=a.visual_review_note,measurement_note=a.measurement_note,contour_audit_paths=a.contour_audit_json);print(json.dumps(result,indent=2));return 0
    if a.action=='slice-orca':
        result=slice_orca(a.input,a.output,a.profile_json,executable=a.executable,timeout_s=a.timeout_s,step=a.step,first_height=a.first_layer_height,source_reference=a.source_reference,thin_wall_fixture=a.thin_wall_fixture);print(json.dumps(result,indent=2));return 0
    if a.action=='visual':result=render_comparison(a.source,a.candidate,a.output_prefix,step=a.step,first_height=a.first_layer_height)
    else:
        template=json.loads(Path(a.slicer_command_json).read_text()) if a.slicer_command_json else None
        if template is not None and (not isinstance(template,list) or not all(isinstance(x,str) for x in template)):p.error('Slicer command must be an argv array')
        result=benchmark(a.input,a.output_dir,repeats=a.repeats,step=a.step,first_height=a.first_layer_height,
                         modes=a.modes,memory_budget_mib=a.memory_budget_mib,timeout_s=a.timeout_s,
                         slicer_command=template,visuals=a.visuals,keep_repeats=a.keep_repeats)
    print(json.dumps(result.get('aggregate',result),indent=2))
    if a.action=='visual':return 0
    ok=all(r['weave']['exit_code']==0 for r in result['runs'])
    if template is not None:
        ok &= all(r.get('full_slice_completed',False) for r in result['runs'])
        ok &= all(r['wall_paths_validated'] for r in result['runs'] if 'wall_comparison' in r or 'wall_comparison_error' in r)
    return 0 if ok else 1

if __name__=='__main__':raise SystemExit(main())
