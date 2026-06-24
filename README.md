# East End Effectors — EE26 Intel Industrial Robotics Arm Challenge

Two-machine workflow. The boundary between them is **one file: `CONTRACT.md`**.

```
├── CONTRACT.md        ← FROZEN interface (dataset spec, UDP schemas, policy handoff). The only shared surface.
├── PLAN.md            ← overall strategy
├── plans/
│   ├── PLAN_DESKTOP.md   ← Black workstation (RT, wired to Franka): bring-up, teleop, record, deploy
│   └── PLAN_OFFROBOT.md  ← this computer: training pipeline, OpenVINO, Pi0
├── robot/             ← DESKTOP-owned (libfranka bridge, sanity checks, deploy tools, Quest docs)
├── training/          ← OFF-ROBOT-owned (SmolVLA-Testing pipeline: clean/annotate/convert/train/eval)
└── challenge2/        ← ball-balance (desktop runtime + off-robot logic)
```

## Who owns what (this is the merge strategy)
- **Desktop person** edits `robot/` + `challenge2/` runtime. **Off-robot person** edits `training/` + `CONTRACT.md` authoring.
- Different directories → conflicts are rare. Keep it that way.
- **`CONTRACT.md` is frozen**: changing a value there (image keys, dims, ports, serials) requires a ping to the other person *before* committing.

## Merge workflow
1. One GitHub remote; both clone. Work on `main`, **pull before you start, push often** (small commits).
2. Stay in your owner dirs. If you must touch the other's dir, say so first.
3. Integration test (do early, with a throwaway 2-episode dataset): Desktop records → off-robot trains a dummy → exports → Desktop deploys. If that loop closes, the contract holds and the real run is just scale.

## The plan in one line
Our own libfranka stack (score-neutral, dedicated 40h arm, proven on this exact insertion task). C1 insertion = classical/VLA floor + SmolVLA→Pi0 via OpenVINO ceiling. C2 ball-balance = classical PD (+OpenVINO tracker for the Intel bonus). See `PLAN.md`.
