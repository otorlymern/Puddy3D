#!/usr/bin/env python3
# Puddy3D - Pi3D + Mido (NanoKontrol2)
# Raspberry Pi 3+ friendly
import math
import os
import platform
import time

import numpy as np
from mido import get_input_names, open_input

# Desktop preview requires: pip install pyglet
try:
    import pyglet
    pyglet.options["gl_legacy"] = True
except Exception:
    pyglet = None

PRINT_MIDI = False
HEADLESS_STATUS_HZ = 5
HEADLESS_FPS_LIMIT = 60
NUM_OBJECTS = 4
TAU = 2.0 * math.pi
DEADZONE = 0.05
MIDI_SMOOTHING_HZ = 18.0

BASE_X_RANGE = 1.44
BASE_Y_RANGE = 1.44
BASE_Z_RANGE = 1.2
FAR_XY_BOOST = 0.5
ROT_Y_RANGE = 180.0

# MIDI mapping - ground-truth NanoKontrol2 CCs
CC_PLAY = 41
CC_STOP = 42
CC_REW = 43
CC_FF = 44
CC_REC = 45
CC_CYCLE = 46
CC_TRACK_LEFT = 58
CC_TRACK_RIGHT = 59

MIDI_MAP = {
    "faders": [0, 1, 2, 3, 4, 5, 6, 7],
    "knobs": [16, 17, 18, 19, 20, 21, 22, 23],
    "solo": [32, 33, 34, 35, 36, 37, 38, 39],
    "mute": [48, 49, 50, 51, 52, 53, 54, 55],
    "rec": [64, 65, 66, 67, 68, 69, 70, 71],
    # Transport buttons mapped to object toggles (Obj1..Obj4)
    "transport": [CC_PLAY, CC_STOP, CC_REW, CC_FF],
    "cycle": [CC_CYCLE],
    "track_left": [CC_TRACK_LEFT],
    "track_right": [CC_TRACK_RIGHT],
}

MIDI_CC_TO_FADER = {cc: i for i, cc in enumerate(MIDI_MAP["faders"])}
MIDI_CC_TO_KNOB = {cc: i for i, cc in enumerate(MIDI_MAP["knobs"])}
MIDI_CC_TO_S = {cc: i for i, cc in enumerate(MIDI_MAP["solo"])}
MIDI_CC_TO_M = {cc: i for i, cc in enumerate(MIDI_MAP["mute"])}
MIDI_CC_TO_R = {cc: i for i, cc in enumerate(MIDI_MAP["rec"])}
MIDI_CC_TO_TRANSPORT = {cc: i for i, cc in enumerate(MIDI_MAP["transport"])}

MODE_NORMAL = "NORMAL"
MODE_LFO_SCALE = "LFO_EDIT_SCALE"
MODE_LFO_MOVE = "LFO_EDIT_MOVE"
MODE_LFO_ROT = "LFO_EDIT_ROT"
MODE_SCULPT = "SCULPT"


def detect_backend():
    env = os.getenv("PUDDY_BACKEND", "").strip().lower()
    if env in ("desktop", "preview"):
        return "desktop"
    if env in ("mac", "headless", "darwin"):
        return "mac"
    if env in ("pi", "pi3d", "linux"):
        return "pi"
    sysname = platform.system()
    if sysname == "Darwin":
        return "desktop" if pyglet is not None else "mac"
    if sysname == "Linux":
        return "pi" if is_raspberry_pi() else "desktop" if pyglet is not None else "pi"
    return "pi"


def is_raspberry_pi():
    model_path = "/proc/device-tree/model"
    try:
        with open(model_path, "r", encoding="utf-8") as fh:
            return "raspberry pi" in fh.read().lower()
    except OSError:
        return False


def remap01(v, lo, hi):
    return lo + (hi - lo) * v


def unit_to_bipolar(v):
    return v * 2.0 - 1.0


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def apply_deadzone(x, dz=DEADZONE):
    ax = abs(x)
    if ax <= dz:
        return 0.0
    scaled = (ax - dz) / (1.0 - dz)
    return math.copysign(scaled, x)


def norm_bipolar(unit_value, dz=DEADZONE):
    x = unit_value * 2.0 - 1.0
    x = apply_deadzone(x, dz)
    return clamp(x, -1.0, 1.0)


def norm_unipolar(unit_value):
    return clamp(unit_value, 0.0, 1.0)


def button_changed(new, old):
    return new != old


def button_rising(new, old):
    return new and not old


def button_falling(new, old):
    return old and not new


class LFO:
    def __init__(self, enabled=False, freq_hz=0.5, amp=0.0, phase=0.0, waveform="sine"):
        self.enabled = enabled
        self.freq_hz = freq_hz
        self.amp = amp
        self.phase = phase
        self.waveform = waveform

    def tick(self, t, dt):
        if not self.enabled or self.amp == 0.0:
            return 0.0
        self.phase += dt * self.freq_hz * TAU
        if self.phase > TAU:
            self.phase -= TAU
        return self.amp * math.sin(self.phase)


class MidiState:
    def __init__(self):
        self.knobs = np.full(8, 64, dtype=np.int16)
        self.faders = np.full(8, 64, dtype=np.int16)
        base_unit = 64.0 / 127.0
        self.knobs_f = np.full(8, base_unit, dtype=np.float32)
        self.faders_f = np.full(8, base_unit, dtype=np.float32)
        self.knobs_target_f = self.knobs_f.copy()
        self.faders_target_f = self.faders_f.copy()
        self.s_buttons = [False] * 8
        self.m_buttons = [False] * 8
        self.r_buttons = [False] * 8
        self.transport = [False] * NUM_OBJECTS
        self.cycle = False
        self.track_left = False
        self.track_right = False
        self.rec_transport = False
        self.s_buttons_prev = self.s_buttons.copy()
        self.m_buttons_prev = self.m_buttons.copy()
        self.r_buttons_prev = self.r_buttons.copy()
        self.transport_prev = self.transport.copy()
        self.cycle_prev = self.cycle
        self.track_left_prev = self.track_left
        self.track_right_prev = self.track_right
        self.rec_transport_prev = self.rec_transport

    def capture_prev(self):
        self.s_buttons_prev = self.s_buttons.copy()
        self.m_buttons_prev = self.m_buttons.copy()
        self.r_buttons_prev = self.r_buttons.copy()
        self.transport_prev = self.transport.copy()
        self.cycle_prev = self.cycle
        self.track_left_prev = self.track_left
        self.track_right_prev = self.track_right
        self.rec_transport_prev = self.rec_transport

    def update_from_msg(self, msg):
        if msg.type != "control_change":
            return
        cc = msg.control
        val = msg.value

        if cc in MIDI_CC_TO_FADER:
            idx = MIDI_CC_TO_FADER[cc]
            self.faders[idx] = val
            self.faders_target_f[idx] = val * (1.0 / 127.0)
            return
        if cc in MIDI_CC_TO_KNOB:
            idx = MIDI_CC_TO_KNOB[cc]
            self.knobs[idx] = val
            self.knobs_target_f[idx] = val * (1.0 / 127.0)
            return
        new_state = val > 0
        if cc in MIDI_CC_TO_S:
            idx = MIDI_CC_TO_S[cc]
            self.s_buttons[idx] = new_state
        elif cc in MIDI_CC_TO_M:
            idx = MIDI_CC_TO_M[cc]
            self.m_buttons[idx] = new_state
        elif cc in MIDI_CC_TO_R:
            idx = MIDI_CC_TO_R[cc]
            self.r_buttons[idx] = new_state
        elif cc in MIDI_CC_TO_TRANSPORT:
            idx = MIDI_CC_TO_TRANSPORT[cc]
            self.transport[idx] = new_state
        elif cc == CC_CYCLE:
            self.cycle = new_state
        elif cc == CC_TRACK_LEFT:
            self.track_left = new_state
        elif cc == CC_TRACK_RIGHT:
            self.track_right = new_state
        elif cc == CC_REC:
            self.rec_transport = new_state

    def tick(self, dt):
        if dt <= 0.0:
            return
        alpha = 1.0 - math.exp(-MIDI_SMOOTHING_HZ * dt)
        self.knobs_f += (self.knobs_target_f - self.knobs_f) * alpha
        self.faders_f += (self.faders_target_f - self.faders_f) * alpha


# ---------- MIDI input

def list_midi_inputs():
    names = get_input_names()
    if names:
        print("MIDI inputs:")
        for name in names:
            print(" - {}".format(name))
    else:
        print("MIDI inputs: none")
    return names


def open_nanoport(names=None, quiet_no_input=False):
    if names is None:
        names = get_input_names()
    port_name = None
    for n in names:
        if "nanokontrol2" in n.lower():
            port_name = n
            break
    if port_name is None:
        for n in names:
            if "nano" in n.lower():
                port_name = n
                break
    if port_name is None and names:
        port_name = names[0]
    if port_name is None:
        if not quiet_no_input:
            print("No MIDI input found. Continue without MIDI.")
        return None
    print("MIDI input:", port_name)
    return open_input(port_name)


# ---------- mesh generators

def gen_uvsphere(slices=12, stacks=8, radius=1.0):
    verts = []
    norms = []
    uvs = []
    inds = []
    for i in range(stacks + 1):
        v = i / float(stacks)
        phi = v * math.pi
        for j in range(slices + 1):
            u = j / float(slices)
            theta = u * (2.0 * math.pi)
            x = math.sin(phi) * math.cos(theta)
            y = math.cos(phi)
            z = math.sin(phi) * math.sin(theta)
            verts.append([radius * x, radius * y, radius * z])
            norms.append([x, y, z])
            uvs.append([u, 1.0 - v])
    for i in range(stacks):
        for j in range(slices):
            a = i * (slices + 1) + j
            b = a + slices + 1
            inds += [a, b, a + 1, b, b + 1, a + 1]
    return np.array(verts, "f"), np.array(norms, "f"), np.array(uvs, "f"), np.array(inds, "i4")


def gen_box(size=1.4):
    h = size * 0.5
    verts = []
    norms = []
    uvs = []
    inds = []

    faces = [
        # z-
        ([(-h, -h, -h), (h, -h, -h), (h, h, -h), (-h, h, -h)], (0.0, 0.0, -1.0)),
        # z+
        ([(-h, -h, h), (-h, h, h), (h, h, h), (h, -h, h)], (0.0, 0.0, 1.0)),
        # x-
        ([(-h, -h, -h), (-h, h, -h), (-h, h, h), (-h, -h, h)], (-1.0, 0.0, 0.0)),
        # x+
        ([(h, -h, -h), (h, -h, h), (h, h, h), (h, h, -h)], (1.0, 0.0, 0.0)),
        # y-
        ([(-h, -h, -h), (-h, -h, h), (h, -h, h), (h, -h, -h)], (0.0, -1.0, 0.0)),
        # y+
        ([(-h, h, -h), (h, h, -h), (h, h, h), (-h, h, h)], (0.0, 1.0, 0.0)),
    ]

    for face_verts, nrm in faces:
        base = len(verts)
        for i, vtx in enumerate(face_verts):
            verts.append(list(vtx))
            norms.append(list(nrm))
            u = 0.0 if i in (0, 3) else 1.0
            v = 0.0 if i in (0, 1) else 1.0
            uvs.append([u, v])
        inds += [base + 0, base + 1, base + 2, base + 0, base + 2, base + 3]

    return np.array(verts, "f"), np.array(norms, "f"), np.array(uvs, "f"), np.array(inds, "i4")


def gen_cone(sides=8, radius=0.6, height=1.2):
    verts = []
    norms = []
    uvs = []
    inds = []

    apex = np.array([0.0, height * 0.5, 0.0], dtype=np.float32)
    base_y = -height * 0.5
    center = np.array([0.0, base_y, 0.0], dtype=np.float32)

    for i in range(sides):
        a0 = (i / float(sides)) * TAU
        a1 = ((i + 1) / float(sides)) * TAU
        b0 = np.array([math.cos(a0) * radius, base_y, math.sin(a0) * radius], dtype=np.float32)
        b1 = np.array([math.cos(a1) * radius, base_y, math.sin(a1) * radius], dtype=np.float32)

        n = np.cross(b0 - apex, b1 - apex)
        n = n / (np.linalg.norm(n) + 1e-6)
        base = len(verts)
        verts += [apex.tolist(), b0.tolist(), b1.tolist()]
        norms += [n.tolist(), n.tolist(), n.tolist()]
        uvs += [[0.5, 1.0], [0.0, 0.0], [1.0, 0.0]]
        inds += [base, base + 1, base + 2]

        base = len(verts)
        verts += [center.tolist(), b1.tolist(), b0.tolist()]
        norms += [[0.0, -1.0, 0.0]] * 3
        uvs += [[0.5, 0.5], [1.0, 0.0], [0.0, 0.0]]
        inds += [base, base + 1, base + 2]

    return np.array(verts, "f"), np.array(norms, "f"), np.array(uvs, "f"), np.array(inds, "i4")


def gen_cylinder(sides=10, radius=0.6, height=1.2):
    verts = []
    norms = []
    uvs = []
    inds = []

    top_y = height * 0.5
    bot_y = -height * 0.5
    top_center = np.array([0.0, top_y, 0.0], dtype=np.float32)
    bot_center = np.array([0.0, bot_y, 0.0], dtype=np.float32)

    for i in range(sides):
        a0 = (i / float(sides)) * TAU
        a1 = ((i + 1) / float(sides)) * TAU
        p0 = np.array([math.cos(a0) * radius, bot_y, math.sin(a0) * radius], dtype=np.float32)
        p1 = np.array([math.cos(a1) * radius, bot_y, math.sin(a1) * radius], dtype=np.float32)
        p2 = np.array([math.cos(a1) * radius, top_y, math.sin(a1) * radius], dtype=np.float32)
        p3 = np.array([math.cos(a0) * radius, top_y, math.sin(a0) * radius], dtype=np.float32)

        mid_ang = (a0 + a1) * 0.5
        n = np.array([math.cos(mid_ang), 0.0, math.sin(mid_ang)], dtype=np.float32)

        base = len(verts)
        verts += [p0.tolist(), p1.tolist(), p2.tolist(), p0.tolist(), p2.tolist(), p3.tolist()]
        norms += [n.tolist()] * 6
        uvs += [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
        inds += [base, base + 1, base + 2, base + 3, base + 4, base + 5]

        base = len(verts)
        verts += [top_center.tolist(), p3.tolist(), p2.tolist()]
        norms += [[0.0, 1.0, 0.0]] * 3
        uvs += [[0.5, 0.5], [0.0, 0.0], [1.0, 0.0]]
        inds += [base, base + 1, base + 2]

        base = len(verts)
        verts += [bot_center.tolist(), p1.tolist(), p0.tolist()]
        norms += [[0.0, -1.0, 0.0]] * 3
        uvs += [[0.5, 0.5], [1.0, 0.0], [0.0, 0.0]]
        inds += [base, base + 1, base + 2]

    return np.array(verts, "f"), np.array(norms, "f"), np.array(uvs, "f"), np.array(inds, "i4")


def _compute_normals(verts, inds):
    norms = np.zeros_like(verts, dtype=np.float32)
    if inds.size == 0:
        norms[:, 2] = 1.0
        return norms
    v0 = verts[inds[:, 0]]
    v1 = verts[inds[:, 1]]
    v2 = verts[inds[:, 2]]
    face_norms = np.cross(v1 - v0, v2 - v0)
    np.add.at(norms, inds[:, 0], face_norms)
    np.add.at(norms, inds[:, 1], face_norms)
    np.add.at(norms, inds[:, 2], face_norms)
    lens = np.linalg.norm(norms, axis=1)
    good = lens > 1e-6
    norms[good] /= lens[good][:, None]
    norms[~good, 2] = 1.0
    return norms


def _coerce_mesh_arrays(verts, norms, uvs, inds):
    v = np.asarray(verts, dtype=np.float32)
    assert v.ndim == 2 and v.shape[1] == 3, "verts must be (N,3)"

    ind = np.asarray(inds)
    assert ind.size > 0, "indices must be non-empty"
    if ind.ndim == 1:
        assert ind.size % 3 == 0, "indices length must be multiple of 3"
        ind = ind.reshape((-1, 3))
    assert ind.ndim == 2 and ind.shape[1] == 3, "indices must be (M,3)"
    assert ind.min() >= 0, "indices must be non-negative"
    ind_max = int(ind.max())
    ind_dtype = np.uint16 if ind_max < 65536 else np.uint32
    ind = ind.astype(ind_dtype, copy=False)

    if norms is None or (hasattr(norms, "__len__") and len(norms) == 0):
        n = _compute_normals(v, ind)
    else:
        n = np.asarray(norms, dtype=np.float32)
        assert n.ndim == 2 and n.shape[1] == 3, "normals must be (N,3)"
        assert n.shape[0] == v.shape[0], "normals must match verts"

    if uvs is None or (hasattr(uvs, "__len__") and len(uvs) == 0):
        uv = np.zeros((v.shape[0], 2), dtype=np.float32)
    else:
        uv = np.asarray(uvs, dtype=np.float32)
        assert uv.ndim == 2 and uv.shape[1] == 2, "uvs must be (N,2)"
        assert uv.shape[0] == v.shape[0], "uvs must match verts"

    return v, n, uv, ind


class SynthObject:
    def __init__(self, shape, base_verts, base_norms, base_pos, color, seed, wire_shape=None, wire_line_inds=None):
        self.shape = shape
        self.wire_shape = wire_shape
        self.wire_line_inds = wire_line_inds
        self.active = True
        self.pos = np.array(base_pos, dtype=np.float32)
        self.base_pos = np.array(base_pos, dtype=np.float32)
        self.rot = np.zeros(3, dtype=np.float32)
        self.scale = 1.0
        self.color = color
        self.last_draw_pos = self.base_pos.copy()
        self.last_draw_rot = self.rot.copy()
        self.last_draw_scale = self.scale

        self.lfo_scale = LFO()
        self.lfo_move = LFO()
        self.lfo_rot = LFO()
        self.needs_rearm_scale = False
        self.needs_rearm_move = False
        self.needs_rearm_rot = False

        self.sculpt_mode = False
        self.zone_strengths = np.zeros(8, dtype=np.float32)
        self.sculpt_layers = np.zeros(8, dtype=np.float32)
        self.boil_amount = 0.0
        self.boil_speed = 1.0
        self.sculpt_strength = 0.35

        self.base_verts = base_verts.astype(np.float32)
        self.working_verts = self.base_verts.copy()
        self.base_norms = base_norms.astype(np.float32)
        self.bound_radius = float(np.max(np.linalg.norm(self.base_verts, axis=1)))

        self.zone_ids = (
            (self.base_verts[:, 0] > 0).astype(np.int8)
            + (self.base_verts[:, 1] > 0).astype(np.int8) * 2
            + (self.base_verts[:, 2] > 0).astype(np.int8) * 4
        )
        self.zone_indices = [np.where(self.zone_ids == i)[0] for i in range(8)]
        self.zone_dirs = self._compute_zone_dirs()

        rng = np.random.RandomState(seed)
        self.boil_seeds = rng.uniform(0.0, TAU, size=self.base_verts.shape).astype(np.float32)
        self.crackle_seeds = rng.uniform(0.0, TAU, size=self.base_verts.shape[0]).astype(np.float32)
        self.drift_seeds = rng.uniform(0.0, TAU, size=self.base_verts.shape[0]).astype(np.float32)
        drift_dirs = rng.normal(size=self.base_verts.shape).astype(np.float32)
        drift_norm = np.linalg.norm(drift_dirs, axis=1, keepdims=True) + 1e-6
        self.drift_dirs = drift_dirs / drift_norm
        bias_dir = rng.normal(size=3).astype(np.float32)
        self.bias_dir = bias_dir / (np.linalg.norm(bias_dir) + 1e-6)
        self.boil_scale = 0.08

        self.move_axis = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        self.move_lfo_mode = "none"
        self.orbit_amp_x = 0.0
        self.orbit_amp_y = 0.0
        self.rot_axis = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        self.collision_dir = np.array([0.0, 0.0, 1.0], dtype=np.float32)
        self.collision_strength = 0.0

    def _compute_zone_dirs(self):
        dirs = np.zeros((8, 3), dtype=np.float32)
        for i in range(8):
            idx = self.zone_indices[i]
            if idx.size > 0:
                centroid = np.mean(self.base_verts[idx], axis=0)
                norm = np.linalg.norm(centroid)
                if norm > 1e-6:
                    dirs[i] = centroid / norm
                    continue
            sx = 1.0 if (i & 1) else -1.0
            sy = 1.0 if (i & 2) else -1.0
            sz = 1.0 if (i & 4) else -1.0
            v = np.array([sx, sy, sz], dtype=np.float32)
            dirs[i] = v / (np.linalg.norm(v) + 1e-6)
        return dirs

    def enter_sculpt_mode(self):
        self.sculpt_mode = True

    def exit_sculpt_mode(self):
        self.sculpt_mode = False

    def reset_deformation(self):
        self.zone_strengths[:] = 0.0
        self.sculpt_layers[:] = 0.0
        self.boil_amount = 0.0
        self.boil_speed = 1.0
        self.collision_strength = 0.0

    def reset_transforms(self):
        self.pos[:] = self.base_pos
        self.rot[:] = 0.0
        self.scale = 1.0
        self.last_draw_pos[:] = self.base_pos
        self.last_draw_rot[:] = 0.0
        self.last_draw_scale = self.scale

    def reset_lfos(self):
        self.lfo_scale.enabled = False
        self.lfo_scale.amp = 0.0
        self.lfo_scale.phase = 0.0
        self.lfo_move.enabled = False
        self.lfo_move.amp = 0.0
        self.lfo_move.phase = 0.0
        self.orbit_amp_x = 0.0
        self.orbit_amp_y = 0.0
        self.lfo_rot.enabled = False
        self.lfo_rot.amp = 0.0
        self.lfo_rot.phase = 0.0

    def set_transform_from_controls(self, midi_state, obj_idx):
        ch_a, ch_b = object_channel_indices(obj_idx)
        k_a = midi_state.knobs_f[ch_a]
        k_b = midi_state.knobs_f[ch_b]
        f_a = midi_state.faders_f[ch_a]
        f_b = midi_state.faders_f[ch_b]

        x_norm = norm_bipolar(k_a)
        y_norm = norm_bipolar(k_b)
        z_norm = norm_bipolar(f_a)
        rot_norm = norm_bipolar(f_b)

        z_depth = (1.0 - z_norm) * 0.5
        xy_scale = 1.0 + FAR_XY_BOOST * z_depth
        x_range = BASE_X_RANGE * xy_scale
        y_range = BASE_Y_RANGE * xy_scale

        pos_x = x_norm * x_range
        pos_y = y_norm * y_range
        pos_z = z_norm * BASE_Z_RANGE
        rot_y = rot_norm * ROT_Y_RANGE

        x_frac = abs(pos_x) / x_range if x_range > 1e-6 else 0.0
        y_frac = abs(pos_y) / y_range if y_range > 1e-6 else 0.0
        xy_mag = min(1.0, (x_frac + y_frac) * 0.5)
        if xy_mag < 0.5:
            bonus = (0.5 - xy_mag) / 0.5
            pos_z += bonus * (BASE_Z_RANGE * 0.5)
        pos_z = clamp(pos_z, -BASE_Z_RANGE, BASE_Z_RANGE * 1.5)

        self.rot[0] = 0.0
        self.rot[1] = rot_y
        self.rot[2] = 0.0
        self.pos[0] = self.base_pos[0] + pos_x
        self.pos[1] = self.base_pos[1] + pos_y
        self.pos[2] = self.base_pos[2] + pos_z

    def set_lfo_params_from_controls(self, midi_state, obj_idx, target):
        ch_a, ch_b = object_channel_indices(obj_idx)
        k_a = midi_state.knobs_f[ch_a]
        k_b = midi_state.knobs_f[ch_b]
        if target == "scale":
            freq = remap01(k_a, 0.05, 2.2)
            amp = remap01(k_b, 0.0, 0.6)
            self.lfo_scale.freq_hz = freq
            self.lfo_scale.amp = amp
        elif target == "move":
            f_a = midi_state.faders_f[ch_a]
            f_b = midi_state.faders_f[ch_b]
            if self.move_lfo_mode == "orbit":
                freq = remap01((k_a + k_b) * 0.5, 0.05, 2.2)
                amp_x = remap01(f_a, 0.0, 0.8) * (0.25 + 0.75 * k_a)
                amp_y = remap01(f_b, 0.0, 0.8) * (0.25 + 0.75 * k_b)
                self.orbit_amp_x = amp_x
                self.orbit_amp_y = amp_y
                self.lfo_move.freq_hz = freq
                self.lfo_move.amp = max(self.orbit_amp_x, self.orbit_amp_y, 1e-6)
            elif self.move_lfo_mode == "x":
                freq = remap01(k_a, 0.05, 2.2)
                amp = remap01(f_a, 0.0, 0.8)
                self.lfo_move.freq_hz = freq
                self.lfo_move.amp = amp
            elif self.move_lfo_mode == "y":
                freq = remap01(k_b, 0.05, 2.2)
                amp = remap01(f_b, 0.0, 0.8)
                self.lfo_move.freq_hz = freq
                self.lfo_move.amp = amp
            else:
                freq = remap01(k_a, 0.05, 2.2)
                amp = remap01(k_b, 0.0, 0.8)
                self.lfo_move.freq_hz = freq
                self.lfo_move.amp = amp
        elif target == "rot":
            freq = remap01(k_a, 0.05, 2.2)
            amp = remap01(k_b, 0.0, 90.0)
            self.lfo_rot.freq_hz = freq
            self.lfo_rot.amp = amp

    def set_sculpt_params_from_controls(self, midi_state, obj_idx):
        for i in range(8):
            self.zone_strengths[i] = norm_bipolar(midi_state.faders_f[i])
        for i in range(8):
            self.sculpt_layers[i] = midi_state.knobs_f[i]
        self.boil_amount = self.sculpt_layers[0]
        self.boil_speed = remap01(self.sculpt_layers[0], 0.3, 2.5)

    def _apply_zone_deform(self, v):
        if not np.any(self.zone_strengths):
            return
        for i in range(8):
            s = self.zone_strengths[i]
            if abs(s) < 1e-4:
                continue
            idx = self.zone_indices[i]
            if idx.size == 0:
                continue
            v[idx] += self.zone_dirs[i] * (s * self.sculpt_strength)

    def _apply_boil(self, v, t):
        if self.boil_amount < 1e-4:
            return
        phase = t * self.boil_speed
        noise = (
            np.sin(self.boil_seeds[:, 0] + phase)
            + np.sin(self.boil_seeds[:, 1] * 1.3 + phase * 1.7)
            + np.sin(self.boil_seeds[:, 2] * 2.1 + phase * 0.7)
        ) * (1.0 / 3.0)
        v += self.base_norms * (noise[:, None] * (self.boil_amount * self.boil_scale))

    def _apply_sculpt_layers(self, v, t):
        layers = self.sculpt_layers
        boil = layers[0]
        if boil > 1e-4:
            self.boil_amount = boil
            self.boil_speed = remap01(boil, 0.3, 2.5)
            self._apply_boil(v, t)

        crackle = layers[1]
        if crackle > 1e-4:
            phase = t * 12.0
            noise = np.sign(np.sin(self.crackle_seeds + phase))
            v += self.base_norms * (noise[:, None] * (crackle * 0.06))

        twist = unit_to_bipolar(layers[2]) * 0.7
        if abs(twist) > 1e-4:
            x = v[:, 0].copy()
            z = v[:, 2].copy()
            angle = twist * v[:, 1]
            c = np.cos(angle)
            s = np.sin(angle)
            v[:, 0] = x * c - z * s
            v[:, 2] = x * s + z * c

        shear = unit_to_bipolar(layers[3]) * 0.45
        if abs(shear) > 1e-4:
            v[:, 0] += shear * v[:, 1]

        pulse = layers[4]
        if pulse > 1e-4:
            swell = math.sin(t * 1.6)
            v += self.base_norms * (swell * pulse * 0.18)

        curl = unit_to_bipolar(layers[5]) * 1.1
        if abs(curl) > 1e-4:
            x = v[:, 0].copy()
            z = v[:, 2].copy()
            radius = np.sqrt(x * x + z * z)
            angle = curl * radius
            c = np.cos(angle)
            s = np.sin(angle)
            v[:, 0] = x * c - z * s
            v[:, 2] = x * s + z * c

        drift = layers[6]
        if drift > 1e-4:
            phase = t * 0.6
            drift_scale = np.sin(self.drift_seeds + phase)
            v += self.drift_dirs * (drift_scale[:, None] * (drift * 0.12))

        bias = unit_to_bipolar(layers[7]) * 0.28
        if abs(bias) > 1e-4:
            v += self.bias_dir * bias

    def _apply_collision_deform(self, v, dt):
        if self.collision_strength < 1e-4:
            return
        dots = self.zone_dirs @ self.collision_dir
        zone = int(np.argmax(dots))
        idx = self.zone_indices[zone]
        if idx.size > 0:
            v[idx] -= self.zone_dirs[zone] * (self.collision_strength * 0.8)
        decay = max(0.0, 1.0 - dt * 4.0)
        self.collision_strength *= decay

    def trigger_collision(self, direction, strength):
        if strength <= self.collision_strength:
            return
        self.collision_strength = strength
        self.collision_dir = direction

    def update(self, t, dt):
        if not self.active:
            return

        v = self.working_verts
        v[:] = self.base_verts

        self._apply_zone_deform(v)
        self._apply_collision_deform(v, dt)
        self._apply_sculpt_layers(v, t)

        if self.shape is not None:
            self.shape.buf[0].re_init(pts=v)
        if self.wire_shape is not None and self.wire_line_inds is not None:
            wire_pts = v[self.wire_line_inds]
            self.wire_shape.buf[0].re_init(pts=wire_pts)

        lfo_scale = self.lfo_scale.tick(t, dt)
        lfo_move = self.lfo_move.tick(t, dt)
        lfo_rot = self.lfo_rot.tick(t, dt)

        scale = max(0.05, self.scale * (1.0 + lfo_scale))
        pos_x = self.pos[0]
        pos_y = self.pos[1]
        pos_z = self.pos[2]
        if self.lfo_move.enabled:
            if self.move_lfo_mode == "orbit":
                phase = self.lfo_move.phase
                pos_x += self.orbit_amp_x * math.sin(phase)
                pos_y += self.orbit_amp_y * math.cos(phase)
            elif self.move_lfo_mode == "x":
                pos_x += lfo_move
            elif self.move_lfo_mode == "y":
                pos_y += lfo_move

        rot_x = self.rot[0] + self.rot_axis[0] * lfo_rot
        rot_y = self.rot[1] + self.rot_axis[1] * lfo_rot
        rot_z = self.rot[2] + self.rot_axis[2] * lfo_rot

        self.last_draw_pos[0] = pos_x
        self.last_draw_pos[1] = pos_y
        self.last_draw_pos[2] = pos_z
        self.last_draw_rot[0] = rot_x
        self.last_draw_rot[1] = rot_y
        self.last_draw_rot[2] = rot_z
        self.last_draw_scale = scale

        if self.shape is not None:
            self.shape.position(pos_x, pos_y, pos_z)
            self.shape.rotateToX(rot_x)
            self.shape.rotateToY(rot_y)
            self.shape.rotateToZ(rot_z)
            self.shape.scale(scale, scale, scale)
        if self.wire_shape is not None:
            self.wire_shape.position(pos_x, pos_y, pos_z)
            self.wire_shape.rotateToX(rot_x)
            self.wire_shape.rotateToY(rot_y)
            self.wire_shape.rotateToZ(rot_z)
            self.wire_shape.scale(scale, scale, scale)

    def draw(self):
        if not self.active:
            return
        if self.shape is not None:
            self.shape.draw()
        if self.wire_shape is not None:
            self.wire_shape.draw()


def object_channel_indices(obj_idx):
    base = obj_idx * 2
    return base, base + 1


def object_button_states(midi_state, obj_idx):
    ch_a, ch_b = object_channel_indices(obj_idx)
    s_active = midi_state.s_buttons[ch_a] or midi_state.s_buttons[ch_b]
    m_active = midi_state.m_buttons[ch_a] or midi_state.m_buttons[ch_b]
    r_active = midi_state.r_buttons[ch_a] or midi_state.r_buttons[ch_b]
    return s_active, m_active, r_active


def make_mesh(kind):
    if kind == 1:
        v, n, uv, ind = gen_uvsphere(12, 8, 1.0)
    elif kind == 2:
        v, n, uv, ind = gen_box(1.4)
    elif kind == 3:
        v, n, uv, ind = gen_cone(8, 0.6, 1.2)
    else:
        v, n, uv, ind = gen_cylinder(10, 0.6, 1.2)
    return v, n, uv, ind


def _tri_indices_to_lines(inds):
    if inds is None:
        return None
    ind = np.asarray(inds)
    if ind.size == 0:
        return None
    if ind.ndim == 2:
        ind = ind.reshape(-1)
    line_inds = []
    for i in range(0, len(ind), 3):
        a = int(ind[i])
        b = int(ind[i + 1])
        c = int(ind[i + 2])
        line_inds.extend([a, b, b, c, c, a])
    return np.array(line_inds, dtype=np.int32)


def _rotation_matrix_xyz(rot_deg):
    rx, ry, rz = np.radians(rot_deg)
    cx = math.cos(rx)
    sx = math.sin(rx)
    cy = math.cos(ry)
    sy = math.sin(ry)
    cz = math.cos(rz)
    sz = math.sin(rz)
    rxm = np.array([[1.0, 0.0, 0.0], [0.0, cx, -sx], [0.0, sx, cx]], dtype=np.float32)
    rym = np.array([[cy, 0.0, sy], [0.0, 1.0, 0.0], [-sy, 0.0, cy]], dtype=np.float32)
    rzm = np.array([[cz, -sz, 0.0], [sz, cz, 0.0], [0.0, 0.0, 1.0]], dtype=np.float32)
    return rzm @ rym @ rxm


def _transform_vertices(verts, pos, rot_deg, scale):
    v = verts * scale
    r = _rotation_matrix_xyz(rot_deg)
    v = v @ r.T
    v = v + pos
    return v


def resolve_intent(midi_state, sculpt_mode, sculpt_target_index):
    modes = []
    for i in range(NUM_OBJECTS):
        if sculpt_mode and sculpt_target_index == i:
            modes.append(MODE_SCULPT)
            continue
        s_active, m_active, r_active = object_button_states(midi_state, i)
        if s_active and not (m_active or r_active):
            modes.append(MODE_LFO_SCALE)
        elif m_active and not (s_active or r_active):
            modes.append(MODE_LFO_MOVE)
        elif r_active and not (s_active or m_active):
            modes.append(MODE_LFO_ROT)
        else:
            modes.append(MODE_NORMAL)
    return modes


def update_sculpt_state(midi_state, sculpt_mode, sculpt_target_index, sculpt_rearm):
    if sculpt_rearm:
        if not midi_state.cycle:
            sculpt_rearm = False
        sculpt_mode = False
    else:
        if button_rising(midi_state.cycle, midi_state.cycle_prev):
            if sculpt_target_index is None:
                sculpt_target_index = 0
        if button_falling(midi_state.cycle, midi_state.cycle_prev):
            sculpt_target_index = None
        sculpt_mode = midi_state.cycle

    if sculpt_mode and not sculpt_rearm:
        if sculpt_target_index is None:
            sculpt_target_index = 0
        if button_rising(midi_state.track_left, midi_state.track_left_prev):
            sculpt_target_index = (sculpt_target_index - 1) % NUM_OBJECTS
        if button_rising(midi_state.track_right, midi_state.track_right_prev):
            sculpt_target_index = (sculpt_target_index + 1) % NUM_OBJECTS
    return sculpt_mode, sculpt_target_index, sculpt_rearm


def update_active_toggles(midi_state, active_toggles):
    for i in range(min(NUM_OBJECTS, len(midi_state.transport))):
        if button_rising(midi_state.transport[i], midi_state.transport_prev[i]):
            active_toggles[i] = not active_toggles[i]
    return active_toggles


def apply_reset(objects, midi_state):
    for i, obj in enumerate(objects):
        obj.reset_deformation()
        obj.reset_transforms()
        obj.reset_lfos()
        s_active, m_active, r_active = object_button_states(midi_state, i)
        obj.needs_rearm_scale = s_active
        obj.needs_rearm_move = m_active
        obj.needs_rearm_rot = r_active


def apply_controls(objects, midi_state, active_toggles, sculpt_mode, sculpt_target_index):
    modes = resolve_intent(midi_state, sculpt_mode, sculpt_target_index)
    for i, obj in enumerate(objects):
        obj.active = active_toggles[i]
        if sculpt_mode and sculpt_target_index == i:
            obj.enter_sculpt_mode()
        else:
            obj.exit_sculpt_mode()

        ch_a, ch_b = object_channel_indices(i)
        m_a = midi_state.m_buttons[ch_a]
        m_b = midi_state.m_buttons[ch_b]
        s_active, m_active, r_active = object_button_states(midi_state, i)
        if obj.needs_rearm_scale and not s_active:
            obj.needs_rearm_scale = False
        if obj.needs_rearm_move and not m_active:
            obj.needs_rearm_move = False
        if obj.needs_rearm_rot and not r_active:
            obj.needs_rearm_rot = False

        obj.lfo_scale.enabled = s_active and not obj.needs_rearm_scale
        obj.lfo_move.enabled = m_active and not obj.needs_rearm_move
        obj.lfo_rot.enabled = r_active and not obj.needs_rearm_rot
        if m_a and m_b:
            obj.move_lfo_mode = "orbit"
        elif m_a:
            obj.move_lfo_mode = "x"
        elif m_b:
            obj.move_lfo_mode = "y"
        else:
            obj.move_lfo_mode = "none"

        mode = modes[i]
        if sculpt_mode:
            if mode == MODE_SCULPT and sculpt_target_index is not None:
                obj.set_sculpt_params_from_controls(midi_state, i)
            continue

        if mode == MODE_NORMAL:
            obj.set_transform_from_controls(midi_state, i)
        elif mode == MODE_LFO_SCALE and obj.lfo_scale.enabled:
            obj.set_lfo_params_from_controls(midi_state, i, "scale")
        elif mode == MODE_LFO_MOVE and obj.lfo_move.enabled:
            obj.set_lfo_params_from_controls(midi_state, i, "move")
        elif mode == MODE_LFO_ROT and obj.lfo_rot.enabled:
            obj.set_lfo_params_from_controls(midi_state, i, "rot")
    return active_toggles, modes


def lfo_state_summary(midi_state):
    parts = []
    for i in range(NUM_OBJECTS):
        s_active, m_active, r_active = object_button_states(midi_state, i)
        parts.append(
            "{}{}{}".format("S" if s_active else "-", "M" if m_active else "-", "R" if r_active else "-")
        )
    return " ".join(parts)


def run_desktop_preview():
    try:
        global pyglet
        if pyglet is None:
            import pyglet as _pyglet
            _pyglet.options["gl_legacy"] = True
            pyglet = _pyglet
        from pyglet.gl import gl
    except Exception as exc:
        print("pyglet import failed ({}). Install with: pip install pyglet".format(exc))
        run_headless_mac()
        return

    window = pyglet.window.Window(1280, 720, caption="Puddy3D Preview", resizable=True)
    gl.glClearColor(0.02, 0.02, 0.02, 1.0)
    gl.glEnable(gl.GL_DEPTH_TEST)
    gl.glEnable(gl.GL_BLEND)
    gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
    gl.glPointSize(2.0)

    # Fog parameters for subtle retro depth shading without crushing contrast.
    FOG_NEAR = 1.8
    FOG_FAR = 5.8
    FOG_COLOR = (0.10, 0.10, 0.12)
    FOG_MIN_BRIGHTNESS = 0.52
    WIRE_MIN_ALPHA = 0.38

    names = list_midi_inputs()
    port = open_nanoport(names=names, quiet_no_input=True)
    midi_state = MidiState()

    colors = [
        (0.9, 0.6, 0.2, 1.0),
        (0.2, 0.8, 0.9, 1.0),
        (0.9, 0.3, 0.5, 1.0),
        (0.6, 0.9, 0.4, 1.0),
    ]
    base_positions = [
        (-0.7, 0.0, 0.0),
        (0.7, 0.0, 0.0),
        (0.0, 0.0, -0.7),
        (0.0, 0.0, 0.7),
    ]
    shapes = [1, 2, 3, 4]

    objects = []
    mesh_tris = []
    mesh_lines = []
    mesh_colors = []
    for i in range(NUM_OBJECTS):
        v, n, _uv, ind = make_mesh(shapes[i])
        obj = SynthObject(
            None,
            v,
            n,
            base_positions[i],
            colors[i],
            seed=1000 + i,
        )
        obj.lfo_scale.freq_hz = 0.5
        obj.lfo_move.freq_hz = 0.4
        obj.lfo_rot.freq_hz = 0.3
        objects.append(obj)
        if ind is not None and len(ind) > 0:
            mesh_tris.append(np.asarray(ind).reshape(-1).astype(np.int32))
        else:
            mesh_tris.append(None)
        mesh_lines.append(_tri_indices_to_lines(ind))
        mesh_colors.append(colors[i])

    label = pyglet.text.Label(
        "",
        font_name="Courier",
        font_size=12,
        x=10,
        y=window.height - 10,
        anchor_x="left",
        anchor_y="top",
        color=(255, 255, 255, 255),
    )

    render_solid = True
    render_wire = True
    camera_z = 4.0
    overlap_pair_colors = {
        (0, 1): (1.0, 0.25, 0.25, 0.9),
        (0, 2): (1.0, 0.7, 0.2, 0.9),
        (0, 3): (1.0, 0.2, 0.8, 0.9),
        (1, 2): (0.2, 1.0, 0.25, 0.9),
        (1, 3): (0.2, 1.0, 1.0, 0.9),
        (2, 3): (0.45, 0.35, 1.0, 0.9),
    }

    start_time = time.time()
    last_time = start_time
    fps_smooth = 0.0
    active_state = [True] * NUM_OBJECTS
    sculpt_mode = False
    sculpt_target_index = None
    active_toggles = [True] * NUM_OBJECTS
    sculpt_rearm = False


    @window.event
    def on_close():
        if port:
            port.close()
        pyglet.app.exit()

    @window.event
    def on_key_press(symbol, modifiers):
        nonlocal render_solid, render_wire
        if symbol == pyglet.window.key.W:
            render_wire = not render_wire
        elif symbol == pyglet.window.key.F:
            render_solid = not render_solid

    def set_perspective(fov_y_degrees, aspect, z_near, z_far):
        top = z_near * math.tan(math.radians(fov_y_degrees * 0.5))
        bottom = -top
        right = top * aspect
        left = -right
        gl.glFrustum(left, right, bottom, top, z_near, z_far)

    @window.event
    def on_resize(width, height):
        gl.glViewport(0, 0, width, height)
        gl.glMatrixMode(gl.GL_PROJECTION)
        gl.glLoadIdentity()
        aspect = float(width) / float(height) if height else 1.0
        set_perspective(60.0, aspect, 0.1, 100.0)
        gl.glMatrixMode(gl.GL_MODELVIEW)
        gl.glLoadIdentity()
        return pyglet.event.EVENT_HANDLED

    def preview_update(_dt):
        nonlocal last_time, fps_smooth, active_state, sculpt_mode, sculpt_target_index, active_toggles, sculpt_rearm
        now = time.time()
        dt = now - last_time
        last_time = now
        t = now - start_time
        if dt <= 0.0:
            dt = 1.0 / 60.0

        midi_state.capture_prev()
        if port:
            for msg in port.iter_pending():
                if PRINT_MIDI:
                    print(msg)
                midi_state.update_from_msg(msg)
        midi_state.tick(dt)

        if button_rising(midi_state.rec_transport, midi_state.rec_transport_prev):
            apply_reset(objects, midi_state)
            sculpt_mode = False
            sculpt_target_index = None
            sculpt_rearm = False
            active_toggles = [True] * NUM_OBJECTS

        active_toggles = update_active_toggles(midi_state, active_toggles)
        sculpt_mode, sculpt_target_index, sculpt_rearm = update_sculpt_state(
            midi_state, sculpt_mode, sculpt_target_index, sculpt_rearm
        )
        active_state, modes = apply_controls(
            objects, midi_state, active_toggles, sculpt_mode, sculpt_target_index
        )

        for obj in objects:
            obj.update(t, dt)

        inst_fps = 1.0 / dt if dt > 0.0 else 0.0
        fps_smooth = fps_smooth * 0.9 + inst_fps * 0.1
        active_list = [str(i + 1) for i, a in enumerate(active_state) if a]
        active_text = ",".join(active_list) if active_list else "none"
        sculpt_label = "none"
        if sculpt_mode and sculpt_target_index is not None:
            sculpt_label = "ON {}".format(sculpt_target_index + 1)
        elif sculpt_mode:
            sculpt_label = "ON"
        lfo_summary = lfo_state_summary(midi_state)
        label.text = "FPS: {:.1f}\nActive: {}\nSculpt: {}\nLFO: {}".format(
            fps_smooth, active_text, sculpt_label, lfo_summary
        )
        label.y = window.height - 10

    @window.event
    def on_draw():
        window.clear()
        gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
        gl.glMatrixMode(gl.GL_MODELVIEW)
        gl.glLoadIdentity()
        gl.glTranslatef(0.0, 0.0, -camera_z)

        # Helper for depth fog using distance from camera
        def fog_factor(z_world):
            dist = camera_z - z_world
            fog = (FOG_FAR - dist) / (FOG_FAR - FOG_NEAR)
            return np.clip(fog, 0.0, 1.0)

        def fogged_rgb(rgb, fog):
            brightness = FOG_MIN_BRIGHTNESS + (1.0 - FOG_MIN_BRIGHTNESS) * fog
            return (
                rgb[0] * brightness + FOG_COLOR[0] * (1.0 - brightness),
                rgb[1] * brightness + FOG_COLOR[1] * (1.0 - brightness),
                rgb[2] * brightness + FOG_COLOR[2] * (1.0 - brightness),
            )

        transformed_verts = [None] * NUM_OBJECTS
        for i, obj in enumerate(objects):
            if not obj.active:
                continue
            verts = _transform_vertices(
                obj.working_verts, obj.last_draw_pos, obj.last_draw_rot, obj.last_draw_scale
            )
            transformed_verts[i] = verts
            tri_inds = mesh_tris[i]
            line_inds = mesh_lines[i]
            rgb = mesh_colors[i][:3]
            if render_solid and tri_inds is not None:
                gl.glEnable(gl.GL_POLYGON_OFFSET_FILL)
                gl.glPolygonOffset(1.0, 1.0)
                tri_verts = verts[tri_inds].astype(np.float32).ravel()
                count = len(tri_inds)
                z_avg = np.mean(verts[tri_inds][:, 2])
                f = fog_factor(z_avg)
                color_list = fogged_rgb(rgb, f) * count
                pyglet.graphics.draw(
                    count, gl.GL_TRIANGLES, ("v3f", tri_verts), ("c3f", color_list)
                )
                gl.glDisable(gl.GL_POLYGON_OFFSET_FILL)

            if render_wire and line_inds is not None:
                line_vertices = verts[line_inds].astype(np.float32)
                count = len(line_inds)
                fog = fog_factor(line_vertices[:, 2])
                brightness = FOG_MIN_BRIGHTNESS + (1.0 - FOG_MIN_BRIGHTNESS) * fog
                color_list = np.empty((count, 4), dtype=np.float32)
                color_list[:, 0] = rgb[0] * brightness + FOG_COLOR[0] * (1.0 - brightness)
                color_list[:, 1] = rgb[1] * brightness + FOG_COLOR[1] * (1.0 - brightness)
                color_list[:, 2] = rgb[2] * brightness + FOG_COLOR[2] * (1.0 - brightness)
                color_list[:, 3] = WIRE_MIN_ALPHA + (1.0 - WIRE_MIN_ALPHA) * fog
                line_verts = line_vertices.ravel()
                pyglet.graphics.draw(
                    count, gl.GL_LINES, ("v3f", line_verts), ("c4f", color_list.ravel())
                )
            elif render_wire and line_inds is None:
                point_verts = verts.astype(np.float32).ravel()
                count = len(verts)
                fog = fog_factor(verts[:, 2])
                brightness = FOG_MIN_BRIGHTNESS + (1.0 - FOG_MIN_BRIGHTNESS) * fog
                color_list = np.empty((count, 4), dtype=np.float32)
                color_list[:, 0] = rgb[0] * brightness + FOG_COLOR[0] * (1.0 - brightness)
                color_list[:, 1] = rgb[1] * brightness + FOG_COLOR[1] * (1.0 - brightness)
                color_list[:, 2] = rgb[2] * brightness + FOG_COLOR[2] * (1.0 - brightness)
                color_list[:, 3] = WIRE_MIN_ALPHA + (1.0 - WIRE_MIN_ALPHA) * fog
                pyglet.graphics.draw(
                    count, gl.GL_POINTS, ("v3f", point_verts), ("c4f", color_list.ravel())
                )

        # Pair-overlap markers using unique third colors per pair.
        gl.glPointSize(7.0)
        for i in range(NUM_OBJECTS):
            if not objects[i].active:
                continue
            for j in range(i + 1, NUM_OBJECTS):
                if not objects[j].active:
                    continue
                pos_i = objects[i].last_draw_pos
                pos_j = objects[j].last_draw_pos
                delta = pos_j - pos_i
                dist = float(np.linalg.norm(delta))
                if dist < 1e-6:
                    continue
                r_i = objects[i].bound_radius * objects[i].last_draw_scale
                r_j = objects[j].bound_radius * objects[j].last_draw_scale
                overlap = (r_i + r_j) - dist
                if overlap <= 0.0:
                    continue
                t = (r_i - 0.5 * overlap) / (dist + 1e-6)
                center = pos_i + delta * np.clip(t, 0.0, 1.0)
                c = overlap_pair_colors.get((i, j), (1.0, 1.0, 1.0, 0.9))
                marker = np.array(
                    [
                        center + np.array([0.0, 0.0, 0.0], dtype=np.float32),
                        center + np.array([0.04, 0.0, 0.0], dtype=np.float32),
                        center + np.array([-0.04, 0.0, 0.0], dtype=np.float32),
                        center + np.array([0.0, 0.04, 0.0], dtype=np.float32),
                        center + np.array([0.0, -0.04, 0.0], dtype=np.float32),
                    ],
                    dtype=np.float32,
                )
                marker_colors = np.array([c] * len(marker), dtype=np.float32)
                pyglet.graphics.draw(
                    len(marker),
                    gl.GL_POINTS,
                    ("v3f", marker.ravel()),
                    ("c4f", marker_colors.ravel()),
                )
        gl.glPointSize(2.0)

        label.draw()

    pyglet.clock.schedule_interval(preview_update, 1.0 / 60.0)
    pyglet.app.run()


def run_headless_mac():
    names = list_midi_inputs()
    port = open_nanoport(names=names, quiet_no_input=True)
    midi_state = MidiState()

    colors = [
        (0.9, 0.6, 0.2, 1.0),
        (0.2, 0.8, 0.9, 1.0),
        (0.9, 0.3, 0.5, 1.0),
        (0.6, 0.9, 0.4, 1.0),
    ]
    base_positions = [
        (-0.7, 0.0, 0.0),
        (0.7, 0.0, 0.0),
        (0.0, 0.0, -0.7),
        (0.0, 0.0, 0.7),
    ]
    shapes = [1, 2, 3, 4]

    objects = []
    for i in range(NUM_OBJECTS):
        v, n, _uv, _ind = make_mesh(shapes[i])
        obj = SynthObject(
            None,
            v,
            n,
            base_positions[i],
            colors[i],
            seed=1000 + i,
        )
        obj.lfo_scale.freq_hz = 0.5
        obj.lfo_move.freq_hz = 0.4
        obj.lfo_rot.freq_hz = 0.3
        objects.append(obj)

    sculpt_mode = False
    sculpt_target_index = None
    active_toggles = [True] * NUM_OBJECTS
    sculpt_rearm = False

    start_time = time.time()
    last_time = start_time
    last_status = 0.0
    status_interval = 1.0 / float(HEADLESS_STATUS_HZ) if HEADLESS_STATUS_HZ > 0 else None
    midi_msgs_since_status = 0

    try:
        while True:
            loop_start = time.time()
            dt = loop_start - last_time
            last_time = loop_start
            t = loop_start - start_time
            if dt <= 0.0:
                dt = 1.0 / 60.0

            midi_state.capture_prev()
            if port:
                for msg in port.iter_pending():
                    if PRINT_MIDI:
                        print(msg)
                    midi_state.update_from_msg(msg)
                    midi_msgs_since_status += 1
            midi_state.tick(dt)

            if button_rising(midi_state.rec_transport, midi_state.rec_transport_prev):
                apply_reset(objects, midi_state)
                sculpt_mode = False
                sculpt_target_index = None
                sculpt_rearm = False
                active_toggles = [True] * NUM_OBJECTS

            active_toggles = update_active_toggles(midi_state, active_toggles)
            sculpt_mode, sculpt_target_index, sculpt_rearm = update_sculpt_state(
                midi_state, sculpt_mode, sculpt_target_index, sculpt_rearm
            )
            active, modes = apply_controls(
                objects, midi_state, active_toggles, sculpt_mode, sculpt_target_index
            )

            for obj in objects:
                obj.update(t, dt)

            if status_interval and (t - last_status) >= status_interval:
                active_list = [str(i + 1) for i, a in enumerate(active) if a]
                active_text = ",".join(active_list) if active_list else "none"
                sculpt_label = "none"
                if sculpt_mode and sculpt_target_index is not None:
                    sculpt_label = "on:{}".format(sculpt_target_index + 1)
                elif sculpt_mode:
                    sculpt_label = "on"
                obj0 = objects[0] if objects else None
                if obj0 is not None:
                    pos = obj0.last_draw_pos
                    status = (
                        "dt={:.3f} active={} sculpt={} o1pos=({:.2f},{:.2f},{:.2f}) "
                        "scale={:.2f} boil={:.2f} midi={}"
                    ).format(
                        dt,
                        active_text,
                        sculpt_label,
                        pos[0],
                        pos[1],
                        pos[2],
                        obj0.last_draw_scale,
                        obj0.boil_amount,
                        midi_msgs_since_status,
                    )
                else:
                    status = "dt={:.3f} active={} sculpt={} midi={}".format(
                        dt, active_text, sculpt_label, midi_msgs_since_status
                    )
                print(status)
                midi_msgs_since_status = 0
                last_status = t

            if HEADLESS_FPS_LIMIT and HEADLESS_FPS_LIMIT > 0:
                min_frame = 1.0 / float(HEADLESS_FPS_LIMIT)
                elapsed = time.time() - loop_start
                if elapsed < min_frame:
                    time.sleep(min_frame - elapsed)
    except KeyboardInterrupt:
        pass
    finally:
        if port:
            port.close()


def run_pi3d():
    try:
        import pi3d
    except Exception as exc:
        print(
            "pi3d import failed ({}). Set PUDDY_BACKEND=mac to run headless.".format(exc)
        )
        return

    def _load_shader(name, fallback="uv_flat"):
        try:
            return pi3d.Shader(name)
        except Exception as exc:
            print("Shader '{}' unavailable ({}); falling back to '{}'".format(name, exc, fallback))
            return pi3d.Shader(fallback)

    class DeformShape(pi3d.Shape):
        def __init__(
            self,
            verts,
            norms,
            uvs,
            inds,
            shader,
            color,
            camera=None,
            light=None,
            name="deform",
            x=0.0,
            y=0.0,
            z=0.0,
            rx=0.0,
            ry=0.0,
            rz=0.0,
            sx=1.0,
            sy=1.0,
            sz=1.0,
            cx=0.0,
            cy=0.0,
            cz=0.0,
        ):
            super().__init__(
                camera=camera,
                light=light,
                name=name,
                x=x,
                y=y,
                z=z,
                rx=rx,
                ry=ry,
                rz=rz,
                sx=sx,
                sy=sy,
                sz=sz,
                cx=cx,
                cy=cy,
                cz=cz,
            )
            v, n, uv, ind = _coerce_mesh_arrays(verts, norms, uvs, inds)
            self._verts = v
            self._norms = n
            self._uvs = uv
            self._inds = ind
            self.buf = [pi3d.Buffer(self, v, uv, ind, n)]
            self.set_draw_details(shader, [], 1.0, 1.0, 1.0, 1.0)
            self.set_material(color)

    def make_shape(kind, shader, color, camera=None, light=None):
        v, n, uv, ind = make_mesh(kind)
        shape = DeformShape(v, n, uv, ind, shader, color, camera=camera, light=light)
        return shape, shape._verts, shape._norms

    # Helper for wireframe overlay
    def make_wire(kind, color, camera=None):
        v, _n, _uv, ind = make_mesh(kind)
        line_inds = _tri_indices_to_lines(ind)
        if line_inds is None or len(line_inds) == 0:
            return None, None
        pts = v[line_inds]
        w = pi3d.Lines(vertices=pts, material=color, line_width=1.7, strip=False, camera=camera)
        w.set_draw_details(wire_shader, [], 1.7, 1.4, 1.7, 1.3)
        w.set_material(color)
        return w, line_inds

    def make_debug_overlay(display):
        try:
            font = pi3d.Font("fonts/FreeSans.ttf", color=(255, 255, 255, 255))
            cam2d = pi3d.Camera(is_3d=False)
            point_text = pi3d.PointText(font, cam2d, max_chars=128, point_size=24)
            block = pi3d.TextBlock(
                x=-display.width / 2 + 10,
                y=display.height / 2 - 20,
                z=1.0,
                rot=0.0,
                char_count=128,
                text_format="",
            )
            point_text.add_text_block(block)
            return point_text, block
        except Exception:
            return None, None

    display = pi3d.Display.create(x=0, y=0, w=1280, h=720, frames_per_second=60)
    display.set_background(0.02, 0.02, 0.02, 1.0)
    # Enable subtle depth fog to match desktop preview
    if hasattr(display, "set_fog"):
        display.set_fog((0.02, 0.02, 0.02, 1.0), 2.0, 6.0)
    camera = pi3d.Camera(is_3d=True)
    light = pi3d.Light(
        lightpos=(3, 4, 6),
        lightcol=(0.9, 0.9, 0.9),
        lightamb=(0.2, 0.2, 0.2),
    )
    shader = _load_shader("mat_light", fallback="mat_flat")
    wire_shader = _load_shader("mat_flat")

    colors = [
        (0.9, 0.6, 0.2, 1.0),
        (0.2, 0.8, 0.9, 1.0),
        (0.9, 0.3, 0.5, 1.0),
        (0.6, 0.9, 0.4, 1.0),
    ]
    base_positions = [
        (-0.7, 0.0, 0.0),
        (0.7, 0.0, 0.0),
        (0.0, 0.0, -0.7),
        (0.0, 0.0, 0.7),
    ]
    shapes = [1, 2, 3, 4]

    objects = []
    for i in range(NUM_OBJECTS):
        shape, v, n = make_shape(shapes[i], shader, colors[i], camera=camera, light=light)
        wire, line_inds = make_wire(shapes[i], colors[i], camera=camera)
        obj = SynthObject(
            shape,
            v,
            n,
            base_positions[i],
            colors[i],
            seed=1000 + i,
            wire_shape=wire,
            wire_line_inds=line_inds,
        )
        obj.lfo_scale.freq_hz = 0.5
        obj.lfo_move.freq_hz = 0.4
        obj.lfo_rot.freq_hz = 0.3
        objects.append(obj)

    port = open_nanoport()
    midi_state = MidiState()
    kb = pi3d.Keyboard()

    point_text, debug_block = make_debug_overlay(display)
    last_debug = 0.0
    fps = 0.0

    start_time = time.time()
    last_time = start_time
    sculpt_mode = False
    sculpt_target_index = None
    active_toggles = [True] * NUM_OBJECTS
    sculpt_rearm = False

    while display.loop_running():
        now = time.time()
        dt = now - last_time
        last_time = now
        t = now - start_time
        if dt <= 0.0:
            dt = 1.0 / 60.0

        midi_state.capture_prev()
        if port:
            for msg in port.iter_pending():
                if PRINT_MIDI:
                    print(msg)
                midi_state.update_from_msg(msg)
        midi_state.tick(dt)

        k = kb.read()
        if k > -1:
            c = chr(k) if k < 256 else ""
            if k == 27:
                break
            if c in ("r", "R"):
                for obj in objects:
                    obj.reset_deformation()

        if button_rising(midi_state.rec_transport, midi_state.rec_transport_prev):
            apply_reset(objects, midi_state)
            sculpt_mode = False
            sculpt_target_index = None
            sculpt_rearm = False

        active_toggles = update_active_toggles(midi_state, active_toggles)
        sculpt_mode, sculpt_target_index, sculpt_rearm = update_sculpt_state(
            midi_state, sculpt_mode, sculpt_target_index, sculpt_rearm
        )
        active, modes = apply_controls(
            objects, midi_state, active_toggles, sculpt_mode, sculpt_target_index
        )

        camera.reset()
        camera.position((0.0, 0.0, 4.0))
        camera.point_at((0, 0, 0))

        for obj in objects:
            obj.update(t, dt)
            obj.draw()

        if point_text and debug_block and (t - last_debug) > 0.25:
            try:
                inst_fps = 1.0 / dt if dt > 0.0 else 0.0
                fps = fps * 0.9 + inst_fps * 0.1
                sculpt_label = "off"
                if sculpt_mode and sculpt_target_index is not None:
                    sculpt_label = "on:{}".format(sculpt_target_index + 1)
                elif sculpt_mode:
                    sculpt_label = "on"
                active_list = [str(i + 1) for i, a in enumerate(active) if a]
                active_text = ",".join(active_list) if active_list else "none"
                debug_block.set_text(
                    "fps: {:.1f}\nsculpt: {}\nactive: {}".format(fps, sculpt_label, active_text)
                )
                point_text.regen()
                last_debug = t
            except Exception:
                point_text = None
                debug_block = None

        if point_text:
            try:
                point_text.draw()
            except Exception:
                point_text = None
                debug_block = None

    if port:
        port.close()
    kb.close()
    display.destroy()


def main():
    backend = detect_backend()
    if backend == "desktop":
        run_desktop_preview()
    elif backend == "mac":
        run_headless_mac()
    else:
        run_pi3d()


if __name__ == "__main__":
    main()
