# QUANTA Alpha — Audit & Build Blueprint

**Start here: [Read the full QUANTA Alpha audit](QUANTA-Alpha-Audit.md).**

This is the design blueprint for building QUANTA Alpha from the QUANTA Elite audit, optimized for self-hosting with mostly free data. It includes all ten requested deliverables, 18 major findings, the P0 / P1 / P2 roadmap, and repository-level module changes.

| File | Purpose |
| --- | --- |
| [QUANTA-Alpha-Audit.md](QUANTA-Alpha-Audit.md) | Full audit and implementation blueprint; readable directly on GitHub |
| [QUANTA-Alpha-Audit.html](QUANTA-Alpha-Audit.html) | Formatted report; download and open in a browser |
| [QUANTA-Audit-Evidence.md](QUANTA-Audit-Evidence.md) | Test evidence, synthetic probe results, and audit limits |
| [QUANTA-Audit-Probes.py](QUANTA-Audit-Probes.py) | Offline reproduction script; configure ROOT as described in the evidence file |

## How to use this blueprint

1. Read the gap analysis and target architecture.
2. Start implementation with P0 reliability and data foundations, then P1 and P2.
3. Use the final module migration section to decide what to reuse, replace, or create.
4. Validate proposed models before treating their scores as investment evidence.

The audit examines commit `7bb6386d176ca98764641031bab327ad0ab1d647`; it is a dated review, not certification of later code. Original audit files are preserved, including statements describing what had and had not been done at audit time. Published to this repository on 2026-09-13.
