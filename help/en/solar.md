# PV inverter (optional)

Evolved PowerNexus shows the values of a PV inverter next to those of the UPSs: power, feed-in, grid draw, yield,
temperature. The chart shows whether the mains voltage rises with PV feed-in and whether a UPS ran on battery while the
plant was producing.

The values arrive via **MQTT** from a system that reads the inverter and announces its sensors the way **Home Assistant**
expects them (MQTT discovery). Evolved PowerNexus does not talk to the inverter itself.

## Setup ("PV" tab, admins only)

| Field | Value |
|---|---|
| Broker | name or IP address of the MQTT broker |
| Port | 1883, usually 8883 with TLS |
| TLS, verify certificate | recommended; without TLS, user name and password cross the network in clear text |
| User, password | as set up on the broker; the password is stored encrypted |
| Discovery prefix | `homeassistant`, unless configured otherwise on the sending system |
| Device | empty = all announced sensors; otherwise part of the device name or the device identifier |
| Sample interval | seconds between stored values (default 60) |
| UPS for comparison | the UPS on the same mains connection |

"Test connection" tries the entered values without saving them and counts the announced sensors. After saving, the
sensors found appear in the list below, together with the field they are assigned to.

## Sensor assignment

The assignment follows unit, device class and name of a sensor: power in W or kW becomes AC power, names with "feed" or
"supplied" become feed-in, "consumption" or "absorbed" become grid draw, "pv_power", "dc" or "string" become DC power
(several strings are added up), energy with "day" or "today" becomes daily yield, with "total" total yield. kW and kWh
are converted.

If an assignment is wrong, enter the **identifier** of the right sensor for that field under "Manual assignment"
("Identifier" column of the sensor list; the input suggests them). If all sensors of a device send to one shared topic,
that is their `unique_id`, otherwise their `state_topic`. A topic that is not announced works too; its message must then
contain the value in the unit of the field.

If a device reports "offline" on its `availability` topic (the inverter at night, for example), its values are neither
shown nor stored, even if the broker still delivers the last ones.

## InverterLink (SMA via Bluetooth)

InverterLink announces its sensors under `homeassistant/sensor/<serial>/<sensor>/config` and sends all values as one
JSON to `inverterlink/<serial>/state` every 30 s. Only broker, port and credentials need to be set, as in the InverterLink
`appsettings.json` (`BrokerHost`, `BrokerPort`, `Username`, `Password`); the prefix stays `homeassistant`. The assignment
follows by itself: AC power, DC1 + DC2 power (added up), daily and total yield, temperature, DC1/DC2 voltage and current,
operating status. The status ID is not used as status. A sensor can be assigned manually by its identifier, e.g.
`inverterlink_sma_<serial>_dc2_voltage`. At night InverterLink sends only part of the values (AC power 0 W, yield,
status); the other fields stay empty then. InverterLink does not provide feed-in or grid draw; that needs a meter with
its own sensor.

## Checking on the broker

```bash
mosquitto_sub -h <broker> -u <user> -P <password> -t 'homeassistant/sensor/#' -v
```

Every sensor appears with a `…/config` message (JSON with `state_topic` and `unit_of_measurement`).

## Data

Values are buffered like those of the UPSs and condensed to hourly values after the retention period. Values from the
former UPS dashboard are imported by `deploy/migrate_legacy_db.py`.
