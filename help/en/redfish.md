# Redfish (server management controller / BMC)

Redfish is the standardised REST interface (DMTF) of modern management controllers: Dell iDRAC 8/9,
HPE iLO 4/5/6, Lenovo XClarity, Supermicro, ASRock Rack, Fujitsu iRMC and OpenBMC-based controllers
such as those in Wortmann TERRA servers (Intel platform, firmware `egs-…`). Control runs over HTTPS,
independent of the operating system – even a hung system can still be powered off this way.

| Action | Redfish call (`ComputerSystem.Reset`) | Effect |
|---|---|---|
| Shut down | `ResetType: GracefulShutdown` | ACPI signal; the operating system shuts down cleanly |
| Reboot | `ResetType: GracefulRestart` | clean restart through the operating system |
| Power off (hard) | `ResetType: ForceOff` | off immediately, **without** shutdown – last step only |
| Power on | `ResetType: On` | |

The call is `POST https://<bmc>/redfish/v1/Systems/<id>/Actions/ComputerSystem.Reset`.
If the server is already in the requested state (e.g. already off), nothing is sent and the step
counts as successful.

**Recommended rule:** step 1 *Shut down*, step 2 after a suitable delay (e.g. 300 s) *Power off (hard)*
with "Continue with next step on error" – so the server is off even if the operating system does not
respond. A server that is already off is not switched again in step 2.

## Setup on the management controller

1. **Check Redfish:** open `https://<bmc-address>/redfish/v1/` in a browser. A JSON response with
   `RedfishVersion` means Redfish is available. Otherwise use IPMI (see [IPMI](ipmi)).
2. **Create a dedicated account** for Evolved PowerNexus with the smallest sufficient permission:

   | Management controller | Required permission |
   |---|---|
   | Dell iDRAC 8/9 | role *Operator* (includes "Login" and "System Control") |
   | HPE iLO 4/5/6 | "Login" and "Virtual Power and Reset" |
   | OpenBMC / Wortmann TERRA (Intel) | role *Operator* |
   | Supermicro, ASRock Rack | role *Operator* |
   | Lenovo XClarity, Fujitsu iRMC | role with power control ("Remote Power"/"Power Management") |

3. **Check from the UPS server** (without triggering anything):

```bash
curl -k -u <user> https://<bmc-address>/redfish/v1/Systems
```

   The response lists the systems (usually exactly one, e.g. `system`, `1` or `System.Embedded.1`).

## Certificate

The connection is always encrypted and the certificate is always verified – in one of two ways:

- **Field "Certificate fingerprint" empty:** the certificate must come from a CA the UPS server trusts.
  For an internal CA, install the CA certificate on the UPS server:

```bash
cp internal-ca.crt /usr/local/share/ca-certificates/
update-ca-certificates
systemctl restart usv-monitor
```

- **Fingerprint entered (pinned):** exactly this one certificate is accepted. This is the usual way for
  management controllers with a self-signed certificate (factory default for iDRAC, iLO and OpenBMC).
  Host name and expiry date do not matter then; the key is what counts.

For an unconfirmed certificate, "Test connection" shows its SHA-256 fingerprint and offers
"Accept fingerprint". **Before** accepting, compare the fingerprint with the certificate shown by the
controller's web interface (browser: padlock → certificate → SHA-256). Credentials are only sent once
the certificate is confirmed.

If the controller's certificate is renewed, the connection fails with "Certificate does NOT match …".
Verify and accept the new fingerprint.

## Setup in Evolved PowerNexus

| Field | Value |
|---|---|
| Interface | Redfish (server BMC) |
| Address | IP or name of the **management controller** (not of the operating system) |
| Port | 443 |
| User / password | account from step 2 |
| System ID | leave empty – detected automatically; only needed with several systems (blade chassis) |
| Certificate fingerprint | empty (CA) or accept via "Test connection" |

"Test connection" reports the Redfish version, system, model, current power state and the reset types
the controller offers. If an action used by a rule is missing there, that step fails – choose another
action. Whether the account may actually switch power is only shown by a real execution, so assign the
permission from step 2 carefully.

In test mode only the call that would be sent is logged, e.g.
`Redfish POST https://10.0.0.21:443/redfish/v1/Systems/<auto>/Actions/ComputerSystem.Reset {"ResetType": "GracefulShutdown"}`.

## Troubleshooting

| Message | Cause | Action |
|---|---|---|
| Certificate is not confirmed by a known CA | self-signed certificate | verify and accept the fingerprint or install the CA |
| Certificate does NOT match the pinned one | certificate renewed – or a different device at this address | check the fingerprint in the controller's web interface, only then accept |
| Login rejected | wrong user/password, account locked | check credentials; some controllers lock accounts after failed attempts |
| Access denied | account without power control permission | assign the role from step 2 |
| System not unique | several systems behind one controller | enter the system ID from the message |
| Reset type … is not offered by the BMC | controller does not know e.g. `GracefulRestart` | use another action |
| Timeout | wrong address, port 443 blocked, controller not on the network | check reachability from the UPS server (`curl -k https://<bmc>/redfish/v1/`) |

## Security notes

- Management controllers belong in a separate management network.
- Use a dedicated account for Evolved PowerNexus, never the controller's administrator account.
- The password is stored encrypted and never returned by the user interface.
