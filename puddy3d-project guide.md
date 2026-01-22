# VSERPI 3D Video Synth (Python / pi3d / NumPy) — Project Guide

## What this is

A **standalone, hardware-controlled 3D video synthesizer** designed to run inside the **VSERPI ecosystem** (Raspberry Pi 3+ + NanoKontrol2).
It generates **low-res / early-3D / PS1-ish** visuals at **720p HDMI** (and should remain compatible with composite/analog workflows later).

This project is an **instrument**, not a “graphics demo.” The UI is the controller. The code exists to make the hardware feel playful and immediate.

---

## Hardware + Environment Target

- **Raspberry Pi 3B/3B+**
- **Korg NanoKontrol2** over USB (primary input)
- **HDMI output @ 720p** (primary target during development)
- Analog/composite output compatibility is desirable but not mandatory for v1
- No keyboard/mouse in performance mode (allowed during setup/dev)

---

## Software Stack

- **Python 3**
- **pi3d** for GPU-accelerated OpenGL ES 3D rendering
- **numpy** for fast vertex math + deformation
- **mido + python-rtmidi** for MIDI input

Guiding principle: **GPU for drawing, NumPy for deformation, minimal Python loops in the hot path.**

---

## Aesthetic + Design Goals

### Visual style

- Early 3D / PlayStation 1 vibe
- Chunky geometry, flat shading, low-poly primitives
- Artifacts are welcome: wobble, jitter, crude deformation, harsh edges

### Interaction style

- Tactile + playful: “Etch-a-sketch meets Play-Doh”
- Fast state changes with hardware buttons
- Encourages _messing things up_ more than precision editing

---

## Core Feature Set (v1)

### Objects

- 4 active 3D objects (primitives at first: cube, sphere-ish, pyramid/cone, torus/cylinder)
- Objects can be toggled **in/out** quickly from NanoKontrol2
- Defaults should share screen space nicely at 720p

### Transform controls (per object)

Each object gets:

- **Knob A:** X position
- **Knob B:** Y position
- **Fader A:** Z position (depth)
- **Fader B:** rotation (choose one axis for v1: usually Yaw or Z)

Transforms should feel stable and not “twitchy.” Use sensible ranges.

### LFO system (per object)

Each object has three LFO toggles:

- **S (Solo):** Scale LFO (freq + amp)
- **M (Mute):** Move LFO (freq + amp) — affects position
- **R (Rec):** Rotation LFO (freq + amp)

LFO should be sine-based for v1. Use **dt-based** phase updates.

### Sculpt Mode (the “Play-Doh” mode)

Trigger: **S + M + R all ON for an object**
Effect: that object becomes the **selected sculpt target**, and the controller focuses on destroying it.

In Sculpt Mode:

- **All 8 faders** become **zone deformation lanes** (bipolar push/pull)
- **Boil** becomes available: animated bubbling wobble (cheap pseudo-noise)
- Distortion should be non-precise, fun, and visually interesting low-res

Only **one object** can be sculpted at a time:

- If multiple objects meet the condition, choose the lowest index deterministically.

### Boil (animated bubbling)

A cheap animated displacement (sine-based pseudo-noise is fine) that makes the mesh wobble like boiling water.

- Driven by per-vertex seed values
- Controlled by a “boil amount” (0..1) and “boil speed”

---

## Control Mapping (Default Plan)

We expect users may reconfigure NanoKontrol2; therefore:

✅ **All CC/button mappings must live in a single mapping dictionary at top of file**
✅ Include `PRINT_MIDI` debug flag to log incoming messages during mapping

### Per-object control allocation (NORMAL mode)

- Obj1: knobs 1–2, faders 1–2
- Obj2: knobs 3–4, faders 3–4
- Obj3: knobs 5–6, faders 5–6
- Obj4: knobs 7–8, faders 7–8

### S/M/R buttons per channel

- S toggles scale LFO edit
- M toggles move LFO edit
- R toggles rotation LFO edit
- S+M+R => Sculpt Mode for that object

### Transport buttons

Use 4 transport buttons as **latching** toggles:

- transport -> object active on/off (Obj1–Obj4)

Exact CC numbers differ by NanoKontrol2 mode; keep them configurable.

---

## Architecture Rules (Keep Codex On Track)

### 1) Do NOT mutate synth state directly inside MIDI read loop

MIDI loop should only update a `MidiState` object.

Each frame:

1. Read MIDI -> update `MidiState`
2. Resolve `Intent` (what the performer wants right now)
3. Apply `Intent` to `SynthObjects`
4. Update/draw scene

This prevents mode logic from turning into spaghetti.

### 2) Must use a `SynthObject` class

Each object owns:

- active state
- pos/rot/scale
- LFO blocks (scale/move/rot)
- deformation params
- cached base vertices + working vertices
- per-vertex seeds + zone ids (precomputed)

### 3) Use `dt` for all time-based motion

No `1/60` assumptions. Frame rate will vary on Pi.

### 4) Performance: avoid per-frame allocations

In hot paths:

- Reuse arrays whenever possible
- Vectorize operations with numpy (no Python loops over vertices per frame)

Meshes must be low-poly. If a user model is loaded, enforce a vertex limit or fail gracefully.

### 5) Always deform from a base mesh

Each frame:

- start from cached base vertices
- apply sculpt + boil into working vertices
- upload working vertices to mesh

Never permanently “walk” the base vertices unless explicitly committing.

---

## Zone Sculpt Deformation (Implementation Notes)

We implement 8 zones per object using octants:

`zone_id = (x > 0) + (y > 0) * 2 + (z > 0) * 4`

Precompute:

- `zone_ids` for every vertex
- `zone_dirs` (direction vectors) per zone:
  - either fixed octant vectors, or normalized centroid direction

In Sculpt Mode:

- faders map to `zone_strength[z]` in [-1, +1]
- deform vertices in each zone by:
  - `verts[mask] += zone_strength[z] * zone_dirs[z] * deform_scale`

Chunky and fun beats “correct.”

---

## Development Milestones

### Milestone 1: “Pixels + MIDI”

- pi3d display opens
- one object drawn
- midi input prints and CC mapping verified

### Milestone 2: “4 Objects + Basic Transform”

- objects toggle on/off
- knobs/faders move + rotate each object

### Milestone 3: “LFO Layer”

- S/M/R toggles LFO per object
- knobs control LFO freq/amp while editing

### Milestone 4: “Sculpt Mode”

- S+M+R triggers sculpt for an object
- all faders deform zones (bipolar)
- boil animates mesh

### Milestone 5: “VSERPI readiness”

- stable framerate
- clean startup behavior
- minimal debug overlay only

---

## Minimal Debug Overlay (Allowed)

We avoid UI, but allow a tiny overlay with:

- FPS estimate
- Sculpt-selected object (or none)
- List of active objects

Must be lightweight.

---

## Non-goals (for now)

- Fancy lighting, shadows, post-processing
- High-poly models
- Complex noise libraries
- On-device model decimation
- Multiple camera modes

These can be added later once the instrument core feels right.

---

## Repository Notes

- Keep it runnable as a single script initially: `python3 main.py`
- Code should remain readable and modifiable by artists, not just engineers
- When stable, split into modules (midi.py, synthobject.py, lfo.py, etc.)

---

## Quick Reminders for Codex

- Optimize for **Raspberry Pi 3+**
- The synth must feel like **hardware**
- Deformation must be **fun**, not precise
- Keep mapping configurable
- Use dt everywhere
- Avoid Python per-vertex loops in the frame loop

End goal: a playable, tactile, standalone 3D video synth that belongs in VSERPI.
