# Belay - Extruder Sync Sensor

![Open Alpha](https://img.shields.io/badge/Open%20Alpha-blue)

Belay is a sensor for FFF 3D printers for keeping a secondary extruder
in sync with the primary extruder, avoiding error accumulation
regardless of how long a print may be. Either a
[Trad Rack](https://github.com/Annex-Engineering/TradRack) or a normal
extruder can be used as the secondary extruder. See
[purpose and how it works](#purpose-and-how-it-works) for more
details.

This project is inspired by the Filament Buffer used by Bambu Lab on their X1
and P1 Series printers.

Our discord server can be found here: 

[![Join me on Discord](https://discord.com/api/guilds/641407187004030997/widget.png?style=banner2)](https://discord.gg/MzTR3zE)

![Image of Belay](Images/render.png?raw=true)

## Getting started

See the [Quick Start document](/docs/Quick_Start.md) to get started.

## Purpose and how it works

Long filament paths or very heavy spools can cause extra resistance
in filament movement, potentially causing the printer's extruder to
struggle. One solution is to add a secondary extruder in series with
the primary extruder to take some of the load; often the secondary
extruder is synced directly to the primary extruder.

However, without a feedback system, the 2 extruders may gradually
drift out of sync from even a small mismatch in how well their
`rotation_distance` calibrations match the actual movement ratios.
Longer prints will build up to a larger mismatch, which may add extra
resistance to the filament's movement and eventually cause one of the
extruders to slip or skip.

Belay prevents error accumulation by dynamically adjusting the
`rotation_distance` setting of the secondary extruder to prevent
buildup of excess slack or tension in the filament between the 2
extruders. The bowden tube going from the secondary extruder to the
primary extruder is split into 2 pieces, and Belay goes between them.
When extruding forward, if the tubes before and after Belay
start getting pulled together, it sets a higher multiplier (lower
`rotation_distance`) for the secondary extruder. If the tubes start
getting pushed apart, it sets a lower multiplier. The multipliers are
reversed when extruding backward.

The following gif shows Belay in use during a print with exaggerated
multipliers of 1.30 and 0.70 to illustrate its movement:

![Belay Demo](Images/belay_demo_1.30_0.70.gif)

(With the default multipliers of 1.05 and 0.95, the movement of the
slider is much slower and shorter and becomes difficult to see, at
least without very high extrusion flowrates)

## Status and future plans

Belay is currently in open alpha. The code has been in use by a few
people using Trad Rack and has been working well so far, but the
current mechanical design is relatively new and may change
significantly.

Some things we may want to try in the future:

- Using an analog hall effect sensor (or similar) and PID control,
  instead of the digital switch and bang-bang control that is
  currently used
- Using the sensor to detect clogs/tangles (for example by detecting
  if the slider reaches either end of its travel via a 2nd switch or by
  replacing the digital switch with an analog one, using an analog
  sensor to determine if the slider is moving too fast for the
  commanded extrusion speed, etc.)
