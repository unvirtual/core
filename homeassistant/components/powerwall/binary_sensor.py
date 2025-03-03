"""Support for powerwall binary sensors."""

from tesla_powerwall import GridStatus, MeterType

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import PowerWallEntity
from .models import PowerwallRuntimeData


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the powerwall sensors."""
    powerwall_data: PowerwallRuntimeData = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities(
        [
            sensor_class(powerwall_data)
            for sensor_class in (
                PowerWallRunningSensor,
                PowerWallGridServicesActiveSensor,
                PowerWallGridStatusSensor,
                PowerWallConnectedSensor,
                PowerWallChargingStatusSensor,
            )
        ]
    )


class PowerWallRunningSensor(PowerWallEntity, BinarySensorEntity):
    """Representation of an Powerwall running sensor."""

    _attr_name = "Powerwall Status"
    _attr_device_class = BinarySensorDeviceClass.POWER

    @property
    def unique_id(self) -> str:
        """Device Uniqueid."""
        return f"{self.base_unique_id}_running"

    @property
    def is_on(self) -> bool:
        """Get the powerwall running state."""
        return self.data.site_master.is_running


class PowerWallConnectedSensor(PowerWallEntity, BinarySensorEntity):
    """Representation of an Powerwall connected sensor."""

    _attr_name = "Powerwall Connected to Tesla"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def unique_id(self) -> str:
        """Device Uniqueid."""
        return f"{self.base_unique_id}_connected_to_tesla"

    @property
    def is_on(self) -> bool:
        """Get the powerwall connected to tesla state."""
        return self.data.site_master.is_connected_to_tesla


class PowerWallGridServicesActiveSensor(PowerWallEntity, BinarySensorEntity):
    """Representation of a Powerwall grid services active sensor."""

    _attr_name = "Grid Services Active"
    _attr_device_class = BinarySensorDeviceClass.POWER

    @property
    def unique_id(self) -> str:
        """Device Uniqueid."""
        return f"{self.base_unique_id}_grid_services_active"

    @property
    def is_on(self) -> bool:
        """Grid services is active."""
        return self.data.grid_services_active


class PowerWallGridStatusSensor(PowerWallEntity, BinarySensorEntity):
    """Representation of an Powerwall grid status sensor."""

    _attr_name = "Grid Status"
    _attr_device_class = BinarySensorDeviceClass.POWER

    @property
    def unique_id(self) -> str:
        """Device Uniqueid."""
        return f"{self.base_unique_id}_grid_status"

    @property
    def is_on(self) -> bool:
        """Grid is online."""
        return self.data.grid_status == GridStatus.CONNECTED


class PowerWallChargingStatusSensor(PowerWallEntity, BinarySensorEntity):
    """Representation of an Powerwall charging status sensor."""

    _attr_name = "Powerwall Charging"
    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING

    @property
    def unique_id(self) -> str:
        """Device Uniqueid."""
        return f"{self.base_unique_id}_powerwall_charging"

    @property
    def is_on(self) -> bool:
        """Powerwall is charging."""
        # is_sending_to returns true for values greater than 100 watts
        return self.data.meters.get_meter(MeterType.BATTERY).is_sending_to()
