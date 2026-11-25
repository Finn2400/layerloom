"""Disk-backed, bounded-buffer geometry ingestion for layer-contour weaving.

Only the assembly/part metadata is resident. XML children are detached during
parsing, source vertices/faces are binary spools, and an on-disk RTree selects
triangle records for each layer. No source mesh or active triangle set is
materialized as a whole. A single layer's polygon working set is the caller's
responsibility.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import math
import os
import posixpath
import re
import shutil
import sqlite3
import struct
import tempfile
import zipfile

from lxml import etree
import numpy as np

from .tokens import PAT_TAG_RE, NO_WEAVE_TAG_RE, token_is_valid

IDENTITY = np.eye(4, dtype=np.float64)
UNITS = {"micron": .001, "millimeter": 1., "centimeter": 10.,
         "inch": 25.4, "foot": 304.8, "meter": 1000.}
_VERTEX = struct.Struct("<3d")
_FACE = struct.Struct("<3Q")


@dataclass
class StoredPart:
    id: int
    label: str
    run: str | None
    noweave: bool
    vertex_count: int
    triangle_count: int
    source_member: str
    source_object_id: str
    vertex_path: Path = field(repr=False)
    face_path: Path = field(repr=False)
    transform: np.ndarray = field(repr=False)
    first_triangle: int = 0
    bounds: np.ndarray | None = field(default=None, repr=False)
    source_color: str | None = None
    source_extruder: str | None = None


@dataclass
class _Object:
    id: str
    label: str
    metadata: dict = field(default_factory=dict)
    components: list = field(default_factory=list)
    vertex_path: Path | None = None
    face_path: Path | None = None
    vertex_count: int = 0
    triangle_count: int = 0
    has_mesh: bool = False
    material_id: str | None = None
    material_index: str | None = None
    triangle_properties: tuple | None = None
    has_face_properties: bool = False
    nonuniform_properties: bool = False


def _tag(element):
    return element.tag.rsplit("}", 1)[-1] if isinstance(element.tag, str) else ""


def _detach(element):
    element.clear()
    parent = element.getparent()
    if parent is not None:
        # Removing the element itself avoids retaining millions of empty nodes.
        parent.remove(element)


def _target(base, name):
    raw = name.replace("\\", "/")
    value = raw.lstrip("/") if raw.startswith("/") else posixpath.normpath(
        posixpath.join(posixpath.dirname(base), raw))
    if not value or value.startswith("../") or value == "..":
        raise ValueError(f"Invalid package reference: {name!r}")
    return value


def _transform(text, unit):
    if not text:
        return IDENTITY.copy()
    values = np.asarray([float(v) for v in text.replace(",", " ").split()])
    if len(values) != 12 or not np.isfinite(values).all():
        raise ValueError("3MF transform must contain twelve finite values")
    result = IDENTITY.copy()
    result[:3, :3] = values[:9].reshape(3, 3).T
    result[:3, 3] = values[9:] * unit
    if abs(np.linalg.det(result[:3, :3])) < 1e-15:
        raise ValueError("Singular 3MF transform is unsupported")
    return result


def _token(label):
    m = PAT_TAG_RE.search(label or "")
    return m.group(1).lower() if m else None


def _display_color(value):
    """Return a uniform 3MF RGBA display color without guessing a palette."""
    text = str(value or '').strip()
    if re.fullmatch(r'#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?', text):
        return text.upper() + ('FF' if len(text) == 7 else '')
    return None


class _VertexReader:
    """Small LRU of vertex blocks, rather than an unbounded memory mapping."""
    def __init__(self, path, count, max_blocks=8, block_vertices=8192):
        self.file = open(path, "rb")
        self.count = count
        self.block_vertices = block_vertices
        self.max_blocks = max_blocks
        self.cache = OrderedDict()

    def get(self, indices):
        indices = np.asarray(indices, dtype=np.int64)
        if len(indices) and (indices.min() < 0 or indices.max() >= self.count):
            raise ValueError("Triangle references an out-of-range vertex")
        out = np.empty((len(indices), 3), dtype=np.float64)
        blocks = indices // self.block_vertices
        for block in np.unique(blocks):
            block = int(block)
            data = self.cache.pop(block, None)
            if data is None:
                self.file.seek(block * self.block_vertices * _VERTEX.size)
                raw = self.file.read(self.block_vertices * _VERTEX.size)
                if len(raw) % _VERTEX.size:
                    raise ValueError("Truncated vertex spool")
                data = np.frombuffer(raw, dtype="<f8").reshape(-1, 3)
            self.cache[block] = data
            while len(self.cache) > self.max_blocks:
                self.cache.popitem(last=False)
            mask = blocks == block
            out[mask] = data[indices[mask] % self.block_vertices]
        return out

    def close(self):
        self.file.close()
        self.cache.clear()


class TriangleStore:
    """Index a prepared 3MF without loading any whole mesh into memory.

    The context owns a private directory beneath ``work_dir`` and removes it on
    exit (also on initialization failure). ``iter_vertices`` / ``iter_faces``
    preserve source indexing for streaming NOWEAVE parts. ``iter_triangles``
    yields transformed, correctly wound float64 arrays of shape (N, 3, 3).
    """
    def __init__(self, input_path, work_dir, memory_budget_mib=1024,
                 reserve_disk_bytes=4 * 1024**3):
        self.input_path = Path(input_path).resolve()
        self.work_dir = Path(work_dir)
        self.memory_budget_mib = float(memory_budget_mib)
        if not math.isfinite(self.memory_budget_mib) or self.memory_budget_mib < 16:
            raise ValueError("TriangleStore needs a memory budget of at least 16 MiB")
        self.reserve_disk_bytes = int(reserve_disk_bytes)
        if self.reserve_disk_bytes < 0:
            raise ValueError("Disk reserve cannot be negative")
        self.parts = []
        self.bounds = np.array([[np.inf] * 3, [-np.inf] * 3])
        self.triangle_count = 0
        self.source_sha256 = ""
        self._directory = None
        self._db = None
        self._models = {}
        self._metadata_bytes = 0
        self._parsed_records = 0
        self._closed = False
        try:
            self.work_dir.mkdir(parents=True, exist_ok=True)
            self._check_disk()
            self._directory = Path(tempfile.mkdtemp(prefix="source_", dir=self.work_dir))
            self._triangle_path = self._directory / "transformed_triangles.f64"
            self._db = sqlite3.connect(str(self._directory / "triangles.sqlite"))
            self._db.execute("PRAGMA journal_mode=OFF")
            self._db.execute("PRAGMA synchronous=OFF")
            self._db.execute("PRAGMA temp_store=FILE")
            self._db.execute("PRAGMA cache_size=-8192")
            self._db.execute("CREATE VIRTUAL TABLE z_index USING rtree(id, zmin, zmax)")
            self._build()
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    @property
    def spool_bytes(self):
        if not self._directory or not self._directory.exists():
            return 0
        return sum(p.stat().st_size for p in self._directory.iterdir() if p.is_file())

    def close(self):
        if self._db is not None:
            self._db.close()
            self._db = None
        if self._directory is not None:
            shutil.rmtree(self._directory, ignore_errors=True)
        self._closed = True

    def _check_disk(self, needed=0):
        if shutil.disk_usage(self.work_dir).free - int(needed) < self.reserve_disk_bytes:
            raise OSError("Insufficient scratch disk space: preserving configured free-space reserve")

    def _metadata(self, amount):
        # Metadata is small for normal assemblies, but not allowed to grow
        # without limit on malicious or excessively instanced packages.
        self._metadata_bytes += int(amount)
        if self._metadata_bytes > self.memory_budget_mib * 1024**2 / 8:
            raise MemoryError("3MF assembly metadata exceeds the memory budget")

    def _periodic_disk(self):
        self._parsed_records += 1
        if self._parsed_records % 65536 == 0:
            self._check_disk(4 * 1024**2)

    @staticmethod
    def _parse(zf, member):
        # Entities are never expanded; external XML/network resources are not
        # part of the 3MF package and must not be accessed.
        with zf.open(member) as fp:
            yield from etree.iterparse(fp, events=("start", "end"),
                                       resolve_entities=False, no_network=True)

    def _root_member(self, zf):
        if "_rels/.rels" in zf.namelist():
            for event, el in self._parse(zf, "_rels/.rels"):
                if event == "end":
                    if _tag(el) == "Relationship" and str(el.get("Type", "")).endswith("/3dmodel"):
                        target = el.get("Target", "").lstrip("/")
                        if target in zf.namelist():
                            return target
                    _detach(el)
        if "3D/3dmodel.model" in zf.namelist():
            return "3D/3dmodel.model"
        models = [n for n in zf.namelist() if n.lower().endswith(".model")]
        if len(models) == 1:
            return models[0]
        raise ValueError("Cannot identify the root 3MF model")

    def _vendor_settings(self, zf):
        member = "Metadata/model_settings.config"
        if member not in zf.namelist():
            return {}
        out = {}; obj = None; part = None
        for event, el in self._parse(zf, member):
            tag = _tag(el)
            if event == "start":
                if tag == "object":
                    obj = el.get("id"); out[obj] = {"name": "", "parts": {}, "extruder": None, "part_extruders": {}}
                    self._metadata(256)
                elif tag == "part":
                    part = el.get("id")
            else:
                if tag == "metadata" and obj and el.get("key", "").lower() == "name":
                    value = el.get("value", "")
                    self._metadata(len(value.encode()) + 128)
                    if part:
                        out[obj]["parts"][part] = value
                    else:
                        out[obj]["name"] = value
                elif tag == "metadata" and obj and el.get("key", "").lower() == "extruder":
                    value = el.get("value", "").strip()
                    self._metadata(len(value.encode()) + 128)
                    if part:
                        out[obj]["part_extruders"][part] = value
                    else:
                        out[obj]["extruder"] = value
                elif tag == "part":
                    part = None
                elif tag == "object":
                    obj = None
                _detach(el)
        return out

    def _model(self, zf, member):
        if member not in zf.namelist():
            raise ValueError(f"Missing referenced 3MF model: {member}")
        unit = 1.; objects = {}; builds = []; children = set(); build_items_seen = False
        materials = {}; active_material = None
        current = None; vf = None; ff = None; in_mesh = False
        unsupported = {"beamlattice", "volumetricdata", "slices", "slice"}
        prefix = hashlib.sha256(member.encode()).hexdigest()[:16]
        try:
            for event, el in self._parse(zf, member):
                tag = _tag(el)
                if tag in unsupported:
                    raise ValueError(f"Unsupported prepared-3MF geometry: {tag}")
                if event == "start":
                    if tag == "model":
                        name = el.get("unit", "millimeter")
                        if name not in UNITS:
                            raise ValueError(f"Unsupported 3MF unit: {name}")
                        unit = UNITS[name]
                    elif tag == "object":
                        oid = el.get("id")
                        if not oid or oid in objects:
                            raise ValueError("Missing or duplicate 3MF object id")
                        current = _Object(oid, el.get("name", ""))
                        current.material_id = el.get("pid")
                        current.material_index = el.get("pindex", "0") if current.material_id else None
                        objects[oid] = current
                        self._metadata(1024 + len(current.label.encode()))
                    elif tag == "basematerials":
                        active_material = el.get("id")
                        if not active_material or active_material in materials:
                            raise ValueError("Missing or duplicate base-material resource id")
                        materials[active_material] = []
                        self._metadata(256 + len(active_material.encode()))
                    elif tag == "mesh":
                        if current is None or current.has_mesh or current.components:
                            raise ValueError("Invalid object mesh definition")
                        current.has_mesh = True; in_mesh = True
                        # Do not interpolate untrusted object IDs into paths.
                        key = hashlib.sha256(current.id.encode()).hexdigest()[:16]
                        current.vertex_path = self._directory / f"{prefix}_{key}.vertices"
                        current.face_path = self._directory / f"{prefix}_{key}.faces"
                        vf = open(current.vertex_path, "wb")
                        ff = open(current.face_path, "wb")
                    elif tag == "component":
                        if current is None or current.has_mesh:
                            raise ValueError("Invalid 3MF component definition")
                        oid = el.get("objectid")
                        if not oid:
                            raise ValueError("Component is missing objectid")
                        path = next((v for k, v in el.attrib.items() if k.rsplit("}", 1)[-1] == "path"), None)
                        child = _target(member, path) if path else member
                        current.components.append((child, oid, _transform(el.get("transform"), unit)))
                        children.add(child)
                        self._metadata(512 + len(child.encode()) + len(oid.encode()))
                    elif tag == "item":
                        build_items_seen = True
                        if el.get("printable", "1").lower() not in {"0", "false", "no"}:
                            oid = el.get("objectid")
                            if not oid:
                                raise ValueError("Build item is missing objectid")
                            builds.append((oid, _transform(el.get("transform"), unit), el.get("partnumber", "")))
                            self._metadata(512 + len(el.get("partnumber", "").encode()))
                    continue
                if tag == "vertex" and in_mesh:
                    try:
                        xyz = tuple(float(el.attrib[k]) * unit for k in ("x", "y", "z"))
                    except (KeyError, ValueError) as exc:
                        raise ValueError("Invalid 3MF vertex") from exc
                    if not all(math.isfinite(v) for v in xyz):
                        raise ValueError("Non-finite source coordinates")
                    vf.write(_VERTEX.pack(*xyz)); current.vertex_count += 1
                    self._periodic_disk()
                elif tag == "triangle" and in_mesh:
                    try:
                        inds = tuple(int(el.attrib[k]) for k in ("v1", "v2", "v3"))
                    except (KeyError, ValueError) as exc:
                        raise ValueError("Invalid triangle indices") from exc
                    if min(inds) < 0 or max(inds) >= current.vertex_count:
                        raise ValueError("Triangle references an out-of-range vertex")
                    if len(set(inds)) < 3:
                        raise ValueError("Degenerate source triangle indices")
                    pid = el.get("pid", current.material_id)
                    p1 = el.get("p1", current.material_index)
                    properties = (pid, p1)
                    current.has_face_properties |= any(k in el.attrib for k in ('pid', 'p1', 'p2', 'p3'))
                    if el.get("p2", p1) != p1 or el.get("p3", p1) != p1:
                        current.nonuniform_properties = True
                    if current.triangle_count == 0:
                        current.triangle_properties = properties
                        self._metadata(sum(len(value.encode()) for value in properties if value) + 128)
                    elif current.triangle_properties != properties:
                        current.nonuniform_properties = True
                    ff.write(_FACE.pack(*inds)); current.triangle_count += 1
                    self._periodic_disk()
                elif tag == "metadata" and current is not None:
                    key = el.get("name", "").split(":")[-1]
                    value = (el.text or "").strip()
                    self._metadata(len(value.encode()) + 128)
                    current.metadata[key] = value
                    if key in {"Title", "Name"} and value and not current.label:
                        current.label = value
                elif tag == "mesh":
                    vf.close(); ff.close(); vf = ff = None; in_mesh = False
                elif tag == "object":
                    if current.has_mesh and (not current.vertex_count or not current.triangle_count):
                        raise ValueError("Empty source mesh")
                    current = None
                elif tag == "base" and active_material is not None:
                    value = el.get("displaycolor", "")
                    self._metadata(len(value.encode()) + 128)
                    materials[active_material].append(_display_color(value))
                elif tag == "basematerials":
                    active_material = None
                _detach(el)
        finally:
            if vf is not None: vf.close()
            if ff is not None: ff.close()
        return {"objects": objects, "builds": builds, "children": children,
                "unit": unit, "build_items_seen": build_items_seen, "materials": materials}

    def _leaves(self, member, oid, matrix, inherited_label, inherited_run, inherited_noweave, trail):
        key = (member, oid)
        if key in trail:
            raise ValueError("Cycle in 3MF component graph")
        if len(trail) > 128:
            raise ValueError("3MF assembly nesting exceeds supported depth")
        obj = self._models.get(member, {}).get("objects", {}).get(oid)
        if obj is None:
            raise ValueError(f"Missing referenced 3MF object {member}:{oid}")
        label = obj.label or inherited_label or f"object_{oid}"
        metadata_run = obj.metadata.get("stack_token", "").lower()
        if metadata_run and not token_is_valid(metadata_run):
            raise ValueError(f"Invalid stack_token on {label}")
        run = _token(obj.label) or metadata_run or inherited_run
        noweave = (bool(NO_WEAVE_TAG_RE.search(label)) or inherited_noweave
                   or obj.metadata.get('noweave', '').lower() in {'1', 'true', 'yes'})
        if obj.has_mesh:
            yield obj, member, matrix, label, run, noweave
            return
        if not obj.components:
            raise ValueError(f"Object {member}:{oid} has no mesh or components")
        for child_member, child_oid, tf in obj.components:
            yield from self._leaves(child_member, child_oid, matrix @ tf, label, run,
                                    noweave, trail | {key})

    def _source_appearance(self, obj, member, sidecar):
        if obj.nonuniform_properties:
            raise ValueError(f'NOWEAVE per-face material properties are not uniform: {obj.label}')
        declared = obj.metadata.get('source_hex', '')
        color = _display_color(declared)
        if declared and color is None:
            raise ValueError(f'Invalid NOWEAVE source_hex color: {obj.label}')
        if color is None:
            pid, index = obj.triangle_properties or (obj.material_id, obj.material_index)
            values = self._models[member]['materials'].get(pid, [])
            try:
                index = int(index)
                color = values[index] if 0 <= index < len(values) else None
            except (TypeError, ValueError):
                color = None
        if obj.has_face_properties and color is None:
            raise ValueError(f'Cannot preserve NOWEAVE per-face material properties: {obj.label}')
        # Provenance only: filament numbers in a source project cannot safely
        # be assigned to the newly grouped output without a printer mapping.
        extruder = (sidecar.get('part_extruders', {}).get(obj.id)
                    or obj.metadata.get('source_extruder')
                    or obj.metadata.get('extruder') or sidecar.get('extruder'))
        return color, extruder

    def _build(self):
        with open(self.input_path, "rb") as fp:
            h = hashlib.sha256()
            for block in iter(lambda: fp.read(1024**2), b""):
                h.update(block)
            self.source_sha256 = h.hexdigest()
        with zipfile.ZipFile(self.input_path) as zf:
            self._metadata(sum(len(n.encode()) + 256 for n in zf.namelist()))
            root = self._root_member(zf)
            vendors = self._vendor_settings(zf)
            todo = deque([root])
            while todo:
                name = todo.popleft()
                if name in self._models: continue
                self._models[name] = self._model(zf, name)
                todo.extend(sorted(self._models[name]["children"] - self._models.keys()))
        model = self._models[root]
        builds = model["builds"]
        # A resource-only prepared mesh may omit build items. An explicit
        # non-printable build must never be re-enabled by that compatibility
        # fallback, even when filtering removes every item.
        if not builds and not model["build_items_seen"]:
            builds = [(o.id, IDENTITY.copy(), o.label) for o in model["objects"].values() if o.has_mesh]
        for oid, matrix, build_label in builds:
            sidecar = vendors.get(oid, {})
            label = build_label or sidecar.get("name", "")
            for obj, member, tf, leaf_label, run, noweave in self._leaves(
                    root, oid, matrix, label, _token(label), bool(NO_WEAVE_TAG_RE.search(label)), set()):
                vendor_label = sidecar.get("parts", {}).get(obj.id, "")
                if not _token(leaf_label) and vendor_label:
                    leaf_label = vendor_label
                    run = _token(vendor_label) or run
                    noweave = noweave or bool(NO_WEAVE_TAG_RE.search(vendor_label))
                self._metadata(1024 + len(leaf_label.encode()))
                source_color, source_extruder = self._source_appearance(obj, member, sidecar) if noweave else (None, None)
                self.parts.append(StoredPart(len(self.parts) + 1, leaf_label, None if noweave else (run or "cmy"),
                    noweave, obj.vertex_count, obj.triangle_count, member, obj.id,
                    obj.vertex_path, obj.face_path, tf,
                    source_color=source_color, source_extruder=source_extruder))
        if not self.parts:
            raise ValueError("3MF contains no printable mesh parts")
        # Metadata graph is no longer needed once instances have been resolved.
        self._models.clear()
        with open(self._triangle_path, "wb") as out:
            for part in self.parts:
                part.first_triangle = self.triangle_count + 1
                bounds = np.array([[np.inf] * 3, [-np.inf] * 3])
                reader = _VertexReader(part.vertex_path, part.vertex_count)
                try:
                    for faces in self.iter_faces(part.id):
                        v = reader.get(faces.reshape(-1))
                        v = v @ part.transform[:3, :3].T + part.transform[:3, 3]
                        triangles = v.reshape(-1, 3, 3)
                        if not np.isfinite(triangles).all():
                            raise ValueError("Non-finite transformed coordinates")
                        bounds[0] = np.minimum(bounds[0], triangles.min(axis=(0, 1)))
                        bounds[1] = np.maximum(bounds[1], triangles.max(axis=(0, 1)))
                        self._check_disk(triangles.nbytes * 3)
                        out.write(np.asarray(triangles, dtype="<f8").tobytes())
                        zs = triangles[:, :, 2]
                        base = self.triangle_count + 1
                        self._db.executemany("INSERT INTO z_index VALUES (?, ?, ?)",
                            ((base + i, float(zmin), float(zmax))
                             for i, (zmin, zmax) in enumerate(zip(zs.min(axis=1), zs.max(axis=1)))))
                        self.triangle_count += len(triangles)
                finally:
                    reader.close()
                part.bounds = bounds
                self.bounds[0] = np.minimum(self.bounds[0], bounds[0])
                self.bounds[1] = np.maximum(self.bounds[1], bounds[1])
                self._db.commit()
        self._check_disk()

    def _part(self, part_id):
        if self._closed:
            raise RuntimeError("TriangleStore is closed")
        if not isinstance(part_id, (int, np.integer)) or not 1 <= part_id <= len(self.parts):
            raise KeyError(part_id)
        return self.parts[int(part_id) - 1]

    def _chunk(self, chunk_size):
        if not isinstance(chunk_size, (int, np.integer)) or chunk_size <= 0:
            raise ValueError("chunk_size must be a positive integer")
        # Leave room for geometry operations and sqlite, not only one array.
        cap = max(1, int(self.memory_budget_mib * 1024**2 / (72 * 32)))
        return min(int(chunk_size), cap, 32768)

    def iter_vertices(self, part_id, chunk_size=32768):
        part = self._part(part_id); size = self._chunk(chunk_size)
        with open(part.vertex_path, "rb") as fp:
            while True:
                raw = fp.read(size * _VERTEX.size)
                if not raw: break
                v = np.frombuffer(raw, dtype="<f8").reshape(-1, 3)
                yield v @ part.transform[:3, :3].T + part.transform[:3, 3]

    def iter_faces(self, part_id, chunk_size=32768):
        part = self._part(part_id); size = self._chunk(chunk_size)
        reflected = np.linalg.det(part.transform[:3, :3]) < 0
        with open(part.face_path, "rb") as fp:
            while True:
                raw = fp.read(size * _FACE.size)
                if not raw: break
                faces = np.frombuffer(raw, dtype="<u8").reshape(-1, 3)
                yield faces[:, [0, 2, 1]] if reflected else faces

    def iter_triangles(self, part_id, z=None, chunk_size=32768):
        part = self._part(part_id); size = self._chunk(chunk_size)
        first = part.first_triangle; last = first + part.triangle_count - 1
        with open(self._triangle_path, "rb") as fp:
            if z is None:
                fp.seek((first - 1) * 72)
                left = part.triangle_count
                while left:
                    n = min(left, size)
                    raw = fp.read(n * 72)
                    if len(raw) != n * 72: raise ValueError("Truncated triangle spool")
                    yield np.frombuffer(raw, dtype="<f8").reshape(-1, 3, 3)
                    left -= n
                return
            z = float(z)
            if not math.isfinite(z):
                raise ValueError("Slice Z must be finite")
            # RTree values are outward-rounded float32 bounds. Filter exact f64
            # bounds afterward, so conservative candidates cannot change slices.
            cursor = self._db.execute("SELECT id FROM z_index WHERE zmin<=? AND zmax>=? AND id>=? AND id<=? ORDER BY id",
                                      (z, z, first, last))
            while True:
                rows = cursor.fetchmany(size)
                if not rows: break
                ids = np.asarray(rows, dtype=np.int64).reshape(-1) - 1
                selected = np.empty((len(ids), 3, 3), dtype=np.float64)
                blocks = ids // 8192
                for block in np.unique(blocks):
                    fp.seek(int(block) * 8192 * 72)
                    raw = fp.read(8192 * 72)
                    data = np.frombuffer(raw, dtype="<f8").reshape(-1, 3, 3)
                    mask = blocks == block
                    selected[mask] = data[ids[mask] % 8192]
                zz = selected[:, :, 2]
                keep = (zz.min(axis=1) <= z) & (zz.max(axis=1) >= z)
                if keep.any(): yield selected[keep]
