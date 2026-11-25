import importlib.util
from pathlib import Path
import numpy as np
import trimesh

from layerloom.contour_store import TriangleStore

spec=importlib.util.spec_from_file_location('contour_stress',Path(__file__).resolve().parents[1]/'scripts/benchmark_contour_stress.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_streamed_grid_box_changes_density_not_shape(tmp_path):
    for n in [1,2,4,8]:
        p=tmp_path/f'box-{n}.3mf'
        counts=module.write_grid_box(p,n,width=4,depth=3,height=2)
        with TriangleStore(p,tmp_path/'scratch',reserve_disk_bytes=0) as store:
            assert store.triangle_count==12*n*n
            assert store.parts[0].vertex_count==6*n*n+2
            vertices=np.concatenate(list(store.iter_vertices(1)))
            faces=np.concatenate(list(store.iter_faces(1)))
            mesh=trimesh.Trimesh(vertices,faces,process=False)
            assert mesh.is_volume
            assert np.isclose(mesh.volume,24)
            assert np.allclose(mesh.bounds,[[0,0,0],[4,3,2]])
        assert counts['triangles']==12*n*n
