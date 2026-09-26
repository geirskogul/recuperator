"""Setting up, migrating, unloading, and following renamed entities."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.recuperator.const import DEFAULTS, DOMAIN

from .conftest import DATA, make_entry, setup_entry


async def test_setup_and_unload(hass: HomeAssistant, entry) -> None:
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("switch.breather_breathing").state == "off"
    assert hass.states.get("select.breather_mode").state == "automatic"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_replay_has_its_own_device(hass: HomeAssistant, entry) -> None:
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    main = devices.async_get_device({(DOMAIN, entry.entry_id)})
    replay = devices.async_get_device({(DOMAIN, f"{entry.entry_id}_replay")})
    assert main is not None and replay is not None
    assert replay.via_device_id == main.id
    for unique_id, domain in (
        ("replay", "image"),
        ("create_replay", "button"),
        ("replay_hours", "number"),
        ("replay_frames", "number"),
    ):
        entity_id = entities.async_get_entity_id(domain, DOMAIN, f"{entry.entry_id}_{unique_id}")
        assert entities.async_get(entity_id).device_id == replay.id
    recovery = entities.async_get_entity_id("number", DOMAIN, f"{entry.entry_id}_recovery_percent")
    assert entities.async_get(recovery).device_id == main.id


async def test_device_page_is_decluttered(hass: HomeAssistant, entry) -> None:
    entities = er.async_get(hass)

    def registry_entry(domain: str, key: str):
        return entities.async_get(entities.async_get_entity_id(domain, DOMAIN, f"{entry.entry_id}_{key}"))

    assert registry_entry("sensor", "last_reason").entity_category == er.EntityCategory.DIAGNOSTIC
    assert registry_entry("sensor", "phase").entity_category is None
    assert registry_entry("number", "settle_delta").disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert registry_entry("number", "recovery_percent").disabled_by is None
    assert registry_entry("text", "diagram_palette").disabled_by is er.RegistryEntryDisabler.INTEGRATION
    # not linked: no Linked unit sensor
    assert entities.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_linked_unit") is None


async def test_migration_splits_timed_phase(hass: HomeAssistant, fans, probes) -> None:
    """1.1 entries had one Timed phase length; both new lengths start from it."""
    options = {k: v for k, v in DEFAULTS.items() if not k.startswith("timed_")}
    options["timed_phase_seconds"] = 45
    entry = MockConfigEntry(
        domain=DOMAIN, title="Breather", data=DATA, options=options, version=1, minor_version=1
    )
    entry.add_to_hass(hass)
    entities = er.async_get(hass)
    old = entities.async_get_or_create(
        "number", DOMAIN, f"{entry.entry_id}_timed_phase_seconds", config_entry=entry
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.minor_version == 2
    assert entry.options["timed_exhaust_seconds"] == 45
    assert entry.options["timed_intake_seconds"] == 45
    assert "timed_phase_seconds" not in entry.options
    assert entities.async_get(old.entity_id) is None


async def test_follows_a_renamed_fan(hass: HomeAssistant, probes) -> None:
    entities = er.async_get(hass)
    exhaust = entities.async_get_or_create("switch", "test", "ex", suggested_object_id="ex")
    intake = entities.async_get_or_create("switch", "test", "in", suggested_object_id="in")
    hass.states.async_set(exhaust.entity_id, "off")
    hass.states.async_set(intake.entity_id, "off")
    entry = await setup_entry(
        hass, make_entry({"exhaust_switch": exhaust.entity_id, "intake_switch": intake.entity_id})
    )

    entities.async_update_entity(exhaust.entity_id, new_entity_id="switch.exhaust_fan")
    await hass.async_block_till_done()

    assert entry.data["exhaust_switch"] == "switch.exhaust_fan"
    assert entry.data["intake_switch"] == intake.entity_id
    assert entry.unique_id == f"switch.exhaust_fan|{intake.entity_id}"
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.exhaust_switch == "switch.exhaust_fan"


async def test_follows_a_renamed_probe(hass: HomeAssistant, fans) -> None:
    entities = er.async_get(hass)
    probe = entities.async_get_or_create("sensor", "test", "probe", suggested_object_id="probe")
    hass.states.async_set(probe.entity_id, "20", {"unit_of_measurement": "°C"})
    hass.states.async_set(DATA["outside_sensor"], "5", {"unit_of_measurement": "°C"})
    entry = await setup_entry(hass, make_entry({"inside_sensor": probe.entity_id}))

    entities.async_update_entity(probe.entity_id, new_entity_id="sensor.room_end")
    await hass.async_block_till_done()

    assert entry.data["inside_sensor"] == "sensor.room_end"
    assert entry.unique_id == f"{DATA['exhaust_switch']}|{DATA['intake_switch']}"
