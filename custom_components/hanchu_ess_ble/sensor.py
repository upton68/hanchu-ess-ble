"""Sensor entities for the Hanchu ESS BLE integration."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEFAULT_POLL_KEYS, DOMAIN
from .coordinator import HanchuBleCoordinator
from .entity import HanchuCoordinatorEntity

UNIT_VAR = "var"

REGISTER_SCALE_FACTORS: dict[str, float] = {
    "P071": 100.0,
    "P069": -1.0,
    "P088": 0.0512,
}

DISABLED_BY_DEFAULT_KEYS: set[str] = {
    "P055",
    "P056",
    "P057",
    "P067",
    "P068",
    "P075",
    "P076",
    "P638",
    "P639",
    "P651",
    "L005", "L006", "L007", "L008", "L009", "L010",
    "L011", "L012", "L013", "L014", "L015", "L016",
    "L017", "L018", "L074", "P647", "P648",
}

SENSORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="rssi",
        translation_key="rssi",
        icon="mdi:bluetooth",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


def _register_description(
    key: str,
    name: str,
    *,
    enabled_default: bool = True,
    **kwargs,
) -> SensorEntityDescription:
    """Build a register sensor description with a default enabled state."""

    return SensorEntityDescription(
        key=key,
        name=name,
        entity_registry_enabled_default=enabled_default,
        **kwargs,
    )


REGISTER_SENSORS: dict[str, SensorEntityDescription] = {
    "P005": _register_description(
        key="P005",
        name="Inverter Power Limit",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        enabled_default=False,
    ),
    "P024": _register_description(
        key="P024",
        name="PV1 Voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "P025": _register_description(
        key="P025",
        name="PV1 Current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "P026": _register_description(
        key="P026",
        name="PV2 Voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "P027": _register_description(
        key="P027",
        name="PV2 Current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "P044": _register_description(
        key="P044",
        name="L1 Voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "P045": _register_description(
        key="P045",
        name="L1 Current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "P053": _register_description(
        key="P053",
        name="Grid Frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "P055": _register_description(
        key="P055",
        name="Active Power",
        enabled_default=False,
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "P056": _register_description(
        key="P056",
        name="Reactive Power",
        enabled_default=False,
        native_unit_of_measurement=UNIT_VAR,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "P057": _register_description(
        key="P057",
        name="Power Factor",
        enabled_default=False,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "P060": _register_description(
        key="P060",
        name="PV Total Power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "P061": _register_description(
        key="P061",
        name="PV Energy Today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    "P062": _register_description(
        key="P062",
        name="PV Energy Total",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    "P067": _register_description(
        key="P067",
        name="Battery Voltage",
        enabled_default=False,
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "P068": _register_description(
        key="P068",
        name="Battery Current",
        enabled_default=False,
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "P069": _register_description(
        key="P069",
        name="Battery Power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "P070": _register_description(
        key="P070",
        name="Battery Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "P071": _register_description(
        key="P071",
        name="Battery SoC",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "P075": _register_description(
        key="P075",
        name="Battery Charge Today",
        enabled_default=False,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    "P076": _register_description(
        key="P076",
        name="Battery Discharge Today",
        enabled_default=False,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    "P079": _register_description(
        key="P079",
        name="EPS Voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "P080": _register_description(
        key="P080",
        name="EPS Current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "P081": _register_description(
        key="P081",
        name="EPS Frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "P082": _register_description(
        key="P082",
        name="EPS Active Power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "P083": _register_description(
        key="P083",
        name="EPS Reactive Power",
        native_unit_of_measurement=UNIT_VAR,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "P084": _register_description(
        key="P084",
        name="EPS Energy Today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    "P085": _register_description(
        key="P085",
        name="EPS Energy Total",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    "P088": _register_description(
        key="P088",
        name="Battery Capacity",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "P237": _register_description(
        key="P237",
        name="AC Coupled PV Power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "P644": _register_description(
        key="P644",
        name="Grid Power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hanchu sensors from a config entry."""
    coordinator: HanchuBleCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        *(HanchuDiagnosticSensor(coordinator, description) for description in SENSORS),
        HanchuLoadPowerSensor(coordinator),
        *(
            HanchuRegisterSensor(
                coordinator,
                register_key,
                REGISTER_SENSORS.get(
                    register_key,
                    _register_description(
                        key=register_key,
                        name=register_key,
                        enabled_default=not register_key.startswith("P"),
                    ),
                ),
            )
            for register_key in DEFAULT_POLL_KEYS
        ),
    ]
    async_add_entities(entities)


class HanchuDiagnosticSensor(HanchuCoordinatorEntity, SensorEntity):
    """Diagnostic BLE sensor."""

    entity_description: SensorEntityDescription

    def __init__(
        self,
        coordinator: HanchuBleCoordinator,
        description: SensorEntityDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address}_{description.key}"

    @property
    def native_value(self):
        """Return the sensor's value."""
        if self.entity_description.key == "rssi":
            return self.coordinator.data.rssi
        return None


class HanchuRegisterSensor(HanchuCoordinatorEntity, SensorEntity):
    """Raw inverter register sensor."""

    _attr_entity_registry_enabled_default = True

    entity_description: SensorEntityDescription

    def __init__(
        self,
        coordinator: HanchuBleCoordinator,
        register_key: str,
        description: SensorEntityDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self._register_key = register_key
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address}_{register_key.lower()}"
        self._attr_entity_registry_enabled_default = (
            register_key not in DISABLED_BY_DEFAULT_KEYS
            and not (
                register_key.startswith("P")
                and description.name == register_key
            )
        )

    @property
    def native_value(self):
        """Return the raw register value."""
        values = self.coordinator.data.values or {}
        value = values.get(self._register_key)
        if value is None:
            return None

        scale_factor = REGISTER_SCALE_FACTORS.get(self._register_key)
        if scale_factor is not None:
            if isinstance(value, (int, float)):
                return value * scale_factor
            if isinstance(value, str):
                try:
                    return float(value) * scale_factor
                except ValueError:
                    return value

        return value


class HanchuLoadPowerSensor(HanchuCoordinatorEntity, SensorEntity):
    """Derived house load power sensor — Grid + AC PV + Battery."""

    _attr_name = "Load Power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:home-lightning-bolt"

    def __init__(self, coordinator: HanchuBleCoordinator) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_load_power"

    @property
    def native_value(self) -> float | None:
        """Return house load = Grid Power + AC PV + Battery Power."""
        values = self.coordinator.data.values or {}
        try:
            grid = float(values.get("P644") or 0)
            pv = float(values.get("P237") or 0)
            battery = float(values.get("P069") or 0)
            return round(grid + pv + battery, 1)
        except (ValueError, TypeError):
            return None
