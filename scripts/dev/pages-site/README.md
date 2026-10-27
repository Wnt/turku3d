# Turku3D

A free-roam game set around Läntinen Rantakatu 15 on the west bank of the Aura river in Turku,
Finland, built from the city's open 3D data. Walk the riverside, take the parked car, and feel
curbs, stairs and quay edges on foot and at the wheel.

**Play:** {{url}}

This repository only holds the built game (`game/`) and its streamed world tiles (`tiles/`). Each
publish replaces the whole branch with one commit; the source and the data pipeline live elsewhere.

## Browser

Desktop Chrome (or another Chromium browser) with WebGPU. WebGL2 is the fallback: add
`?backend=webgl` to the URL. The first load streams about 100 MB of tiles. An Xbox controller is
recommended; keyboard and mouse work too.

## Controls

| Action | Xbox controller | Keyboard / mouse |
|---|---|---|
| Move / steer | Left stick | WASD / arrows |
| Look | Right stick | Click the canvas, then move the mouse |
| Sprint | A (hold) | Shift |
| Jump | X | Space |
| Interact (shops, missions) | A | E |
| Enter / exit car | Y (within 4 m) | F |
| Accelerate | RT | W / Up |
| Brake / reverse | LT | S / Down |
| Handbrake | RB | Space |
| Reset camera | R3 | C |
| Map | View | M |
| Pause | Menu | Escape |
| Back out of a shop menu | B | Escape |
| Mute | D-pad down | N |

Press a controller button once after connecting it so the browser exposes it.

## Data sources

Buildings, street areas, lidar terrain, trees and aerial imagery come from the City of Turku's open
data; the street network from OpenStreetMap. The full list with licences is in
[ATTRIBUTION.md](ATTRIBUTION.md).
