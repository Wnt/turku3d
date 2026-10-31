# Turku3D documentation

**[Play the game](https://wnt.github.io/turku-game/)** · [Project README](../README.md)

Choose the entry point for what you want to do:

| Audience / question | Start here |
| --- | --- |
| **Player:** How do I play? What are the controls? | [Playing Turku3D](PLAYING.md) |
| **Contributor:** How do I propose a change? | [CONTRIBUTING](../CONTRIBUTING.md) |
| **Developer:** How do I run the browser game or tests? | [Development guide](DEVELOPING.md), [game README](../game/README.md) |
| **Server developer:** How does multiplayer work? | [Server README](../server/README.md), [protocol](../server/PROTOCOL.md) |
| **World/pipeline developer:** Where are the data and rendering contracts? | [Architecture](ARCHITECTURE.md), [formats](specs/), [data sources](../data-manifests/sources.yaml) |
| **Game designer:** What is the mission/economy design? | [Story and missions](design/story.md) |
| **Maintainer:** How do worktrees, releases and remote services work? | [Agent/project policy](../AGENTS.md), [developer tools](../scripts/dev/README.md) |
| **Researcher:** Where are measured results and older plans? | [Status snapshots](STATUS.md), [reports](reports/), [research](research/), [history](HISTORY.md) |

## How to read the technical documents

- **Implementation and current configuration take precedence over dated research or design plans.** Source code, tests and the current deployment scripts describe what ships today.
- `ARCHITECTURE.md` records foundational decisions and evidence, including early research; it is not a claim that every planned subsystem has shipped.
- `STATUS.md` and `reports/` contain dated snapshots, measurements and earlier acceptance gaps. A previous tile count, release commit or benchmark is not a live health indicator.
- `briefs/`, `pilot/`, `archive/` and older runbooks may document completed or superseded work. Follow links from the current development guide before running commands on real data or production services.

Code is MIT-licensed; third-party and geographic data have separate terms. See [NOTICE](../NOTICE.md) and [LICENSE](../LICENSE).
