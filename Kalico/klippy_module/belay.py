# Belay extruder-syncing sensor support
#
# Copyright (C) 2023-2025 Ryan Ghosh <rghosh776@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import math
from abc import ABC, abstractmethod


class Belay:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()

        # read name
        self.name = config.get_name().split()[1]

        # create slider sensor
        self.slider_sensor = get_selected_subclass(
            SliderSensor, "sensor_type", config
        )(config, self)

        # create secondary extruder
        self.secondary_extruder = get_selected_subclass(
            SecondaryExtruder, "extruder_type", config
        )(config, self)

        # create extrusion direction monitor (if it doesn't already exist)
        # and register extrusion direction update handler
        monitor_name = "belay_extrusion_direction_monitor"
        monitor = self.printer.lookup_object(monitor_name, default=None)
        if monitor is None:
            monitor = ExtrusionDirectionMonitor(self.printer)
            self.printer.add_object(monitor_name, monitor)
        monitor.register_direction_update_handler(
            self.slider_sensor.handle_extrusion_direction_update
        )

        # register event handlers
        for event in self.secondary_extruder.get_enable_events():
            self.printer.register_event_handler(event, self.enable)
        for event in self.secondary_extruder.get_disable_events():
            self.printer.register_event_handler(event, self.disable)

        # read other values
        self.debug_level = config.getint(
            "debug_level", default=0, minval=0, maxval=2
        )

        # other variables
        self.enabled = False
        self.user_disable = False
        self.next_cmd_generator = None
        self.gcode = self.printer.lookup_object("gcode")

        # register commands
        self.gcode.register_mux_command(
            "QUERY_BELAY",
            "BELAY",
            self.name,
            self.cmd_QUERY_BELAY,
            desc=self.cmd_QUERY_BELAY_help,
        )
        self.gcode.register_mux_command(
            "ENABLE_BELAY",
            "BELAY",
            self.name,
            self.cmd_ENABLE_BELAY,
            desc=self.cmd_ENABLE_BELAY_help,
        )
        self.gcode.register_mux_command(
            "DISABLE_BELAY",
            "BELAY",
            self.name,
            self.cmd_DISABLE_BELAY,
            desc=self.cmd_DISABLE_BELAY_help,
        )
        self.gcode.register_mux_command(
            "BELAY_CLEAR_OVERRIDE",
            "BELAY",
            self.name,
            self.cmd_BELAY_CLEAR_OVERRIDE,
            desc=self.cmd_BELAY_CLEAR_OVERRIDE_help,
        )
        self.gcode.register_mux_command(
            "BELAY_NEXT",
            "BELAY",
            self.name,
            self.cmd_BELAY_NEXT,
            desc=self.cmd_BELAY_NEXT_help,
        )

    def enable(self):
        if self.enabled or self.user_disable:
            return
        for condition in self.secondary_extruder.get_enable_conditions():
            if not condition():
                return
        self.enabled = True
        self.slider_sensor.handle_enable()

    def disable(self):
        if not self.enabled:
            return
        for condition in self.secondary_extruder.get_disable_conditions():
            if not condition():
                return
        self.reset_multiplier()
        self.enabled = False
        self.slider_sensor.handle_disable()

    def set_multiplier(self, multiplier, print_msg=True):
        if not self.enabled:
            return

        self.secondary_extruder.set_multiplier(multiplier)
        if (print_msg and self.debug_level >= 1) or self.debug_level >= 2:
            self.gcode.respond_info(
                "Set secondary extruder multiplier: %f" % multiplier
            )

    def reset_multiplier(self):
        self.set_multiplier(1.0, print_msg=False)
        if self.debug_level >= 1:
            self.gcode.respond_info("Reset secondary extruder multiplier")

    def start_next_cmd_generator(self, generator):
        self.next_cmd_generator = generator
        next(self.next_cmd_generator)

    cmd_QUERY_BELAY_help = "Report Belay sensor state"

    def cmd_QUERY_BELAY(self, gcmd):
        self.gcode.respond_info(
            "belay {}: {}".format(
                self.name, self.slider_sensor.get_state_description()
            )
        )

    cmd_ENABLE_BELAY_help = "Enable Belay extrusion multiplier adjustment"

    def cmd_ENABLE_BELAY(self, gcmd):
        self.user_disable = False
        self.enable()
        if not self.enabled:
            raise self.printer.command_error(
                "Conditions not met to enable belay {}".format(self.name)
            )

    cmd_DISABLE_BELAY_help = "Disable Belay extrusion multiplier adjustment"

    def cmd_DISABLE_BELAY(self, gcmd):
        if gcmd.get_int("OVERRIDE", 0):
            self.user_disable = True
        self.disable()
        if self.enabled:
            raise self.printer.command_error(
                "Conditions not met to disable belay {}".format(self.name)
            )

    cmd_BELAY_CLEAR_OVERRIDE_help = (
        "Clears any user override that would prevent the Belay from being"
        " automatically enabled"
    )

    def cmd_BELAY_CLEAR_OVERRIDE(self, gcmd):
        self.user_disable = False

    cmd_BELAY_NEXT_help = (
        "You will be prompted to use this command if Belay requires user"
        " confirmation"
    )

    def cmd_BELAY_NEXT(self, gcmd):
        if self.next_cmd_generator:
            try:
                next(self.next_cmd_generator)
            except Exception as e:
                self.next_cmd_generator = None
                if not isinstance(e, StopIteration):
                    raise
        else:
            raise self.printer.command_error("BELAY_NEXT command is inactive")

    def get_status(self, eventtime):
        status = {
            "enabled": self.enabled,
            "state_description": self.slider_sensor.get_state_description(),
            "slider_dimensionless_position": (
                self.slider_sensor.get_dimensionless_position()
            ),
        }
        status.update(self.slider_sensor.get_status(eventtime))
        return status


DIRECTION_UPDATE_INTERVAL = 0.1


class ExtrusionDirectionMonitor:
    def __init__(self, printer):
        self.printer = printer
        self.reactor = self.printer.get_reactor()

        # register event handlers
        self.printer.register_event_handler(
            "klippy:connect", self.handle_connect
        )
        self.printer.register_event_handler("klippy:ready", self.handle_ready)

        # other variables
        self.toolhead = None
        self.update_direction_timer = self.reactor.register_timer(
            self.update_direction
        )
        self.last_direction = True
        self.flush_id = True
        self.last_flushed_e_pos = 0.0
        self.direction_update_handlers = []

    def handle_connect(self):
        self.toolhead = self.printer.lookup_object("toolhead")

    def handle_ready(self):
        self.reactor.update_timer(self.update_direction_timer, self.reactor.NOW)

    def _get_lookahead(self):
        if hasattr(self.toolhead, "lookahead"):
            return self.toolhead.lookahead
        else:
            return self.toolhead.move_queue

    def update_direction(self, eventtime):
        if self._get_lookahead().get_last() is not None:
            self.toolhead.register_lookahead_callback(
                lambda pt, f=self.flush_id: self.handle_flush(pt, f)
            )
        return eventtime + DIRECTION_UPDATE_INTERVAL

    def handle_flush(self, print_time, curr_flush_id):
        # return if this lookahead flush was already handled
        if self.flush_id != curr_flush_id:
            return

        # get ending extruder position of moves that will be flushed
        last_move = self._get_lookahead().get_last()
        if last_move is not None:
            e_pos = last_move.end_pos[3]
        else:
            e_pos = self.last_flushed_e_pos

        # note net direction of moves and call handler callbacks if the
        # direction changed
        direction = e_pos >= self.last_flushed_e_pos
        if direction != self.last_direction:
            for handler in self.direction_update_handlers:
                handler(direction)

        self.last_direction = direction
        self.flush_id = not self.flush_id
        self.last_flushed_e_pos = e_pos

    def register_direction_update_handler(self, callback):
        self.direction_update_handlers.append(callback)

    def get_status(self, eventtime):
        return {
            "last_extrusion_direction": self.last_direction,
            "last_flushed_extruder_position": self.last_flushed_e_pos,
        }


class NamedConfigOptionChoice(ABC):
    @classmethod
    @abstractmethod
    def get_name(cls):
        pass


def get_selected_subclass(parent_class, config_option, config, default=None):
    choices = {}
    for cls in parent_class.__subclasses__():
        choices[cls.get_name()] = cls
    if default is None:
        return config.getchoice(config_option, choices)
    if default not in choices:
        raise Exception(
            "Default '{}' is not among subclass names of parent class {}".format(
                default, parent_class
            )
        )
    return config.getchoice(config_option, choices, default=default)


# Slider sensors


class SliderSensor(NamedConfigOptionChoice, ABC):
    @abstractmethod
    def __init__(self, config, belay):
        pass

    @abstractmethod
    def handle_extrusion_direction_update(self, new_direction):
        pass

    @abstractmethod
    def handle_enable(self):
        pass

    @abstractmethod
    def handle_disable(self):
        pass

    @abstractmethod
    def get_state_description(self):
        pass

    @abstractmethod
    def get_dimensionless_position(self):
        pass

    def get_status(self, eventtime):
        return {}


class SingleDigitalSwitch(SliderSensor):
    def __init__(self, config, belay):
        self.printer = config.get_printer()
        self.belay = belay

        # register button
        sensor_pin = config.get("sensor_pin")
        buttons = self.printer.load_object(config, "buttons")
        buttons.register_buttons([sensor_pin], self.sensor_callback)

        # read other values
        self.multiplier_high = config.getfloat(
            "multiplier_high", default=1.05, minval=1.0
        )
        self.multiplier_low = config.getfloat(
            "multiplier_low", default=0.95, minval=0.5, maxval=1.0
        )

        # other variables
        self.last_state = False
        self.last_direction = True
        self.gcode = self.printer.lookup_object("gcode")

        # register commands
        gcode = self.printer.lookup_object("gcode")
        gcode.register_mux_command(
            "BELAY_SET_MULTIPLIER",
            "BELAY",
            self.belay.name,
            self.cmd_BELAY_SET_MULTIPLIER,
            desc=self.cmd_BELAY_SET_MULTIPLIER_help,
        )

    @classmethod
    def get_name(cls):
        return "single_digital_switch"

    def sensor_callback(self, eventtime, state):
        self.last_state = state
        self._update_multiplier()

    def _update_multiplier(self, print_msg=True):
        if not self.belay.enabled:
            return

        if self.last_state == self.last_direction:
            # compressed/forward or expanded/backward
            multiplier = self.multiplier_high
        else:
            # compressed/backward or expanded/forward
            multiplier = self.multiplier_low
        self.belay.set_multiplier(multiplier, print_msg=print_msg)

    def handle_extrusion_direction_update(self, new_direction):
        self.last_direction = new_direction
        self._update_multiplier(print_msg=False)

    def handle_enable(self):
        self._update_multiplier()

    def handle_disable(self):
        return

    def get_state_description(self):
        if self.last_state:
            return "compressed"
        return "expanded"

    def get_dimensionless_position(self):
        if self.last_state:
            return 1.0
        return -1.0

    cmd_BELAY_SET_MULTIPLIER_help = (
        "Sets multiplier_high and/or multiplier_low. Does not persist across"
        " restarts."
    )

    def cmd_BELAY_SET_MULTIPLIER(self, gcmd):
        self.multiplier_high = gcmd.get_float(
            "HIGH", self.multiplier_high, minval=1.0
        )
        self.multiplier_low = gcmd.get_float(
            "LOW", self.multiplier_low, minval=0.0, maxval=1.0
        )


SWITCH_COMPRESSION = 0
SWITCH_EXPANSION = 1


class DualDigitalSwitch(SliderSensor):
    def __init__(self, config, belay):
        self.printer = config.get_printer()
        self.belay = belay

        # register buttons
        compression_sensor_pin = config.get("compression_sensor_pin")
        expansion_sensor_pin = config.get("expansion_sensor_pin")
        buttons = self.printer.load_object(config, "buttons")
        buttons.register_buttons(
            [compression_sensor_pin],
            lambda e, s: self.sensor_callback(e, s, switch=SWITCH_COMPRESSION),
        )
        buttons.register_buttons(
            [expansion_sensor_pin],
            lambda e, s: self.sensor_callback(e, s, switch=SWITCH_EXPANSION),
        )

        # read other values
        self.multiplier_high = config.getfloat(
            "multiplier_high", default=1.05, minval=1.0
        )
        self.multiplier_low = config.getfloat(
            "multiplier_low", default=0.95, minval=0.5, maxval=1.0
        )
        self.multiplier_mid = config.getfloat(
            "multiplier_mid",
            default=1.0,
            minval=self.multiplier_low,
            maxval=self.multiplier_high,
        )

        # other variables
        self.last_state = [False, False]
        self.last_direction = True
        self.gcode = self.printer.lookup_object("gcode")

        # register commands
        gcode = self.printer.lookup_object("gcode")
        gcode.register_mux_command(
            "BELAY_SET_MULTIPLIER",
            "BELAY",
            self.belay.name,
            self.cmd_BELAY_SET_MULTIPLIER,
            desc=self.cmd_BELAY_SET_MULTIPLIER_help,
        )

    @classmethod
    def get_name(cls):
        return "dual_digital_switch"

    def sensor_callback(self, eventtime, state, switch):
        self.last_state[switch] = state
        self._update_multiplier()

    def _update_multiplier(self, print_msg=True):
        if not self.belay.enabled:
            return

        if self.last_state[0] == self.last_state[1]:
            # slider is in the middle zone
            multiplier = self.multiplier_mid
        else:
            # slider is in one of the 2 outer zones
            if self.last_state[SWITCH_COMPRESSION] == self.last_direction:
                # compressed/forward or expanded/backward
                multiplier = self.multiplier_high
            else:
                # compressed/backward or expanded/forward
                multiplier = self.multiplier_low
        self.belay.set_multiplier(multiplier, print_msg=print_msg)

    def handle_extrusion_direction_update(self, new_direction):
        self.last_direction = new_direction
        self._update_multiplier(print_msg=False)

    def handle_enable(self):
        self._update_multiplier()

    def handle_disable(self):
        return

    def get_state_description(self):
        if self.last_state[0] == self.last_state[1]:
            return "neutral"
        if self.last_state[SWITCH_COMPRESSION]:
            return "compressed"
        return "expanded"

    def get_dimensionless_position(self):
        if self.last_state[0] == self.last_state[1]:
            return 0.0
        if self.last_state[SWITCH_COMPRESSION]:
            return 1.0
        return -1.0

    cmd_BELAY_SET_MULTIPLIER_help = (
        "Sets multiplier_high, multiplier_low, and/or multiplier_mid. Does not"
        " persist across restarts."
    )

    def cmd_BELAY_SET_MULTIPLIER(self, gcmd):
        self.multiplier_high = gcmd.get_float(
            "HIGH", self.multiplier_high, minval=1.0
        )
        self.multiplier_low = gcmd.get_float(
            "LOW", self.multiplier_low, minval=0.0, maxval=1.0
        )
        self.multiplier_mid = gcmd.get_float(
            "MID",
            self.multiplier_mid,
            minval=self.multiplier_low,
            maxval=self.multiplier_high,
        )


class AnalogSliderSensor(SliderSensor):
    def __init__(self, config, belay):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.belay = belay

        # read slider travel
        self.slider_travel = config.getfloat("slider_travel", above=0.0)
        self.slider_travel_half = self.slider_travel / 2

        # create sensor (for converting ADC readings to slider positions)
        self.sensor = get_selected_subclass(
            AnalogPositionSensor, "sensor_subtype", config
        )(config, -self.slider_travel_half, self.slider_travel_half)

        # set up ADC callback
        ppins = self.printer.lookup_object("pins")
        self.mcu_adc = ppins.setup_pin("adc", config.get("sensor_pin"))
        adc_sample_time = config.getfloat(
            "adc_sample_time", default=0.001, above=0.0
        )
        adc_sample_count = config.getint(
            "adc_sample_count", default=1, minval=1
        )
        control_loop_interval = config.getfloat(
            "control_loop_interval", default=0.1, above=0.0
        )
        self.mcu_adc.setup_minmax(adc_sample_time, adc_sample_count)
        self.mcu_adc.setup_adc_callback(
            control_loop_interval, self.adc_callback
        )

        # create controller
        slider_setpoint = config.getfloat(
            "slider_setpoint",
            default=0.0,
            above=-self.slider_travel_half,
            below=self.slider_travel_half,
        )
        multiplier_max_offset = config.getfloat(
            "multiplier_max_offset", default=0.5, above=0.0, below=1.0
        )
        self.controller = SliderPIDController(
            config, slider_setpoint, multiplier_max_offset
        )

        # read other values
        msg_interval = config.getint(
            "message_interval",
            default=5,
            above=math.ceil(control_loop_interval),
        )
        self.loops_per_msg = round(msg_interval / control_loop_interval)

        # other variables
        self.toolhead = None
        self.last_slider_pos = 0.0
        self.last_raw_multiplier_offset = 0.0
        self.msg_loop_counter = 0
        self.gcode = self.printer.lookup_object("gcode")

        # register_commands
        self.gcode.register_mux_command(
            "BELAY_CALIBRATE",
            "BELAY",
            self.belay.name,
            self.cmd_BELAY_CALIBRATE,
            desc=self.cmd_BELAY_CALIBRATE_help,
        )
        self.gcode.register_mux_command(
            "BELAY_SET_SLIDER_SETPOINT",
            "BELAY",
            self.belay.name,
            self.cmd_BELAY_SET_SLIDER_SETPOINT,
            desc=self.cmd_BELAY_SET_SLIDER_SETPOINT_help,
        )

    def handle_connect(self):
        self.toolhead = self.printer.lookup_object("toolhead")

    @classmethod
    def get_name(cls):
        return "analog_position_sensor"

    def handle_extrusion_direction_update(self, new_direction):
        self.last_direction = new_direction
        if self.belay.enabled:
            self._set_multiplier(self.last_raw_multiplier_offset)

    def handle_enable(self):
        eventtime = self.reactor.monotonic()
        mcu = self.printer.lookup_object("mcu")
        est_print_time = mcu.estimated_print_time(eventtime)
        extruder = self.toolhead.get_extruder()
        last_e_pos = extruder.find_past_position(est_print_time)
        self.controller.reset(self.last_slider_pos, last_e_pos)
        self.last_raw_multiplier_offset = 0.0

    def handle_disable(self):
        return

    def get_state_description(self):
        slider_pos_percentage = round(
            self.last_slider_pos / self.slider_travel_half * 100
        )
        if self.last_slider_pos >= 0.0:
            return "compressed {}mm ({}%) from center".format(
                self.last_slider_pos, slider_pos_percentage
            )
        return "expanded {}mm ({}%) from center".format(
            -self.last_slider_pos, -slider_pos_percentage
        )

    def get_dimensionless_position(self):
        return self.last_slider_pos / self.slider_travel_half

    def adc_callback(self, read_time, read_value):
        # note slider position
        self.last_slider_pos = self.sensor.get_position(read_time, read_value)
        if not self.belay.enabled:
            return

        # update extruder multiplier
        extruder = self.toolhead.get_extruder()
        e_pos = extruder.find_past_position(read_time)
        self.last_raw_multiplier_offset = self.controller.update(
            self.last_slider_pos, e_pos
        )
        self._set_multiplier(
            self.last_raw_multiplier_offset,
            print_msg=(self.msg_loop_counter == 0),
        )
        self.msg_loop_counter = (self.msg_loop_counter + 1) % self.loops_per_msg

    def _set_multiplier(self, raw_multiplier_offset, print_msg=False):
        if self.last_direction:
            multiplier_offset = -raw_multiplier_offset
        else:
            multiplier_offset = raw_multiplier_offset
        self.belay.set_multiplier(1.0 + multiplier_offset, print_msg=print_msg)

    cmd_BELAY_CALIBRATE_help = "Calibrate the Belay sensor"

    def cmd_BELAY_CALIBRATE(self, gcmd):
        self.belay.start_next_cmd_generator(self._calibrate())

    def _calibrate(self):
        self.gcode.respond_info(
            "Move belay {} to its fully expanded state, and hold it there while"
            " using BELAY_NEXT to continue calibration.".format(self.belay.name)
        )
        yield

        min_pos_reading = self.mcu_adc.get_last_value()[0]
        self.gcode.respond_info(
            "Move belay {} to its fully compressed state, and hold it there"
            " while using BELAY_NEXT to continue calibration.".format(
                self.belay.name
            )
        )
        yield

        max_pos_reading = self.mcu_adc.get_last_value()[0]
        sensor_msg = self.sensor.update_calibration(
            min_pos_reading, max_pos_reading
        )
        self.gcode.respond_info(
            "Calibration complete:\n{}\nMake sure to update the printer config"
            " file with these parameters so they will be kept across restarts:".format(
                sensor_msg
            )
        )

    cmd_BELAY_SET_SLIDER_SETPOINT_help = "Set the target slider position"

    def cmd_BELAY_SET_SLIDER_SETPOINT(self, gcmd):
        if gcmd.get_int("DIMENSIONLESS", 0):
            setpoint = (
                gcmd.get_float("SETPOINT", default=0.0, above=-1.0, below=1.0)
                * self.slider_travel_half
            )
        else:
            setpoint = gcmd.get_float(
                "SETPOINT",
                default=0.0,
                above=-self.slider_travel_half,
                below=self.slider_travel_half,
            )
        self.controller.set_slider_setpoint(setpoint)

    def get_status(self, eventtime):
        return {"slider_position": self.last_slider_pos}


class SliderPIDController:
    def __init__(self, config, setpoint_initial, output_min_max_abs):
        self.slider_setpoint = setpoint_initial
        self.output_min_max_abs = output_min_max_abs

        # read values from config
        self.Kp = config.getfloat("pid_Kp", minval=0.0)  # proportional gain
        self.Ki = config.getfloat("pid_Ki", above=0.0)  # integral gain
        self.Kd = config.getfloat("pid_Kd", minval=0.0)  # derivative gain
        self.Ti = self.Kp / self.Ki  # integral "time"
        self.Td = self.Kd / self.Kp  # derivative "time"
        self.Tt = config.getfloat(  # tracking "time" constant
            "pid_Tt",
            default=math.sqrt(self.Ti * self.Td),
            above=self.Td,
            below=self.Ti,
        )

        # other variables
        self.last_slider_pos = 0.0
        self.last_e_pos = 0.0
        self.last_error = 0.0
        self.integral = 0.0

    def update(self, slider_pos, e_pos):
        error = self.slider_setpoint - slider_pos
        de = abs(e_pos - self.last_e_pos)

        proportional = self.Kp * error
        self.integral += self.Ki * (self.last_error + error) / 2.0 * de
        derivative = -self.Kd * (slider_pos - self.last_slider_pos) / de
        output_raw = proportional + self.integral + derivative
        output_clamped = min(
            max(output_raw, -self.output_min_max_abs),
            self.output_min_max_abs,
        )

        self.integral += (output_clamped - output_raw) * de / max(self.Tt, de)
        self.last_slider_pos = slider_pos
        self.last_e_pos = e_pos
        self.last_error = error

        return output_clamped

    def set_slider_setpoint(self, setpoint):
        self.slider_setpoint = setpoint

    def reset(self, last_slider_pos, last_e_pos):
        self.last_slider_pos = last_slider_pos
        self.last_e_pos = last_e_pos
        self.last_error = self.slider_setpoint - self.last_slider_pos
        self.integral = 0.0


class AnalogPositionSensor(NamedConfigOptionChoice, ABC):
    @abstractmethod
    def __init__(self, config, min_position, max_position):
        pass

    @abstractmethod
    def get_position(self, read_time, read_value):
        pass

    @abstractmethod
    def update_calibration(self, min_position_reading, max_position_reading):
        pass


class LinearPotentiometer(AnalogPositionSensor):
    def __init__(self, config, min_position, max_position):
        self.min_pos = min_position
        self.max_pos = max_position
        self.pos_span = self.max_pos - self.min_pos
        self.min_pos_reading = config.get_float(
            "min_position_reading", default=0.0
        )
        self.max_pos_reading = config.get_float(
            "max_position_reading", default=1.0
        )

    @classmethod
    def get_name(cls):
        return "linear_potentiometer"

    def get_position(self, read_time, read_value):
        reading_span = self.max_pos_reading - self.min_pos_reading
        return (
            self.min_pos
            + (read_value - self.min_pos_reading) / reading_span * self.pos_span
        )

    def update_calibration(self, min_position_reading, max_position_reading):
        self.min_pos_reading = min_position_reading
        self.max_pos_reading = max_position_reading
        return "min_position_reading: {}\nmax_position_reading: {}".format(
            min_position_reading, max_position_reading
        )


# Secondary extruders


class SecondaryExtruder(NamedConfigOptionChoice, ABC):
    @abstractmethod
    def __init__(self, config, belay):
        pass

    def get_enable_events(self):
        return []

    def get_disable_events(self):
        return []

    def get_enable_conditions(self):
        return []

    def get_disable_conditions(self):
        return []

    @abstractmethod
    def set_multiplier(self, multiplier):
        pass


class TradRack(SecondaryExtruder):
    def __init__(self, config, belay):
        self.printer = config.get_printer()
        self.belay = belay

        # register event handlers
        self.printer.register_event_handler(
            "klippy:connect", self.handle_connect
        )

        # other variables
        self.set_multiplier_fn = None
        self.enable_conditions = []
        self.disable_conditions = []

    def handle_connect(self):
        trad_rack = self.printer.lookup_object("trad_rack")
        self.set_multiplier_fn = trad_rack.set_fil_driver_multiplier
        self.enable_conditions = [trad_rack.is_fil_driver_synced]
        self.disable_conditions = [trad_rack.is_fil_driver_synced]

    @classmethod
    def get_name(cls):
        return "trad_rack"

    def get_enable_events(self):
        return ["trad_rack:synced_to_extruder"]

    def get_disable_events(self):
        return ["trad_rack:unsyncing_from_extruder"]

    def get_enable_conditions(self):
        return self.enable_conditions

    def get_disable_conditions(self):
        return self.disable_conditions

    def set_multiplier(self, multiplier):
        self.set_multiplier_fn(multiplier)


class ExtruderStepper(SecondaryExtruder):
    def __init__(self, config, belay):
        self.printer = config.get_printer()
        self.belay = belay

        # read extruder stepper name
        self.extruder_stepper_name = config.get("extruder_stepper_name")

        # register event handlers
        self.printer.register_event_handler(
            "klippy:connect", self.handle_connect
        )

        # register commands
        gcode = self.printer.lookup_object("gcode")
        gcode.register_mux_command(
            "BELAY_SET_STEPPER",
            "BELAY",
            self.belay.name,
            self.cmd_BELAY_SET_STEPPER,
            desc=self.cmd_BELAY_SET_STEPPER_help,
        )

        # other variables
        self.set_multiplier_fn = None

    def handle_connect(self):
        self._set_extruder_stepper(self.extruder_stepper_name)

    def _set_extruder_stepper(self, extruder_stepper_name):
        printer_extruder_stepper = self.printer.lookup_object(
            "extruder_stepper {}".format(extruder_stepper_name)
        )
        stepper = printer_extruder_stepper.extruder_stepper.stepper
        base_rotation_dist = stepper.get_rotation_distance()[0]
        self.set_multiplier_fn = lambda m: stepper.set_rotation_distance(
            base_rotation_dist / m
        )

    @classmethod
    def get_name(cls):
        return "extruder_stepper"

    def get_enable_events(self):
        return ["klippy:ready"]

    def set_multiplier(self, multiplier):
        self.set_multiplier_fn(multiplier)

    cmd_BELAY_SET_STEPPER_help = (
        "Select the extruder_stepper object to be controlled by the Belay"
    )

    def cmd_BELAY_SET_STEPPER(self, gcmd):
        self.belay.disable()
        self._set_extruder_stepper(gcmd.get("STEPPER"))
        self.belay.enable()


def load_config_prefix(config):
    return Belay(config)
