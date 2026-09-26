"""Config, reconfigure and options flows: adding, re-pointing and tuning a recuperator."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .diagram import PALETTE, palette_to_text
from .const import (
    CONF_EXHAUST_SWITCH,
    CONF_INSIDE_SENSOR,
    CONF_INTAKE_SWITCH,
    CONF_LINK_EXHAUST_SWITCH,
    CONF_LINK_INTAKE_SWITCH,
    CONF_LINK_TYPE,
    CONF_OUTSIDE_SENSOR,
    CONF_PALETTE,
    CONF_PASSIVE_INTAKE,
    DEFAULTS,
    DOMAIN,
    LINK_EXHAUST_FAN,
    LINK_INTAKE_FAN,
    LINK_NONE,
    LINK_RECUPERATOR,
    LINK_TYPES,
    REPLAY_KEYS,
    SETTINGS,
    unique_id_for,
)
from .controller import linked_fans

FAN_DOMAINS = ["switch", "fan", "light", "input_boolean"]
CONF_RESET = "reset_to_defaults"


def _fan_picker():
    return selector.EntitySelector(selector.EntitySelectorConfig(domain=FAN_DOMAINS))


def _probe_picker():
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
    )


def _devices_schema(defaults: dict[str, Any]) -> dict:
    """The four entity pickers, pre-filled where a value is known."""

    def key(name: str):
        value = defaults.get(name)
        return vol.Required(name, default=value) if value else vol.Required(name)

    return {
        key(CONF_EXHAUST_SWITCH): _fan_picker(),
        key(CONF_INTAKE_SWITCH): _fan_picker(),
        key(CONF_INSIDE_SENSOR): _probe_picker(),
        key(CONF_OUTSIDE_SENSOR): _probe_picker(),
    }


def _errors(data: dict[str, Any], in_use: dict[str, str] | None = None) -> dict[str, str]:
    errors: dict[str, str] = {}
    if data[CONF_EXHAUST_SWITCH] == data[CONF_INTAKE_SWITCH]:
        errors[CONF_INTAKE_SWITCH] = "same_switch"
    if data[CONF_INSIDE_SENSOR] == data[CONF_OUTSIDE_SENSOR]:
        errors[CONF_OUTSIDE_SENSOR] = "same_sensor"
    for key in (CONF_EXHAUST_SWITCH, CONF_INTAKE_SWITCH):
        if in_use and data[key] in in_use:
            errors.setdefault(key, "fan_in_use")
    return errors


def _entry_fans(entry) -> list[str]:
    """Every fan a recuperator entry drives, its linked unit's included."""
    own = [entry.data.get(CONF_EXHAUST_SWITCH), entry.data.get(CONF_INTAKE_SWITCH)]
    return [f for f in (*own, *linked_fans(entry.data)) if f]


def _fans_in_use(hass, exclude_entry_id: str | None = None) -> dict[str, str]:
    """fan entity -> title of the recuperator that already drives it."""
    return {
        fan: entry.title
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.entry_id != exclude_entry_id
        for fan in _entry_fans(entry)
    }


class RecuperatorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a recuperator: pick its two fans and two probes."""

    VERSION = 1
    MINOR_VERSION = 2  # 1.2: separate timed exhaust and intake lengths

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _errors(user_input, _fans_in_use(self.hass))
            if not errors:
                await self.async_set_unique_id(unique_id_for(user_input))
                self._abort_if_unique_id_configured()
                name = user_input.pop(CONF_NAME)
                return self.async_create_entry(title=name, data=user_input, options=dict(DEFAULTS))
        defaults = user_input or {}
        schema = {vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "Recuperator")): selector.TextSelector()}
        schema.update(_devices_schema(defaults))
        return self.async_show_form(step_id="user", data_schema=vol.Schema(schema), errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Point an existing recuperator at different fans or probes."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            # Other recuperators' fans, and this one's linked unit, are off limits.
            in_use = _fans_in_use(self.hass, entry.entry_id)
            in_use.update({fan: entry.title for fan in linked_fans(entry.data) if fan})
            errors = _errors(user_input, in_use)
            if not errors:
                # The unique ID follows the fans, so the old pair is not left "taken".
                return self.async_update_reload_and_abort(
                    entry, unique_id=unique_id_for(user_input), data_updates=user_input
                )
        defaults = user_input or dict(entry.data)
        return self.async_show_form(
            step_id="reconfigure", data_schema=vol.Schema(_devices_schema(defaults)), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> RecuperatorOptionsFlow:
        return RecuperatorOptionsFlow()


MAX_STOPS = 20  # colour rows on the Diagram colours screen
CONF_RESET_COLOURS = "reset_colours"


def _temp_key(i: int) -> str:
    return f"stop_{i:02d}_temperature"


def _colour_key(i: int) -> str:
    return f"stop_{i:02d}_colour"


def _to_rgb(hex_colour: str) -> list[int]:
    return [int(hex_colour[k : k + 2], 16) for k in (1, 3, 5)]


def _to_hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(int(v) for v in rgb)


class RecuperatorOptionsFlow(OptionsFlow):
    """Configure: a menu with the numeric settings and the diagram colours."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["settings", "link", "replay", "colours"])

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The cycle's settings; reset puts them back (colours and replay settings are kept)."""
        keep = {k: v for k, v in self.config_entry.options.items() if k == CONF_PALETTE or k in REPLAY_KEYS}
        if user_input is not None:
            if user_input.pop(CONF_RESET, False):
                return self.async_create_entry(data={**DEFAULTS, **keep})
            return self.async_create_entry(data={**DEFAULTS, **user_input, **keep})
        current = {**DEFAULTS, **self.config_entry.options}
        schema: dict = {}
        for s in SETTINGS:
            if s.key in REPLAY_KEYS:
                continue
            cfg = selector.NumberSelectorConfig(
                min=s.minimum, max=s.maximum, step=s.step, mode=selector.NumberSelectorMode.BOX
            )
            if s.unit:
                cfg["unit_of_measurement"] = s.unit
            schema[vol.Required(s.key, default=current[s.key])] = selector.NumberSelector(cfg)
        schema[vol.Optional(CONF_PASSIVE_INTAKE, default=bool(current.get(CONF_PASSIVE_INTAKE, False)))] = (
            selector.BooleanSelector()
        )
        schema[vol.Optional(CONF_RESET, default=False)] = selector.BooleanSelector()
        return self.async_show_form(step_id="settings", data_schema=vol.Schema(schema))

    async def async_step_link(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """A linked unit that breathes opposite to this one.

        Stored with the fans and probes (not with the settings, so resetting the
        settings keeps it). Saving restarts the recuperator with the new link.
        """
        entry = self.config_entry
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _link_errors(user_input, entry, _fans_in_use(self.hass, entry.entry_id))
            if not errors:
                data = {k: v for k, v in entry.data.items() if k not in LINK_KEYS}
                data.update(_link_data(user_input))
                self.hass.config_entries.async_update_entry(entry, data=data)
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_create_entry(data=dict(entry.options))
        current = user_input if user_input is not None else dict(entry.data)
        return self.async_show_form(step_id="link", data_schema=_link_schema(current), errors=errors)

    async def async_step_replay(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The settings used by the Create replay button (and saved by the action)."""
        if user_input is not None:
            return self.async_create_entry(data={**self.config_entry.options, **user_input})
        current = {**DEFAULTS, **self.config_entry.options}
        schema: dict = {}
        for s in SETTINGS:
            if s.key not in REPLAY_KEYS:
                continue
            cfg = selector.NumberSelectorConfig(
                min=s.minimum, max=s.maximum, step=s.step, mode=selector.NumberSelectorMode.BOX
            )
            if s.unit:
                cfg["unit_of_measurement"] = s.unit
            schema[vol.Required(s.key, default=current[s.key])] = selector.NumberSelector(cfg)
        return self.async_show_form(step_id="replay", data_schema=vol.Schema(schema))

    async def async_step_colours(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The diagram's colour scale: a temperature box and a colour picker per stop.

        Rows are pre-filled with the current scale, with empty rows after them for
        new stops. Clearing a row's temperature removes that stop. Stops are sorted
        by temperature, so their order on the screen does not matter.
        """
        errors: dict[str, str] = {}
        options = dict(self.config_entry.options)
        if user_input is not None:
            if user_input.get(CONF_RESET_COLOURS):
                options.pop(CONF_PALETTE, None)
                return self.async_create_entry(data=options)
            stops: list[tuple[float, str]] = []
            for i in range(1, MAX_STOPS + 1):
                t = user_input.get(_temp_key(i))
                if t is None or t == "":
                    continue
                rgb = user_input.get(_colour_key(i))
                if not rgb:
                    errors[_colour_key(i)] = "colour_missing"
                    continue
                stops.append((float(t), _to_hex(rgb)))
            stops.sort()
            if not errors:
                if len(stops) < 2:
                    errors["base"] = "too_few_stops"
                elif any(a[0] == b[0] for a, b in zip(stops, stops[1:])):
                    errors["base"] = "duplicate_temperature"
            if not errors:
                palette = tuple(stops)
                if palette == PALETTE:
                    options.pop(CONF_PALETTE, None)
                else:
                    options[CONF_PALETTE] = palette_to_text(palette)
                return self.async_create_entry(data=options)
        current = self.config_entry.runtime_data.palette if user_input is None else None
        schema: dict = {}
        for i in range(1, MAX_STOPS + 1):
            if current is not None and i <= len(current):
                t, c = current[i - 1]
                t_key = vol.Optional(_temp_key(i), description={"suggested_value": t})
                c_key = vol.Optional(_colour_key(i), description={"suggested_value": _to_rgb(c)})
            elif user_input is not None:
                t_key = vol.Optional(_temp_key(i), description={"suggested_value": user_input.get(_temp_key(i))})
                c_key = vol.Optional(_colour_key(i), description={"suggested_value": user_input.get(_colour_key(i))})
            else:
                t_key, c_key = vol.Optional(_temp_key(i)), vol.Optional(_colour_key(i))
            schema[t_key] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-80, max=80, step=0.5, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="°C"
                )
            )
            schema[c_key] = selector.ColorRGBSelector()
        schema[vol.Optional(CONF_RESET_COLOURS, default=False)] = selector.BooleanSelector()
        return self.async_show_form(step_id="colours", data_schema=vol.Schema(schema), errors=errors)


# -- the Linked unit page ------------------------------------------------------------

LINK_KEYS = (CONF_LINK_TYPE, CONF_LINK_EXHAUST_SWITCH, CONF_LINK_INTAKE_SWITCH)
# the fans each kind of linked unit needs
LINK_NEEDS = {
    LINK_NONE: (),
    LINK_RECUPERATOR: (CONF_LINK_EXHAUST_SWITCH, CONF_LINK_INTAKE_SWITCH),
    LINK_INTAKE_FAN: (CONF_LINK_INTAKE_SWITCH,),
    LINK_EXHAUST_FAN: (CONF_LINK_EXHAUST_SWITCH,),
}


def _link_schema(current: dict[str, Any]) -> vol.Schema:
    """Type of linked unit, and its fan(s); the fans are pre-filled if known."""

    def fan_key(key: str):
        return vol.Optional(key, description={"suggested_value": current.get(key)})

    type_selector = selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=LINK_TYPES, translation_key=CONF_LINK_TYPE, mode=selector.SelectSelectorMode.LIST
        )
    )
    return vol.Schema({
        vol.Required(CONF_LINK_TYPE, default=current.get(CONF_LINK_TYPE, LINK_NONE)): type_selector,
        fan_key(CONF_LINK_EXHAUST_SWITCH): _fan_picker(),
        fan_key(CONF_LINK_INTAKE_SWITCH): _fan_picker(),
    })


def _link_errors(user_input: dict[str, Any], entry, in_use: dict[str, str]) -> dict[str, str]:
    """The linked unit's fans: present for its type, and not driven by anything else."""
    errors: dict[str, str] = {}
    own = {entry.data[CONF_EXHAUST_SWITCH], entry.data[CONF_INTAKE_SWITCH]}
    needed = LINK_NEEDS[user_input[CONF_LINK_TYPE]]
    for key in needed:
        fan = user_input.get(key)
        if not fan:
            errors[key] = "link_fan_missing"
        elif fan in own:
            errors[key] = "link_fan_is_own"
        elif fan in in_use:
            errors[key] = "fan_in_use"
    if len(needed) == 2 and not errors and user_input[needed[0]] == user_input[needed[1]]:
        errors[needed[1]] = "same_switch"
    return errors


def _link_data(user_input: dict[str, Any]) -> dict[str, Any]:
    """What to store: the type and only the fans that type uses."""
    link_type = user_input[CONF_LINK_TYPE]
    data: dict[str, Any] = {CONF_LINK_TYPE: link_type}
    for key in LINK_NEEDS[link_type]:
        data[key] = user_input[key]
    return data
