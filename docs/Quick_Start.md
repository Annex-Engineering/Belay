# Quick Start

This document provides an overview of how to get a Belay sensor
running.

**Table of Contents**
- [Prerequisites](#prerequisites)
- [BOM/sourcing](#bomsourcing)
- [Printed parts](#printed-parts)
- [Assembly](#assembly)
- [Klipper installation](#klipper-installation)
  - [Klippy module](#klippy-module)
    - [Klippy module installation](#klippy-module-installation)
    - [Enabling Moonraker updates](#enabling-moonraker-updates)
  - [Config files](#config-files)
- [Testing](#testing)
  - [Check sensor polarity](#check-sensor-polarity)
  - [First print](#first-print)
- [Further reading](#further-reading)

## Prerequisites

Your 3D printer must meet the following requirements to work with
Belay:

- Must use 1.75mm filament.
- Must run [Klipper firmware](https://github.com/Klipper3d/klipper/).
- The toolhead or primary extruder must have a collet/coupling to
  secure a 4mm bowden tube at its inlet. The secondary extruder must
  have one at its outlet as well.

## BOM/sourcing

The following hardware is required:

| Component/Item                        | Qty Required  | Notes                                                               |
| ---                                   | ---           | ---                                                                 |
| Omron D2F-L Microswitch               | 1             | See Trad Rack BOM/sourcing guide for other variants that will work  |
| Collet                                | 2             |                                                                     |
| Collet clip                           | 2             |                                                                     |
| M2 x 12mm Pan Head Self Tapping Screw | 2             | Longer screws will also work but will stick out                     |

See [the STLs README](/STLs/README.md#collet-types) for collet type options.
Some example sources for collets and collet clips:
- 5mm collet - [KB3D](https://kb-3d.com/store/spare-parts/487-bondtech-push-fit-collar-for-bowden-coupling-175mm-7350011413331.html)
- 5mm collet clip - [KB3D](https://kb-3d.com/store/e3d/48-e3d-bowden-collet-clip-175mm-1644688775189.html)
- 6mm collet and collet clip - [AliExpress](https://www.aliexpress.us/item/2255801046836641.html)

See the
[Annex BOM/sourcing guide](https://docs.google.com/spreadsheets/d/1O3eyVuQ6M4F03MJSDs4Z71_XyNjXL5HFTZr1jsaAtRc/edit?usp=sharing)
for recommended sources for all other components.

## Printed parts

See the following files/folders:

- [Print Settings and File Key](/Print_Settings_and_File_Key.txt):
  print settings to use and info on reading the STL filenames.
- [STLs folder](/STLs): contains all STL files.
- [STLs README](/STLs/README.md): information on what parts to print.

## Assembly

This section lists the assembly steps.

Insert the 2 collets into the sensor housing and slider:

![](images/add_exit_collet.png?raw=true)
![](images/add_entry_collet.png?raw=true)

Place the slider into the sensor housing. Make sure the arrow is visible and
facing the same direction as in the image:

![](images/add_slider.png?raw=true)

Place the cover onto the sensor housing:

![](images/add_cover.png?raw=true)

Insert the microswitch from the bottom and secure with 2 M2x12 screws (going
through the cover, through the switch, and into the sensor housing). Make sure
the tip of the lever on the microswitch is pointing to the left:

![](images/add_microswitch.png?raw=true)

Push the slider to the left until it reaches the end of its travel.
Then insert the entry bowden tube and push it in as far as it can go.
The tube should extend past the left end of the slider and into the
sensor housing:

![](images/add_entry_tube.png?raw=true)

Gently pull the tube until the slider reaches the other end of its travel. Then
add a collet clip by inserting it through the slot in the cover:

![](images/add_entry_clip.png?raw=true)

Insert the exit bowden tube, then add a collet clip:

![](images/add_exit_tube_and_clip.png?raw=true)

Solder 2 wires to the microswitch and secure them to the sensor housing with a
zip tie as shown below:

![](images/wiring_example.jpg?raw=true)

## Klipper installation

This section lists the steps to get Belay set up to work with Klipper:

### Klippy module

This section involves adding the Belay Klippy module(s) to Klipper
and enabling updates through Moonraker.

#### Klippy module installation

Run the following commands to download and install the Klippy
module(s):

```
cd ~
curl -LJO https://raw.githubusercontent.com/Annex-Engineering/Belay/main/Klipper_Stuff/klippy_module/install.sh
chmod +x install.sh
./install.sh
```

Then remove the install script with the following command:

```
rm install.sh
```

Finally, restart the klipper service using the following command:

```
sudo systemctl restart klipper
```

> [!TIP]
> If you ever need to run the install script again in the future (for
> example if additional Klippy modules get added), you can do so
> without recreating the `belay_klippy_module` directory using the
> following commands:
> ```
> cd ~
> ./belay_klippy_module/Klipper_Stuff/klippy_module/install.sh <branch_name>
> ```
> If unspecified, `branch_name` defaults to `main`.

#### Enabling Moonraker updates

To enable updates of the Belay Klippy module(s) through Moonraker,
add the following to your `moonraker.conf` file. This file is usually
located in `~/printer_data/config/`:

```
[update_manager belay]
type: git_repo
path: ~/belay_klippy_module
origin: https://github.com/Annex-Engineering/Belay.git
primary_branch: main
managed_services: klipper
```

Then restart the moonraker service using the following command:

```
sudo systemctl restart moonraker
```

### Config files

Sample config files are provided for different secondary extruder
types. Copy the file from the corresponding subfolder of the
[klipper_config folder](/Klipper_Stuff/klipper_config/) and
[include it](https://www.klipper3d.org/Config_Reference.html#include)
in your main printer config file.

Change the `sensor_pin` config option to match the pin you are using.
In addition, make sure to follow all instructions left in the comments
of the config file (which are specific to the type of secondary
extruder you are using) for any further changes that may be needed.

Restart klipper once you have finished making changes to the config
file(s).

## Testing

This section explains how to test that Belay is working properly:

### Check sensor polarity

Push the bowden tubes before and after Belay together by hand so that
Belay is compressed. Run the following command and observe the console
output:

```
QUERY_BELAY BELAY=my_belay
```

Then pull the bowden tubes apart so that Belay is expanded and run
the command again.

The expected result is that the console should have reported that
belay my_belay was "compressed" the first time and "expanded" the
second time. If these are reversed, add or remove `!` in the value for
`sensor_pin` in the config file. If the reported states matched, then
it is likely that either the wrong pin was used or there is a problem
with wiring.

### First print

During a print, Belay's slider should stay close to the middle of its
travel range without hitting either end.

The recommended starting value for the `debug_level` config option is
`1`. With this setting, the extruder multiplier set by Belay will be
reported in the console whenever Belay's switch changes state.

Throughout a print with `debug_level` set to 1, the reported
multiplier should generally flip back and forth between the values of
the config options `multiplier_high` and `multiplier_low` (which
default to `1.05` and `0.95` respectively). However, it is possible
that the same multiplier may appear in the console 2 or more times in
a row if the state of the switch changed during a retraction; this is
normal.

If Belay seems to be working and you do not want to see these messages
in the console anymore, you can set `debug_level` to `0` in the config
to disable them.


## Further reading

See the [Overview document](README.md) to see the full documents
available for Belay, for example to see all available gcode commands
or config options.
