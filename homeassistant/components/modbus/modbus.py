"""Support for Modbus."""
from __future__ import annotations

import asyncio
from collections import namedtuple
from collections.abc import Callable
import logging
from typing import Any

from pymodbus.client.sync import (
    BaseModbusClient,
    ModbusSerialClient,
    ModbusTcpClient,
    ModbusUdpClient,
)
from pymodbus.constants import Defaults
from pymodbus.exceptions import ModbusException
from pymodbus.pdu import ModbusResponse
from pymodbus.transaction import ModbusRtuFramer
import voluptuous as vol

from homeassistant.const import (
    CONF_DELAY,
    CONF_HOST,
    CONF_METHOD,
    CONF_NAME,
    CONF_PORT,
    CONF_TIMEOUT,
    CONF_TYPE,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import Event, HomeAssistant, ServiceCall, callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_ADDRESS,
    ATTR_HUB,
    ATTR_SLAVE,
    ATTR_STATE,
    ATTR_UNIT,
    ATTR_VALUE,
    CALL_TYPE_COIL,
    CALL_TYPE_DISCRETE,
    CALL_TYPE_REGISTER_HOLDING,
    CALL_TYPE_REGISTER_INPUT,
    CALL_TYPE_WRITE_COIL,
    CALL_TYPE_WRITE_COILS,
    CALL_TYPE_WRITE_REGISTER,
    CALL_TYPE_WRITE_REGISTERS,
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_CLOSE_COMM_ON_ERROR,
    CONF_MSG_WAIT,
    CONF_PARITY,
    CONF_RETRIES,
    CONF_RETRY_ON_EMPTY,
    CONF_STOPBITS,
    DEFAULT_HUB,
    MODBUS_DOMAIN as DOMAIN,
    PLATFORMS,
    RTUOVERTCP,
    SERIAL,
    SERVICE_RESTART,
    SERVICE_STOP,
    SERVICE_WRITE_COIL,
    SERVICE_WRITE_REGISTER,
    SIGNAL_START_ENTITY,
    SIGNAL_STOP_ENTITY,
    TCP,
    UDP,
)

_LOGGER = logging.getLogger(__name__)


ConfEntry = namedtuple("ConfEntry", "call_type attr func_name")
RunEntry = namedtuple("RunEntry", "attr func")
PYMODBUS_CALL = [
    ConfEntry(
        CALL_TYPE_COIL,
        "bits",
        "read_coils",
    ),
    ConfEntry(
        CALL_TYPE_DISCRETE,
        "bits",
        "read_discrete_inputs",
    ),
    ConfEntry(
        CALL_TYPE_REGISTER_HOLDING,
        "registers",
        "read_holding_registers",
    ),
    ConfEntry(
        CALL_TYPE_REGISTER_INPUT,
        "registers",
        "read_input_registers",
    ),
    ConfEntry(
        CALL_TYPE_WRITE_COIL,
        "value",
        "write_coil",
    ),
    ConfEntry(
        CALL_TYPE_WRITE_COILS,
        "count",
        "write_coils",
    ),
    ConfEntry(
        CALL_TYPE_WRITE_REGISTER,
        "value",
        "write_register",
    ),
    ConfEntry(
        CALL_TYPE_WRITE_REGISTERS,
        "count",
        "write_registers",
    ),
]


async def async_modbus_setup(
    hass: HomeAssistant,
    config: ConfigType,
) -> bool:
    """Set up Modbus component."""

    hass.data[DOMAIN] = hub_collect = {}
    for conf_hub in config[DOMAIN]:
        my_hub = ModbusHub(hass, conf_hub)
        hub_collect[conf_hub[CONF_NAME]] = my_hub

        # modbus needs to be activated before components are loaded
        # to avoid a racing problem
        if not await my_hub.async_setup():
            return False

        # load platforms
        for component, conf_key in PLATFORMS:
            if conf_key in conf_hub:
                hass.async_create_task(
                    async_load_platform(hass, component, DOMAIN, conf_hub, config)
                )

    async def async_stop_modbus(event: Event) -> None:
        """Stop Modbus service."""

        async_dispatcher_send(hass, SIGNAL_STOP_ENTITY)
        for client in hub_collect.values():
            await client.async_close()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, async_stop_modbus)

    async def async_write_register(service: ServiceCall) -> None:
        """Write Modbus registers."""
        unit = 0
        if ATTR_UNIT in service.data:
            unit = int(float(service.data[ATTR_UNIT]))
        if ATTR_SLAVE in service.data:
            unit = int(float(service.data[ATTR_SLAVE]))
        address = int(float(service.data[ATTR_ADDRESS]))
        value = service.data[ATTR_VALUE]
        hub = hub_collect[
            service.data[ATTR_HUB] if ATTR_HUB in service.data else DEFAULT_HUB
        ]
        if isinstance(value, list):
            await hub.async_pymodbus_call(
                unit, address, [int(float(i)) for i in value], CALL_TYPE_WRITE_REGISTERS
            )
        else:
            await hub.async_pymodbus_call(
                unit, address, int(float(value)), CALL_TYPE_WRITE_REGISTER
            )

    async def async_write_coil(service: ServiceCall) -> None:
        """Write Modbus coil."""
        unit = 0
        if ATTR_UNIT in service.data:
            unit = int(float(service.data[ATTR_UNIT]))
        if ATTR_SLAVE in service.data:
            unit = int(float(service.data[ATTR_SLAVE]))
        address = service.data[ATTR_ADDRESS]
        state = service.data[ATTR_STATE]
        hub = hub_collect[
            service.data[ATTR_HUB] if ATTR_HUB in service.data else DEFAULT_HUB
        ]
        if isinstance(state, list):
            await hub.async_pymodbus_call(unit, address, state, CALL_TYPE_WRITE_COILS)
        else:
            await hub.async_pymodbus_call(unit, address, state, CALL_TYPE_WRITE_COIL)

    for x_write in (
        (SERVICE_WRITE_REGISTER, async_write_register, ATTR_VALUE, cv.positive_int),
        (SERVICE_WRITE_COIL, async_write_coil, ATTR_STATE, cv.boolean),
    ):
        hass.services.async_register(
            DOMAIN,
            x_write[0],
            x_write[1],
            schema=vol.Schema(
                {
                    vol.Optional(ATTR_HUB, default=DEFAULT_HUB): cv.string,
                    vol.Exclusive(ATTR_SLAVE, "unit"): cv.positive_int,
                    vol.Exclusive(ATTR_UNIT, "unit"): cv.positive_int,
                    vol.Required(ATTR_ADDRESS): cv.positive_int,
                    vol.Required(x_write[2]): vol.Any(
                        cv.positive_int, vol.All(cv.ensure_list, [x_write[3]])
                    ),
                }
            ),
        )

    async def async_stop_hub(service: ServiceCall) -> None:
        """Stop Modbus hub."""
        async_dispatcher_send(hass, SIGNAL_STOP_ENTITY)
        hub = hub_collect[service.data[ATTR_HUB]]
        await hub.async_close()

    async def async_restart_hub(service: ServiceCall) -> None:
        """Restart Modbus hub."""
        async_dispatcher_send(hass, SIGNAL_START_ENTITY)
        hub = hub_collect[service.data[ATTR_HUB]]
        await hub.async_restart()

    for x_service in (
        (SERVICE_STOP, async_stop_hub),
        (SERVICE_RESTART, async_restart_hub),
    ):
        hass.services.async_register(
            DOMAIN,
            x_service[0],
            x_service[1],
            schema=vol.Schema({vol.Required(ATTR_HUB): cv.string}),
        )
    return True


class ModbusHub:
    """Thread safe wrapper class for pymodbus."""

    def __init__(self, hass: HomeAssistant, client_config: dict[str, Any]) -> None:
        """Initialize the Modbus hub."""

        # generic configuration
        self._client: BaseModbusClient | None = None
        self._async_cancel_listener: Callable[[], None] | None = None
        self._in_error = False
        self._lock = asyncio.Lock()
        self.hass = hass
        self.name = client_config[CONF_NAME]
        self._config_type = client_config[CONF_TYPE]
        self._config_delay = client_config[CONF_DELAY]
        self._pb_call: dict[str, RunEntry] = {}
        self._pb_class = {
            SERIAL: ModbusSerialClient,
            TCP: ModbusTcpClient,
            UDP: ModbusUdpClient,
            RTUOVERTCP: ModbusTcpClient,
        }
        self._pb_params = {
            "port": client_config[CONF_PORT],
            "timeout": client_config[CONF_TIMEOUT],
            "reset_socket": client_config[CONF_CLOSE_COMM_ON_ERROR],
            "retries": client_config[CONF_RETRIES],
            "retry_on_empty": client_config[CONF_RETRY_ON_EMPTY],
        }
        if self._config_type == SERIAL:
            # serial configuration
            self._pb_params.update(
                {
                    "method": client_config[CONF_METHOD],
                    "baudrate": client_config[CONF_BAUDRATE],
                    "stopbits": client_config[CONF_STOPBITS],
                    "bytesize": client_config[CONF_BYTESIZE],
                    "parity": client_config[CONF_PARITY],
                }
            )
        else:
            # network configuration
            self._pb_params["host"] = client_config[CONF_HOST]
            if self._config_type == RTUOVERTCP:
                self._pb_params["framer"] = ModbusRtuFramer

        Defaults.Timeout = client_config[CONF_TIMEOUT]
        if CONF_MSG_WAIT in client_config:
            self._msg_wait = client_config[CONF_MSG_WAIT] / 1000
        elif self._config_type == SERIAL:
            self._msg_wait = 30 / 1000
        else:
            self._msg_wait = 0

    def _log_error(self, text: str, error_state: bool = True) -> None:
        log_text = f"Pymodbus: {self.name}: {text}"
        if self._in_error:
            _LOGGER.debug(log_text)
        else:
            _LOGGER.error(log_text)
            self._in_error = error_state

    async def async_setup(self) -> bool:
        """Set up pymodbus client."""
        try:
            self._client = self._pb_class[self._config_type](**self._pb_params)
        except ModbusException as exception_error:
            self._log_error(str(exception_error), error_state=False)
            return False

        for entry in PYMODBUS_CALL:
            func = getattr(self._client, entry.func_name)
            self._pb_call[entry.call_type] = RunEntry(entry.attr, func)

        async with self._lock:
            if not await self.hass.async_add_executor_job(self._pymodbus_connect):
                err = f"{self.name} connect failed, retry in pymodbus"
                self._log_error(err, error_state=False)
                return False

        # Start counting down to allow modbus requests.
        if self._config_delay:
            self._async_cancel_listener = async_call_later(
                self.hass, self._config_delay, self.async_end_delay
            )
        return True

    @callback
    def async_end_delay(self, args: Any) -> None:
        """End startup delay."""
        self._async_cancel_listener = None
        self._config_delay = 0

    async def async_restart(self) -> None:
        """Reconnect client."""
        if self._client:
            await self.async_close()

        await self.async_setup()

    async def async_close(self) -> None:
        """Disconnect client."""
        if self._async_cancel_listener:
            self._async_cancel_listener()
            self._async_cancel_listener = None
        async with self._lock:
            if self._client:
                try:
                    self._client.close()
                except ModbusException as exception_error:
                    self._log_error(str(exception_error))
                del self._client
                self._client = None
                message = f"modbus {self.name} communication closed"
                _LOGGER.warning(message)

    def _pymodbus_connect(self) -> bool:
        """Connect client."""
        try:
            self._client.connect()  # type: ignore[union-attr]
        except ModbusException as exception_error:
            self._log_error(str(exception_error), error_state=False)
            return False
        else:
            message = f"modbus {self.name} communication open"
            _LOGGER.info(message)
            return True

    def _pymodbus_call(
        self, unit: int | None, address: int, value: int | list[int], use_call: str
    ) -> ModbusResponse:
        """Call sync. pymodbus."""
        kwargs = {"unit": unit} if unit else {}
        entry = self._pb_call[use_call]
        try:
            result = entry.func(address, value, **kwargs)
        except ModbusException as exception_error:
            self._log_error(str(exception_error))
            return None
        if not hasattr(result, entry.attr):
            self._log_error(str(result))
            return None
        self._in_error = False
        return result

    async def async_pymodbus_call(
        self,
        unit: int | None,
        address: int,
        value: int | list[int],
        use_call: str,
    ) -> ModbusResponse | None:
        """Convert async to sync pymodbus call."""
        if self._config_delay:
            return None
        async with self._lock:
            if not self._client:
                return None  # pragma: no cover
            result = await self.hass.async_add_executor_job(
                self._pymodbus_call, unit, address, value, use_call
            )
            if self._msg_wait:
                # small delay until next request/response
                await asyncio.sleep(self._msg_wait)
            return result
