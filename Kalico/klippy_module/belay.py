# Belay extruder-syncing sensor support
#
# Copyright (C) 2023-2026 Ryan Ghosh <rghosh776@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import math
from abc import ABC, abstractmethod
from collections import deque
from os import path


class Belay:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()

        # read name
        self.name = config.get_name().split()[1]

        # create logger
        log_file_path = path.expanduser(
            config.get(
                "log_file_path",
                default="~/printer_data/logs/belay_{}.csv".format(self.name),
            )
        )
        _, ext = path.splittext(log_file_path)
        if ext != ".csv":
            raise config.error(
                "belay {}: option 'log_file_path' must end in '.csv'".format(
                    self.name
                )
            )
        log_update_interval = config.getfloat(
            "log_update_interval", default=0.1, above=0.0
        )
        enable_logging_initial = config.getboolean(
            "enable_logging", default=False
        )
        self.logger = BelayLogger(
            log_file_path,
            log_update_interval,
            self.printer,
            lambda: self.enabled,
            active_initial=enable_logging_initial,
        )

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

        # get BELAY_NEXT command manager (creating it if it doesn't already
        # exist)
        next_cmd_manager_name = "belay_next_cmd_manager"
        self.next_cmd_manager = self.printer.lookup_object(
            next_cmd_manager_name, default=None
        )
        if self.next_cmd_manager is None:
            self.next_cmd_manager = BelayNextCommandManager(self.printer)
            self.printer.add_object(
                next_cmd_manager_name, self.next_cmd_manager
            )

        # register event handlers
        for event in self.secondary_extruder.get_enable_events():
            self.printer.register_event_handler(event, self.enable)
        for event in self.secondary_extruder.get_disable_events():
            self.printer.register_event_handler(event, self.disable)
        self.printer.register_event_handler(
            "klippy:connect", self.handle_connect
        )

        # add columns to logger
        self.log_multiplier = self.logger.add_column(
            "extruder_multiplier", 1.0, format=".6f"
        )
        log_net_flushed_moves_direction = self.logger.add_column(
            "net_flushed_moves_direction", 1, format="d"
        )
        monitor.register_direction_update_handler(
            lambda d: log_net_flushed_moves_direction(int(d))
        )
        self.logger.add_column(
            "slider_dimensionless_position",
            0.0,
            format=".3f",
            get_value_fn=lambda e: self.slider_sensor.get_dimensionless_position(),
        )

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
        self.gcode.register_mux_command(
            "BELAY_START_LOGGING",
            "BELAY",
            self.name,
            self.cmd_BELAY_START_LOGGING,
            desc=self.cmd_BELAY_START_LOGGING,
        )
        self.gcode.register_mux_command(
            "BELAY_STOP_LOGGING",
            "BELAY",
            self.name,
            self.cmd_BELAY_STOP_LOGGING,
            desc=self.cmd_BELAY_STOP_LOGGING,
        )

    def handle_connect(self):
        # add additional printer-related columns to logger
        mcu = self.printer.lookup_object("mcu")
        toolhead = self.printer.lookup_object("toolhead")

        self.logger.add_column(
            "estimated_print_time",
            0.0,
            format=".3f",
            get_value_fn=lambda e, m=mcu: m.estimated_print_time(e),
        )
        self.logger.add_column(
            "toolhead_print_time",
            0.0,
            format=".3f",
            get_value_fn=lambda e, t=toolhead: t.print_time,
        )

        def get_curr_e_position(eventtime):
            est_print_time = mcu.estimated_print_time(eventtime)
            extruder = toolhead.get_extruder()
            return extruder.find_past_position(est_print_time)

        self.logger.add_column(
            "curr_e_position",
            0.0,
            format=".3f",
            get_value_fn=get_curr_e_position,
        )
        self.logger.add_column(
            "toolhead_e_position",
            0.0,
            format=".3f",
            get_value_fn=lambda e, t=toolhead: t.get_position()[3],
        )

    def enable(self):
        if (
            self.enabled
            or self.user_disable
            or not self.secondary_extruder.can_enable()
        ):
            return
        self.enabled = True
        self.slider_sensor.handle_enable()

    def disable(self):
        if not self.enabled or not self.secondary_extruder.can_disable():
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
        self.log_multiplier(multiplier)

    def reset_multiplier(self):
        self.set_multiplier(1.0, print_msg=False)
        if self.debug_level >= 1:
            self.gcode.respond_info("Reset secondary extruder multiplier")

    def start_next_cmd_generator(self, generator):
        self.next_cmd_generator = generator
        next(self.next_cmd_generator)
        self.next_cmd_manager.note_active(self)

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
                self.next_cmd_manager.note_active(self)
            except Exception as e:
                self.next_cmd_generator = None
                self.next_cmd_manager.note_inactive(self)
                if not isinstance(e, StopIteration):
                    raise
        else:
            raise self.printer.command_error(
                "BELAY_NEXT command is inactive for belay {}".format(self.name)
            )

    cmd_BELAY_START_LOGGING_help = "Start Belay logging"

    def cmd_BELAY_START_LOGGING(self, gcmd):
        duration = gcmd.get_float("DURATION", None, above=0.0)
        self.logger.start_logging(duration=duration)

    cmd_BELAY_STOP_LOGGING_help = "Stop Belay logging"

    def cmd_BELAY_STOP_LOGGING(self, gcmd):
        self.logger.stop_logging()

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


class BelayNextCommandManager:
    def __init__(self, printer):
        self.printer = printer
        self.belay_stack = deque()

        # register commands
        gcode = self.printer.lookup_object("gcode")
        gcode.register_mux_command(
            "BELAY_NEXT",
            "BELAY",
            None,
            self.cmd_BELAY_NEXT,
            desc=Belay.cmd_BELAY_NEXT_help,
        )

    def note_active(self, belay):
        try:
            if self.belay_stack[-1] is belay:
                return
            self.belay_stack.remove(belay)
        except (IndexError, ValueError):
            pass
        self.belay_stack.append(belay)

    def note_inactive(self, belay):
        try:
            self.belay_stack.remove(belay)
        except ValueError:
            pass

    def cmd_BELAY_NEXT(self, gcmd):
        try:
            self.belay_stack[-1].cmd_BELAY_NEXT(gcmd)
        except IndexError:
            raise self.printer.command_error("BELAY_NEXT command is inactive")


class BelayLogger:
    def __init__(
        self,
        file_path,
        update_interval,
        printer,
        is_belay_enabled,
        active_initial=False,
    ):
        self.file_path = file_path
        self.update_interval = update_interval
        self.printer = printer
        self.is_belay_enabled = is_belay_enabled
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        self.active_initial = active_initial

        # register evenat handlers
        printer.register_event_handler("klippy:ready", self.handle_ready)

        # other variables
        self.update_timer = self.reactor.register_timer(self.update_log)
        self.log_file = None
        self.data = {}
        self.formats = {}
        self.data_callbacks = []
        self.row_str = None
        self.stop_time = None
        self.columns_locked = False

    def handle_ready(self):
        # block additional columns from being added
        self.columns_locked = True

        # create header and row strings
        header = ""
        self.row_str = ""
        col_names = self.formats.keys().sort()
        for col_name in col_names:
            header += ",{}".format(col_name)
            self.row_str += ",{{{}:{}}}".format(
                col_name, self.formats[col_name]
            )
        header = header[1:] + "\n"
        self.row_str = self.row_str[1:] + "\n"

        # check 1st line of file and write the header if it is not already
        # there, or use a different file if there is an existing line that
        # doesn't match
        need_new_file = False
        try:
            with open(self.file_path, "r+") as f:
                first_line = f.readline()
                if first_line == "":
                    f.write(header)
                elif first_line != header:
                    need_new_file = True
            if need_new_file:
                root, ext = self.file_path.splittext()
                new_path = self.file_path
                num = 1
                while path.exists(new_path):
                    new_path = "{}_{}{}".format(root, num, ext)
                    num += 1
                self.gcode.respond_info(
                    "File '{}' already exists but its first line does not match"
                    " the column headers. The file '{}' will be used instead.".format(
                        self.file_path, new_path
                    )
                )
                self.file_path = new_path
                with open(self.file_path, "w") as f:
                    f.write(header)
        except IOError as e:
            raise self.printer.command_error(
                "Error reading or writing to file '%s': %s",
                self.file_path,
                str(e),
            )

        # start logging
        if self.active_initial:
            self.start_logging()

    def add_column(self, name, initial_value, format="", get_value_fn=None):
        if self.columns_locked:
            raise Exception(
                "Log columns cannot be added after the printer has reached the"
                " 'ready' state"
            )
        self.data[name] = initial_value
        self.formats[name] = format
        if get_value_fn is not None:
            self.data_callbacks.append(
                lambda e, cn=name: self._update_column(cn, get_value_fn(e))
            )
        return lambda v, cn=name: self._update_column(cn, v)

    def _update_column(self, column_name, value):
        self.data[column_name] = value

    def start_logging(self, duration=None):
        if duration:
            self.stop_time = self.reactor.monotonic() + duration
        else:
            self.stop_time = None
        self.log_file = open(self.file_path, "a")
        self.reactor.update_timer(self.update_timer, self.reactor.NOW)

    def stop_logging(self):
        self.stop_time = 0.0

    def update_log(self, eventtime):
        # skip writing if belay is disabled
        if not self.is_belay_enabled():
            return eventtime + self.update_interval

        # skip writing, close file, and stop updates if logging stop time has
        # been reached
        if self.stop_time is not None and eventtime >= self.stop_time:
            self.log_file.close()
            return self.reactor.NEVER

        # update any values that are not updated automatically
        for callback in self.data_callbacks:
            callback(eventtime)

        # write a row of data
        try:
            self.log_file.write(self.row_str.format(**self.data))
        except IOError as e:
            raise self.printer.command_error(
                "Error writing to file '%s': %s", self.file_path, str(e)
            )

        # schedule next update
        return eventtime + self.update_interval


class NamedConfigOptionChoice(ABC):
    @classmethod
    @abstractmethod
    def get_name(cls):
        pass


def get_selected_subclass(parent_class, config_option, config, default=None):
    choices = {}
    for cls in parent_class.__subclasses__():
        if issubclass(cls, NamedConfigOptionChoice):
            choices[cls.get_name()] = cls
    if default is None:
        return config.getchoice(config_option, choices)
    if default not in choices:
        raise Exception(
            "Default '{}' is not the config choice name of a subclass of both"
            "{} and NamedConfigOptionChoice".format(default, parent_class)
        )
    return config.getchoice(config_option, choices, default=default)


# Slider sensors


class SliderSensor(ABC):
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


class SingleDigitalSwitch(SliderSensor, NamedConfigOptionChoice):
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


class DualDigitalSwitch(SliderSensor, NamedConfigOptionChoice):
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


class AnalogSliderSensor(SliderSensor, NamedConfigOptionChoice):
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
            "multiplier_max_offset",
            default=0.5,
            above=0.0,
        )
        self.controller = SliderPIDController(
            config, slider_setpoint, multiplier_max_offset, self.belay.logger
        )

        # read other values
        msg_interval = config.getfloat(
            "message_interval",
            default=5.0,
            minval=control_loop_interval,
        )
        self.loops_per_msg = round(msg_interval / control_loop_interval)

        # register event handlers
        self.printer.register_event_handler(
            "klippy:connect", self.handle_connect
        )

        # other variables
        self.toolhead = None
        self.last_slider_pos = 0.0
        self.last_raw_multiplier_offset = 0.0
        self.last_direction = True
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
            self.last_slider_pos / self.slider_travel_half * 100.0
        )
        if self.last_slider_pos >= 0.0:
            return "compressed {:.3f}mm ({}%) from center".format(
                self.last_slider_pos, slider_pos_percentage
            )
        return "expanded {:.3f}mm ({}%) from center".format(
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
        self.reactor.register_async_callback(
            lambda e,
            rmo=self.last_raw_multiplier_offset,
            pm=(self.msg_loop_counter == 0): self._set_multiplier(
                rmo, print_msg=pm
            )
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
            " file with these parameters so they will be kept across restarts.".format(
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
    def __init__(self, config, setpoint_initial, output_min_max_abs, logger):
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

        # add columns to logger
        self.log_slider_pos = logger.add_column(
            "slider_position", 0.0, format=".3f"
        )
        self.log_de = logger.add_column(
            "controller_extruder_position_change", 0.0, format=".3f"
        )
        self.log_error = logger.add_column(
            "controller_slider_position_error", 0.0, format=".3f"
        )
        self.log_setpoint = logger.add_column(
            "controller_slider_setpoint", self.slider_setpoint, format=".3f"
        )
        self.log_p_term = logger.add_column(
            "controller_P_term", 0.0, format=".6f"
        )
        self.log_i_term = logger.add_column(
            "controller_I_term", 0.0, format=".6f"
        )
        self.log_d_term = logger.add_column(
            "controller_D_term", 0.0, format=".6f"
        )
        self.log_raw_output = logger.add_column(
            "controller_raw_output", 0.0, format=".6f"
        )
        self.log_output = logger.add_column(
            "controller_output", 0.0, format=".6f"
        )

        # other variables
        self.last_slider_pos = 0.0
        self.last_e_pos = 0.0
        self.last_error = 0.0
        self.last_output = 0.0
        self.integral = 0.0

    def update(self, slider_pos, e_pos):
        # calculate extruder position change and slider position error
        de = abs(e_pos - self.last_e_pos)
        if de == 0.0:
            return self.last_output
        error = self.slider_setpoint - slider_pos

        # calculate PID terms and controller output
        proportional = self.Kp * error
        self.integral += self.Ki * (self.last_error + error) / 2.0 * de
        derivative = -self.Kd * (slider_pos - self.last_slider_pos) / de
        output_raw = proportional + self.integral + derivative
        output_clamped = min(
            max(output_raw, -self.output_min_max_abs),
            self.output_min_max_abs,
        )

        # log items
        self.log_slider_pos(slider_pos)
        self.log_de(de)
        self.log_error(error)
        self.log_setpoint(self.slider_setpoint)
        self.log_p_term(proportional)
        self.log_i_term(self.integral)
        self.log_d_term(derivative)
        self.log_raw_output(output_raw)
        self.log_output(output_clamped)

        # deal with integral windup and update state for next loop iteration
        self.integral += (output_clamped - output_raw) * de / max(self.Tt, de)
        self.last_slider_pos = slider_pos
        self.last_e_pos = e_pos
        self.last_error = error
        self.last_output = output_clamped

        return output_clamped

    def set_slider_setpoint(self, setpoint):
        self.slider_setpoint = setpoint

    def reset(self, last_slider_pos, last_e_pos):
        self.last_slider_pos = last_slider_pos
        self.last_e_pos = last_e_pos
        self.last_error = self.slider_setpoint - self.last_slider_pos
        self.last_output = 0.0
        self.integral = 0.0


class AnalogPositionSensor(ABC):
    @abstractmethod
    def __init__(self, config, min_position, max_position):
        pass

    @abstractmethod
    def get_position(self, read_time, read_value):
        pass

    @abstractmethod
    def update_calibration(self, min_position_reading, max_position_reading):
        pass


class LinearPositionSensor(AnalogPositionSensor, NamedConfigOptionChoice):
    def __init__(self, config, min_position, max_position):
        self.min_pos = min_position
        self.max_pos = max_position
        self.pos_span = self.max_pos - self.min_pos
        self.min_pos_reading = config.getfloat(
            "min_position_reading", default=0.0
        )
        self.max_pos_reading = config.getfloat(
            "max_position_reading", default=1.0
        )

    @classmethod
    def get_name(cls):
        return "linear_position_sensor"

    def get_position(self, read_time, read_value):
        reading_span = self.max_pos_reading - self.min_pos_reading
        return (
            self.min_pos
            + (read_value - self.min_pos_reading) / reading_span * self.pos_span
        )

    def update_calibration(self, min_position_reading, max_position_reading):
        self.min_pos_reading = min_position_reading
        self.max_pos_reading = max_position_reading
        return (
            "min_position_reading: {:.6f}\nmax_position_reading: {:.6f}".format(
                min_position_reading, max_position_reading
            )
        )


class LinearPotentiometer(AnalogPositionSensor, NamedConfigOptionChoice):
    def __init__(self, config, min_position, max_position):
        type_options = {
            "linear_output": LinearPositionSensor,
            "gnd_and_signal_with_pullup": LinearPotentiometerTwoWirePullup,
        }
        self.sensor = config.getchoice("sensor_wiring", type_options)(
            config, min_position, max_position
        )

    @classmethod
    def get_name(cls):
        return "linear_potentiometer"

    def get_position(self, read_time, read_value):
        return self.sensor.get_position(read_time, read_value)

    def update_calibration(self, min_position_reading, max_position_reading):
        return self.sensor.update_calibration(
            min_position_reading, max_position_reading
        )


class LinearPotentiometerTwoWirePullup(AnalogPositionSensor):
    def __init__(self, config, min_position, max_position):
        self.min_pos = min_position
        self.max_pos = max_position
        self.pos_span = self.max_pos - self.min_pos
        self.r_pullup = config.getfloat(
            "pullup_resistor", default=4700.0, above=0.0
        )
        self.r_min_pos = config.getfloat(
            "min_position_resistance", default=0.0, minval=0.0
        )
        self.r_max_pos = config.getfloat(
            "max_position_resistance", default=10000.0, minval=0.0
        )
        self.r_span = self.r_max_pos - self.r_min_pos

    def get_position(self, read_time, read_value):
        return self.min_pos + self.pos_span / self.r_span * (
            self.r_pullup * read_value / (1.0 - read_value) - self.r_min_pos
        )

    def update_calibration(self, min_position_reading, max_position_reading):
        self.r_min_pos = (
            self.r_pullup * min_position_reading / (1.0 - min_position_reading)
        )
        self.r_span = self.r_pullup * (
            max_position_reading / (1.0 - max_position_reading)
            - min_position_reading / (1.0 - min_position_reading)
        )
        self.r_max_pos = self.r_min_pos + self.r_span
        return "min_position_resistance: {:.6f}\nmax_position_resistance: {:.6f}".format(
            self.r_min_pos, self.r_max_pos
        )


# Secondary extruders


class SecondaryExtruder(ABC):
    @abstractmethod
    def __init__(self, config, belay):
        pass

    def get_enable_events(self):
        return []

    def get_disable_events(self):
        return []

    def can_enable(self):
        return True

    def can_disable(self):
        return True

    @abstractmethod
    def set_multiplier(self, multiplier):
        pass


class TradRack(SecondaryExtruder, NamedConfigOptionChoice):
    def __init__(self, config, belay):
        self.printer = config.get_printer()
        self.belay = belay

        # register event handlers
        self.printer.register_event_handler(
            "klippy:connect", self.handle_connect
        )

        # other variables
        self.trad_rack = None

    def handle_connect(self):
        self.trad_rack = self.printer.lookup_object("trad_rack")

    @classmethod
    def get_name(cls):
        return "trad_rack"

    def get_enable_events(self):
        return ["trad_rack:synced_to_extruder"]

    def get_disable_events(self):
        return ["trad_rack:unsyncing_from_extruder"]

    def can_enable(self):
        return self.trad_rack.is_fil_driver_synced()

    def can_disable(self):
        return self.trad_rack.is_fil_driver_synced()

    def set_multiplier(self, multiplier):
        self.trad_rack.set_fil_driver_multiplier(multiplier)


class ExtruderStepper(SecondaryExtruder, NamedConfigOptionChoice):
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
