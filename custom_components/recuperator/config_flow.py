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
    CONF_OUTSIDE_SENSOR,
    CONF_PALETTE,
    DEFAULTS,
    DOMAIN,
    SETTINGS,
)

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


def _errors(data: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if data[CONF_EXHAUST_SWITCH] == data[CONF_INTAKE_SWITCH]:
        errors[CONF_INTAKE_SWITCH] = "same_switch"
    if data[CONF_INSIDE_SENSOR] == data[CONF_OUTSIDE_SENSOR]:
        errors[CONF_OUTSIDE_SENSOR] = "same_sensor"
    return errors


class RecuperatorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a recuperator: pick its two fans and two probes."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _errors(user_input)
            if not errors:
                await self.async_set_unique_id(
                    f"{user_input[CONF_EXHAUST_SWITCH]}|{user_input[CONF_INTAKE_SWITCH]}"
                )
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
            errors = _errors(user_input)
            if not errors:
                return self.async_update_reload_and_abort(entry, data_updates=user_input)
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
        return self.async_show_menu(step_id="init", menu_options=["settings", "colours"])

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Every numeric setting on one screen; reset puts them back (colours are kept)."""
        keep = {k: v for k, v in self.config_entry.options.items() if k == CONF_PALETTE}
        if user_input is not None:
            if user_input.pop(CONF_RESET, False):
                return self.async_create_entry(data={**DEFAULTS, **keep})
            return self.async_create_entry(data={**DEFAULTS, **user_input, **keep})
        current = {**DEFAULTS, **self.config_entry.options}
        schema: dict = {}
        for s in SETTINGS:
            cfg = selector.NumberSelectorConfig(
                min=s.minimum, max=s.maximum, step=s.step, mode=selector.NumberSelectorMode.BOX
            )
            if s.unit:
                cfg["unit_of_measurement"] = s.unit
            schema[vol.Required(s.key, default=current[s.key])] = selector.NumberSelector(cfg)
        schema[vol.Optional(CONF_RESET, default=False)] = selector.BooleanSelector()
        return self.async_show_form(step_id="settings", data_schema=vol.Schema(schema))

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
