"""Exact nearest triangles with bounded search memory and stable paint ties.

The first BVH traversal finds the true minimum squared distance; a separate
pass chooses the lowest original face ID within the export tie tolerance.
Numba supplies LLVM in portable wheels, without requiring a local compiler.
No model cache, approximation, or decimation is involved. JIT disk caching is
disabled, so frozen apps and read-only package directories are supported.
"""
import math
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from numba import njit

@njit(inline='always')
def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

@njit(inline='always')
def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

@njit(inline='always')
def along(a, b, s):
    return (a[0] + b[0] * s, a[1] + b[1] * s, a[2] + b[2] * s)

@njit(inline='always')
def sq(a, b):
    d = sub(a, b)
    return dot(d, d)

@njit(inline='always')
def segment(p, a, b):
    ab = sub(b, a)
    dd = dot(ab, ab)
    return sq(p, along(a, ab, min(1.0, max(0.0, dot(sub(p, a), ab) / dd)) if dd else 0.0))

@njit(inline='always')
def triangle_distance(p, t):
    a = (t[0, 0], t[0, 1], t[0, 2])
    b = (t[1, 0], t[1, 1], t[1, 2])
    c = (t[2, 0], t[2, 1], t[2, 2])
    ab = sub(b, a)
    ac = sub(c, a)
    ap = sub(p, a)
    d1 = dot(ab, ap)
    d2 = dot(ac, ap)
    if d1 <= 0 and d2 <= 0:
        return sq(p, a)
    bp = sub(p, b)
    d3 = dot(ab, bp)
    d4 = dot(ac, bp)
    if d3 >= 0 and d4 <= d3:
        return sq(p, b)
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and (d3 <= 0):
        denominator = d1 - d3
        return sq(p, along(a, ab, d1 / denominator)) if denominator else min(segment(p, a, b), segment(p, a, c))
    cp = sub(p, c)
    d5 = dot(ab, cp)
    d6 = dot(ac, cp)
    if d6 >= 0 and d5 <= d6:
        return sq(p, c)
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and (d6 <= 0):
        denominator = d2 - d6
        return sq(p, along(a, ac, d2 / denominator)) if denominator else min(segment(p, a, b), segment(p, a, c))
    va = d3 * d6 - d5 * d4
    if va <= 0 and d4 - d3 >= 0 and (d5 - d6 >= 0):
        denominator = d4 - d3 + (d5 - d6)
        return sq(p, along(b, sub(c, b), (d4 - d3) / denominator)) if denominator else min(segment(p, b, c), segment(p, a, c))
    denominator = va + vb + vc
    if denominator <= 0:
        return min(segment(p, a, b), segment(p, b, c), segment(p, c, a))
    q = along(along(a, ab, vb / denominator), ac, vc / denominator)
    return sq(p, q)

@njit(nogil=True)
def build(tri, ids, bounds, nodes):
    """Build a balanced AABB tree using iterative breadth-first partitioning."""
    nodes[0, 2] = 0
    nodes[0, 3] = len(ids)
    count = 1
    index = 0
    while index < count:
        start = nodes[index, 2]
        end = nodes[index, 3]
        minimum = ids[start]
        for axis in range(3):
            bounds[index, axis] = math.inf
            bounds[index, axis + 3] = -math.inf
        for j in range(start, end):
            face = ids[j]
            minimum = min(minimum, face)
            for vertex in range(3):
                for axis in range(3):
                    value = tri[face, vertex, axis]
                    bounds[index, axis] = min(bounds[index, axis], value)
                    bounds[index, axis + 3] = max(bounds[index, axis + 3], value)
        nodes[index, 4] = minimum
        if end - start > 8:
            axis = 0
            for other in range(1, 3):
                if bounds[index, other + 3] - bounds[index, other] > bounds[index, axis + 3] - bounds[index, axis]:
                    axis = other
            centers = np.empty(end - start)
            for j in range(start, end):
                face = ids[j]
                centers[j - start] = tri[face, 0, axis] + tri[face, 1, axis] + tri[face, 2, axis]
            middle = (end - start) // 2
            order = np.argpartition(centers, middle)
            ids[start:end] = ids[start:end][order]
            nodes[index, 0] = count
            nodes[index, 1] = count + 1
            nodes[count, 2] = start
            nodes[count, 3] = start + middle
            nodes[count + 1, 2] = start + middle
            nodes[count + 1, 3] = end
            count += 2
        index += 1
    return count

@njit(inline='always')
def bound(bounds, index, p):
    total = 0.0
    for axis in range(3):
        d = max(bounds[index, axis] - p[axis], 0.0, p[axis] - bounds[index, axis + 3])
        total += d * d
    return total

@njit(nogil=True)
def query(tri, ids, bounds, nodes, points, tolerance, stack_size):
    """Query one bounded batch without Python objects or a candidate matrix.

The separate tie traversal uses the actual minimum, avoiding tolerance drift
when several successively near-equal distances are encountered. Node minimum
face IDs prune tie branches without losing stable source-paint identity.
"""
    result = np.empty(len(points), np.int64)
    distances = np.empty(len(points))
    for q in range(len(points)):
        p = points[q]
        best = math.inf
        face_id = len(tri)
        stack = np.empty(stack_size, np.int64)
        stack[0] = 0
        count = 1
        while count:
            count -= 1
            index = stack[count]
            if bound(bounds, index, p) > best:
                continue
            left = nodes[index, 0]
            if left < 0:
                for j in range(nodes[index, 2], nodes[index, 3]):
                    face = ids[j]
                    distance = triangle_distance(p, tri[face])
                    if distance < best:
                        best = distance
                        face_id = face
            else:
                right = nodes[index, 1]
                if bound(bounds, left, p) > bound(bounds, right, p):
                    left, right = (right, left)
                stack[count] = right
                stack[count + 1] = left
                count += 2
        limit = best + tolerance
        stack[0] = 0
        count = 1
        while count:
            count -= 1
            index = stack[count]
            if nodes[index, 4] >= face_id or bound(bounds, index, p) > limit:
                continue
            left = nodes[index, 0]
            if left < 0:
                for j in range(nodes[index, 2], nodes[index, 3]):
                    face = ids[j]
                    if face < face_id and triangle_distance(p, tri[face]) <= limit:
                        face_id = face
            else:
                right = nodes[index, 1]
                if nodes[left, 4] > nodes[right, 4]:
                    left, right = (right, left)
                stack[count] = right
                stack[count + 1] = left
                count += 2
        result[q] = face_id
        distances[q] = best
    return (result, distances)

class NearestTriangles:
    """Immutable exact BVH. No cached model data or writable JIT cache needed."""

    def __init__(self, surface, *, control=None):
        self.control = control
        count = len(surface.faces)
        if not count:
            raise ValueError('Nearest-surface search requires triangles')
        leaf_capacity = 1 << (max(1, (count + 7) // 8) - 1).bit_length()
        capacity = 2 * leaf_capacity - 1
        self.stack_size = leaf_capacity.bit_length() + 2
        if control is not None:
            control.check_memory(count * 80 + capacity * 88, stage='paint search index')
        self.triangles = np.array(surface.triangles, dtype=np.float64, order='C', copy=True)
        self.ids = np.arange(count, dtype=np.int64)
        self.bounds = np.empty((capacity, 6))
        self.nodes = np.full((capacity, 5), -1, dtype=np.int64)
        used = build(self.triangles, self.ids, self.bounds, self.nodes)
        self.bounds = self.bounds[:used]
        self.nodes = self.nodes[:used]
        self.tolerance = max(1e-14, float(np.max(surface.extents)) ** 2 * 1e-14)
        if control is not None:
            control.checkpoint()
        query(self.triangles, self.ids, self.bounds, self.nodes, np.zeros((0, 3)), self.tolerance, self.stack_size)
        if control is not None:
            control.checkpoint()

    def query(self, points, *, return_distance=False):
        """Query in chunks with at most four GIL-free ordinary worker threads."""
        points = np.asarray(points, dtype=np.float64)
        if points.ndim != 2 or points.shape[1:] != (3,):
            raise ValueError('Nearest-surface queries must be XYZ points')
        result = np.empty(len(points), dtype=np.int64)
        distances = np.empty(len(points), dtype=np.float64)
        for start in range(0, len(points), 4096):
            batch = np.ascontiguousarray(points[start:start + 4096])
            if not np.isfinite(batch).all():
                raise ValueError('Nearest-surface queries must be finite')
            if self.control is not None:
                self.control.check_memory(len(batch) * (self.stack_size * 8 + 64), stage='nearest-surface search')
            args = (self.triangles, self.ids, self.bounds, self.nodes)
            if len(batch) < 1024:
                found, squared = query(*args, batch, self.tolerance, self.stack_size)
            else:
                with ThreadPoolExecutor(max_workers=4) as pool:
                    futures = [pool.submit(query, *args, part, self.tolerance, self.stack_size) for part in np.array_split(batch, 4)]
                    batches = [future.result() for future in futures]
                found = np.concatenate([item[0] for item in batches])
                squared = np.concatenate([item[1] for item in batches])
            result[start:start + len(batch)] = found
            distances[start:start + len(batch)] = squared
            if self.control is not None:
                self.control.checkpoint()
        return (result, distances) if return_distance else result
