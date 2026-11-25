"""Small curved red/white/blue weave coupons and measured palette import.

Uses existing LayerLoom slots m=red, w=white, c=blue for this job only.
Physical filament names and assignments are explicit in the exported 3MF.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
import trimesh
import manifold3d as manifold

from layerloom.calibrated_palette import (lab_for_hex, normalize_hex, rgb01_to_hex,
                                         delta_e00, nearest_calibrated_palette_entry)
from layerloom.export import LeafMeshSpec, write_basic_3mf, write_orca_multipart_3mf
from layerloom.ingest import _extract_meshes_from_3mf
from layerloom.weave import weave_pipeline

SLOTS = {'m': 'red', 'w': 'white', 'c': 'blue'}
HEX = {'m': '#ff0000', 'w': '#ffffff', 'c': '#0000ff'}


def balanced_recipe(token: str, count: int) -> str:
    if count == 0:
        return 'w'
    if count == 8:
        return token
    return ''.join(token if ((i+1)*count)//8 > (i*count)//8 else 'w' for i in range(8))


def recipes():
    result = [(balanced_recipe(t,n), f'{SLOTS[t]}_{n}_of_8') for t in ('m','c') for n in range(9)]
    result += [('mwmwmwmw','red_50_repeat'),('mmmmwwww','red_50_grouped'),
               ('cwcwcwcw','blue_50_repeat'),('ccccwwww','blue_50_grouped'),
               ('w','white_repeat'),('wmwmwmwm','red_50_phase_shift')]
    return result


def nominal_hex(token):
    colors=np.array([[int(HEX[t][i:i+2],16)/255 for i in (1,3,5)] for t in token])
    return rgb01_to_hex(colors.mean(0))


def build(output: Path, step: float = 0.08):
    output.mkdir(parents=True,exist_ok=True)
    if step <= 0:
        raise ValueError('Layer height must be positive')
    base_height=8*step
    # A curved dot exposes many layers to the viewer; flat tiles expose mainly
    # their top filament. Hemisphere construction uses the existing solid engine.
    cap=manifold.Manifold.sphere(5.12,48).trim_by_plane((0,0,1),0).to_mesh()
    dome=trimesh.Trimesh(cap.vert_properties[:,:3],cap.tri_verts,process=True)
    base=trimesh.creation.box(extents=[112,40,base_height])
    base.apply_translation([56,20,base_height/2])
    parts=[('calibration_base__PAT_w__',base)]
    rows=[]
    for i,(token,description) in enumerate(recipes()):
        row,col=divmod(i,9)
        x,y=8+12*col,32-12*row
        d=dome.copy(); d.apply_translation([x,y,base_height])
        parts.append((f'dot_{i+1:02d}__PAT_{token}__',d))
        rows.append({'sample_id':f'{i+1:02d}','row':row+1,'column':col+1,
                     'token':token,'description':description,'target_hex':nominal_hex(token),
                     'measured_hex':'','layer_height_mm':step,'x_mm':x,'y_mm':y})
    write_basic_3mf(parts,str(output/'RWB_dots_labeled.3mf'),title='RWB calibration dot recipes')
    weave_pipeline(str(output/'RWB_dots_labeled.3mf'),step=step,
                   output_path=str(output/'RWB_dots_woven_internal.3mf'),export_stls=False,
                   first_layer_color=None,verbose=False)
    specs=[]
    for name,m in _extract_meshes_from_3mf(str(output/'RWB_dots_woven_internal.3mf')):
        token={'all_magenta':'m','all_white':'w','all_cyan':'c'}[name]
        specs.append(LeafMeshSpec('all_'+SLOTS[token],m,{'stack_token':token}))
    write_orca_multipart_3mf(specs,str(output/'RWB_dots_PRINT_008mm.3mf'),
                            title='RWB calibration dots - 0.08mm',
                            extruder_by_token={'m':1,'w':2,'c':3})
    with (output/'measurements.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    entries={}
    for r in rows:
        entries.setdefault(r['token'],{'token':r['token'],'hex':r['target_hex'],
                                     'nominal_hex':r['target_hex'],'calibrated':False})
    document={'name':'RWB electrostatic provisional 0.08mm','slot_filaments':SLOTS,
              'layer_height_mm':step,'entries':list(entries.values()),
              'warning':'Nominal screen targets only. Import measurements before calibrated matching.'}
    (output/'RWB_provisional_palette.json').write_text(json.dumps(document,indent=2))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle
    fig,ax=plt.subplots(figsize=(11.2,4.8))
    for r in rows:
        ax.add_patch(Circle((r['x_mm'],r['y_mm']),5.12,facecolor=r['target_hex'],edgecolor='0.3'))
        ax.text(r['x_mm'],r['y_mm']-6,str(r['sample_id']),ha='center',va='top',fontsize=9)
    ax.set(xlim=(0,112),ylim=(0,43),aspect='equal')
    ax.axis('off');ax.set_title('RWB test dots: row 1 red/white; row 2 blue/white; row 3 repeats/order tests')
    fig.savefig(output/'dot_map.png',dpi=180,bbox_inches='tight');plt.close(fig)
    return rows


def import_measurements(csv_path: Path, output: Path):
    with csv_path.open(newline='') as f:
        rows=list(csv.DictReader(f))
    groups={}
    for row in rows:
        value=row.get('measured_hex','').strip()
        if not value:
            continue
        hx=normalize_hex(value)
        if hx is None:
            raise ValueError(f"Invalid measured_hex for sample {row['sample_id']}")
        token=row['token']
        if not token or set(token)-set(SLOTS):
            raise ValueError(f'Invalid recipe {token}')
        groups.setdefault(token,[]).append((row,hx,lab_for_hex(hx)))
    if not groups:
        raise ValueError('No measured colors. Fill measured_hex with measured sRGB hex values first.')
    entries=[]
    errors=[]
    for token,samples in groups.items():
        lab=np.mean([s[2] for s in samples],axis=0)
        entries.append({'token':token,'hex':samples[0][1],'nominal_hex':samples[0][0]['target_hex'],
                        'calibrated':True,'measured_L':float(lab[0]),'measured_a':float(lab[1]),
                        'measured_b':float(lab[2]),'sample_count':len(samples),
                        'sample_ids':[s[0]['sample_id'] for s in samples]})
        for row,hx,measured_lab in samples:
            errors.append({'sample_id':row['sample_id'],'token':token,
                           'target_hex':row['target_hex'],'measured_hex':hx,
                           'delta_E_2000':float(delta_e00(lab_for_hex(row['target_hex']),measured_lab)[0])})
    document={'name':'Measured RWB electrostatic 0.08mm','slot_filaments':SLOTS,
              'calibration':{'source_csv':str(csv_path),'layer_height_mm':0.08,
                             'unmeasured_recipes':'excluded','sequence_order':'preserved'},'entries':entries}
    output.write_text(json.dumps(document,indent=2))
    with output.with_suffix('.errors.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(errors[0]));writer.writeheader();writer.writerows(errors)
    mapped=[]
    for value in np.linspace(-10,10,81):
        strength=abs(float(value))/10
        rgb=[1,1-strength,1-strength] if value<0 else [1-strength,1-strength,1]
        target=rgb01_to_hex(rgb)
        allowed={'m','w'} if value<0 else {'c','w'} if value>0 else {'w'}
        candidates=[e for e in entries if set(e['token'])<=allowed]
        if not candidates:
            continue
        match=nearest_calibrated_palette_entry(candidates,target)
        mapped.append({'potential':float(value),'target_hex':target,'token':match['token'],
                       'measured_hex':match['hex']})
    with output.with_suffix('.potential_lookup.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['potential','target_hex','token','measured_hex']);writer.writeheader();writer.writerows(mapped)
    return document


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='action',required=True)
    b=sub.add_parser('build');b.add_argument('output',type=Path)
    c=sub.add_parser('measure');c.add_argument('csv',type=Path);c.add_argument('output',type=Path)
    a=p.parse_args()
    if a.action=='build':build(a.output)
    else:import_measurements(a.csv,a.output)


if __name__=='__main__':main()
