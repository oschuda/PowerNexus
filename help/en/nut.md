# NUT server (optional)

Evolved PowerNexus shares UPSes on the network via the NUT protocol. Computers with a NUT client (`upsmon`) then shut
themselves down when the UPS is on battery and the battery runs low, or when a rule instructs them to (FSD, "Forced
Shutdown"). This is the simplest way to connect many computers: NAS devices and Linux servers usually include a NUT
client, and no per-computer credentials are needed on Evolved PowerNexus.

The server is part of Evolved PowerNexus. The package `nut-server` is not needed; the values come from the running
monitoring. It is **read-only**: clients cannot switch anything, only a rule can set FSD.

## Setup in Evolved PowerNexus (tab "Services")

| Field | Value |
|---|---|
| Shared UPSes | the NUT name used by clients is shown in brackets |
| Allowed networks | e.g. `172.16.11.0/24`. Connections from elsewhere are dropped immediately |
| Port | 3493 (NUT default) |
| User, password | shared by all clients; the password is stored as a hash only |

**Important:** NUT transfers password and values unencrypted. So enter trusted networks only and use a dedicated
password that is not valid anywhere else.

Check that it runs (also from another computer):

```bash
upsc -l <ups-server>
upsc <nut-name>@<ups-server> ups.status
```

## Setup on the client

On Ubuntu Server and Arch Linux install the package `nut-client` or `nut`.

`/etc/nut/nut.conf`:

```
MODE=netclient
```

`/etc/nut/upsmon.conf` (the role must be `secondary`, older versions write `slave`):

```
MONITOR <nut-name>@<ups-server> 1 <user> <password> secondary
SHUTDOWNCMD "/sbin/shutdown -h +0"
```

```bash
systemctl restart nut-monitor      # Arch Linux: nut-monitor.service
```

The client then appears as logged in on the tab "Services".

## FSD via a rule

On the tab "Targets" create an entry of type **NUT clients (FSD)**, for one UPS or all shared ones. In a rule the action
is **Send FSD to NUT clients**. The logged-in clients then shut themselves down.

FSD stays set for at least 10 minutes and ends after that as soon as the UPS is no longer on battery. Otherwise computers
would shut down again right after booting when mains power returns. It can also be cleared by hand on the tab "Services".
FSD does not survive a restart of Evolved PowerNexus.

In test mode FSD is not set, only logged.

## Behaviour on failure

If Evolved PowerNexus has no current values for a UPS, the server answers `DATA-STALE`. `upsmon` then only reports
"communication lost" and does not shut down. The same applies if Evolved PowerNexus or its computer fails completely: the
client no longer protects anything then. An active FSD is reported even without current values.

## Troubleshooting

| Message on the client | Cause | Action |
|---|---|---|
| `Connection failure` / `Connection refused` | server off, port blocked, client not in the allowed networks | check tab "Services", open the firewall |
| `Access denied` | wrong user or password, or role `primary`/`master` | check credentials, use role `secondary` |
| `Unknown UPS` | wrong NUT name, UPS not shared | use the name from tab "Services" |
| `Data stale` | Evolved PowerNexus cannot reach the UPS | check dashboard and events |
