<img src="custom_components/recuperator/brand/icon.png" alt="" width="96" align="right">

# Recuperator

A Home Assistant integration that runs a **single-tube ceramic recuperator** (a small heat-recovery ventilator) as a continuous **breathing cycle**:

1. **Exhaust**: one fan blows indoor air out through the ceramic core. The core soaks up the indoor air's heat (or coolness).
2. **Pause**: both fans off for a moment.
3. **Intake**: a second fan blows outdoor air in through the same core, which gives the stored heat back to the incoming air.
4. **Pause**, then exhaust again, and so on.

Two temperature probes, one at each end of the core, tell the integration when each phase has done its job, so the cycle adapts to the weather: long, efficient phases when there is a big temperature difference, shorter capped phases in deep cold, and simple timed breathing when indoor and outdoor air are about the same temperature. The goal is to flush moisture out of a basement (or any room) while losing as little heat as possible.

The cycle runs inside the integration, not in automations, so there are no automation traces every half minute. It has its own device with a Breathing switch, a Mode selector, read-outs, and every setting exposed for tuning.

## Contents

- [What you need](#what-you-need)
- [Installing](#installing)
- [Adding a recuperator](#adding-a-recuperator)
- [First run](#first-run)
- [How the cycle decides when to switch](#how-the-cycle-decides-when-to-switch)
- [Entities](#entities)
- [Modes](#modes)
- [Settings](#settings)
- [Resetting to defaults](#resetting-to-defaults)
- [Tuning guide](#tuning-guide)
- [Safety behaviour](#safety-behaviour)
- [Dashboard card](#dashboard-card)
- [Troubleshooting](#troubleshooting)

## What you need

- **Two fans, each on its own on/off switch** in Home Assistant: an *exhaust* fan (inside to outside) and an *intake* fan (outside to inside). Any `switch`, `fan`, `light` or `input_boolean` entity works, for example the two outlets of a smart plug.
- **Two temperature sensors in the airflow at the two ends of the core**:
  - the **inside probe** at the indoor end: it reads indoor air while exhausting;
  - the **outside probe** at the outdoor end: it reads outdoor air while taking air in.

  DS18B20 probes on an ESP32 with ESPHome work well. Read them every few seconds (for example `update_interval: 2s`), because a phase only lasts tens of seconds. An ESPHome `delta` filter (for example `delta: 0.1`) is fine; the integration treats "no new value" as "unchanged".

- Home Assistant 2025.2 or newer.

## Installing

Through HACS:

1. HACS, the three-dot menu, **Custom repositories**. Add `https://github.com/geirskogul/recuperator` with type **Integration**.
2. Find **Recuperator** in HACS, **Download**.
3. **Restart Home Assistant**.

To update later: HACS shows the new version; Update, then restart Home Assistant.

## Adding a recuperator

Settings, Devices & services, **Add integration**, **Recuperator**, then pick:

| Field | What to choose |
| --- | --- |
| Name | Anything, for example *Basement Breather*. Entity names start with it. |
| Exhaust fan | The switch for the fan that blows **indoor air out** |
| Intake fan | The switch for the fan that blows **outdoor air in** |
| Inside probe | The sensor at the **indoor end** of the core |
| Outside probe | The sensor at the **outdoor end** of the core |

Everything else has sensible defaults and can be changed later. To point it at different fans or probes afterwards: the integration's three-dot menu, **Reconfigure** (settings are kept).

## First run

A new recuperator starts with **Breathing off**, and it does not touch the fans until you switch it on.

1. Set **Mode** to **Timed** and turn **Breathing** on. Check that the exhaust fan runs first, then both stop, then the intake fan runs. If they are the wrong way round, swap them with **Reconfigure**.
2. Watch the two probe readings. During exhaust the outside probe should move towards the inside probe's temperature; during intake the inside probe should move towards the outside probe's. If it is the other way round, the probes are swapped: **Reconfigure**.
3. Set **Mode** to **Automatic**. The first couple of cycles learn the indoor and outdoor air temperatures; after that the phases adapt.

## How the cycle decides when to switch

In each phase, one probe reads the air being blown through (the **reference**) and the other shows how far the core's far end has caught up (the **far** probe):

| Phase | Fan | Reference probe | Far probe |
| --- | --- | --- | --- |
| Exhaust | exhaust fan | inside (indoor air) | outside (outer end of the core warming) |
| Intake | intake fan | outside (outdoor air) | inside (inner end of the core cooling) |

A running phase ends at the first of:

1. **Supply colder than room** (intake only, after *Minimum phase*): the air entering the room is more than *Maximum supply drop* below the room temperature. **Supply below floor**: the same against the optional fixed *Minimum supply temperature*.
2. **Recovered**: the far probe has closed *Recovery target* per cent of the gap between where it started and the air temperature. The air temperature is the reference probe, or the last measured indoor/outdoor temperature while the reference probe is still catching up after the switch (probes need 10–30 s to register a change).
3. **Settled**: the far probe has got at least halfway to the recovery target, and both probes have since moved less than *Settle change* during the last *Settle window*. A far probe that has not moved yet does **not** count as settled: it means the core is still doing its job.
4. **Maximum phase**: a hard time limit.

...but never before **Minimum phase**. Then both fans are off for **Pause**, and the other phase starts.

**Timed phases** (a fixed *Timed phase* length) are used instead when:
- Mode is **Timed**;
- indoor and outdoor air are less than **Similar temperatures** apart (mild weather, or the core disconnected); there is nothing to recover, but the room still breathes;
- a probe is unavailable (the phase finishes on time; the next phase uses the probes again if the probe is back).

**Cold weather** (outdoor air at or below **Cold threshold**, measured by the outside probe during intake):
- Intake never runs longer than **Cold intake limit** (default 45 s), so less cold air comes in.
- Exhaust runs **at least as long as the last intake**, and at most **Cold exhaust extra** seconds longer. Warm air leaving last keeps the core's outdoor end from icing up and keeps the room from cooling down.
- The **Cold weather** indicator is on while these limits apply.

The same logic works in heat (summer): the core stores the indoor coolness during exhaust and cools the incoming air.

## Entities

For a recuperator named *Basement Breather*:

| Entity | What it is |
| --- | --- |
| `switch.basement_breather_breathing` | **Breathing** on/off. Off: both fans are switched off once, then left alone (you can run them by hand). |
| `select.basement_breather_mode` | **Mode**: Automatic, Timed, Exhaust only, Intake only |
| `sensor.basement_breather_phase` | Exhaust, Pause, Intake or Stopped. Attributes: when the phase started, the next phase, whether (and why) it is timed, cold weather, mode |
| `sensor.basement_breather_last_change_reason` | Why the last phase ended: Recovered, Settled, Maximum time, Cold limit, Timed, Supply colder than room, Supply below floor, Started, Stopped |
| `sensor.basement_breather_last_exhaust` / `..._last_intake` | Length of the last exhaust / intake phase (s) |
| `sensor.basement_breather_basement_temperature` | Indoor air temperature: the inside probe at the end of the last exhaust |
| `sensor.basement_breather_outdoor_temperature` | Outdoor air temperature: the outside probe at the end of the last intake |
| `sensor.basement_breather_heat_recovery` | How much of the gap the core closed in the last temperature-driven phase (%) |
| `image.basement_breather_diagram` | A live picture of the pipe, filled with the temperature gradient (see [Diagram](#diagram)) |
| `image.basement_breather_diagram_replay` | The last animated replay made with **Create replay** (see [Replay](#replay-watch-it-breathe)) |
| `text.basement_breather_diagram_colours` | The diagram's colour scale, editable (see [Diagram colours](#diagram-colours)) |
| `binary_sensor.basement_breather_cold_weather` | On while the cold-weather limits apply |
| `binary_sensor.basement_breather_frost_risk` | On if, in cold weather, the last exhaust never warmed the core's outdoor face above freezing (condensation there can ice up) |
| `number.basement_breather_...` | One per setting (below), under the device's **Configuration** section |
| `button.basement_breather_reset_settings_to_defaults` | Puts every setting back to its default |

Breathing and Mode survive a Home Assistant restart, and so do the learned basement and outdoor temperatures. After a restart the cycle waits up to 60 s for both probes to report, then starts again with exhaust.

## Modes

| Mode | What happens |
| --- | --- |
| **Automatic** | The probe-driven cycle described above (normal use) |
| **Timed** | Fixed-length phases (*Timed phase*), ignoring the probes except for the cold intake limit. Good for checking the wiring, or if the probes are unreliable. |
| **Exhaust only** | The exhaust fan runs continuously (for example to dry the room out quickly, or to test airflow) |
| **Intake only** | The intake fan runs continuously |

Changing mode takes effect within a second; a running fan is stopped and the pause is kept before the other starts.

## Settings

All settings take effect within a second, without restarting the cycle. Change them either on the device page (the **Configuration** section, one number per setting, handy on a dashboard), or all together with **Configure**, then **Settings**.

| Setting | Default | Range | What it does |
| --- | --- | --- | --- |
| Recovery target | 80 % | 5–100 | End a phase when the far probe has closed this much of the gap. See [Choosing the recovery target](#choosing-the-recovery-target). |
| Settle window | 15 s | 5–120 | Time over which "settled" is judged |
| Settle change | 0.2 °C | 0.05–2 | Both probes moving less than this over the settle window counts as settled |
| Minimum phase | 20 s | 5–300 | No phase ends sooner (protects the fans and relays from rapid switching) |
| Maximum phase | 120 s | 10–900 | No phase lasts longer. If set below Minimum phase, Minimum phase wins. |
| Pause | 1 s | 0–30 | Both fans off between phases |
| Timed phase | 60 s | 10–900 | Phase length in Timed mode, in mild weather, and while a probe is unavailable |
| Similar temperatures | 2.0 °C | 0–20 | Indoor and outdoor air closer than this: timed phases instead of probe-driven ones. 0 turns this off. |
| Cold threshold | −5 °C | −40–15 | Outdoor temperature at or below which the cold-weather limits apply |
| Cold intake limit | 300 s | 10–900 | In cold weather, intake never runs longer than this. Leave enough time for fresh air to get through the ducting (see [Winter](#winter)) |
| Cold exhaust extra | 7 s | 0–60 | In cold weather, exhaust runs at least as long as the last intake and at most this much longer |
| Maximum supply drop | 3 °C | 0.5–20 | During intake, once *Minimum phase* has passed, end the intake if the air entering the room is more than this much colder than the room (the basement temperature measured at the end of the last exhaust). Follows the room, so it works in every season. **The main draught protection.** |
| Diagram colours | weather-service scale | text | The diagram's colour stops (see [Diagram colours](#diagram-colours)) |
| Minimum supply temperature | −30 °C (off) | −30–25 | Optional hard floor: the intake also ends if the air entering the room drops below this. Only set it if there is a temperature the room must never see (for example water pipes). |

## Choosing the recovery target

The probes sit in the airstream at the two faces of the core. During intake, the inside probe reads the air leaving the core into the room. With a good core it stays close to room temperature for a long time, because the core warms the incoming air, and only starts to drop when the core has given up most of its stored heat (the "breakthrough"). Exhaust works the same way in reverse at the outdoor face.

- **Low target (10–25 %)**: switch as soon as the far end *starts* to change. The core never runs out, so incoming air arrives warm. **Best heat recovery.** This is how commercial single-tube units work; they reverse about every minute.
- **High target (70–90 %)**: let the core fill up or empty completely. Longer phases move more air per phase (**more ventilation** through a long hose), but the last part of each phase recovers little heat.
- With a large core or slow fans, even a low target can take minutes to reach. *Maximum phase* then sets the rhythm; that is fine.

If ventilation matters more than heat recovery, use a higher target or longer *Minimum phase*, and rely on *Minimum supply temperature* and the cold-weather limits to protect the room in winter.

## Winter

A basement can be cold in winter (say 7 °C with −25 °C outside), and it should still breathe. So the room is protected **relative to its own temperature**, not by a fixed number:

- **Maximum supply drop** ends an intake when the incoming air is more than 3 °C (default) colder than the room. With a 7 °C basement, intake continues while the core warms the incoming air to 4 °C or more.
- **Minimum phase** comes first, so every intake runs long enough to actually bring fresh air in. With a long hose or slow fans that can take several minutes: at first the intake just pulls back the air that was exhausted into the hose. Watch the outside probe during an intake: when it has settled at outdoor temperature, fresh air is arriving.
- **Cold intake limit** is a backstop, not the main protection. Keep it longer than the time fresh air needs to arrive.
- **Exhaust runs at least as long as the last intake**, and at most *Cold exhaust extra* longer, which keeps warming the core's outdoor end.
- **Frost risk**: if a cold-weather exhaust never gets the core's outdoor face above freezing, humid exhaust air can ice up there. The *Frost risk* indicator comes on; raise *Cold exhaust extra* or shorten the intakes.

## Real-world example

Measured on the first installation (three ceramic cores in a row, small duct fans at low speed, a hose to outside, outdoor air 12 °C, basement 20.5 °C):
- Fresh air took about **3 minutes** to reach the core during intake (the outside probe settling at outdoor temperature).
- For the first **3 minutes** of intake the air entering the basement stayed within 0.3 °C of room temperature; after that it fell about 0.9 °C a minute.
- With *Maximum supply drop* 3 °C, intakes end after about 6–7 minutes; exhausts behave the same way in reverse. A full breath takes about 12–15 minutes.

## Upgrading

Settings you already have keep their stored values when a new version changes a default. After upgrading to 0.1.3, check **Cold intake limit** (the old default 45 s is too short for most duct runs; the new default is 300 s) and **Minimum supply temperature** (now off by default, −30 °C). Or press **Reset settings to defaults**, which resets all settings.

## Resetting to defaults

- **Reset settings to defaults** (button on the device page) resets all settings **and** the diagram colours.
- **Configure**, **Settings**, tick **Reset settings to defaults**: resets the numeric settings only; the colours are kept.
- **Configure**, **Diagram colours**, tick **Reset colours to defaults**: resets the colours only.

The chosen fans and probes, Breathing and Mode are always kept.

## Tuning guide

| What you see | Try |
| --- | --- |
| Phases end very quickly, lots of switching | Raise *Minimum phase*, or raise *Recovery target* |
| Phases always hit *Maximum time* (Last change reason) | The core never gets to the target: lower *Recovery target* (e.g. 70 %), or raise *Maximum phase* if you want it to keep going |
| Phases often end as *Settled* at low heat recovery | The probes are slow or the fans weak: raise *Settle window* (e.g. 25 s) |
| Cold draughts | Lower *Maximum supply drop* (e.g. 2 °C) |
| Intakes end too soon to bring in fresh air | Raise *Maximum supply drop*, and make *Minimum phase* at least as long as fresh air takes to reach the core (watch the outside probe settle at outdoor temperature during intake) |
| *Frost risk* comes on | Raise *Cold exhaust extra*, or shorten intakes (*Maximum supply drop*, *Cold intake limit*) |
| Phases always run to *Maximum phase* | Normal with a big core and slow fans: the far end never reaches the target. Lower *Recovery target*, or treat *Maximum phase* as your cycle length |
| Frost or ice at the outdoor end of the core | Raise *Cold exhaust extra*, so warm air runs longer after each intake |
| Room not drying out | Lower *Recovery target* (more air changes per hour), or run *Exhaust only* for a while |
| Always timed when the weather is mild | Expected. Lower *Similar temperatures* if you want the probes to decide even for small differences |

The **Last change reason**, **Last exhaust**, **Last intake** and **Heat recovery** sensors, shown in history graphs, are the best guide to what the cycle is doing.

## Safety behaviour

- **The two fans are never on together.** A fan is only switched on after the other one reports *off*. If a switch does not turn off (a stuck relay, a lost network connection), the cycle waits rather than start the other fan.
- **Always switch off before switching on**, with the *Pause* in between.
- Commands are re-sent at most every 5 seconds if a switch does not follow, and not at all while a switch is unavailable.
- **Breathing off** switches both fans off once and then leaves them alone. **Removing or disabling the integration** also switches both fans off.
- A probe that becomes unavailable does not stop breathing: the phase finishes on time.
- **Relay wear:** a cycle of about a minute means roughly 2,500 switchings a day per fan. Ordinary relays (for example in smart plugs) are not rated for that for long. For permanent use, switch the fans with solid-state relays, or lengthen the phases (*Minimum phase*, *Recovery target*).

## Diagram

The **Diagram** image entity draws the recuperator as a pipe with pinched ends: the **inside** (room) end on the left, the **outside** end on the right. The inside of the pipe is filled with a gradient from the inside probe's temperature to the outside probe's, in colours approximating the U.S. National Weather Service temperature maps (purple for extreme cold, blues around freezing, greens, yellow, orange, red for heat). The current phase and the airflow direction are shown above it: exhaust flows left to right (inside to outside), intake right to left. It redraws when the phase changes, and when the probes change at most every 10 seconds. The gradient is drawn straight between the two probes; the real temperature inside the core is not measured.

![Winter example](docs/diagram-winter.svg)
![Summer example](docs/diagram-summer.svg)

### Diagram colours

Both the temperature setpoints and their colours can be changed:
- **Configure**, then **Diagram colours**: one row per stop, with a temperature box and a **colour picker**, pre-filled with the current scale. Change a colour by clicking its colour box. Clear a temperature to remove that stop, or fill in one of the empty rows to add one (up to 20 stops). The order does not matter: stops are sorted by temperature. Tick **Reset colours to defaults** to restore the weather-service scale.
- Or, for quick edits from a dashboard, the **Diagram colours** text entity on the device page: the whole scale on one line, stops separated by `;`, for example `-10 #2f6fdc; 5 #3cc4c6; 20 #f1e344; 35 #d9401f`.

Colours in between stops are blended, and temperatures beyond the ends use the end colours. An invalid scale (a stop without a colour, two stops at the same temperature, fewer than two stops) is refused, and the old one is kept. The diagram redraws as soon as the colours change.

The default scale:

| °C | −40 | −30 | −20 | −12 | −5 | 0 | 5 | 10 | 15 | 20 | 25 | 30 | 35 | 40 | 46 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| colour | `#e3c6f5` | `#b07ad6` | `#6f3fb4` | `#3a3fbf` | `#2f6fdc` | `#4fa3ec` | `#3cc4c6` | `#46bf62` | `#9fd34a` | `#f1e344` | `#f6b637` | `#ee7f25` | `#d9401f` | `#a8161f` | `#7b0b43` |

Show it on a dashboard with a Picture Entity card:

```yaml
type: picture-entity
entity: image.basement_breather_diagram
show_name: false
show_state: false
```

## Replay: watch it breathe

The action **Recuperator: Create replay** turns recorded history into an **animated diagram**. The gradient shifts through each breath, the Exhaust/Intake label and arrow switch with each phase, and a marker moves along a timeline showing the time of day. It loops, and needs nothing but a browser.

Run it from Developer tools, Actions, or from an automation or script:

```yaml
action: recuperator.create_replay
data:
  hours: 24              # how much history (0.25 to 168)
  playback_seconds: 60   # length of one loop
  # end: "2026-09-25 08:00:00"   # optional, default now
  # frames: 0            # 0 = one per minute of history (60 to 1440)
```

The result:
- appears in the **Diagram replay** image entity. Show it with a Picture Entity card, like the live diagram:
  ```yaml
  type: picture-entity
  entity: image.basement_breather_diagram_replay
  show_name: false
  show_state: false
  ```
- is saved as `/config/www/recuperator/<name>-replay.svg`, reachable at `http://<home-assistant>:8123/local/recuperator/<name>-replay.svg`. Open it in any browser, or share the file. Home Assistant only serves `/local/` if the `www` folder existed when it started, so if the link does not work the first time, restart Home Assistant once.
- The action also returns the file's address, the period and the number of frames (Developer tools shows this as the response).

It uses the recorder's history of the two probes and the Phase sensor, so it can only go back as far as the recorder keeps history (10 days by default). Each run replaces the previous replay.

A fresh replay of the last day, every morning:

```yaml
automation:
  - alias: Recuperator daily replay
    triggers:
      - trigger: time
        at: "06:00:00"
    actions:
      - action: recuperator.create_replay
        data:
          hours: 24
          playback_seconds: 60
```

## Dashboard card

```yaml
type: entities
title: Basement breather
entities:
  - switch.basement_breather_breathing
  - select.basement_breather_mode
  - sensor.basement_breather_phase
  - sensor.basement_breather_last_change_reason
  - sensor.basement_breather_last_exhaust
  - sensor.basement_breather_last_intake
  - sensor.basement_breather_heat_recovery
  - sensor.basement_breather_basement_temperature
  - sensor.basement_breather_outdoor_temperature
  - binary_sensor.basement_breather_cold_weather
```

Add the diagram above it with the Picture Entity card from [Diagram](#diagram).

Add a *history-graph* card with the two probes and `sensor.basement_breather_phase` to see the cycle.

## Troubleshooting

- **Nothing happens:** is **Breathing** on? Is the **Phase** sensor changing? Check the two fan switches work from Home Assistant by hand (with Breathing off).
- **Always "Timed":** look at the Phase sensor's `timed` attribute: `temperatures_similar` (mild weather, or the core not connected), `sensor_unavailable` (a probe is offline) or `mode`.
- **Stuck in one phase with the fan off:** the other fan's switch probably still reports *on* (interlock). Check that switch.
- **Logs:** add to `configuration.yaml`:
  ```yaml
  logger:
    logs:
      custom_components.recuperator: debug
  ```
  Each phase change is then logged with its reason.

## Licence

MIT. See [LICENSE](LICENSE).
