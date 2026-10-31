# Playing Turku3D

**[Launch the public game](https://wnt.github.io/turku-game/)** · [Project overview](../README.md)

Turku3D is a playable, evolving open-world game based on central Turku, Finland. In 2027, a former software developer becomes a pizza courier after AI-driven layoffs. Getting paid means renting a courier permit, taking orders, racing the delivery clock and deciding what you can afford next.

## Start a session

1. Open the game in **desktop Google Chrome**, preferably with a keyboard/mouse or Xbox-style controller. The game uses WebGPU by default; add `?backend=webgl` for its WebGL2 fallback.
2. Let the city and its collision data load. When the main menu becomes available, select **Multiplayer** to enter the public city. The service assigns you a nickname.
3. Follow the on-screen mission markers and minimap: visit the black market for a courier permit, then the pizzeria for an order. Deliver pizzas before the deadline for better tips. Explore freely between jobs.

**Important differences:** The public Pages build currently offers **Multiplayer** as its start-menu entry. The local development build also offers **New game**, **Continue**, **Load game** and developer features. Solo save slots and their behavior are documented in [the UI implementation notes](../game/src/ui/README.md); do not assume those menus exist on the public site.

## Controls

| Action | Keyboard and mouse | Xbox-style controller |
| --- | --- | --- |
| Walk / steer | WASD or arrow keys | Left stick |
| Look around | Click game canvas for pointer lock; move mouse | Right stick |
| Sprint (on foot) | Shift | A while moving |
| Jump | Space | X |
| Interact with shops / delivery markers | E | A |
| Enter or exit a nearby vehicle | F | Y |
| Accelerate | W / Up | RT |
| Brake / reverse | S / Down | LT |
| Handbrake | Space | RB |
| Map: small / expanded | M | View |
| Camera reset | C | Right stick click (R3) |
| Vehicle upright reset | R | Use the in-game reset prompt |
| Pause menu | Escape | Menu |
| Mute | N | D-pad down |
| Fullscreen | F10 | On-screen fullscreen control |

Connect the controller and press one of its buttons so the browser can detect it. Menus use the arrow keys or W/S and Enter/E, or controller stick/D-pad and A; Escape or B goes back. The game also has touch controls on supported devices, but its primary performance target is desktop Chrome.

## Deliveries, money and multiplayer

- Rent a courier permit at the black market to accept delivery work. A permit covers a limited number of orders.
- Take pizza orders from the pizzeria. Following the route and arriving quickly earns tips; some later orders cover longer distances or multiple stops.
- Save toward a scooter, car or trophy truck at the vehicle dealer. Alternative black-market purchases change the economics of the game.
- In multiplayer, other players and their vehicles share the city. The scoreboard ranks **paid pizza drop-offs**, not all money earned, for each Europe/Helsinki clock hour. The game displays current and completed hours.

Details of the economy, story and unlocks are in [the gameplay design](design/story.md) and [gameplay implementation notes](../game/src/gameplay/README.md). These documents include spoilers.

## What to expect

**Turku3D is a prototype.** The terrain, buildings, facades and road/route data derive from real geographic inputs but are not a perfect reconstruction. AI-generated appearances and on-demand world generation can vary in quality or loading time. New areas may take a moment to appear; missing geometry or collision problems are possible.

**Firefox is unsupported.** It can start with reduced performance and displays an explicit warning. Chrome is the supported path. If WebGPU cannot initialize, try `?backend=webgl`. Multiplayer needs the external server and world-generation services; availability and response times can change.

For reproducible reports, include your Chrome version, GPU, backend, map location or coordinates if available, steps to reproduce, and whether you were in multiplayer. [Report a problem](https://github.com/Wnt/turku3d/issues/new).
