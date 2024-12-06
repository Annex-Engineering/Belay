# Status Reference

This document is modeled after Kalico's
[Status Reference document](https://docs.kalico.gg/Status_Reference.html)
but only contains items pertaining to Belay.

## belay

The following information is available in
[belay some_name](Config_Reference.md#belay) objects:
- `printer["belay <config_name>"].last_state`: Returns True if the belay's
  sensor is in a triggered state (indicating its slider is compressed).
- `printer["belay <config_name>"].enabled`: Returns True if the belay is
  currently enabled.
