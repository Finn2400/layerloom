import json
import numpy as np
import pytest
import trimesh
from PIL import Image
from layerloom.desktop_glb_import import ImportSettings, import_model, _appearance, _palette
from layerloom.paint_io import load_paint_document


def write_box(path):
    mesh = trimesh.creation.box(extents=[.008, .006, .004])
    mesh.visual.vertex_colors = [0, 255, 255, 255]
    path.write_bytes(trimesh.Scene(mesh).export(file_type='glb'))


def test_closed_vertex_color_solid_preserved_with_file_units(tmp_path):
    src = tmp_path/'box.glb'; write_box(src); original = src.read_bytes()
    manifest = import_model(src, tmp_path/'out.3mf', tmp_path/'out.json')
    assert manifest['input_triangles'] == manifest['sampled_triangles'] == 12
    assert manifest['geometry_issues'] == []
    np.testing.assert_allclose(manifest['dimensions_mm'], [8, 4, 6], atol=1e-5)
    assert manifest['objects'][0]['source_hex'] == '#00ffff'
    doc = load_paint_document(tmp_path/'out.3mf')
    assert len(doc.meshes) == 1
    assert src.read_bytes() == original


def test_texture_recovers_color_and_marks_open_regions(tmp_path):
    m = trimesh.creation.box(extents=[.01, .01, .01])
    m.unmerge_vertices()
    uv = np.tile([[.1,.1],[.9,.1],[.5,.9]], (12,1))
    pixels = np.zeros((16,16,3), dtype=np.uint8); pixels[:,:8] = [255,0,0]; pixels[:,8:] = [0,255,0]
    m.visual = trimesh.visual.TextureVisuals(uv=uv, material=trimesh.visual.material.PBRMaterial(baseColorTexture=Image.fromarray(pixels)))
    src = tmp_path/'texture.glb'; src.write_bytes(trimesh.Scene(m).export(file_type='glb'))
    result = import_model(src, tmp_path/'out.3mf', tmp_path/'out.json', ImportSettings(colors=64))
    assert len(result['objects']) == 2
    assert {x['source_hex'] for x in result['objects']} == {'#ff0000', '#00ff00'}
    assert result['geometry_issues']
    assert result['sampled_triangles'] <= 200000
    assert result['sampled_triangles'] > 12


def test_palette_is_deterministic_and_actually_bounded():
    rgb = np.random.default_rng(4).random((2000,3)); weights=np.ones(2000)
    a, ids = _palette(rgb,weights,64); b, ids2 = _palette(rgb,weights,64)
    assert len(a) <= 64 and len(np.unique(ids)) <= 64
    np.testing.assert_array_equal(a,b); np.testing.assert_array_equal(ids,ids2)


def test_missing_requested_color_source_rejected_without_output(tmp_path):
    src=tmp_path/'box.glb';write_box(src)
    with pytest.raises(ValueError,match='no base-color texture'):
        import_model(src,tmp_path/'out.3mf',tmp_path/'out.json',ImportSettings(color_source='texture'))
    assert not (tmp_path/'out.3mf').exists()


def test_material_factor_is_applied_in_linear_space():
    m=trimesh.creation.box()
    m.visual=trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(baseColorFactor=[.25,.5,1,1]))
    _,rgb,_=_appearance(m,ImportSettings(detail='original'),100)
    # trimesh stores baseColorFactor as quantized uint8.
    np.testing.assert_allclose(rgb[0],[.538,.737,1.],atol=.005)


def test_unsupported_texture_transform_fails_clearly(tmp_path):
    from pygltflib import GLTF2
    src=tmp_path/'box.glb';write_box(src)
    gltf=GLTF2().load(str(src));gltf.extensionsUsed=['KHR_texture_transform'];gltf.save_binary(str(src))
    with pytest.raises(ValueError,match='KHR_texture_transform'):
        import_model(src,tmp_path/'out.3mf',tmp_path/'out.json')


@pytest.mark.parametrize('settings', [ImportSettings(colors=1),ImportSettings(colors=257),ImportSettings(detail='mystery')])
def test_invalid_settings(settings):
    with pytest.raises(ValueError): settings.validate()


def test_auto_combines_texture_material_and_vertex_colors():
    m = trimesh.creation.box()
    image = Image.fromarray(np.full((4, 4, 3), 128, dtype=np.uint8))
    material = trimesh.visual.material.PBRMaterial(baseColorTexture=image, baseColorFactor=[.5,.5,.5,1])
    m.visual = trimesh.visual.TextureVisuals(uv=np.full((len(m.vertices),2),.5), material=material)
    m.visual.vertex_attributes['color'] = np.tile([.5,.5,.5,1.], (len(m.vertices),1))
    _, rgb, _ = _appearance(m, ImportSettings(detail='original'),100)
    from layerloom.desktop_glb_import import _srgb_to_linear, linear_rgb_to_srgb
    expected = linear_rgb_to_srgb(_srgb_to_linear(np.array([128/255]*3)) * (128/255) * .5)
    np.testing.assert_allclose(rgb[0], expected, atol=1e-6)


def test_scene_instances_and_transforms_survive(tmp_path):
    m=trimesh.creation.box(extents=[.002,.003,.004]);m.visual.vertex_colors=[0,255,255,255]
    scene=trimesh.Scene();scene.add_geometry(m, geom_name='box', node_name='one')
    tf=np.eye(4);tf[0,3]=.01
    scene.graph.update(frame_to='two', matrix=tf, geometry='box')
    src=tmp_path/'instances.glb';src.write_bytes(scene.export(file_type='glb'))
    result=import_model(src,tmp_path/'out.3mf',tmp_path/'out.json')
    assert result['input_triangles'] == result['sampled_triangles'] == 24
    np.testing.assert_allclose(result['dimensions_mm'],[12,4,3],atol=1e-5)
    assert not result['geometry_issues']


def test_glb_material_and_vertex_colors_survive_mesh_copy(tmp_path):
    from pygltflib import GLTF2, Material, PbrMetallicRoughness
    # Material + COLOR_0 is loaded as TextureVisuals even without an image.
    a=trimesh.creation.box(extents=[.002]*3);a.visual.vertex_colors=[0,255,255,255]
    b=trimesh.creation.box(extents=[.002]*3);b.apply_translation([.004,0,0]);b.visual.vertex_colors=[255,0,255,255]
    src=tmp_path/'combined.glb';src.write_bytes(trimesh.Scene(trimesh.util.concatenate([a,b])).export(file_type='glb'))
    gltf=GLTF2().load(str(src))
    gltf.materials=[Material(pbrMetallicRoughness=PbrMetallicRoughness(baseColorFactor=[1,1,1,1]))]
    for m in gltf.meshes:
        for prim in m.primitives: prim.material=0
    gltf.save_binary(str(src))
    result=import_model(src,tmp_path/'out.3mf',tmp_path/'out.json')
    assert {x['source_hex'] for x in result['objects']} == {'#00ffff','#ff00ff'}
    assert not result['geometry_issues']
