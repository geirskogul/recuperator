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

1. **Supply too cold** (intake only): the air entering the room is below *Minimum supply temperature*.
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
| `sensor.basement_breather_last_change_reason` | Why the last phase ended: Recovered, Settled, Maximum time, Cold limit, Timed, Supply too cold, Started, Stopped |
| `sensor.basement_breather_last_exhaust` / `..._last_intake` | Length of the last exhaust / intake phase (s) |
| `sensor.basement_breather_basement_temperature` | Indoor air temperature: the inside probe at the end of the last exhaust |
| `sensor.basement_breather_outdoor_temperature` | Outdoor air temperature: the outside probe at the end of the last intake |
| `sensor.basement_breather_heat_recovery` | How much of the gap the core closed in the last temperature-driven phase (%) |
| `binary_sensor.basement_breather_cold_weather` | On while the cold-weather limits apply |
| `number.basement_breather_...` | One per setting (below), under the device's **Configuration** section |
| `button.basement_breather_reset_settings_to_defaults` | Puts every setting back to its default |

Breathing and Mode survive a Home Assistant restart. After a restart the cycle starts again with exhaust.

## Modes

| Mode | What happens |
| --- | --- |
| **Automatic** | The probe-driven cycle described above (normal use) |
| **Timed** | Fixed-length phases (*Timed phase*), ignoring the probes except for the cold intake limit. Good for checking the wiring, or if the probes are unreliable. |
| **Exhaust only** | The exhaust fan runs continuously (for example to dry the room out quickly, or to test airflow) |
| **Intake only** | The intake fan runs continuously |

Changing mode takes effect within a second; a running fan is stopped and the pause is kept before the other starts.

## Settings

All settings take effect within a second, without restarting the cycle. Change them either on the device page (the **Configuration** section, one number per setting, handy on a dashboard), or all together with **Configure** on the integration.

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
| Cold intake limit | 45 s | 10–300 | In cold weather, intake never runs longer than this |
| Cold exhaust extra | 7 s | 0–60 | In cold weather, exhaust runs at least as long as the last intake and at most this much longer |
| Minimum supply temperature | 5 °C | −30–25 | During intake the inside probe reads the air entering the room. If it drops below this, the intake ends (after *Minimum phase*, or the *Cold intake limit* if that is shorter). Applies in every mode. Raise it (e.g. 12 °C) to avoid cold draughts. |

## Choosing the recovery target

The probes sit in the airstream at the two faces of the core. During intake, the inside probe reads the air leaving the core into the room. With a good core it stays close to room temperature for a long time, because the core warms the incoming air, and only starts to drop when the core has given up most of its stored heat (the "breakthrough"). Exhaust works the same way in reverse at the outdoor face.

- **Low target (10–25 %)**: switch as soon as the far end *starts* to change. The core never runs out, so incoming air arrives warm. **Best heat recovery.** This is how commercial single-tube units work; they reverse about every minute.
- **High target (70–90 %)**: let the core fill up or empty completely. Longer phases move more air per phase (**more ventilation** through a long hose), but the last part of each phase recovers little heat.
- With a large core or slow fans, even a low target can take minutes to reach. *Maximum phase* then sets the rhythm; that is fine.

If ventilation matters more than heat recovery, use a higher target or longer *Minimum phase*, and rely on *Minimum supply temperature* and the cold-weather limits to protect the room in winter.

## Resetting to defaults

Either:
- press **Reset settings to defaults** on the device page, or
- **Configure**, tick **Reset to defaults**, Submit.

Only the settings are reset. The chosen fans and probes, Breathing and Mode are kept.

## Tuning guide

| What you see | Try |
| --- | --- |
| Phases end very quickly, lots of switching | Raise *Minimum phase*, or raise *Recovery target* |
| Phases always hit *Maximum time* (Last change reason) | The core never gets to the target: lower *Recovery target* (e.g. 70 %), or raise *Maximum phase* if you want it to keep going |
| Phases often end as *Settled* at low heat recovery | The probes are slow or the fans weak: raise *Settle window* (e.g. 25 s) |
| Cold draughts in winter | Raise *Minimum supply temperature* (e.g. 12 °C); lower *Cold intake limit* (e.g. 30 s), or raise *Cold threshold* so the limits start earlier |
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
