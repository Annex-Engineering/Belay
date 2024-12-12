# G-Codes

This document is modeled after Kalico's
[G-Codes document](https://docs.kalico.gg/G-Codes.html) but only
contains items pertaining to Belay.

### QUERY_BELAY
`QUERY_BELAY BELAY=<config_name>`: Queries the state of the belay
specified by `BELAY`.

### BELAY_SET_MULTIPLIER
`BELAY_SET_MULTIPLIER BELAY=<config_name> [HIGH=<multiplier_high>]
[LOW=<multiplier_low>]`: Sets the values of multiplier_high and/or
multiplier_low for the belay specified by `BELAY`, overriding their
values from the corresponding
[belay config section](Config_Reference.md#belay). Values set by this
command will not persist across restarts.

### ENABLE_BELAY
`ENABLE_BELAY BELAY=<config_name>`: Enables extrusion multiplier adjustments by
the belay specified by `BELAY` for its corresponding secondary extruder.

### DISABLE_BELAY
`DISABLE_BELAY BELAY=<config_name> [OVERRIDE=<0|1>]`: Resets the extrusion
multiplier for the secondary extruder corresponding to the belay specified by
`BELAY` and disables extrusion multiplier adjustments by the belay. If
`OVERRIDE` is 1, then the specified belay cannot be re-enabled automatically
until the ENABLE_BELAY command is used with the same belay specified for the
`BELAY` parameter. The `OVERRIDE` parameter currently is only relevant if
extruder_type is set to 'trad_rack' in the corresponding
[belay config section](Config_Reference.md#belay). If not specified,
`OVERRIDE` defaults to 0.

### BELAY_SET_STEPPER
`BELAY_SET_STEPPER BELAY=<config_name> STEPPER=<extruder_stepper_name>`: Selects
the extruder_stepper whose multiplier will be controlled by the belay specified
by `BELAY`. The multiplier for the previous stepper will be reset back
to 1 before switching to the new stepper. Stepper selections made by this
command will not persist across restarts. This command is only available if
extruder_type is set to 'extruder_stepper' in the corresponding
[belay config section](Config_Reference.md#belay).
