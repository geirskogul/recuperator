"""Config, reconfigure and options flows: adding, re-pointing and tuning a recuperator."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_EXHAUST_SWITCH,
    CONF_INSIDE_SENSOR,
    CONF_INTAKE_SWITCH,
    CONF_OUTSIDE_SENSOR,
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


class RecuperatorOptionsFlow(OptionsFlow):
    """Configure: every tunable setting on one screen, plus reset to defaults."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            if user_input.pop(CONF_RESET, False):
                return self.async_create_entry(data=dict(DEFAULTS))
            return self.async_create_entry(data={**DEFAULTS, **user_input})
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
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))
