#!/usr/bin/env python3
# Puddy3D - Pi3D + Mido (NanoKontrol2)
# Raspberry Pi 3+ friendly
import math
import time
import numpy as np

import pi3d
from mido import get_input_names, open_input

PRINT_MIDI = False
NUM_OBJECTS = 4

# MIDI mapping - ground-truth NanoKontrol2 CCs
MIDI_MAP = {
    "faders": [0, 1, 2, 3, 4, 5, 6, 7],
    "knobs": [16, 17, 18, 19, 20, 21, 22, 23],
    "solo": [32, 33, 34, 35, 36, 37, 38, 39],
    "mute": [48, 49, 50, 51, 52, 53, 54, 55],
    "rec": [64, 65, 66, 67, 68, 69, 70, 71],
    # Transport buttons mapped to object toggles (Obj1..Obj4)
    "transport": [41, 42, 43, 44],
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


def remap01(v, lo, hi):
    return lo + (hi - lo) * v


def unit_to_bipolar(v):
    return v * 2.0 - 1.0


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
        self.phase += dt * self.freq_hz * math.tau
        if self.phase > math.tau:
            self.phase -= math.tau
        return self.amp * math.sin(self.phase)


class MidiState:
    def __init__(self):
        self.knobs = np.full(8, 64, dtype=np.int16)
        self.faders = np.full(8, 64, dtype=np.int16)
        base_unit = 64.0 / 127.0
        self.knobs_f = np.full(8, base_unit, dtype=np.float32)
        self.faders_f = np.full(8, base_unit, dtype=np.float32)
        self.s_buttons = [False] * 8
        self.m_buttons = [False] * 8
        self.r_buttons = [False] * 8
        self.transport = [False] * NUM_OBJECTS
        self._last_cc = {}

    def update_from_msg(self, msg):
        if msg.type != "control_change":
            return
        cc = msg.control
        val = msg.value

        if cc in MIDI_CC_TO_FADER:
            idx = MIDI_CC_TO_FADER[cc]
            self.faders[idx] = val
            self.faders_f[idx] = val * (1.0 / 127.0)
            return
        if cc in MIDI_CC_TO_KNOB:
            idx = MIDI_CC_TO_KNOB[cc]
            self.knobs[idx] = val
            self.knobs_f[idx] = val * (1.0 / 127.0)
            return

        last_val = self._last_cc.get(cc, 0)
        is_press = val > 0 and last_val == 0
        self._last_cc[cc] = val
        if not is_press:
            return

        if cc in MIDI_CC_TO_S:
            idx = MIDI_CC_TO_S[cc]
            self.s_buttons[idx] = not self.s_buttons[idx]
        elif cc in MIDI_CC_TO_M:
            idx = MIDI_CC_TO_M[cc]
            self.m_buttons[idx] = not self.m_buttons[idx]
        elif cc in MIDI_CC_TO_R:
            idx = MIDI_CC_TO_R[cc]
            self.r_buttons[idx] = not self.r_buttons[idx]
        elif cc in MIDI_CC_TO_TRANSPORT:
            idx = MIDI_CC_TO_TRANSPORT[cc]
            self.transport[idx] = not self.transport[idx]


# ---------- MIDI input

def open_nanoport():
    names = get_input_names()
    port_name = None
    for n in names:
        if "nano" in n.lower():
            port_name = n
            break
    if port_name is None and names:
        port_name = names[0]
    if port_name is None:
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
        a0 = (i / float(sides)) * math.tau
        a1 = ((i + 1) / float(sides)) * math.tau
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
        a0 = (i / float(sides)) * math.tau
        a1 = ((i + 1) / float(sides)) * math.tau
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


class DeformShape(pi3d.Shape):
    def __init__(self, verts, norms, uvs, inds, shader, color):
        super().__init__()
        self.buf = [pi3d.Buffer(self, verts, uvs, inds, norms)]
        self.set_draw_details(shader, [], 1.0, 1.0, 1.0, 1.0)
        self.set_material(color)


class SynthObject:
    def __init__(self, shape, base_verts, base_norms, base_pos, color, seed):
        self.shape = shape
        self.active = True
        self.pos = np.array(base_pos, dtype=np.float32)
        self.base_pos = np.array(base_pos, dtype=np.float32)
        self.rot = np.zeros(3, dtype=np.float32)
        self.scale = 1.0
        self.color = color

        self.lfo_scale = LFO()
        self.lfo_move = LFO()
        self.lfo_rot = LFO()

        self.sculpt_mode = False
        self.zone_strengths = np.zeros(8, dtype=np.float32)
        self.boil_amount = 0.0
        self.boil_speed = 1.0
        self.sculpt_strength = 0.35

        self.base_verts = base_verts.astype(np.float32)
        self.working_verts = self.base_verts.copy()
        self.base_norms = base_norms.astype(np.float32)

        self.zone_ids = (
            (self.base_verts[:, 0] > 0).astype(np.int8)
            + (self.base_verts[:, 1] > 0).astype(np.int8) * 2
            + (self.base_verts[:, 2] > 0).astype(np.int8) * 4
        )
        self.zone_indices = [np.where(self.zone_ids == i)[0] for i in range(8)]
        self.zone_dirs = self._compute_zone_dirs()

        rng = np.random.RandomState(seed)
        self.boil_seeds = rng.uniform(0.0, math.tau, size=self.base_verts.shape).astype(np.float32)
        self.boil_scale = 0.08

        self.move_axis = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        self.rot_axis = np.array([0.0, 1.0, 0.0], dtype=np.float32)

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
        self.boil_amount = 0.0
        self.boil_speed = 1.0

    def set_transform_from_controls(self, midi_state, obj_idx):
        ch_a, ch_b = object_channel_indices(obj_idx)
        k_a = midi_state.knobs_f[ch_a]
        k_b = midi_state.knobs_f[ch_b]
        f_a = midi_state.faders_f[ch_a]
        f_b = midi_state.faders_f[ch_b]

        pos_x = unit_to_bipolar(k_a) * 1.2
        pos_y = unit_to_bipolar(k_b) * 0.9
        pos_z = unit_to_bipolar(f_a) * 1.2
        rot_y = unit_to_bipolar(f_b) * 180.0

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
        freq = remap01(k_a, 0.05, 2.2)
        if target == "scale":
            amp = remap01(k_b, 0.0, 0.6)
            self.lfo_scale.freq_hz = freq
            self.lfo_scale.amp = amp
        elif target == "move":
            amp = remap01(k_b, 0.0, 0.8)
            self.lfo_move.freq_hz = freq
            self.lfo_move.amp = amp
        elif target == "rot":
            amp = remap01(k_b, 0.0, 90.0)
            self.lfo_rot.freq_hz = freq
            self.lfo_rot.amp = amp

    def set_sculpt_params_from_controls(self, midi_state, obj_idx):
        self.zone_strengths[:] = midi_state.faders_f
        self.zone_strengths *= 2.0
        self.zone_strengths -= 1.0
        ch_a, ch_b = object_channel_indices(obj_idx)
        self.boil_amount = midi_state.knobs_f[ch_a]
        self.boil_speed = remap01(midi_state.knobs_f[ch_b], 0.3, 2.5)

    def _apply_zone_deform(self, v):
        if not self.sculpt_mode:
            return
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

    def update(self, t, dt):
        if not self.active:
            return

        v = self.working_verts
        v[:] = self.base_verts

        self._apply_zone_deform(v)
        self._apply_boil(v, t)

        self.shape.buf[0].re_init(pts=v)

        lfo_scale = self.lfo_scale.tick(t, dt)
        lfo_move = self.lfo_move.tick(t, dt)
        lfo_rot = self.lfo_rot.tick(t, dt)

        scale = max(0.05, self.scale * (1.0 + lfo_scale))
        pos_x = self.pos[0] + self.move_axis[0] * lfo_move
        pos_y = self.pos[1] + self.move_axis[1] * lfo_move
        pos_z = self.pos[2] + self.move_axis[2] * lfo_move

        rot_x = self.rot[0] + self.rot_axis[0] * lfo_rot
        rot_y = self.rot[1] + self.rot_axis[1] * lfo_rot
        rot_z = self.rot[2] + self.rot_axis[2] * lfo_rot

        self.shape.position(pos_x, pos_y, pos_z)
        self.shape.rotateToX(rot_x)
        self.shape.rotateToY(rot_y)
        self.shape.rotateToZ(rot_z)
        self.shape.scale(scale, scale, scale)

    def draw(self):
        if self.active:
            self.shape.draw()


def object_channel_indices(obj_idx):
    base = obj_idx * 2
    return base, base + 1


def object_button_states(midi_state, obj_idx):
    ch_a, ch_b = object_channel_indices(obj_idx)
    s_active = midi_state.s_buttons[ch_a] or midi_state.s_buttons[ch_b]
    m_active = midi_state.m_buttons[ch_a] or midi_state.m_buttons[ch_b]
    r_active = midi_state.r_buttons[ch_a] or midi_state.r_buttons[ch_b]
    return s_active, m_active, r_active


def make_shape(kind, shader, color):
    if kind == 1:
        v, n, uv, ind = gen_uvsphere(12, 8, 1.0)
    elif kind == 2:
        v, n, uv, ind = gen_box(1.4)
    elif kind == 3:
        v, n, uv, ind = gen_cone(8, 0.6, 1.2)
    else:
        v, n, uv, ind = gen_cylinder(10, 0.6, 1.2)

    shape = DeformShape(v, n, uv, ind, shader, color)
    return shape, v, n


def resolve_intent(midi_state):
    active = list(midi_state.transport)

    sculpt_candidates = []
    for i in range(NUM_OBJECTS):
        s_active, m_active, r_active = object_button_states(midi_state, i)
        if s_active and m_active and r_active:
            sculpt_candidates.append(i)
    sculpt_idx = min(sculpt_candidates) if sculpt_candidates else None

    modes = []
    for i in range(NUM_OBJECTS):
        if sculpt_idx == i:
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
    return active, sculpt_idx, modes


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


def main():
    display = pi3d.Display.create(x=0, y=0, w=1280, h=720, frames_per_second=60)
    display.set_background(0.02, 0.02, 0.02, 1.0)
    shader = pi3d.Shader("mat_flat")

    camera = pi3d.Camera(is_3d=True)
    pi3d.Light(lightpos=(3, 4, 6), lightcol=(0.9, 0.9, 0.9), lightamb=(0.2, 0.2, 0.2))

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
        shape, v, n = make_shape(shapes[i], shader, colors[i])
        obj = SynthObject(
            shape,
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

    port = open_nanoport()
    midi_state = MidiState()
    kb = pi3d.Keyboard()

    point_text, debug_block = make_debug_overlay(display)
    last_debug = 0.0
    fps = 0.0

    start_time = time.time()
    last_time = start_time

    while display.loop_running():
        now = time.time()
        dt = now - last_time
        last_time = now
        t = now - start_time
        if dt <= 0.0:
            dt = 1.0 / 60.0

        if port:
            for msg in port.iter_pending():
                if PRINT_MIDI:
                    print(msg)
                midi_state.update_from_msg(msg)

        k = kb.read()
        if k > -1:
            c = chr(k) if k < 256 else ""
            if k == 27:
                break
            if c in ("r", "R"):
                for obj in objects:
                    obj.reset_deformation()

        active, sculpt_idx, modes = resolve_intent(midi_state)

        for i, obj in enumerate(objects):
            obj.active = active[i]
            if sculpt_idx == i:
                obj.enter_sculpt_mode()
            else:
                obj.exit_sculpt_mode()

            s_active, m_active, r_active = object_button_states(midi_state, i)
            obj.lfo_scale.enabled = s_active
            obj.lfo_move.enabled = m_active
            obj.lfo_rot.enabled = r_active

            mode = modes[i]
            if mode == MODE_NORMAL:
                obj.set_transform_from_controls(midi_state, i)
            elif mode == MODE_LFO_SCALE:
                obj.set_lfo_params_from_controls(midi_state, i, "scale")
            elif mode == MODE_LFO_MOVE:
                obj.set_lfo_params_from_controls(midi_state, i, "move")
            elif mode == MODE_LFO_ROT:
                obj.set_lfo_params_from_controls(midi_state, i, "rot")
            elif mode == MODE_SCULPT:
                obj.set_sculpt_params_from_controls(midi_state, i)

        camera.reset()
        camera.position((0.0, 0.0, 4.0))
        camera.look_at((0, 0, 0))

        for obj in objects:
            obj.update(t, dt)
            obj.draw()

        if point_text and debug_block and (t - last_debug) > 0.25:
            inst_fps = 1.0 / dt if dt > 0.0 else 0.0
            fps = fps * 0.9 + inst_fps * 0.1
            sculpt_label = "none" if sculpt_idx is None else str(sculpt_idx + 1)
            active_list = [str(i + 1) for i, a in enumerate(active) if a]
            active_text = ",".join(active_list) if active_list else "none"
            debug_block.set_text(
                "fps: {:.1f}\nsculpt: {}\nactive: {}".format(fps, sculpt_label, active_text)
            )
            point_text.regen()
            last_debug = t

        if point_text:
            point_text.draw()

    if port:
        port.close()
    kb.close()
    display.destroy()


if __name__ == "__main__":
    main()
