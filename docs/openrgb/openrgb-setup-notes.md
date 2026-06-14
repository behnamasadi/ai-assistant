# OpenRGB Setup Notes

Host: `server` (`/home/behnam`), Ubuntu 24.04, kernel 6.17
App: OpenRGB AppImage `/home/behnam/OpenRGB_1.0rc2_x86_64_0fca93e.AppImage` (reports `0.9+ (1.0rc2)`)
Last updated: 2026-06-14

> This doc was consolidated from a session-by-session log. The current state is
> described first; the chronological history (including a couple of wrong turns
> that were later corrected) is condensed at the bottom under **History**.

---

## Current status — SOLVED

- **PC OFF → every LED dark.** Handled by the BIOS **ErP Ready (S4+S5)** toggle,
  which cuts +5V standby power to all controllers. Confirmed by user.
- **PC ON → every LED dark (or any color you choose).** A single root systemd
  service (`openrgb.service`) runs OpenRGB as a resident SDK server and holds
  **all** controllers — Corsair fans, RGB RAM, GPU, ASUS Aura, **and** the
  be quiet! hub fans — at Direct black on every boot. Confirmed by user: case is
  fully dark while running.

Every RGB device in this machine is now reachable by OpenRGB. (An earlier
conclusion that the be quiet! fans were unreachable in software was wrong — see
History.)

---

## Hardware topology (final, confirmed by colored-flash tests)

OpenRGB detects **5 controllers** when run as root with `i2c-dev` available:

```text
0: Corsair Vengeance Pro RGB          # RAM stick 1   (i2c / SMBus)
1: Corsair Vengeance Pro RGB          # RAM stick 2   (i2c / SMBus)
2: ASUS ROG STRIX GeForce RTX 3090    # GPU           (i2c)
3: Corsair Commander Core XT          # AIO + top fans (USB HID, 1b1c:0c2a)
4: ASUS TUF GAMING Z690-PLUS WIFI D4  # Aura board    (USB HID, 0b05:19af)
```

What physically lights up where:

| Fans / LEDs                              | Controller                         | OpenRGB zone                      |
|------------------------------------------|------------------------------------|-----------------------------------|
| **Top / radiator fans**                  | Corsair Commander Core XT (USB)    | `RGB Port 4`, `5`, `6` @ 16 LEDs  |
| **Side be quiet! Light Wings** (×3)      | be quiet! ARGB hub → Aura header   | `Aura Addressable 3` @ size 120   |
| **Bottom be quiet! Light Wings** (×3)    | be quiet! ARGB hub → Aura header   | `Aura Addressable 2` @ size 120   |
| RGB RAM (2 sticks)                        | Corsair Vengeance Pro RGB (i2c)    | `Corsair Pro Zone`                |
| GPU                                       | ASUS RTX 3090 (i2c)                | (unknown zone)                    |
| Motherboard onboard LED                  | ASUS Aura board (USB)              | `Aura Mainboard`                  |

Fans on the be quiet! hub read `be quiet! LIGHT WINGS BO LW-120S-HHR-PWM`.

### The be quiet! hub (the part that confused things for two sessions)

It is **not** a USB device, so `lsusb` never shows it — which is why it was once
mistaken for "unreachable." It is a passive ARGB hub with:

- **Outputs:** `LED1 LED2 LED3` (top row), `LED4 LED5 LED6` (bottom row) — 6 fans.
- **Inputs:** `POWER` (SATA/molex) and **`RGB IN`** (5V 3-pin ARGB sync input).

The `RGB IN` lead is plugged into the motherboard's **Aura Addressable 3**
(ADD_GEN2, 5V 3-pin) header. So OpenRGB drives these fans *through* the ASUS Aura
USB device (controller 4), by writing to the `Aura Addressable 3` zone. OpenRGB
ships that zone at **size 0**, meaning zero LEDs addressed → the hub received no
data and free-ran its built-in rainbow. Sizing the zone (to 120, harmless if
larger than the real LED count) is what made the fans controllable.

> **Correction (2026-06-14):** there are actually **two** be quiet! hubs — the
> **side** fans on `Aura Addressable 3` and the **bottom** fans on `Aura
> Addressable 2` — not one hub on AA3 as first written. Each hub is a *mirroring
> splitter*, so its fans can't be addressed individually. See
> **Per-fan identification** above for the full mapping and proof.

---

## Per-fan identification (individual fan control)

Mixed: the **top fans are independently controllable**, but the **side and bottom
fans are mirrored groups** (the be quiet! hubs are splitters). Mapping done by
lighting fans distinct colors and photographing the case (2026-06-14).

### Top fans (Corsair Commander Core XT) — confirmed individually controllable

The three top fans are three separate ports on the Corsair, each 16 LEDs:

| Physical position (front → back) | Color shown in the ID test | Corsair zone |
|----------------------------------|----------------------------|--------------|
| front (by the front I/O buttons) | blue                       | `RGB Port 6` |
| middle                           | red                        | `RGB Port 4` |
| rear (by the window)             | green                      | `RGB Port 5` |

So physical order front→back is **Port 6, Port 4, Port 5**. (Ports 1–3 and the
External port carry no LEDs on this rig.)

**Gotcha that matters for scripting individual fans:** this Corsair build
**ignores per-*zone* CLI colour commands** — e.g.
`--device 3 --zone 4 --color FF0000 --zone 5 --color 00FF00` does *nothing*, and
the fan firmware free-runs its rainbow (looks like the command was ignored). What
*does* work is **per-LED whole-device** addressing: put the whole device in
`direct` and pass one comma-separated colour list covering every LED in order
(External, then Port4 ×16, Port5 ×16, Port6 ×16 = 49 entries). Colour the blocks
to address each fan. (Whole-device single `--color` also works; only *per-zone*
targeting is the broken path here.)

How the top-fan ID was run (everything else blacked out so only the top lights):
```bash
# build per-LED list: external black, Port4 red×16, Port5 green×16, Port6 blue×16
COLORS="000000"
for i in $(seq 16); do COLORS="$COLORS,FF0000"; done   # Port 4 → red
for i in $(seq 16); do COLORS="$COLORS,00FF00"; done   # Port 5 → green
for i in $(seq 16); do COLORS="$COLORS,0000FF"; done   # Port 6 → blue
sudo systemctl stop openrgb.service                    # free the USB devices
sudo HOME=/root /path/OpenRGB...AppImage --server \
  --device 0 --mode direct --color 000000 \            # RAM stick 1 off
  --device 1 --mode direct --color 000000 \            # RAM stick 2 off
  --device 2 --mode direct --color 000000 \            # GPU off
  --device 4 --mode direct --color 000000 \            # Aura/be quiet off
  --device 4 --zone 3 --size 120 --color 000000 \      # AA3 (be quiet hub) off
  --device 3 --mode direct --color "$COLORS"           # Corsair top fans painted
```

Operational notes learned while doing this:
- **One server only.** If `openrgb.service` (or any prior `--server`) is still up,
  a new `--server` connects to it as a *client*, applies the colours to the
  *running* server, then fails to bind port 6742 and exits. Confusing but
  harmless — just means: stop the existing server first.
- **Killing the test server cleanly:** `sudo pkill -x OpenRGB_1.0rc2_` (match by
  exact process *name*, not `-f`/full-cmdline — a `-f` pattern also matches the
  shell that contains the pattern string and kills itself). Or kill the PID bound
  to 6742: `sudo ss -tlnp | grep 6742`.
- When the test is done, `sudo systemctl start openrgb.service` to restore the
  saved look.

### Side + bottom fans (be quiet! hubs) — NOT individually controllable

These are **mirroring hubs**, and the side and bottom sets are on **two different
motherboard headers** (the old note claiming "all be quiet! fans on AA3" was
wrong — corrected here):

| Fan group        | Motherboard zone        | Behaviour                          |
|------------------|-------------------------|------------------------------------|
| **Side** (×3)    | `Aura Addressable 3`    | mirrored — all 3 show the same     |
| **Bottom** (×3)  | `Aura Addressable 2`    | mirrored — all 3 show the same     |
| (motherboard TUF logo / GPU-area accent) | `Aura Addressable 1` | not the case fans       |

**Why they can't be addressed individually:** each be quiet! ARGB hub is a
**splitter**, not a daisy-chain. It copies the *same* ~20-LED data stream to every
fan port, so all fans on a hub display the identical pattern. Proven by painting
the zone in six 20-LED colour blocks (red, green, blue, …): instead of each fan
taking the next block, **every** fan showed block 1 (red). You can still animate a
pattern *within* a fan ring (e.g. a gradient), but all fans on that hub mirror it.

So, per-fan control summary for this rig:

- **Top 3 (Corsair)** → fully independent (Port 6 front, Port 4 mid, Port 5 rear).
- **Side 3 (AA3)** → one mirrored group, no per-fan control.
- **Bottom 3 (AA2)** → one mirrored group, no per-fan control.

How the be quiet! ID was run (top off; paint one zone in 6 blocks, others black):
```bash
# 120-LED list = 6 blocks of 20: red,green,blue,yellow,magenta,cyan
AA=""; add(){ for i in $(seq 20); do AA="$AA,$1"; done; }
add FF0000; add 00FF00; add 0000FF; add FFFF00; add FF00FF; add 00FFFF; AA="${AA#,}"
sudo HOME=/root /path/OpenRGB...AppImage --server \
  --device 3 --mode direct --color 000000 \            # top fans off
  --device 4 --mode direct --color 000000 \            # Aura all off first
  --device 4 --zone 1 --size 120 --color 000000 \      # AA1 off
  --device 4 --zone 3 --size 120 --color 000000 \      # AA3 (side) off
  --device 4 --zone 2 --size 120 --color "$AA"         # AA2 (bottom) painted
# all bottom fans showed red (block 1) => mirrored hub.
# To find WHICH header a fan group is on, light AA1/AA2/AA3 distinct solid colours.
```

### Schematic

A read-it-later diagram of the fan map lives next to this file:
`case-fan-map.svg` (editable, text-based) and `case-fan-map.png` (rendered).
Shows T1/T2/T3 top fans with their ports, and the side/bottom mirrored groups.

### The `rgbfan` command (per-fan / per-group control)

`/usr/local/bin/rgbfan` is a companion to `rgb`: where `rgb` sets a whole-rig
"look", `rgbfan` addresses **individual top fans and the side/bottom groups**. It
bakes in everything learned above (per-LED Corsair list, AA2/AA3 zones, the
one-server-only kill/relaunch dance). Passwordless via
`/etc/sudoers.d/openrgb-rgbfan`; self-elevates with `sudo`.

```bash
rgbfan identify                                  # T1/T2/T3=blue/red/green, side=cyan, bottom=magenta
rgbfan top-front=blue top-mid=red top-rear=green # the 3 top fans, individually
rgbfan side=ff0000 bottom=0000ff                 # side red, bottom blue (each a mirrored group)
rgbfan all warm                                  # everything one colour
rgbfan off                                       # all black, held
rgbfan restore                                   # hand control back to openrgb.service (saved look)
rgbfan list                                      # list controllers/zones
rgbfan help
```

Targets: `top-front`(P6) `top-mid`(P4) `top-rear`(P5) `top` `side`(AA3) `bottom`(AA2)
`accent`(AA1) `ram` `gpu` `all`. Colours: 6-digit hex or names (off/red/green/blue/
white/yellow/cyan/magenta/orange/purple/warm). **Unlisted targets default to off**,
so a scene is exactly what you ask for. Scenes are **live only** — on reboot
`openrgb.service` restores the saved `rgb` look (run `rgbfan restore` to return
sooner). It stops `openrgb.service` while active (one OpenRGB server at a time).

---

## Live effects — `temp` and `talking` modes

Two animated modes (added 2026-06-14). Unlike static scenes (which restart an
OpenRGB `--server`), live modes keep **`openrgb.service` as the resident server**
and run a separate **`rgbfan-daemon`** that connects to it as an **SDK client**
(`openrgb-python` on `127.0.0.1:6742`) and streams colour updates — smooth, no
restarts.

```bash
rgbfan temp        # fans coloured by temperature (live gauge)
rgbfan talking     # fans pulse/flow with Claude's spoken TTS summary (aliases: cloud, interactive)
rgbfan restore     # stop live mode, back to the saved `rgb` look
rgbfan status      # show mode / daemon / server
```

### `temp` mode
Every ~1.5 s the daemon reads CPU + GPU temps and colours the fans cool→hot
(blue→red, HSV hue 210°→0° over **35–82 °C**):
- **Top fans** = CPU package temp (`/sys/class/thermal/thermal_zone2/temp`,
  `x86_pkg_temp`). The top fans are the AIO radiator fans, so this is thematically
  the CPU/AIO temperature.
- **Side + bottom** = GPU temp (`nvidia-smi --query-gpu=temperature.gpu`).

### `talking` mode (Grok/Tesla-style)
A flowing gradient (cyan→blue→purple→pink) sweeps across the case, each fan group
hue-offset so colour *flows*, and brightness pulses with the **speech loudness**.
**Dark when silent** (`IDLE_BRI = 0.0`) — it only lights up while the TTS summary
speaks, so the case stays dark by default (respects the ErP/off philosophy).

How the audio reactivity works (no audio capture needed; full detail in
**The AI summary → speech → lights pipeline** below):
1. The Stop hook `~/.claude/hooks/tts-summarize.sh` already makes an MP3 of the
   one-line summary and plays it with `ffplay`.
2. A block added around that `ffplay` call (only runs when `mode == talking`):
   - `ffmpeg` decodes the MP3 to PCM and pipes it to
     `/usr/local/bin/rgbfan-envelope`, which writes a 30 fps loudness envelope to
     `/run/rgbfan/env.json`;
   - writes the playback start epoch to `/run/rgbfan/talk` right before `ffplay`,
     and clears it after.
3. The daemon, in `talking` mode, steps through the envelope by wall-clock and
   maps each level to brightness → the fans pulse in sync with the voice.

### Architecture / files
- **`/usr/local/bin/rgbfan-daemon`** (root, runs `/home/behnam/anaconda3/bin/python3`)
  — the SDK client that animates colours. Run by a systemd unit.
- **`/etc/systemd/system/rgbfan-daemon.service`** (enabled) — runs the daemon;
  `ExecStartPre` creates `/run/rgbfan` (mode `0777`).
- **`/usr/local/bin/rgbfan-envelope`** — PCM→loudness-envelope helper for the hook.
- **Control dir `/run/rgbfan/`** (tmpfs, `0777`): `mode` (temp|talking|off),
  `talk` (speech start epoch), `env.json` (loudness envelope).
- **Persistence:** `rgbfan` also writes the chosen mode to **`/etc/openrgb/fanmode`**;
  on reboot the daemon falls back to that, so the last live mode resumes. `rgbfan
  restore`/`off` and any static scene reset it to `off`. (Default left as
  `talking` → dark case that pulses only when Claude speaks.)
- **Dependency:** `openrgb-python` (installed in anaconda:
  `/home/behnam/anaconda3/bin/pip3 install openrgb-python`).
- **`rgb` and static `rgbfan` cooperate** with live modes: both write `off` to the
  mode files first, so a static look/scene wins and the daemon yields.

### Tuning (edit constants at the top of `/usr/local/bin/rgbfan-daemon`)
- `temp`: `TMIN`/`TMAX` (°C range mapped to the colour gradient).
- `talking`: `HUE_CENTER`/`HUE_SWEEP` (palette), `SWEEP_HZ` (flow speed),
  `OFF_SIDE`/`OFF_BOTTOM` (cross-case hue spread), `IDLE_BRI` (silent glow — `0.0`
  = dark), `ATTACK`/`RELEASE` (pulse snappiness).
After editing: `sudo systemctl restart rgbfan-daemon.service`.

---

## The AI summary → speech → lights pipeline (the "summarizer")

`talking` mode is driven by a **summarizer**: when the AI assistant finishes a
turn, a short spoken sentence is generated from its reply and the fans pulse with
that voice. The key design choice — and the thing to preserve if you ever swap the
AI engine — is that it speaks a **one-sentence summary of the final answer, not the
answer word-by-word**. You hear *"Merged the fix and deployed to production"*, not
the whole multi-paragraph reply read aloud.

### Why summarize instead of reading verbatim
- A full assistant reply is often hundreds of words with code, paths, and lists —
  unlistenable and far longer than the work itself.
- Reading the **streaming** output word-by-word would narrate half-formed thoughts,
  tool calls, and edits. Instead we wait for the turn to **end** and take only the
  **final** assistant message, then compress it to a single outcome sentence.
- One sentence ≈ a few seconds of speech → a short, meaningful light pulse, then the
  case goes dark again (matches the ErP/"dark by default" philosophy).

### How it works today (Claude Code)
The summarizer is a Claude Code **Stop hook**:
`~/.claude/hooks/tts-summarize.sh`, wired in `~/.claude/settings.json` under
`"Stop"`. The Stop event fires once when the assistant finishes responding.

End-to-end flow:

```text
AI turn ends
   │
   ▼  (1) TRIGGER  — Claude "Stop" hook fires, receives JSON on stdin
tts-summarize.sh
   │
   ▼  (2) EXTRACT  — read transcript_path; pull text of the LAST assistant
   │                 message only (skip if it was tool-use-only / empty)
   ▼  (3) SUMMARIZE — `claude -p --model haiku` compresses it to ONE spoken
   │                 sentence (≤ CLAUDE_TTS_MAX_WORDS, default 20)
   ▼  (4) SPEAK    — edge-tts neural voice → /tmp/tts-claude-*.mp3 → ffplay
   │
   └─▶ (5) LIGHTS  — only if /run/rgbfan/mode == talking:
                     ffmpeg decodes the MP3 → rgbfan-envelope → /run/rgbfan/env.json
                     (30 fps loudness), write start epoch → /run/rgbfan/talk,
                     clear it when playback ends. The rgbfan-daemon reads the
                     envelope by wall-clock and maps loudness → fan brightness.
```

Robustness baked into the hook (so it's quiet and well-behaved):
- **Single-instance**: a new fire `pkill`s prior playback and takes a `flock`; if
  still contended it exits silently — you never get two voices.
- **Dedupe**: a SHA-1 of the reply text in `/tmp/tts-claude.last-hash` means the
  same answer is never spoken twice.
- **Non-answer guard**: skips Haiku meta-replies like *"no message to summarize."*
- **Disable switch**: `touch ~/.claude/tts-disabled`.
- **Config**: `~/.claude/tts.conf` can set `CLAUDE_TTS_VOICE`, `CLAUDE_TTS_RATE`,
  `CLAUDE_TTS_PITCH`, `CLAUDE_TTS_MAX_WORDS`.

The lights link is deliberately **decoupled**: the hook only writes
`env.json` + `talk` *if* `talking` mode is active, and the `rgbfan-daemon` only
reads them. So the summarizer speaks whether or not the fans are in `talking`
mode, and the fans react whenever a summary happens to play — neither hard-depends
on the other.

### Engine-agnostic design (use any AI assistant, not just Claude)

The pipeline is intentionally **agnostic of the AI engine behind it**. Claude is
just today's source; only three small pieces are Claude-specific, and each is a
clean swap point. Stages 4–5 (TTS + envelope + lights) are 100% engine-independent
and need no changes.

| Stage | Claude-specific today | The real contract | How to swap the engine |
|-------|-----------------------|-------------------|------------------------|
| (1) Trigger | Claude Code `Stop` hook | "fire once when a turn finishes" | any end-of-turn signal: another tool's hook, a shell wrapper that runs after the CLI exits, a file-watch on a log, a `tmux`/`expect` capture |
| (2) Extract final text | reads Claude transcript JSONL, last `assistant` entry | "give me the final reply as plain text" | pipe the other engine's last message / its console output's final block into the script on stdin instead of parsing the transcript |
| (3) Summarize | `claude -p --model haiku` | "compress text → one ≤N-word sentence" | point at any summarizer: `ollama run`, an OpenAI/`llm` CLI call, or even a local extractive summary — same prompt, different binary |
| (4) Speak | — | text → audio | already engine-agnostic (`edge-tts`) |
| (5) Lights | — | audio → loudness envelope → fan brightness | already engine-agnostic (`rgbfan-envelope` + daemon) |

In short: **the summarizer is "final AI output → one sentence → voice → light
pulse."** Only *how you obtain the final output* and *which model compresses it*
are tied to Claude; swapping those two lines re-points the whole thing at any other
AI assistant without touching the speech or the lighting.

> **Implemented (2026-06-14).** This is no longer just a design — the agnostic
> pipeline lives in the repo at
> [`scripts/ai-summary-tts/`](../../scripts/ai-summary-tts/). The summarize step is
> behind an `AI_ENGINE=claude|openai|ollama` switch (default `claude`, identical to
> the original), with TTS + envelope + lights as the shared backend. The live
> `~/.claude/hooks/tts-summarize.sh` is now a thin shim that delegates to it; the
> original Claude-only hook is backed up as `tts-summarize.sh.bak-2026-06-14`
> (one-line revert in that script's README).

---

## What is installed / configured

### 1. udev rules — non-root access to the USB controllers
`/etc/udev/rules.d/60-openrgb-local.rules`:
```udev
# Local OpenRGB access for detected RGB controllers
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0b05", ATTRS{idProduct}=="19af", MODE:="0660", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="1b1c", ATTRS{idProduct}=="0c2a", MODE:="0660", GROUP="plugdev", TAG+="uaccess"
```
- `0b05:19af` = ASUS AURA LED Controller, `1b1c:0c2a` = Corsair Commander Core XT.
- `behnam` is in group `plugdev`, so these `hidraw` nodes become user-accessible.
- The AppImage still pops a generic "udev rules not installed" warning — it is
  **cosmetic** here; the rules that matter are installed.
- Note: the RAM/GPU controllers are on **i2c/SMBus**, not hidraw, and still
  require **root** regardless of these rules.

### 2. i2c / SMBus access — needed for RGB RAM + GPU
- Kernel boot param in `/etc/default/grub` (backup `/etc/default/grub.bak.*`):
  ```
  GRUB_CMDLINE_LINUX_DEFAULT="quiet splash acpi_enforce_resources=lax"
  ```
  then `sudo update-grub`. Live in `/proc/cmdline` after the reboot (done).
- `/etc/modules-load.d/i2c-dev.conf` contains `i2c-dev`. On kernel 6.17 `i2c-dev`
  is **builtin** (`modules.builtin`), so `lsmod` won't list it and the service's
  `modprobe i2c-dev` is a harmless no-op; `/dev/i2c-*` nodes are present anyway.
- `i2c-tools` package installed.

### 3. Root service + profile system (the single source of truth)
Only **one** OpenRGB server may hold the USB devices at a time, so this root
service is the only one that runs. The old per-user `openrgb-off.service` was
`disable --now`'d. `loginctl enable-linger behnam` is on.

`/etc/systemd/system/openrgb.service`:
```ini
[Unit]
Description=OpenRGB - apply RGB look to all controllers (root, incl. i2c RGB RAM)
After=multi-user.target
[Service]
Type=simple
ExecStartPre=/sbin/modprobe i2c-dev
ExecStart=/usr/local/bin/openrgb-apply.sh
Restart=on-failure
RestartSec=3
[Install]
WantedBy=multi-user.target
```
Enabled with `sudo systemctl enable --now openrgb.service`.

The service runs **`/usr/local/bin/openrgb-apply.sh`**, which applies a named
"look" to every controller and then holds state as a resident `--server`. With no
argument it reads the look name from **`/etc/openrgb/look`** (so the chosen look
re-applies on every boot). It is the brain behind the `rgb` command (see
**Operating it**). Looks are defined in a `case` statement: `off`, `white`,
`rainbow`, `spectrum`, `breathing`, solid colors, and `color:RRGGBB`.

Key implementation points inside the script:
- A dynamic per-device loop (`--list-devices` count) sets every controller, so new
  controllers are covered automatically.
- An explicit `--device "ASUS TUF…" --zone 3 --size 120 …` line sizes and drives
  the **be quiet! hub** (Aura Addressable 3 ships at size 0; without sizing, the
  hub fans aren't addressed and free-run rainbow).
- For effect looks (`rainbow`/`spectrum`/`breathing`) it sets **hardware modes**
  on the controllers that support them (RAM, GPU, Aura/be quiet hub) and a
  **static** rainbow/colour on the Direct-only Corsair top fans, which can't
  animate. See the mode table in **Gotchas**.

Resident (not one-shot) on purpose: the Corsair Commander Core XT only has a
volatile `Direct` mode and drifts back to firmware rainbow if no host holds it.
(`openrgb-apply.sh` with look `off` reproduces the original all-off behaviour.)

**Passwordless `rgb` command** — `/etc/sudoers.d/openrgb-rgb` (mode 0440) grants:
```
behnam ALL=(root) NOPASSWD: /usr/local/bin/rgb
```
so `rgb <look>` re-execs itself under sudo without a password. The rule is scoped
to that one root-owned script (behnam cannot modify it, so this is not a privesc).

#### Where these files came from (provenance)
None of this ships with OpenRGB or Ubuntu — they are **custom files created for
this setup** (not from any package). What each one is and how it was installed:

| File | Owner/mode | What it is |
|------|-----------|------------|
| `/usr/local/bin/rgb` | root 0755 | the command you type; validates the look, writes `/etc/openrgb/look`, restarts the service |
| `/usr/local/bin/openrgb-apply.sh` | root 0755 | the "brain" the service runs; builds the OpenRGB args for a look and holds them as a `--server` |
| `/usr/local/bin/rgbfan` | root 0755 | per-fan / per-group control + live modes; see **The `rgbfan` command** and **Live effects** |
| `/usr/local/bin/rgbfan-daemon` | root 0755 | SDK-client daemon for `temp`/`talking` live modes (runs anaconda python) |
| `/usr/local/bin/rgbfan-envelope` | root 0755 | PCM→loudness-envelope helper used by the TTS hook for `talking` mode |
| `/etc/openrgb/look` | root 0644 | one word — the active `rgb` look, re-applied on boot |
| `/etc/openrgb/fanmode` | root 0644 | persisted live mode (temp/talking/off) the daemon resumes on boot |
| `/etc/sudoers.d/openrgb-rgb` | root 0440 | the NOPASSWD rule above |
| `/etc/sudoers.d/openrgb-rgbfan` | root 0440 | NOPASSWD rule for `rgbfan` |
| `/etc/systemd/system/openrgb.service` | root 0644 | the resident server that runs `openrgb-apply.sh` |
| `/etc/systemd/system/rgbfan-daemon.service` | root 0644 | runs `rgbfan-daemon` for live modes (enabled) |
| `~/.claude/hooks/tts-summarize.sh` | behnam 0755 | TTS Stop hook; feeds the speech envelope to `talking` mode (see **Live effects**) |

They were installed by writing each to a temp file and copying it into place as
root, e.g.:
```bash
sudo install -m 0755 -o root -g root /tmp/rgb              /usr/local/bin/rgb
sudo install -m 0755 -o root -g root /tmp/openrgb-apply.sh /usr/local/bin/openrgb-apply.sh
```
`/usr/local/bin` is on `$PATH`, which is why `rgb` is runnable from anywhere.
Flow when you run `rgb rainbow`: **`rgb`** (writes `look`, restarts service) →
**`openrgb.service`** → **`openrgb-apply.sh rainbow`** → OpenRGB pushes colors to
the hardware and holds them.

### 4. Root OpenRGB config
`/root/.config/OpenRGB/` is OpenRGB's config dir when run as root — it holds the
live `OpenRGB.json` (detector settings) and `sizes.ors` (zone-size cache), both
managed by OpenRGB itself; leave them alone. The root service does **not** load
any `.orp` profile — `openrgb-apply.sh` sets every look via CLI args. Save custom
GUI profiles here if you want the native `--profile` workflow (see **Operating
it**); the old stale `alloff/calm/diag-red.orp` profiles were removed during
cleanup (they predated the hub fix and had `Aura Addressable 3` at size 0).

### 5. BIOS (ASUS, Del → F7 Advanced Mode)
- **Enabled:** Advanced → APM Configuration → **ErP Ready → Enabled (S4+S5)**.
  Cuts all standby power → everything dark when the PC is off. Trade-off: no
  Wake-on-LAN, no USB wake, no USB charging while off.
- Alternative (keeps WoL, motherboard-Aura only): Advanced → Onboard Devices
  Configuration → RGB LED Lighting → "When system is in sleep/hibernate/soft-off"
  → Off. Not used here; ErP is the total kill and was preferred.

---

## Operating it

### The `rgb` command (everyday use)
`/usr/local/bin/rgb` switches looks instantly, no password, and the choice
persists across reboots (saved in `/etc/openrgb/look`, re-applied by the service).

```bash
rgb                 # show the current look
rgb list            # list available looks
rgb <look>          # switch (e.g. rgb off, rgb white, rgb rainbow)
rgb color 22AAFF    # any solid 6-digit hex colour
```

Available looks: `off`, `white` (= `calm`), `rainbow`, `spectrum`, `breathing`,
`red`, `green`, `blue`, `cyan`, `purple`, `pink`, `warm`, `ocean`, `fire`.
Whatever look is active is what comes back on the next boot.

- `off` — everything Direct black (the original behaviour).
- `white`/`calm` — soft ~70% white, good for a work machine.
- `rainbow` — hardware moving rainbow on RAM/GPU/Aura/be quiet hub; static rainbow
  ring on the Direct-only Corsair top fans.
- `spectrum` — smooth whole-device colour cycle where supported.
- `breathing` — slow blue pulse (Corsair held static blue).
- solids + `color:RRGGBB` — one Direct colour on everything.

To **add or tweak a look**, edit the `case` block in
`/usr/local/bin/openrgb-apply.sh`, then re-apply with `rgb <look>` (or
`sudo systemctl restart openrgb.service`).

### Service control / device list
```bash
sudo systemctl status openrgb.service
sudo systemctl restart openrgb.service       # re-applies the saved look
sudo HOME=/root /home/behnam/OpenRGB_1.0rc2_x86_64_0fca93e.AppImage --list-devices
```

### Tweak colors interactively in the GUI
The root service holds the devices, so stop it first, then run the GUI as root
(i2c devices need root):
```bash
sudo systemctl stop openrgb.service
sudo HOME=/root /home/behnam/OpenRGB_1.0rc2_x86_64_0fca93e.AppImage
# …then re-enable the service when done:
sudo systemctl start openrgb.service
```

### Doing the same thing natively in OpenRGB (its own profiles)
OpenRGB has a built-in profile system; the `rgb` command is a wrapper that exists
only to work around this rig's quirks (see caveats). If you'd rather use OpenRGB
directly:

**In the GUI** (run it as root so RAM/GPU are included — stop the service first):
1. `sudo systemctl stop openrgb.service`
2. `sudo HOME=/root /home/behnam/OpenRGB_1.0rc2_x86_64_0fca93e.AppImage`
3. Per device, pick a **Mode** (Direct / Static / Rainbow / Spectrum Cycle / …) and
   color. For the be quiet! fans, select the **ASUS Aura** device → **Aura
   Addressable 3** zone and set its **size to 120** (Zones → resize) so its LEDs
   exist, then choose a mode/color.
4. Bottom bar → type a name → **Save Profile**. Repeat for each look (e.g.
   `rainbow`, `calm`, `off`). Profiles are written to
   `/root/.config/OpenRGB/<name>.orp` (or `~/.config/OpenRGB/` if run as your user).
5. Load any profile later from the **Profiles** dropdown, or apply it from the CLI:

```bash
# Load a saved profile by name (looks in the running user's ~/.config/OpenRGB)
sudo HOME=/root /home/behnam/OpenRGB_1.0rc2_x86_64_0fca93e.AppImage --profile rainbow
```

**Caveats that make the native path awkward here** (and why the script exists):
- **`--profile` is one-shot** — it sets colors and exits. The **Corsair Commander
  Core XT is Direct-only and drifts back to firmware rainbow** seconds later unless
  a server keeps holding it. You'd need to keep an OpenRGB `--server` running, e.g.
  `… --server --profile rainbow`, which is exactly what `openrgb-apply.sh` does.
- **Root + i2c** — RAM and GPU only appear when OpenRGB runs as root with `i2c-dev`
  available, so GUI/CLI must be run with `sudo HOME=/root …`.
- **Zone size 0** — a freshly loaded profile only drives the be quiet! hub if its
  `Aura Addressable 3` zone was saved at size 120; otherwise those fans stay on
  their own rainbow. Set the size before saving.
- **One server at a time** — stop `openrgb.service` before launching the GUI, or
  they fight over the USB devices. Restart it when done.

So: native profiles are fine for occasional manual tweaking, but the resident
`openrgb.service` + `rgb` command is what makes a look **stick across reboots and
hold the Direct-only Corsair**.

**Best of both — wire a GUI-designed profile into the `rgb` menu.** Design a look
in the GUI, size `Aura Addressable 3` to 120, and **Save Profile** as e.g.
`mylook` (lands in `/root/.config/OpenRGB/mylook.orp`). Then add a branch to the
`case` block in `/usr/local/bin/openrgb-apply.sh` that loads it as a resident
server:
```bash
  mylook)
    # Look designed + saved in the OpenRGB GUI (/root/.config/OpenRGB/mylook.orp).
    # --profile restores the saved modes/colours AND the AA3 zone size, and
    # --server holds it so the Direct-only Corsair doesn't drift.
    exec "$APP" --server --profile mylook
    ;;
```
(That branch `exec`s directly, so it skips the `ARGS`/`all_direct` machinery —
the profile already carries everything, including the hub zone size, as long as
you sized AA3 before saving.) After editing, `rgb mylook` switches to it and it
survives reboots like any other look.

### Re-testing the wiring later
The one-off diagnostic scripts were removed during cleanup. If you ever need to
re-identify which controller drives a fan, the method is reproducible: stop the
service, run a transient root unit that sets each controller (or each Aura
Addressable zone) to a distinct color, photograph the case, then restart the
service — exactly the colored-flash approach recorded under **History**.

---

## Gotchas / lessons (so we don't re-learn them)

- **Corsair Commander Core XT only honors `--mode direct`.** A `--mode static`
  command silently does nothing on it (a static-red test once showed no change
  and looked like the fans weren't Corsair — they were). Always use Direct here.
  This is why the top fans get a *static* rainbow ring rather than a moving one.
- **Per-controller mode support** (from `--list-devices`), which is what the
  effect looks rely on:

  | Controller                     | Hardware effects available                                   |
  |--------------------------------|--------------------------------------------------------------|
  | Corsair Commander Core XT      | **Direct only** — no hardware effects                        |
  | Corsair Vengeance Pro RGB (RAM)| Direct, Static, Rainbow Wave, Color Shift/Pulse/Wave, Rainbow, …|
  | ASUS RTX 3090 GPU              | Direct, Off, Static, Breathing, Spectrum Cycle, Rainbow, Chase, …|
  | ASUS TUF Aura (incl. be quiet hub via Aura Addressable 3) | Direct, Off, Static, Breathing, Spectrum Cycle, Rainbow, Chase, … |
- **A zone at size 0 means OpenRGB addresses zero LEDs on it** — it sends the
  attached device *no data*, so the device free-runs its own effect. A device on
  a 0-sized addressable header looks "uncontrollable" but isn't; just resize it.
  Sizing larger than the physical LED count is harmless.
- **`lsusb` only shows USB controllers.** Passive ARGB hubs (like the be quiet!
  one) are invisible to it; absence from `lsusb` is **not** proof a thing is
  unreachable. Trace cables / test the addressable headers instead.
- **`--client localhost:6742 --list-devices` double-lists devices** in this build
  (server's devices + a local detection pass). Don't target by index in client
  mode; stop the service and run a single fresh instance instead.
- **Cosmetic log lines** to ignore: the AppImage "udev rules not installed"
  warning, the `Failed to read i2c device PCI device ID` line (one SMBus adapter
  among ~18 failing a probe — all 5 devices still detect), and a "Connection
  attempt failed" autoconnect probe when no server is already running.
- **Only one OpenRGB server at a time** can own the USB HID devices. The root
  service is it; keep the user service disabled.
- **ARGB is 5V 3-pin; the board also has a 12V 4-pin RGB header** that looks
  similar. If ever rewiring, match the keying/arrow — wrong header destroys ARGB
  gear. (Not needed for the current setup; the hub is already on a 3-pin header.)

---

## Outstanding TODO

- **Rotate the sudo password.** It was pasted into agent chat transcripts on
  2026-06-13 — treat as burned:
  ```bash
  passwd
  secret-tool store --label="server sudo" service sudo host server user behnam
  ```
  Agents pull it via `secret-tool lookup service sudo host server user behnam | sudo -S …`
  (see `linux-keyring.md`). Never paste it again.

---

## History (condensed)

- **2026-03-18 — install + non-root access.** Set up the AppImage, identified the
  two USB controllers (Corsair Commander Core XT, ASUS Aura board), installed the
  `60-openrgb-local.rules` udev file so `behnam` could reach the hidraw nodes
  without root. Launched the GUI; saw the zone-resize prompt.
- **2026-06-13 — "lights off" automation, first cut.** Created profiles
  (`alloff`, `calm`) and a per-user `openrgb-off.service`. Found that lights stay
  on when the PC is *off* because of +5V standby power — a BIOS matter, not
  software (ErP Ready fixes it).
- **2026-06-13 — RAM + GPU brought under control.** The earlier
  `Failed to read i2c device PCI device ID` was just `i2c-dev` not loaded, not a
  locked SMBus. Added `acpi_enforce_resources=lax`, `i2c-dev` autoload, and
  `i2c-tools`; moved to a **root** `openrgb.service` running `openrgb-alloff.sh`
  so it can reach the i2c RAM/GPU. Disabled the user service. Now holding 5
  controllers dark.
- **2026-06-13 — red-flash diagnostic + a WRONG conclusion.** A red flash showed
  top fans + RAM + Aura controllable, but side/bottom be quiet! fans stayed
  rainbow. Because `lsusb` showed no third USB controller, this was (incorrectly)
  written up as "no software path — must rewire or use a hub button." That test
  was run while the Aura Addressable zones were size 0, so it was a false
  negative.
- **2026-06-13 (evening) — photos/videos/audio reviewed; theory corrected.** The
  hub is a be quiet! ARGB hub with a `RGB IN` that the user traced to the
  motherboard. Config inspection showed all `Aura Addressable` zones at size 0 →
  the hub was simply never being addressed. Predicted a software-only fix.
- **2026-06-13 (night) — RESOLVED.** A 3-color flash (Aura Addressable 1/2/3 =
  red/green/blue at size 120) turned the be quiet! fans **blue** → they're on
  **Aura Addressable 3**. A Corsair-Direct-red vs AA3-blue pass confirmed top
  fans = Corsair, the rest = the hub. Baked `--zone 3 --size 120 … black` into
  `openrgb-alloff.sh` and restarted the service. Case now fully dark while
  running. Done.
- **2026-06-13 — profile system + `rgb` command.** Replaced the single-purpose
  all-off script with `/usr/local/bin/openrgb-apply.sh` (named looks: off / white /
  rainbow / spectrum / breathing / solids / `color:RRGGBB`), driven by
  `/etc/openrgb/look` and switched via a passwordless `/usr/local/bin/rgb`
  wrapper (sudoers drop-in `openrgb-rgb`). Effect looks use hardware modes where
  supported and a static rainbow ring on the Direct-only Corsair fans. Service
  unit repointed to `openrgb-apply.sh`. Later cleanup removed the superseded
  `openrgb-alloff.sh` and the stale `alloff/calm/diag-red.orp` profiles (the
  `off` look reproduces the old all-off behaviour anyway).
