# Belay extruder-syncing sensor support
#
# Copyright (C) 2023-2025 Ryan Ghosh <rghosh776@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
DIRECTION_UPDATE_INTERVAL = 0.1


class Belay:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()

        # initial type-specific setup
        type_options = ["trad_rack", "extruder_stepper"]
        self.type = config.getchoice(
            "extruder_type", {t: t for t in type_options}
        )
        if self.type == "trad_rack":
            enable_events = ["trad_rack:synced_to_extruder"]
            disable_events = ["trad_rack:unsyncing_from_extruder"]
            self.enable_initial = False
        elif self.type == "extruder_stepper":
            self.extruder_stepper_name = config.get("extruder_stepper_name")
            enable_events = []
            disable_events = []
            self.enable_initial = True

        # register event handlers
        self.printer.register_event_handler(
            "klippy:connect", self.handle_connect
        )
        self.printer.register_event_handler("klippy:ready", self.handle_ready)
        for event in enable_events:
            self.printer.register_event_handler(event, self.handle_enable)
        for event in disable_events:
            self.printer.register_event_handler(event, self.handle_disable)

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
        self.debug_level = config.getint(
            "debug_level", default=0.0, minval=0.0, maxval=2.0
        )

        # other variables
        self.name = config.get_name().split()[1]
        self.enabled = False
        self.user_disable = False
        self.last_state = False
        self.last_direction = True
        self.set_multiplier = None
        self.enable_conditions = [
            lambda: not self.enabled,
            lambda: not self.user_disable,
        ]
        self.disable_conditions = [lambda: self.enabled]
        self.gcode = self.printer.lookup_object("gcode")
        self.toolhead = None
        self.update_direction_timer = self.reactor.register_timer(
            self.update_direction
        )
        self.flush_id = True
        self.last_flushed_e_pos = 0.0

        # register commands
        self.gcode.register_mux_command(
            "QUERY_BELAY",
            "BELAY",
            self.name,
            self.cmd_QUERY_BELAY,
            desc=self.cmd_QUERY_BELAY_help,
        )
        self.gcode.register_mux_command(
            "BELAY_SET_MULTIPLIER",
            "BELAY",
            self.name,
            self.cmd_BELAY_SET_MULTIPLIER,
            desc=self.cmd_BELAY_SET_MULTIPLIER_help,
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

        # register extruder_stepper-only commands
        if self.type == "extruder_stepper":
            self.gcode.register_mux_command(
                "BELAY_SET_STEPPER",
                "BELAY",
                self.name,
                self.cmd_BELAY_SET_STEPPER,
                desc=self.cmd_BELAY_SET_STEPPER_help,
            )

    def handle_connect(self):
        self.toolhead = self.printer.lookup_object("toolhead")

        # finish type-specific setup
        if self.type == "trad_rack":
            trad_rack = self.printer.lookup_object("trad_rack")
            self.set_multiplier = trad_rack.set_fil_driver_multiplier
            self.enable_conditions.append(trad_rack.is_fil_driver_synced)
            self.disable_conditions.append(trad_rack.is_fil_driver_synced)
        elif self.type == "extruder_stepper":
            self._set_extruder_stepper(self.extruder_stepper_name)

    def _set_extruder_stepper(self, extruder_stepper_name):
        printer_extruder_stepper = self.printer.lookup_object(
            "extruder_stepper {}".format(extruder_stepper_name)
        )
        stepper = printer_extruder_stepper.extruder_stepper.stepper
        base_rotation_dist = stepper.get_rotation_distance()[0]
        self.set_multiplier = lambda m: stepper.set_rotation_distance(
            base_rotation_dist / m
        )

    def handle_ready(self):
        if self.enable_initial:
            self.handle_enable()

    def handle_enable(self):
        for condition in self.enable_conditions:
            if not condition():
                return
        self.enabled = True
        self.reactor.update_timer(self.update_direction_timer, self.reactor.NOW)
        self.update_multiplier()

    def handle_disable(self):
        for condition in self.disable_conditions:
            if not condition():
                return
        self.reset_multiplier()
        self.reactor.update_timer(
            self.update_direction_timer, self.reactor.NEVER
        )
        self.enabled = False

    def sensor_callback(self, eventtime, state):
        self.last_state = state
        if self.enabled:
            self.update_multiplier()

    def update_multiplier(self, print_msg=True):
        if self.last_state == self.last_direction:
            # compressed/forward or expanded/backward
            multiplier = self.multiplier_high
        else:
            # compressed/backward or expanded/forward
            multiplier = self.multiplier_low
        self.set_multiplier(multiplier)
        if (print_msg and self.debug_level >= 1) or self.debug_level >= 2:
            self.gcode.respond_info(
                "Set secondary extruder multiplier: %f" % multiplier
            )

    def reset_multiplier(self):
        self.set_multiplier(1.0)
        if self.debug_level >= 1:
            self.gcode.respond_info("Reset secondary extruder multiplier")

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
        # return if disabled or if this lookahead flush was already handled
        if not self.enabled or self.flush_id != curr_flush_id:
            return

        # get ending extruder position of moves that will be flushed
        last_move = self._get_lookahead().get_last()
        if last_move is not None:
            e_pos = last_move.end_pos[3]
        else:
            e_pos = self.last_flushed_e_pos

        # note net direction of moves and update the extruder multiplier if the
        # direction changed
        prev_direction = self.last_direction
        self.last_direction = e_pos >= self.last_flushed_e_pos
        if self.last_direction != prev_direction:
            if self.debug_level >= 2:
                self.gcode.respond_info(
                    "New Belay sensor direction: %s" % self.last_direction
                )
            self.update_multiplier(False)

        self.flush_id = not self.flush_id
        self.last_flushed_e_pos = e_pos

    cmd_QUERY_BELAY_help = "Report Belay sensor state"

    def cmd_QUERY_BELAY(self, gcmd):
        if self.last_state:
            state_info = "compressed"
        else:
            state_info = "expanded"
        self.gcode.respond_info("belay {}: {}".format(self.name, state_info))

    cmd_ENABLE_BELAY_help = "Enable Belay extrusion multiplier adjustment"

    def cmd_ENABLE_BELAY(self, gcmd):
        self.user_disable = False
        self.handle_enable()
        if not self.enabled:
            raise self.printer.command_error(
                "Conditions not met to enable belay {}".format(self.name)
            )

    cmd_DISABLE_BELAY_help = "Disable Belay extrusion multiplier adjustment"

    def cmd_DISABLE_BELAY(self, gcmd):
        if gcmd.get_int("OVERRIDE", 0):
            self.user_disable = True
        self.handle_disable()
        if self.enabled:
            raise self.printer.command_error(
                "Conditions not met to disable belay {}".format(self.name)
            )

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

    cmd_BELAY_CLEAR_OVERRIDE_help = (
        "Clears any user override that would prevent the Belay from being"
        " automatically enabled"
    )

    def cmd_BELAY_CLEAR_OVERRIDE(self, gcmd):
        self.user_disable = False

    cmd_BELAY_SET_STEPPER_help = (
        "Select the extruder_stepper object to be controlled by the Belay"
    )

    def cmd_BELAY_SET_STEPPER(self, gcmd):
        self.handle_disable()
        self._set_extruder_stepper(gcmd.get("STEPPER"))
        self.handle_enable()

    def get_status(self, eventtime):
        return {"last_state": self.last_state, "enabled": self.enabled}


def load_config_prefix(config):
    return Belay(config)
