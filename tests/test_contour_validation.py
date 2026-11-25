import hashlib
import json
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET

import pytest
import trimesh
from layerloom.export import write_basic_3mf
from layerloom.contour_options import ContourOptions
from layerloom.contour_weave import weave_contours
from layerloom.contour_validation import audit_contours


def generate(tmp_path,noweave=False):
    source=tmp_path/'source.3mf';candidate=tmp_path/'woven.3mf'
    box=trimesh.creation.box(extents=[2,3,.45]);box.apply_translation([0,0,.225])
    parts=[('box__PAT_ccy__',box)]
    if noweave:
        detail=trimesh.creation.box(extents=[.4,.6,.3]);detail.apply_translation([5,2,.15])
        parts.append(('detail__NOWEAVE__',detail))
    write_basic_3mf(parts,str(source))
    options=ContourOptions(first_layer_height=.16,xy_tolerance_mm=.02,reserve_disk_gib=0,scratch_dir=str(tmp_path))
    weave_contours(source,candidate,step=.08,first_layer_color='m',options=options,verbose=False)
    return source,candidate


def test_independent_audit_uses_all_actual_layers(tmp_path):
    a,b=generate(tmp_path,noweave=True)
    result=audit_contours(a,b)
    assert result['passed'] and result['layers']==5
    assert not result['toolpaths_validated']
    assert result['passthrough']['passed']
    rows=[json.loads(x) for x in Path(result['layers_report']).read_text().splitlines()]
    assert [x['slice_z'] for x in rows]==pytest.approx([.08,.2,.28,.36,.44])
    assert all(r['passed'] for r in rows)
    assert result['source_hash_matches_candidate_reference']


def shift_candidate(candidate,dx,only_now=False):
    ns={'m':'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'}
    with zipfile.ZipFile(candidate) as z:members={i.filename:z.read(i.filename) for i in z.infolist()}
    root=ET.fromstring(members['3D/3dmodel.model'])
    for obj in root.findall('m:resources/m:object',ns):
        if only_now and '__NOWEAVE__' not in obj.get('name',''):continue
        for vertex in obj.findall('m:mesh/m:vertices/m:vertex',ns):
            vertex.set('x',str(float(vertex.get('x'))+dx))
    members['3D/3dmodel.model']=ET.tostring(root)
    with zipfile.ZipFile(candidate,'w',zipfile.ZIP_DEFLATED) as z:
        for name,value in members.items():z.writestr(name,value)
    report_path=candidate.with_suffix('.report.json');report=json.loads(report_path.read_text())
    report['output_sha256']=hashlib.sha256(candidate.read_bytes()).hexdigest()
    report_path.write_text(json.dumps(report))


def test_readback_audit_detects_geometry_change_despite_builder_claim(tmp_path):
    a,b=generate(tmp_path)
    shift_candidate(b,.1)
    result=audit_contours(a,b)
    assert result['candidate_hash_matches_report']
    assert not result['passed']
    assert result['failed_layers']==5
    assert result['observed_max_boundary_hausdorff_mm']>.09


def test_passthrough_change_is_not_silently_excluded(tmp_path):
    a,b=generate(tmp_path,noweave=True)
    shift_candidate(b,.01,only_now=True)
    result=audit_contours(a,b)
    assert result['failed_layers']==0
    assert not result['passthrough']['passed']
    assert not result['passed']


def test_passthrough_material_change_is_not_silently_excluded(tmp_path):
    a,b=generate(tmp_path,noweave=True)
    ns={'m':'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'}
    with zipfile.ZipFile(b) as z:members={i.filename:z.read(i.filename) for i in z.infolist()}
    root=ET.fromstring(members['3D/3dmodel.model'])
    for obj in root.findall('m:resources/m:object',ns):
        if '__NOWEAVE__' not in obj.get('name',''):continue
        meta=obj.find('m:metadata[@name="layerloom:source_hex"]',ns)
        if meta is None:meta=ET.SubElement(obj,'{'+ns['m']+'}metadata',{'name':'layerloom:source_hex'})
        meta.text='#124578FF'
    members['3D/3dmodel.model']=ET.tostring(root)
    with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
        for name,value in members.items():z.writestr(name,value)
    path=b.with_suffix('.report.json');report=json.loads(path.read_text())
    report['output_sha256']=hashlib.sha256(b.read_bytes()).hexdigest();path.write_text(json.dumps(report))
    result=audit_contours(a,b)
    assert result['candidate_hash_matches_report'] and result['failed_layers']==0
    detail=result['passthrough']['parts'][0]
    assert detail['geometry_matches'] and not detail['material_provenance_matches']
    assert not result['passed']


def test_audit_rejects_mismatched_grid_policy(tmp_path):
    a,b=generate(tmp_path)
    path=b.with_suffix('.report.json');r=json.loads(path.read_text());r['numerical_export_grid_mm']=.1;path.write_text(json.dumps(r))
    with pytest.raises(ValueError,match='numerical policy'):
        audit_contours(a,b)


def test_direct_singleton_geometry_is_included_in_contour_audit(tmp_path):
    source=tmp_path/'source.3mf';candidate=tmp_path/'woven.3mf'
    box=trimesh.creation.box(extents=[2,3,.45]);box.apply_translation([0,0,.225])
    write_basic_3mf([('box__PAT_c__',box)],str(source))
    options=ContourOptions(first_layer_height=.16,xy_tolerance_mm=0,reserve_disk_gib=0)
    weave_contours(source,candidate,step=.08,first_layer_color='c',options=options,verbose=False)
    report=json.loads(candidate.with_suffix('.report.json').read_text())
    assert report['direct_copy_parts']==1
    result=audit_contours(source,candidate)
    assert result['passed']
    rows=[json.loads(x) for x in Path(result['layers_report']).read_text().splitlines()]
    assert all(r['filaments']['c']['reference_area_mm2']==6 for r in rows)
    shift_candidate(candidate,.1)
    assert audit_contours(source,candidate)['failed_layers']==5


def test_topology_failure_is_separate_from_boundary_distance():
    from shapely.geometry import box
    from shapely.ops import unary_union
    from layerloom.contour_validation import _geometry_comparison
    reference=unary_union([box(0,0,1,.01),box(1.01,0,2.01,.01)])
    candidate=box(0,0,2.01,.01)
    result=_geometry_comparison(reference,candidate,.02)
    assert result['boundary_within_certified_limit']
    assert not result['topology_matches']
    assert not result['passed']
