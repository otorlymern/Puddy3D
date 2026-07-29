# Puddy3D: a low poly 3D Video Synth for [VSESRPI](https://andreijaycreativecoding.com/Video-Synthesis-Ecosphere-RPI)

_A tactile, hardware-controlled 3D video instrument/psudeo baby playing with carefully made play-doh sculptures of geometric shapes, then getting bored
and mushing and yankin on em every which way. oh and the baby can also make them float in predictable patterns with its minde. _

---

## Overview

This project is a **standalone 3D video synthesizer** designed for the
[VSESRPI](https://andreijaycreativecoding.com/Video-Synthesis-Ecosphere-RPI)\* - Video Synthesizer Ecosystem for Raspberry Pi, by Andrei Jay.
"The Video Synthesis Ecosphere RPI are a set of real time open source video synthesis and processing tools" for the **Raspberry Pi+**, is controlled entirely from a
**Korg NanoKontrol2**, and outputs via HDMI (or composite) at 720p. If you dont have one, buy one from

I wanted a way to include very basic manipulatable 3d graphics into my hardware a/v set up.
Ive always apprecciated a good "standard", and since, and Andrei has been very influential to me

Think:

> _Etch-A-Sketch × Play-Doh × early PlayStation_

---

## Core Ideas

- **Hardware first**
  No mouse, no GUI, no menus during performance.
  The NanoKontrol2 _is_ the interface.

- **Low-res on purpose**
  Chunky geometry, flat shading, visible artifacts.
  Precision is not the goal — character is.

- **Play over control**
  Sculpting is exaggerated and messy.
  It should feel like physically deforming a digital object.

- **VSERPI compatible**
  Built to live alongside other VSERPI instruments on Raspberry Pi.

---

## Hardware Target

- Raspberry Pi **3B / 3B+**
- Korg **NanoKontrol2** (USB, CC mode)
- HDMI output @ **720p**
- Composite / analog output compatibility is a goal
- Keyboard/mouse allowed for setup only

---

## Software Stack

- **Python 3**
- **pi3d** — OpenGL ES rendering on Raspberry Pi
- **NumPy** — fast vertex deformation
- **mido + python-rtmidi** — MIDI input from NanoKontrol2

Guiding rule:
**GPU draws, NumPy deforms, Python stays out of tight loops.**

---

## What the Synth Does

### Objects

- 4 independent 3D objects on screen
- Objects can be toggled on/off instantly
- Default sizes and positions are designed to share the frame cleanly at 720p

### Normal Mode (default)

Each object is controlled by **two NanoKontrol2 channels**:

Per object:

- **2 knobs** → X and Y position
- **2 faders** → Z position (depth) and rotation
- **6 buttons (S / M / R × 2)** → mode switches

### LFO System

Each object has three LFOs:

- **Scale LFO** (Solo button)
- **Move LFO** (Mute button)
- **Rotation LFO** (Rec button)

LFOs are sine-based, time-correct (dt-driven), and intentionally simple.

### Sculpt Mode (“Play-Doh Mode”)

When **Solo + Mute + Rec** are all active for an object:

- That object enters **Sculpt Mode**
- Controller focus locks to it
- **All 8 faders** become deformation controls
- Geometry is pushed, pulled, twisted, and broken
- A **“boil”** effect adds animated wobble

Only one object can be sculpted at a time.
If multiple qualify, the lowest-index object wins.

---

## Sculpting Model (Important)

Sculpting is **zone-based**, not vertex-by-vertex.

Each mesh is split into **8 zones (octants)** based on vertex position.
Each fader controls one zone.

This gives:

- Big, physical-feeling deformations
- Consistent behavior across different shapes
- A deliberately crude, early-3D aesthetic

Precision is intentionally sacrificed in favor of _feel_.

## Setup

### macOS desktop preview

```bash
cd /Users/augie/Projects/Puddy3D
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements-core.txt -r requirements-desktop.txt
```

### Raspberry Pi

```bash
cd /Users/augie/Projects/Puddy3D
python3 -m pip install -r requirements-core.txt -r requirements-pi.txt
```

## Startup

### macOS desktop preview

```bash
cd /Users/augie/Projects/Puddy3D
source .venv/bin/activate
PUDDY_BACKEND=desktop python3 puddy.py
```

### macOS headless fallback

```bash
cd /Users/augie/Projects/Puddy3D
source .venv/bin/activate
PUDDY_BACKEND=mac python3 puddy.py
```

### Raspberry Pi

```bash
cd /Users/augie/Projects/Puddy3D
python3 puddy.py
```

### Double-click launcher on macOS

- `Launch Puddy.command` runs the desktop preview from Finder
- If `.venv` exists, it will use it automatically
- First run may require Finder -> right click -> Open
- `Puddy.app` is a compiled app wrapper that opens Terminal and launches Puddy
- `Puddy Launcher.applescript` is the source for rebuilding `Puddy.app`

---

## MIDI Mapping (Ground Truth)

The synth expects the NanoKontrol2 in **CC mode**.

---

Faders: CC 0–7
Knobs: CC 16–23

Solo: CC 32–39
Mute: CC 48–55
Rec: CC 64–71

Transport:
Play 41
Stop 42
Rew 43
FF 44
Rec 45
Cycle 46
Track Left 58
Track Right 59

---

## Current Control Map

The live code uses the NanoKontrol2 in **CC mode** and groups channels in pairs:

- Object 1 = channels 0 and 1
- Object 2 = channels 2 and 3
- Object 3 = channels 4 and 5
- Object 4 = channels 6 and 7

### Object activation

- `Play` toggles Object 1 on/off
- `Stop` toggles Object 2 on/off
- `Rew` toggles Object 3 on/off
- `FF` toggles Object 4 on/off

### Global reset / panic

- `Record` transport button is global panic/reset
- It clears sculpt deformation
- It resets transforms
- It disables all LFOs
- Any lit Solo/Mute/Rec-row LFO buttons must be toggled off and back on to re-arm

### Normal transform mode

Each object uses two channels:

- Channel A knob = X position
- Channel B knob = Y position
- Channel A fader = Z position
- Channel B fader = Y rotation

Behavior notes:

- X/Y/Z/rotation are centered bipolar controls with deadzone and clamp
- X/Y travel expands as the object moves deeper in Z
- Z gets extra forward range when X/Y are near center

### LFO enable logic

Per object:

- Scale LFO enabled = `Solo[chA] OR Solo[chB]`
- Move LFO enabled = `Mute[chA] OR Mute[chB]`
- Rotation LFO enabled = `Rec[chA] OR Rec[chB]`

Button LED state is the truth. Turning a latch off disables that LFO immediately.

### LFO edit mapping

When exactly one LFO family is active for an object, that object's controls edit that LFO.

#### Scale LFO

- Solo active by itself for that object
- Knob A = frequency
- Knob B = amplitude

#### Move LFO

- Mute active by itself for that object

Modes:

- Mute A only = X LFO
- Mute B only = Y LFO
- Mute A + Mute B = orbit LFO

Controls:

- X mode: Knob A = frequency, Fader A = amplitude
- Y mode: Knob B = frequency, Fader B = amplitude
- Orbit mode: average of Knob A/B = frequency, Fader A = orbit width, Fader B = orbit height

#### Rotation LFO

- Rec-row active by itself for that object
- Knob A = frequency
- Knob B = amplitude

### Sculpt mode

- `Cycle` toggles Sculpt Mode on/off
- `Track Left` / `Track Right` select the sculpt target object
- Sculpt changes persist after leaving Sculpt Mode

When Sculpt Mode is active:

- All 8 faders control the target object's zone strengths
- All 8 knobs control the target object's sculpt layers

Sculpt layers:

- Knob 1 = boil
- Knob 2 = crackle
- Knob 3 = twist
- Knob 4 = shear
- Knob 5 = pulse
- Knob 6 = curl
- Knob 7 = drift
- Knob 8 = bias

### Desktop preview keys

- `f` toggles solid fill
- `w` toggles wire overlay
