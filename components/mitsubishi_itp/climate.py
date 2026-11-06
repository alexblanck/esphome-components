import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import climate, time, uart
from esphome.const import (
    CONF_CUSTOM_FAN_MODES,
    CONF_ID,
    CONF_SUPPORTED_FAN_MODES,
    CONF_SUPPORTED_MODES,
    CONF_TIME_ID,
)
from esphome.core import coroutine

from . import MitsubishiUART, itp_packet_ns

DEPENDENCIES = [
    "uart",
]

CONF_UART_HEATPUMP = "uart_heatpump"
CONF_UART_THERMOSTAT = "uart_thermostat"

CONF_ENHANCED_MHK_SUPPORT = (
    "enhanced_mhk"  # EXPERIMENTAL. Will be set to default eventually.
)
CONF_MHK_F_CORRECTION = "mhk_fahrenheit_correction"
# Minimum difference between low (heat) and high (cool) setpoints, stored in °C.
# 0 allows equal setpoints; inverted setpoints (low > high) are still corrected.
CONF_MIN_SETPOINT_GAP = "minimum_setpoint_gap"
# In HEAT_COOL the gap is the only deadband for MITP's own heat/cool changeover, so
# smaller gaps are only allowed with HEAT_COOL disabled.
HEAT_COOL_MIN_SETPOINT_GAP = 2.0

DEFAULT_POLLING_INTERVAL = "5s"

DEFAULT_CLIMATE_MODES = ["OFF", "HEAT", "DRY", "COOL", "FAN_ONLY", "HEAT_COOL"]
DEFAULT_FAN_MODES = ["AUTO", "QUIET", "LOW", "MEDIUM", "HIGH"]
CUSTOM_FAN_MODES = {"VERYHIGH": itp_packet_ns.FAN_MODE_VERYHIGH}

validate_custom_fan_modes = cv.enum(CUSTOM_FAN_MODES, upper=True)


def validate_min_setpoint_gap(config):
    if config[CONF_MIN_SETPOINT_GAP] < HEAT_COOL_MIN_SETPOINT_GAP and "HEAT_COOL" in [
        str(mode).upper() for mode in config[CONF_SUPPORTED_MODES]
    ]:
        raise cv.Invalid(
            f"{CONF_MIN_SETPOINT_GAP} below {HEAT_COOL_MIN_SETPOINT_GAP}°C requires HEAT_COOL to be "
            f"removed from {CONF_SUPPORTED_MODES} (it's included by default). In HEAT_COOL, too small "
            "a gap could cause rapid switching between heating and cooling.",
            path=[CONF_MIN_SETPOINT_GAP],
        )
    return config


CONFIG_SCHEMA = cv.All(
    climate.climate_schema(MitsubishiUART)
    .extend(
        {
            cv.GenerateID(CONF_ID): cv.declare_id(MitsubishiUART),
            cv.Required(CONF_UART_HEATPUMP): cv.use_id(uart.UARTComponent),
            cv.Optional(CONF_UART_THERMOSTAT): cv.use_id(uart.UARTComponent),
            cv.OnlyWith(CONF_TIME_ID, "time"): cv.use_id(time.RealTimeClock),
            cv.Optional(
                CONF_SUPPORTED_MODES, default=DEFAULT_CLIMATE_MODES
            ): cv.ensure_list(climate.validate_climate_mode),
            cv.Optional(
                CONF_SUPPORTED_FAN_MODES, default=DEFAULT_FAN_MODES
            ): cv.ensure_list(climate.validate_climate_fan_mode),
            cv.Optional(CONF_CUSTOM_FAN_MODES, default=["VERYHIGH"]): cv.ensure_list(
                validate_custom_fan_modes
            ),
            cv.Optional(CONF_ENHANCED_MHK_SUPPORT, default=False): cv.boolean,
            cv.Optional(CONF_MHK_F_CORRECTION, default=False): cv.boolean,
            # Accepts units ("2°C", "3.6°F", "2K"); a bare number is °C.
            cv.Optional(CONF_MIN_SETPOINT_GAP, default="2°C"): cv.All(
                cv.temperature_delta, cv.Range(min=0.0, max=10.0)
            ),
        }
    )
    .extend(cv.polling_component_schema(DEFAULT_POLLING_INTERVAL)),
    validate_min_setpoint_gap,
)


def final_validate(config):
    schema = uart.final_validate_device_schema(
        "mitsubishi_itp",
        uart_bus=CONF_UART_HEATPUMP,
        require_tx=True,
        require_rx=True,
        data_bits=8,
        parity="EVEN",
        stop_bits=1,
    )
    if CONF_UART_THERMOSTAT in config:
        schema = schema.extend(
            uart.final_validate_device_schema(
                "mitsubishi_itp",
                uart_bus=CONF_UART_THERMOSTAT,
                require_tx=True,
                require_rx=True,
                data_bits=8,
                parity="EVEN",
                stop_bits=1,
            )
        )
    schema(config)


FINAL_VALIDATE_SCHEMA = final_validate


@coroutine
async def to_code(config):
    hp_uart_component = await cg.get_variable(config[CONF_UART_HEATPUMP])
    mitp_component = cg.new_Pvariable(config[CONF_ID], hp_uart_component)

    await cg.register_component(mitp_component, config)
    await climate.register_climate(mitp_component, config)

    # If thermostat defined
    if CONF_UART_THERMOSTAT in config:
        # Register thermostat with MITP
        ts_uart_component = await cg.get_variable(config[CONF_UART_THERMOSTAT])
        cg.add(getattr(mitp_component, "set_thermostat_uart")(ts_uart_component))

    # If RTC defined
    if CONF_TIME_ID in config:
        rtc_component = await cg.get_variable(config[CONF_TIME_ID])
        cg.add(getattr(mitp_component, "set_time_source")(rtc_component))
    elif CONF_UART_THERMOSTAT in config and config.get(CONF_ENHANCED_MHK_SUPPORT):
        raise cv.RequiredFieldInvalid(
            f"A 'time' component is required if {CONF_ENHANCED_MHK_SUPPORT} is set."
        )

    # Traits
    traits = mitp_component.config_traits()

    if CONF_SUPPORTED_MODES in config:
        cg.add(traits.set_supported_modes(config[CONF_SUPPORTED_MODES]))

    if CONF_SUPPORTED_FAN_MODES in config:
        cg.add(traits.set_supported_fan_modes(config[CONF_SUPPORTED_FAN_MODES]))

    if CONF_CUSTOM_FAN_MODES in config and len(config[CONF_CUSTOM_FAN_MODES]) > 0:
        cg.add(mitp_component.set_supported_custom_fan_modes(config[CONF_CUSTOM_FAN_MODES]))

    cg.add(mitp_component.set_min_setpoint_gap(config[CONF_MIN_SETPOINT_GAP]))

    # Debug Settings
    if enhanced_mhk_protocol := config.get(CONF_ENHANCED_MHK_SUPPORT):
        cg.add(
            getattr(mitp_component, "set_enhanced_mhk_support")(enhanced_mhk_protocol)
        )
    if mhk_f_correction := config.get(CONF_MHK_F_CORRECTION):
        cg.add(getattr(mitp_component, "set_mhk_f_correction")(mhk_f_correction))

    try:
        cg.add_library(
            name="itp-packet",
            repository="https://github.com/muart-group/itp-packet.git#v2",
            version="v2", # This doesn't appear to do anything, but is required
        )
    except ValueError as e:
        # If a library is manually defined in the ESPHome config it will "conflict"
        # Catching this here prevents this method from stopping codegen and the process
        # will proceed with only the config library. This may break if there is another ValueError here.
        print(f"Didn't add public itp-packet library due to:{e}")
