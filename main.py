#!/usr/bin/env python3
#–Puddy: 3D + Mido
# (NanoKontrol2)
# Raspberry Pi 3 friendly first pass
import math, time, os, sys, random
from dataclasses import dataclass
import numpy as np

import pi3d
from mido import get_input_names, open_input

# ---------- helpers
def clamp(x, lo, hi): return max(lo, min(hi, x))
def lerp(a,b,t): return a + (b-a)*t
def remap01(v, lo, hi): return lo + (hi-lo)*v

# stable hash for vertex-based explode dir
def hash3(v):
    # v is (x,y,z) np array
    dot = v[0]*12.9898 + v[1]*78.233 + v[2]*37.719
    s = math.sin(dot) * 43758.5453
    r = s - math.floor(s)
    # make three components
    a = r
    b = (math.sin(dot*1.3)*43758.5453) % 1.0
    c = (math.sin(dot*1.7)*43758.5453) % 1.0
    vec = np.array([a,b,c], dtype=np.float32)*2.0 - 1.0
    # normalize
    n = np.linalg.norm(vec) + 1e-6
    return vec / n

# ---------- mesh generator (UV sphere / box / grid)
def gen_uvsphere(slices=32, stacks=24, radius=1.0):
    verts = []
    norms = []
    uvs = []
    inds = []
    for i in range(stacks+1):
        v = i/float(stacks)
        phi = v*math.pi  # 0..pi
        for j in range(slices+1):
            u = j/float(slices)
            theta = u*math.tau  # 0..2pi
            x = math.sin(phi)*math.cos(theta)
            y = math.cos(phi)
            z = math.sin(phi)*math.sin(theta)
            verts.append([radius*x, radius*y, radius*z])
            norms.append([x,y,z])
            uvs.append([u,1.0-v])
    for i in range(stacks):
        for j in range(slices):
            a = i*(slices+1) + j
            b = a + slices + 1
            inds += [a, b, a+1, b, b+1, a+1]
    return np.array(verts,'f'), np.array(norms,'f'), np.array(uvs,'f'), np.array(inds,'i4')

def gen_box(sub=8, size=1.5):
    # start from a grid then extrude as cube faces (simple way: start with sphere for now)
    return gen_uvsphere(slices=sub*2, stacks=sub, radius=size*0.5)

def gen_grid(nx=40, nz=40, size=2.0):
    verts=[]; norms=[]; uvs=[]; inds=[]
    for iz in range(nz+1):
        v = iz/float(nz)
        z = (v-0.5)*size
        for ix in range(nx+1):
            u = ix/float(nx)
            x = (u-0.5)*size
            verts.append([x,0.0,z])
            norms.append([0.0,1.0,0.0])
            uvs.append([u,1.0-v])
    for iz in range(nz):
        for ix in range(nx):
            a = iz*(nx+1)+ix
            b = a+nx+1
            inds += [a,b,a+1, b,b+1,a+1]
    return np.array(verts,'f'), np.array(norms,'f'), np.array(uvs,'f'), np.array(inds,'i4')

# ---------- Pi3D shape wrapper we can deform each frame
class DeformShape(pi3d.Shape):
    def __init__(self, verts, norms, uvs, inds, shader, color=(1,1,1,1)):
        super().__init__()
        self.base_verts = verts.copy()
        self.verts = verts.copy()
        self.norms = norms.copy()
        self.uvs = uvs.copy()
        self.inds = inds.copy()
        self.buf = [pi3d.Buffer(self, self.verts, self.uvs, self.inds, self.norms)]
        self.set_draw_details(shader, [], 1.0, 1.0, 1.0, 1.0)  # flat color via mat_shader
        self.unif = [color[0], color[1], color[2], color[3]]
        self.positionX(self.x()); self.positionY(self.y()); self.positionZ(self.z())

    def apply_deform(self, params, t, rnd_amt):
        # compute deformed positions from base
        v = self.base_verts.copy()

        # optional wobble noise (simple sin on axes)
        if params.noise_amp > 0.0:
            w = params.noise_amp
            v[:,0] += np.sin(v[:,2]*2.3 + t*2.1)*0.05*w
            v[:,2] += np.cos(v[:,0]*2.1 + t*1.7)*0.05*w

        # twist around Y
        if abs(params.twist) > 1e-4:
            ang = params.twist
            # angle proportional to y (−1..+1)
            y = v[:,1]
            twistAng = ang * y
            cosA = np.cos(twistAng); sinA = np.sin(twistAng)
            x = v[:,0].copy(); z = v[:,2].copy()
            v[:,0] = x*cosA - z*sinA
            v[:,2] = x*sinA + z*cosA

        # squash/stretch Y
        v[:,1] *= params.squash

        # voxel quantization (snap to grid)
        if params.voxel > 1e-5:
            g = params.voxel
            v = np.floor(v / g + 0.5) * g

        # explode outward with stable hash dir
        if params.explode > 1e-5:
            # make per-vertex unit random dirs (stable from base_verts)
            dirs = np.apply_along_axis(hash3, 1, self.base_verts)
            v += dirs * params.explode * (0.5 + rnd_amt*0.5)

        # global uniform scale
        v *= params.scale

        self.verts[:] = v
        self.buf[0].re_init(pts=self.verts)  # upload new verts

@dataclass
class Params:
    scale: float = 1.0
    twist: float = 0.0
    squash: float = 1.0
    voxel: float = 0.0
    explode: float = 0.0
    hue: float = 0.0
    wobble: float = 0.0
    rot_speed: float = 0.0
    lfo_rate: float = 0.5
    voxel_rand: float = 0.0
    noise_amp: float = 0.0
    cam_dist: float = 1.8

def make_mat_shader():
    # use built-in 'mat_flat' for speed
    return pi3d.Shader("mat_flat")

def hue_to_rgb(h):
    # simple HSV(h,1,1) to RGB
    h = (h % 1.0) * 6.0
    c = 1.0; x = 1.0 - abs((h % 2.0) - 1.0)
    if   0<=h<1: r,g,b = c,x,0
    elif 1<=h<2: r,g,b = x,c,0
    elif 2<=h<3: r,g,b = 0,c,x
    elif 3<=h<4: r,g,b = 0,x,c
    elif 4<=h<5: r,g,b = x,0,c
    else:        r,g,b = c,0,x
    return (r,g,b)

# ---------- MIDI input (NanoKontrol2)
def open_nanoport():
    names = get_input_names()
    port_name = None
    # prefer nanoKONTROL if present
    for n in names:
        if "nano" in n.lower():
            port_name = n; break
    if port_name is None and names:
        port_name = names[0]
    if port_name is None:
        print("No MIDI input found. Continue without MIDI.")
        return None
    print("MIDI input:", port_name)
    return open_input(port_name)

def map_midi(msg, params):
    # Expect ControlChange; typical NanoKontrol2 CC numbers:
    # Faders: 0..7, Knobs: 16..23
    if msg.type != 'control_change': return
    v = msg.value / 127.0
    cc = msg.control
    if   0 <= cc <= 7:
        if cc==0: params.scale     = remap01(v, 0.2, 3.0)
        if cc==1: params.twist     = remap01(v, -2.0, 2.0)
        if cc==2: params.squash    = remap01(v, 0.2, 2.0)
        if cc==3: params.voxel     = remap01(v, 0.0, 0.2)
        if cc==4: params.explode   = remap01(v, 0.0, 0.5)
        if cc==5: params.hue       = v
        if cc==6: params.wobble    = v
        if cc==7: params.rot_speed = remap01(v, -1.0, 1.0)
    elif 16 <= cc <= 23:
        k = cc - 16
        if k==0: params.lfo_rate   = remap01(v, 0.05, 2.0)
        if k==1: params.voxel_rand = v
        if k==2: params.noise_amp  = remap01(v, 0.0, 0.5)
        if k==3: params.cam_dist   = remap01(v, 0.8, 3.5)

# ---------- primitives and model loading
def make_shape(kind, shader):
    if kind == 1:   v,n,uv,ind = gen_uvsphere(40,30,1.0)
    elif kind == 2: v,n,uv,ind = gen_box(10,1.5)
    elif kind == 3: v,n,uv,ind = gen_uvsphere(20,12,1.0)
    elif kind == 4: v,n,uv,ind = gen_grid(70,70,2.5)
    else:
        # try OBJ
        path = os.path.join("models","teapot.obj")
        if os.path.exists(path):
            # crude OBJ loader for verts only (triangulated) – fallback to sphere if fail
            try:
                vs=[]; ns=[]; ts=[]; faces=[]
                with open(path,'r') as f:
                    for line in f:
                        if line.startswith('v '):
                            _,x,y,z = line.strip().split()[:4]
                            vs.append([float(x), float(y), float(z)])
                        elif line.startswith('vt '):
                            _,u,vv = line.strip().split()[:3]
                            ts.append([float(u), float(vv)])
                        elif line.startswith('vn '):
                            _,nx,ny,nz = line.strip().split()[:4]
                            ns.append([float(nx), float(ny), float(nz)])
                        elif line.startswith('f '):
                            # supports f v/t/n
                            parts = line.strip().split()[1:]
                            if len(parts)==3:
                                faces.append(parts)
                if not faces:
                    raise Exception("no faces")
                # expand to vertex lists (no index reuse)
                V=[]; N=[]; T=[]; I=[]
                for tri in faces:
                    for p in tri:
                        toks=p.split('/')
                        vi=int(toks[0])-1
                        ti=int(toks[1])-1 if len(toks)>1 and toks[1] else 0
                        ni=int(toks[2])-1 if len(toks)>2 and toks[2] else 0
                        V.append(vs[vi]); T.append(ts[ti] if ts else [0,0]); N.append(ns[ni] if ns else [0,1,0])
                I = list(range(len(V)))
                v = np.array(V,'f'); n = np.array(N,'f'); uv=np.array(T,'f'); ind=np.array(I,'i4')
            except Exception as e:
                print("OBJ load failed:", e, "-> fallback sphere")
                v,n,uv,ind = gen_uvsphere(40,30,1.0)
        else:
            v,n,uv,ind = gen_uvsphere(40,30,1.0)

    shape = DeformShape(v,n,uv,ind, shader)
    return shape

# ---------- main app
def main():
    DISPLAY = pi3d.Display.create(x=0, y=0, w=0, h=0, frames_per_second=60)
    DISPLAY.set_background(0.0,0.0,0.0,1.0)
    shader = make_mat_shader()

    camera = pi3d.Camera(is_3d=True)
    light = pi3d.Light(lightpos=(3,4,6), lightcol=(0.9,0.9,0.9), lightamb=(0.2,0.2,0.2))

    # params and objects
    params = Params()
    objects = []
    base = make_shape(1, shader)
    objects.append(base)

    # midi
    port = open_nanoport()

    # keyboard
    kb = pi3d.Keyboard()

    t0 = time.time()
    angle = 0.0
    wire = False
    current_kind = 1

    while DISPLAY.loop_running():
        t = time.time() - t0

        # MIDI poll
        if port:
            for msg in port.iter_pending():
                map_midi(msg, params)

        # keyboard
        k = kb.read()
        if k > -1:
            c = chr(k) if k<256 else ''
            if k==27: break  # ESC
            elif c=='w' or c=='W':
                wire = not wire
                for o in objects:
                    o.set_line_width(1 if wire else 0)
            elif c=='r' or c=='R':
                params = Params()
            elif c==' ':
                # clone
                sh = make_shape(current_kind, shader)
                dx = random.uniform(-0.6,0.6)
                dz = random.uniform(-0.6,0.6)
                sh.translateX(dx); sh.translateZ(dz)
                objects.append(sh)
            elif k==8: # backspace
                if len(objects)>1:
                    objects.pop()
            elif c in '12345':
                current_kind = int(c)
                # replace base object (keep clones)
                objects[0] = make_shape(current_kind, shader)

        # auto wobble LFO
        lfo = math.sin(t * params.lfo_rate * math.tau) * params.wobble

        # camera
        camZ = params.cam_dist * (1.0 + 0.1*lfo)
        camera.reset()
        camera.position((0.0, 0.0, camZ))
        camera.look_at((0,0,0))

        # global rotation
        angle += params.rot_speed * (1.0/60.0) * 360.0

        # color
        rgb = hue_to_rgb(params.hue)

        # draw & deform
        for idx, obj in enumerate(objects):
            rnd_amt = params.voxel_rand
            obj.unif = [rgb[0], rgb[1], rgb[2], 1.0]
            obj.apply_deform(params, t + idx*0.1, rnd_amt)
            obj.rotateIncY(params.rot_speed * 360.0 * (1.0/60.0))
            obj.draw()

    if port: port.close()
    kb.close()
    DISPLAY.destroy()

if __name__ == '__main__':
    main()
