# G-Codes

This document is modeled after Klipper's
[G-Codes document](https://www.klipper3d.org/G-Codes.html) but only
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
