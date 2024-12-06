# Belay Configuration reference

This document is modeled after Klipper's
[Config Reference document](https://www.klipper3d.org/Config_Reference.html)
but only contains items pertaining to Belay.

### [belay]

Belay extruder sync sensors (one may define any number of sections
with a "belay" prefix).

```
[belay my_belay]
extruder_type:
#   The type of secondary extruder. Available choices are 'trad_rack'
#   or 'extruder_stepper'. This parameter must be specified.
extruder_stepper_name:
#   The name of the extruder_stepper being used as the secondary
#   extruder. Must be specified if extruder_type is set to
#   'extruder_stepper', but should not be specified otherwise. For
#   example, if the config section for the secondary extruder is
#   [extruder_stepper my_extruder_stepper], this parameter's value
#   would be 'my_extruder_stepper'.
#multiplier_high: 1.05
#   High multiplier to set for the secondary extruder when extruding
#   forward and Belay is compressed or when extruding backward and
#   Belay is expanded. The default is 1.05.
#multiplier_low: 0.95
#   Low multiplier to set for the secondary extruder when extruding
#   forward and Belay is expanded or when extruding backward and
#   Belay is compressed. The default is 0.95.
#debug_level: 0
#   Controls messages sent to the console. If set to 0, no messages
#   will be sent. If set to 1, multiplier resets will be reported, and
#   the multiplier will be reported whenever it is set in response to
#   a switch state change. If set to 2, the behavior is the same as 1
#   but with an additional message whenever the multiplier is set in
#   response to detecting an extrusion direction change. The default
#   is 0.
```
