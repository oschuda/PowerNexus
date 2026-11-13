# Grid quality

The "Grid quality" tab assesses the power supply at the input of the UPSes. If you select a location, the assessment
applies to its UPSes. It is based on the values the UPS reports. It does not replace a power analysis with a measuring
instrument: a UPS usually does not measure harmonics or short voltage spikes.

| Display | Meaning | Classification |
|---|---|---|
| Score | overall value 0–100 from the available individual assessments | good from 80, critical below 40 |
| Frequency | deviation from the nominal 50 Hz | normal ±0.1 Hz, critical above ±0.5 Hz |
| Input voltage | voltage at the UPS input | normal 207–253 V, critical below 190 V |
| Unbalance | deviation between phases, three-phase UPSes only | |
| Flutter current | strongly fluctuating current | only if the UPS reports the current |

If a UPS does not report a value, it is not part of the score and the tile stays empty.

**History:** the chart shows voltage and, if reported, frequency per UPS. This reveals patterns, such as dips at the same
time of day or several UPSes of a location deviating at the same time.

## What to do about anomalies?

| Observation | Possible cause | Action |
|---|---|---|
| Voltage permanently too low or too high | grid, overloaded supply line | electrician, energy supplier |
| Dips at fixed times | large loads switching on | distribute loads, check timing |
| Unstable frequency | very rare on the interconnected grid; more likely generator or island operation | check the supply |
| Only one UPS affected | its supply line or its measurement | check the UPS and its connection |
