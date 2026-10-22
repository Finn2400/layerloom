import zipfile
import json

import numpy as np
import pytest
import trimesh

from layerloom.paint_io import decode_paint_annotation,load_paint_document
from layerloom.paint_model import PaintMesh,PaintDocument
from layerloom.paint_native import encode_paint_slot,export_native_painted_3mf


@pytest.mark.parametrize('slot',[0,1,2,3,4,15,18,33])
def test_leaf_encoder_roundtrip(slot):
    assert decode_paint_annotation(encode_paint_slot(slot),(0,1,2),None)==[((0,1,2),slot)]


def test_native_four_color_roundtrip_exact_geometry_and_progress(tmp_path):
    cube=trimesh.creation.box()
    palette=np.array([[179,102,255,255],[0,0,0,255],[255,255,255,255],[255,255,0,255]],np.uint8)
    colors=palette[np.arange(len(cube.faces))%4]
    doc=PaintDocument([PaintMesh('cube','Four colors',cube.vertices,cube.faces,colors)])
    events=[];path=tmp_path/'four.3mf'
    report=export_native_painted_3mf(doc,path,center_on_plate=False,progress=lambda a,b:events.append((a,b)))
    loaded=load_paint_document(path).meshes[0]
    assert np.array_equal(loaded.vertices,doc.meshes[0].vertices)
    assert np.array_equal(loaded.faces,doc.meshes[0].faces)
    assert np.array_equal(loaded.face_rgba,colors)
    assert events==[(0,1),(1,1)]
    assert report['physical_filament_slots']==4
    with zipfile.ZipFile(path) as z:
        config=json.loads(z.read('Metadata/project_settings.config'))
        assert 'filament_type' not in config


def test_native_rejects_open_mesh_without_repair(tmp_path):
    cube=trimesh.creation.box();cube.update_faces(np.arange(len(cube.faces))!=0)
    doc=PaintDocument([PaintMesh('cube','Open cube',cube.vertices,cube.faces,np.tile([255,0,0,255],(len(cube.faces),1)))])
    with pytest.raises(ValueError,match='repaired closed'):
        export_native_painted_3mf(doc,tmp_path/'open.3mf')
    assert not (tmp_path/'open.3mf').exists()
