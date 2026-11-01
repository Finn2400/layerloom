from pathlib import Path
import numpy as np
import pytest
import trimesh
import manifold3d as mf
from layerloom.export import write_basic_3mf
from layerloom.region_weave import prepare_document, source_status, weave_regions, validate_written_output
from layerloom.paint_io import load_paint_document


def colored_cube(path, *, size=4, offset=(0,0,0), recipe='c'):
    cube=trimesh.creation.box(extents=[size]*3)
    cube.apply_translation(np.asarray(offset)+[0,0,size/2])
    cyan=cube.face_normals[:,0]>.9
    write_basic_3mf([(f'cyan__PAT_{recipe}__',cube.submesh([np.flatnonzero(cyan)],append=True,repair=False)),
                     ('magenta__PAT_m__',cube.submesh([np.flatnonzero(~cyan)],append=True,repair=False))],str(path))
    return cube


def test_closed_cube_partition_fills_original_without_overlaps(tmp_path):
    src=tmp_path/'cube.3mf'; original=colored_cube(src); before=src.read_bytes()
    assert source_status(src)==(True,None)
    doc,overrides=prepare_document(src)
    assert len(doc.meshes)==1 and set(overrides.values())=={'c','m'}
    out=tmp_path/'woven.3mf'
    report=weave_regions(src,out,layer_height=.5,pitch=.5)
    assert report['maximum_internal_layer_gap_mm2'] < 1e-8
    assert report['maximum_layer_overlap_mm2'] < 1e-8
    assert report['saved_geometry']['overlap_mm3'] < 1e-6
    assert abs(report['saved_geometry']['volume_mm3']-original.volume)<1e-4
    solids=[]
    for part in load_paint_document(out).meshes:
        mesh=trimesh.Trimesh(part.vertices,part.faces,process=False);mesh.merge_vertices()
        solids.append(mf.Manifold(mf.Mesh64(mesh.vertices,mesh.faces.astype(np.uint64))))
    union=mf.Manifold.batch_boolean(solids,mf.OpType.Add)
    # Export centers on the 256 mm slicer plate and grounds Z.
    original.apply_translation([128, 128, 0])
    reference=mf.Manifold(mf.Mesh64(original.vertices,original.faces.astype(np.uint64)))
    assert float((reference-union).volume())<1e-5
    assert float((union-reference).volume())<1e-5
    assert src.read_bytes()==before


def test_open_source_is_rejected_and_existing_output_untouched(tmp_path):
    src=tmp_path/'open.3mf';m=trimesh.creation.box();m.update_faces(np.arange(11))
    write_basic_3mf([('part__PAT_c__',m)],str(src))
    assert source_status(src)[0] is False
    out=tmp_path/'existing.3mf';out.write_bytes(b'keep me')
    with pytest.raises(ValueError,match='intact source is open'):
        weave_regions(src,out)
    assert out.read_bytes()==b'keep me'


def test_first_layer_override_preserves_volume(tmp_path):
    src=tmp_path/'cube.3mf';colored_cube(src)
    report=weave_regions(src,tmp_path/'out.3mf',layer_height=.5,pitch=.5,first_layer_color='y')
    assert report['resolved_first_layer_color']=='y'
    assert abs(report['saved_geometry']['volume_mm3']-64)<1e-4
    yellow=next(m for m in load_paint_document(tmp_path/'out.3mf').meshes if 'yellow' in m.name)
    assert np.ptp(yellow.vertices[:,2])==pytest.approx(.5,abs=1e-6)


def test_missing_assignment_is_not_silently_recolored(tmp_path):
    src=tmp_path/'cube.3mf';write_basic_3mf([('unassigned',trimesh.creation.box())],str(src))
    with pytest.raises(ValueError,match='Assign a filament recipe'):
        prepare_document(src)


def test_cavity_and_disconnected_thin_solid_are_retained(tmp_path):
    outer=trimesh.creation.box(extents=[4,4,4]); outer.apply_translation([0,0,2])
    cavity=trimesh.creation.box(extents=[2,2,2]); cavity.apply_translation([0,0,2]); cavity.invert()
    thin=trimesh.creation.box(extents=[1,1,.1]); thin.apply_translation([4,0,.15])
    src=tmp_path/'cavity.3mf'
    write_basic_3mf([('shell__PAT_c__',outer),('cavity__PAT_m__',cavity),('thin__PAT_y__',thin)],str(src))
    assert source_status(src)==(True,None)
    report=weave_regions(src,tmp_path/'out.3mf',layer_height=.5,pitch=.5)
    assert report['saved_geometry']['volume_mm3']==pytest.approx(56.1,abs=1e-4)
    yellow=next(m for m in load_paint_document(tmp_path/'out.3mf').meshes if 'yellow' in m.name)
    assert np.ptp(yellow.vertices[:,2])==pytest.approx(.1,abs=1e-6)


def test_overlapping_source_solids_are_rejected(tmp_path):
    a=trimesh.creation.box(); b=a.copy(); b.apply_translation([.25,.25,.25])
    src=tmp_path/'overlap.3mf'
    write_basic_3mf([('a__PAT_c__',a),('b__PAT_m__',b)],str(src))
    supported,reason=source_status(src)
    assert not supported and 'overlap' in reason.lower()


def test_failed_saved_geometry_validation_does_not_replace_output(tmp_path,monkeypatch):
    from layerloom import region_weave
    src=tmp_path/'cube.3mf';colored_cube(src)
    out=tmp_path/'keep.3mf';out.write_bytes(b'previous output')
    def reject(path):
        raise ValueError('encoded geometry rejected')
    monkeypatch.setattr(region_weave,'validate_written_output',reject)
    with pytest.raises(ValueError,match='encoded geometry rejected'):
        weave_regions(src,out,layer_height=.5)
    assert out.read_bytes()==b'previous output'


def test_first_layer_auto_handles_a_sub_layer_component(tmp_path):
    thin=trimesh.creation.box(extents=[1,1,.1])
    src=tmp_path/'thin.3mf';write_basic_3mf([('thin__PAT_c__',thin)],str(src))
    report=weave_regions(src,tmp_path/'out.3mf',layer_height=.5,first_layer_color='auto')
    assert report['resolved_first_layer_color']=='c'
    assert report['saved_geometry']['volume_mm3']==pytest.approx(.1,abs=1e-6)


def test_arrange_recipe_order_is_preserved_after_palette_deduplication(tmp_path):
    from layerloom.paint_export import assign_palette
    src=tmp_path/'cube.3mf';colored_cube(src,recipe='cyc')
    document,overrides=prepare_document(src)
    assignments=assign_palette(document,'Normal',recipe_overrides=overrides)
    assert {entry['token'] for entry in assignments.values()}=={'cyc','m'}
    report=weave_regions(src,tmp_path/'out.3mf',layer_height=.5)
    assert {row['recipe'] for row in report['region_assignments']}=={'cyc','m'}
    assert report['saved_geometry']['volume_mm3']==pytest.approx(64,abs=1e-4)
