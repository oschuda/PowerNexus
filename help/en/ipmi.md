# IPMI (server management controller / BMC)

IPMI controls the server through its management controller (BMC) – independent of the operating system.

| Action | ipmitool command | Effect |
|---|---|---|
| Shut down | `chassis power soft` | ACPI signal like a short press of the power button; the operating system shuts down cleanly |
| Power off (hard) | `chassis power off` | off immediately, **without** shutdown – use only as the last step |
| Power on | `chassis power on` | |

For servers with iDRAC, iLO or current BMC firmware (e.g. Wortmann TERRA, firmware `egs-…`),
**Redfish** is the more modern interface (see [Redfish](redfish)). IPMI is the fallback for BMCs
without Redfish.

## Setup on the management controller

1. Enable **IPMI over LAN** (iDRAC: *iDRAC Settings → Network → IPMI Settings*;
   iLO: *Security → Access Settings → IPMI/DCMI over LAN*; TERRA/Intel BMC: web interface
   *Configuration → IPMI*).
2. Create a dedicated account with privilege **Operator** (sufficient for power control).
3. Check from the UPS server:

```bash
ipmitool -I lanplus -H <bmc-address> -U <user> -P <password> -L OPERATOR chassis power status
```

## Setup in Evolved PowerNexus

| Field | Value |
|---|---|
| Interface | IPMI |
| Address | IP of the **management controller** (not of the operating system) |
| Port | 623 |
| User / password | account from step 2 |

The password is passed to `ipmitool` via an environment variable and does not appear in the
process list.

## Troubleshooting

| Message | Cause | Action |
|---|---|---|
| ipmitool is not installed | package missing | `sudo apt install ipmitool` on the UPS server |
| `Unable to establish IPMI v2 / RMCP+ session` | IPMI over LAN off, wrong password | steps 1 and 2 |
| `Insufficient privilege level` | account without operator privilege | raise the privilege |
| Shut down has no effect | operating system ignores ACPI | check *power button → shut down* setting on the server |
