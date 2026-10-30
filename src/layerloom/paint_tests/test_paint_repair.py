import json

import numpy as np
import pytest
import trimesh

from layerloom.paint_model import PaintDocument, PaintMesh
from layerloom.paint_repair import repair_paint_document
from layerloom.surface_field_weave import mesh_defects


def painted(mesh, current=(255,0,0,255), source=(0,255,0,255), key='test'):
    return PaintMesh(key,key,mesh.vertices,mesh.faces,
                     np.tile(current,(len(mesh.faces),1)),np.tile(source,(len(mesh.faces),1)))


def test_overlap_union_retains_disconnected_small_solid_and_paint():
    first=trimesh.creation.box(extents=(2,2,2))
    second=first.copy();second.apply_translation((1,0,0))
    tiny=trimesh.creation.box(extents=(.001,.001,.001));tiny.apply_translation((4,0,0))
    combined=trimesh.util.concatenate([first,second,tiny])
    item=painted(combined)
    item.face_rgba[12:24]=[0,0,255,255]
    before=item.copy()
    result,audit=repair_paint_document(PaintDocument([item]))
    repaired=result.meshes[0];mesh=trimesh.Trimesh(repaired.vertices,repaired.faces,process=False)
    assert np.isclose(mesh.volume,12+1e-9,atol=1e-5)
    assert mesh.body_count==2
    assert np.array_equal(item.vertices,before.vertices)
    assert np.array_equal(item.face_rgba,before.face_rgba)
    assert set(map(tuple,np.unique(repaired.face_rgba,axis=0)))=={(255,0,0,255),(0,0,255,255)}
    assert np.all(repaired.source_rgba==[0,255,0,255])
    assert not any(mesh_defects(mesh).values())
    assert audit['meshes'][0]['positive_solid_components_retained']==3
    json.dumps(audit)


def test_small_hole_cap_preserves_original_and_current_paint():
    cube=trimesh.creation.box(extents=(.1,.1,.1))
    cube.update_faces(np.arange(len(cube.faces))!=0)
    result,audit=repair_paint_document(PaintDocument([painted(cube)]))
    item=result.meshes[0]
    assert len(audit['meshes'][0]['hole_caps'])==1
    assert np.all(item.face_rgba==[255,0,0,255])
    assert np.all(item.source_rgba==[0,255,0,255])
    assert trimesh.Trimesh(item.vertices,item.faces,process=False).is_volume


def test_large_hole_fails_without_dropping_component():
    cube=trimesh.creation.box()
    cube.update_faces(np.arange(len(cube.faces))!=0)
    with pytest.raises(ValueError,match='hole cap span'):
        repair_paint_document(PaintDocument([painted(cube)]))


def test_only_explicit_opposite_zero_thickness_pair_is_removed():
    cube=trimesh.creation.box()
    v=np.r_[cube.vertices,[[2,0,0],[2,1,0],[2,0,1]]]
    f=np.r_[cube.faces,[[8,9,10],[10,9,8]]]
    result,audit=repair_paint_document(PaintDocument([painted(trimesh.Trimesh(v,f,process=False))]))
    rows=audit['meshes'][0]['source_components']
    assert sum(r['action']=='remove_opposite_zero_thickness_duplicate_pair' for r in rows)==1
    assert np.isclose(trimesh.Trimesh(result.meshes[0].vertices,result.meshes[0].faces,process=False).volume,1)


def test_instances_keep_separate_keys_and_placement():
    cube=trimesh.creation.box()
    other=cube.copy();other.apply_translation((17,9,3))
    result,audit=repair_paint_document(PaintDocument([painted(cube,key='first'),painted(other,key='second')]))
    assert [m.key for m in result.meshes]==['first','second']
    centers=[m.vertices.mean(axis=0) for m in result.meshes]
    assert np.allclose(centers[1]-centers[0],[17,9,3])
    assert len(audit['meshes'])==2


def test_edge_touch_is_locally_regularized_without_dropping_either_solid():
    first=trimesh.creation.box()
    second=first.copy();second.apply_translation((1,1,0))
    combined=trimesh.util.concatenate([first,second])
    result,audit=repair_paint_document(PaintDocument([painted(combined)]))
    mesh=trimesh.Trimesh(result.meshes[0].vertices,result.meshes[0].faces,process=False)
    assert mesh.body_count==2
    assert not any(mesh_defects(mesh).values())
    assert abs(mesh.volume-2)<.001
    precision=audit['meshes'][0]['precision']
    assert 0<precision['vertices_moved']<len(mesh.vertices)
    assert precision['maximum_local_displacement_mm']<.001


def test_missing_manifold_provenance_api_has_actionable_error(monkeypatch):
    from layerloom import paint_repair
    monkeypatch.setattr(paint_repair.mf,'Mesh64',None)
    with pytest.raises(RuntimeError,match='upgrade manifold3d'):
        repair_paint_document(PaintDocument([painted(trimesh.creation.box())]))
