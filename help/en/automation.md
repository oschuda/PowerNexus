# Automation: rules and targets

When an event occurs at a UPS (e.g. power failure), Evolved PowerNexus can shut down target computers
in an orderly way. There are two building blocks:

- **Targets** – *how* a computer is controlled (interface, address, login).
- **Rules** – *when* and *what* happens (event, conditions, wait time, steps).

## Interfaces

| Interface | For | Actions | Help |
|---|---|---|---|
| SSH | Ubuntu Server, Arch Linux | Shut down, reboot | [SSH](ssh) |
| PowerShell Remoting (WinRM) | Windows | Shut down, reboot | [WinRM](winrm) |
| Redfish | Server management controllers (iDRAC, iLO, TERRA/OpenBMC, Supermicro) | Shut down, reboot, power off, power on | [Redfish](redfish) |
| IPMI | older management controllers without Redfish | Shut down, power off, power on | [IPMI](ipmi) |
| OPC-UA | controllers (e.g. Bosch Rexroth XM42), industrial PCs | Shut down (= write variable) | [OPC-UA](opcua) |
| NUT client (FSD) | computers with `upsmon` on the built-in NUT server | set FSD, computers shut themselves down | [NUT server](nut) |
| UPS server itself | this computer (optional) | shut down, last step only | [Self-shutdown](self-shutdown) |
| HTTP API | NAS, Proxmox, Home Assistant, Node-RED etc. | Shut down (= configured call) | [HTTP API](http) |

A running wait time appears on every page with a countdown and "Cancel". After a restart of the service it continues if the event still exists (at least 30 seconds so it can still be cancelled). If execution had already begun, it is not repeated but reported as interrupted.

Rules can notify recipient lists by e-mail, see [E-mail and Microsoft 365](email).

## Structure of a rule

| Part | Meaning |
|---|---|
| Trigger | one or more events, e.g. *Power failure*, *Battery low* |
| Scope | all UPS, one specific UPS or a UPS group |
| Conditions (optional) | e.g. *battery charge below 60 %*, *runtime below 15 min* – all must be met |
| Wait time | seconds until execution; can be cancelled during this time |
| Cancel on | e.g. *Mains power restored* – ends a running wait time |
| Steps | executed one after another; targets within a step in parallel |
| Repeat guard | the same rule triggers at most once per UPS within the period |

### Sequence

1. An event occurs (visible under **Events**).
2. Matching, enabled rules start their wait time.
3. Conditions are **checked again** before execution. If, for example, the battery charge has risen
   again, the run is cancelled.
4. The steps are executed; every result is recorded as an event.

The **AUTOMATION ACTIVE / PAUSED** switch in the header blocks every execution.

## Testing

- **Test connection** (target): checks login and permission without triggering anything.
- **Test rule**: shows which commands *would* be executed and checks the connection to every
  target – without triggering anything.
- **Test mode**: see [Test mode](dry-run).
- **Trigger a rule by hand** (admin only, via API): `POST /api/policies/<rule>/trigger` with `{"ups_id": "<ups>"}` starts the rule as if its event had occurred. Waiting time, cancelling and test mode apply as in a real incident. **Without test mode the steps are really carried out.** Only a test with the UPS itself proves that a real failure is detected.

## Storage

Targets are stored in `hosts.yaml`, rules in `policies.yaml` in the application directory.
Passwords are encrypted; the key is `/etc/usv-monitor/secret.key`. If this key is lost, all
passwords have to be entered again.
