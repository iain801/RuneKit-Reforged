"""Optional OpenCL matcher: only a count and at most 51 positions leave the GPU."""

from collections import OrderedDict
import logging
import threading

import numpy as np
import pyopencl as cl

_LOG = logging.getLogger(__name__)
_LOCK = threading.RLock()
_ENGINE = None

SOURCE = r"""
__kernel void match(__global const uchar4 *image, int iw,
 __global const uchar4 *needle, int nw, int nh,
 __global const int2 *anchors, int na, __global const int *limits,
 int x, int y, int cols, int rows,
 __global int *counts, __global int *positions) {
 int id=get_global_id(0), lane=get_local_id(0), group=get_group_id(0);
 int px=x+id%cols, py=y+id/cols;
 int valid=id<cols*rows;
 for(int a=0; valid && a<na; a++) {
  int2 o=anchors[a]; uchar4 p=image[(py+o.y)*iw+px+o.x], n=needle[o.y*nw+o.x];
  int d=abs((int)p.x-n.x)+abs((int)p.y-n.y)+abs((int)p.z-n.z);
  if(d>30) valid=0;
 }
 for(int ny=0; valid && ny<nh; ny++) for(int nx=0; valid && nx<nw; nx++) {
  uchar4 n=needle[ny*nw+nx];
  if(n.w==0) continue;
  uchar4 p=image[(py+ny)*iw+px+nx];
  int d=abs((int)p.x-n.x)+abs((int)p.y-n.y)+abs((int)p.z-n.z);
  if(d>limits[n.w]) valid=0;
 }
 // A local prefix sum preserves row order, unlike an atomic append counter.
 __local int ranks[256]; ranks[lane]=valid; barrier(CLK_LOCAL_MEM_FENCE);
 for(int offset=1;offset<256;offset*=2) {
  int add=lane>=offset?ranks[lane-offset]:0;
  barrier(CLK_LOCAL_MEM_FENCE); ranks[lane]+=add; barrier(CLK_LOCAL_MEM_FENCE);
 }
 // Later matches in a group can never belong to the first 51 global matches.
 if(valid && ranks[lane]<=51) positions[group*51+ranks[lane]-1]=id;
 if(lane==255) counts[group]=min(ranks[lane],51);
}
__kernel void gather(__global const int *counts, __global const int *positions,
 int groups, __global int *result) {
 int found=0;
 for(int g=0;g<groups && found<51;g++) {
  int n=counts[g];
  for(int j=0;j<n && found<51;j++) result[1+found++]=positions[g*51+j];
 }
 result[0]=found;
}
"""


class GpuSnapshot:
    def __init__(self, engine, image):
        self.engine = engine
        self.width = image.shape[1]
        self.buffer = engine.upload(image)
        self.failed = False

    def find(self, needle, x, y, width, height):
        if self.failed:
            return None
        try:
            with _LOCK:
                return self.engine.find(self, needle, x, y, width, height)
        except cl.Error:
            self.failed = True
            _LOG.warning(
                "GPU search failed; using CPU for this snapshot", exc_info=True
            )
            return None


class _Engine:
    def __init__(self):
        devices = [
            d
            for p in cl.get_platforms()
            for d in p.get_devices(device_type=cl.device_type.GPU)
        ]
        if not devices:
            raise RuntimeError("No OpenCL GPU available")
        self.context = cl.Context([devices[0]])
        self.queue = cl.CommandQueue(self.context)
        self.program = cl.Program(self.context, SOURCE).build()
        self.match = cl.Kernel(self.program, "match")
        self.gather = cl.Kernel(self.program, "gather")
        # Reproduce the JavaScript double-precision alpha comparison exactly,
        # including values at the tolerance boundary, using integer GPU tests.
        limits = np.array(
            [max(d for d in range(766) if d * (a / 255) <= 30) for a in range(256)],
            dtype=np.int32,
        )
        self.limits = self.upload(limits)
        self.templates = OrderedDict()
        self.capacity = 0
        self.result = cl.Buffer(self.context, cl.mem_flags.WRITE_ONLY, 52 * 4)
        _LOG.info("GPU image matching enabled: %s", devices[0].name.strip())

    def upload(self, image):
        return cl.Buffer(
            self.context,
            cl.mem_flags.READ_ONLY | cl.mem_flags.COPY_HOST_PTR,
            hostbuf=np.ascontiguousarray(image),
        )

    def template(self, needle):
        key = (needle.shape, needle.tobytes())
        if key in self.templates:
            self.templates.move_to_end(key)
            return self.templates[key]
        opaque = np.argwhere(needle[:, :, 3] == 255)
        anchors = []
        if len(opaque):
            colors = needle[opaque[:, 0], opaque[:, 1], :3].astype(np.int16)
            distances = np.full(len(opaque), 1024)
            chosen = 0
            for _ in range(min(10, len(opaque))):
                anchors.append(opaque[chosen][::-1])
                distances = np.minimum(
                    distances, np.abs(colors - colors[chosen]).sum(axis=1)
                )
                distances[chosen] = -1
                chosen = int(distances.argmax())
        offsets = np.array(anchors or [[0, 0]], dtype=np.int32)
        value = self.upload(needle), self.upload(offsets), len(anchors)
        # Bound both entry count and retained template size. Large one-off
        # templates are usable but do not occupy the cache.
        if needle.nbytes <= 262144:
            self.templates[key] = value
            if len(self.templates) > 32:
                self.templates.popitem(last=False)
        return value

    def find(self, snapshot, needle, x, y, width, height):
        nh, nw = needle.shape[:2]
        columns, rows = width - nw + 1, height - nh + 1
        if columns <= 0 or rows <= 0:
            return []
        groups = (columns * rows + 255) // 256
        if groups > self.capacity:
            self.counts = cl.Buffer(self.context, cl.mem_flags.READ_WRITE, groups * 4)
            self.positions = cl.Buffer(
                self.context, cl.mem_flags.READ_WRITE, groups * 51 * 4
            )
            self.capacity = groups
        template, anchors, anchor_count = self.template(needle)
        integer = np.int32
        self.match(
            self.queue,
            (groups * 256,),
            (256,),
            snapshot.buffer,
            integer(snapshot.width),
            template,
            integer(nw),
            integer(nh),
            anchors,
            integer(anchor_count),
            self.limits,
            integer(x),
            integer(y),
            integer(columns),
            integer(rows),
            self.counts,
            self.positions,
        )
        self.gather(
            self.queue,
            (1,),
            (1,),
            self.counts,
            self.positions,
            integer(groups),
            self.result,
        )
        out = np.empty(52, dtype=np.int32)
        cl.enqueue_copy(self.queue, out, self.result)
        return [
            {"x": x + int(v) % columns, "y": y + int(v) // columns}
            for v in out[1 : 1 + out[0]]
        ]


def upload_snapshot(image):
    global _ENGINE
    with _LOCK:
        try:
            if _ENGINE is None:
                _ENGINE = _Engine()
            return GpuSnapshot(_ENGINE, image)
        except cl.Error as error:
            raise RuntimeError("OpenCL initialization or upload failed") from error
