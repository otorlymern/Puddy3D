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

## Build

Mac dev
pip install -r requirements-core.txt -r requirements-desktop.txt

Raspberry Pi
pip install -r requirements-core.txt -r requirements-pi.txt

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
