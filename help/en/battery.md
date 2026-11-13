# Battery

The "Battery" tab assesses each UPS in three separate points. This keeps it visible whether a good rating is backed by
evidence or whether data is simply missing.

| Point | Basis |
|---|---|
| Technical condition | what the UPS itself reports: battery service life, "replace battery", self-test |
| Remaining service life | replacement date recommended by the UPS, otherwise installation date plus a typical 5 years |
| Data basis | which information is available and which is missing for a reliable statement |

## Installation date

On the "Battery" tab select the UPS and enter the date, or click **Installed today** after a replacement (roles admin and
operator). An empty date removes the entry; then what the UPS reports applies.

If the replacement date from the UPS is older than the entered installation date, it still stems from the old battery and
is no longer used. After a replacement, also change the date on the UPS if it offers this. A "replace battery" message
from the UPS itself remains until the UPS clears it, often after a self-test.

## Checking the battery in operation

The remaining runtime in minutes is the UPS's own estimate, not a measurement by Evolved PowerNexus. With an ageing battery it can be far too high. Reliable checks are:

- **Self-test:** trigger it regularly, for example quarterly. For a USB UPS with NUT: `upscmd <ups> test.battery.start.quick` (asks for a NUT user with the `instcmds` permission), result with `upsc <ups> ups.test.result`. The UPS briefly switches to battery.
- **Discharge test under real load:** time the actual runtime and compare it with the reported one, for example once a year and only with protected target computers. This is the only direct proof. A runtime reported without load says little.
- **History:** compare runtimes only at a similar load. A battery voltage that collapses quickly on battery is a sign of ageing.

## Battery forecast

The trend from measurements only appears with a trained model and a sufficiently long measurement history. Until then the
page deliberately shows no forecast, but the assessment from the three points.
