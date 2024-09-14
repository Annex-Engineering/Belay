# What to print

All parts in the STLs folder need to be printed, with the exception
that some parts have multiple variants (for different bowden tube
collet types) and you only need 1 variant of each of these parts:

## Collet types

The following bowden tube collet types are supported:

- "5mm collet" (E3D V6, Bondtech LGX)
- "6mm collet" (UM2)
- ECAS04

The filename of each variant of the housing_core STL will contain either
`(5mm_collet)`, `(6mm_collet)`, or `(ecas04)` to indicate the collet type it is
meant for.

"5mm" and "6mm" refer to the approximate diameter of the part of the collet that
the collet clip goes around, highlighted in blue below:

![5mm and 6mm collets](/Images/5mm_and_6mm_collets.png?raw=true)

If using the 5mm or 6mm collet options, a collet clip should be used to prevent
the bowden tube from moving. A collet clip is unnecessary if using the ECAS04
option.
