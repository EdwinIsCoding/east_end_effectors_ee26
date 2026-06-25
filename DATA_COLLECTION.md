# Data Collection Methodology (EE26)

Operator playbook for collecting teleop demonstrations on the Panda and converting
them to train-ready LeRobot datasets. This is the **how-we-run-it** doc; for the
on-disk recording *format/layout* see `robot/franka_xr_teleop/DATA_COLLECTION.md`,
and for the full bring-up detail see `CLAUDE.md` (D1 teleop + D2 data collection).

Run everything on the **Desktop** booted into the **RT kernel** (`6.8.0-rt8-franka`).
Capture uses CPU only; GPU/training happens later after rebooting into `6.8.0-124-generic`.

## Cadence: 20 × 20

Collect in **batches of 20 episodes**. Convert each finished batch **in the background**
(CPU) while you record the next 20. Repeat until all data is in, then reboot to train.

```
record batch01 (20 eps) ──► Ctrl-C ──► convert batch01 (bg) ─┐
                                                              ├─ record batch02 (20 eps) ──► ...
                                                              ┘
```

## One-time-per-boot bring-up

1. **Robot ready:** user-stop released, brakes unlocked, **FCI active** in Desk.
2. **Bridge** (holds home, publishes obs on UDP 28081):
   ```bash
   cd robot/franka_xr_teleop
   ./build/cpp/teleop_bridge/franka_xr_teleop_bridge --robot-ip 192.168.1.11 --obs-port 28081
   ```
   Healthy: `Gripper ready …`, `control_command_success_rate≈0.97–1`, `q_err_max≈0.002` (holding home).
   On startup the bridge homes via the safe lift-first route and **opens the gripper**.
3. **Quest 3 teleop link** (re-do steps after any headset reconnect):
   ```bash
   adb kill-server && adb start-server && adb devices -l          # want state "device"
   adb reverse --remove-all && adb reverse tcp:63901 tcp:63901    # tunnel (does NOT persist)
   cd /opt/apps/roboticsservice && ./run3D.sh                     # service + Unity (once)
   ```
   In the Quest app connect to `127.0.0.1` and **engage the clutch** with controllers near the arm pose.

## Recording a batch

The recorder must be running **before** you press A — the bridge only streams obs, it does not save.
**Only one camera consumer at a time** (kill any `live_camera_view.py` first).

```bash
cd robot/franka_xr_teleop
source ~/ee26_cam_venv/bin/activate
./tools/record_data_collection_session.py --reset-cameras --recording-id batch01
```

Controller buttons (markers ride the obs stream → `episode_events.jsonl`):

| Button | Action |
|---|---|
| **A** | episode **start** |
| **B** | episode **end** → rehome to home + gripper opens |
| **left-controller X** | **discard** current episode (rehomes like B, but the A→X span never becomes an episode) |

Do 20 A→B episodes, then **Ctrl-C** (or SIGTERM). The recorder stops and **auto-splits** the session into
`recordings/<id>/episodes/episode_NNN/` (joints + both D405 clips).

## Converting a batch (background, CPU)

```bash
cd robot/franka_xr_teleop
nohup ./tools/process_recording.sh batch01 > recordings_cc/batch01.process.log 2>&1 &
```

Runs `main.py clean → annotate → convert --primary-camera wrist_d405` against the lerobot venv and writes:
- `recordings_cc/cleaned/batch01/` — motion-trimmed, re-segmented (intermediate, kept)
- `recordings_cc/lerobot/batch01/` — **train-ready** LeRobotDataset v3 (`data/*.parquet`, `videos/…`, `meta/…`)

⚠️ Keep `--primary-camera wrist_d405` (the default) so wrist → `observation.images.top`; a wrong value silently
mismatches the train/deploy image keys (`CONTRACT.md §1`).

## Dropping a bad episode

- **Live, in the moment:** press **left-controller X** while the episode is failing.
- **Post-hoc, before convert:** QA `recordings/<id>/episodes/episode_NNN/cameras/wrist_d405/rgb.mp4`
  (`episodes_index.json` maps `episode_NNN`→markers), then exclude by 0-based index:
  `./tools/process_recording.sh <id> --drop-episodes 2,5`.
- Episodes renumber `0..N-1` either way.

## Data locations (all gitignored)

| Stage | Path |
|---|---|
| raw session | `robot/franka_xr_teleop/recordings/<id>/` |
| cleaned | `robot/franka_xr_teleop/recordings_cc/cleaned/<id>/` |
| LeRobot v3 (train-ready) | `robot/franka_xr_teleop/recordings_cc/lerobot/<id>/` |

## Gotchas

- **`Device or resource busy` (errno 16)** on record start → a stray camera consumer holds the D405s.
  `pkill -f live_camera_view`; confirm `fuser /dev/video*` is empty; re-run.
- **Headset sleeps / connect error** → only the USB→ADB link dropped. Wake the headset, redo the two `adb`
  commands, reconnect to `127.0.0.1`. **Do not** restart the bridge or re-home.
- **B-rehome trips `cartesian_reflex`** (rare) → recover + re-home, don't lose the session:
  `panda_libfranka_sanity --mode recover-only` then `move_to_home 192.168.1.11`.
- **Don't train and teleop at once** (shared machine / FCI single-client).
