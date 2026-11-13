# HTTP API (generic call)

For devices and applications that can be shut down through a web interface – e.g. Proxmox VE,
TrueNAS, Home Assistant or a Node-RED flow. For the action *Shut down* Evolved PowerNexus sends exactly
one configured HTTP call and evaluates the status code.

The call must trigger **in a single request**. Interfaces that require a login with a session ID
first (e.g. Synology DSM) are not supported this way – use [SSH](ssh) for those.

## Fields

| Field | Meaning |
|---|---|
| Address, port | server; port 443 (https) or 80 (http), 8006 for Proxmox |
| Protocol | `https` (recommended) or `http` (unencrypted – tokens travel the network in clear text) |
| Method | usually `POST` |
| Path | path with query, e.g. `/api2/json/nodes/pve1/status` |
| Body, Content-Type | what is sent, e.g. `{"entity_id": "script.nas_off"}` with `application/json` |
| Additional headers | one `Name: value` per line; **no secrets** – they are stored unencrypted |
| Authentication | *none*; *Basic* (user and password fields); *Bearer token* (token in the password field, sent as `Authorization: Bearer …`); *Token in custom header* (enter the header name, token in the password field) |
| Expected status | status codes that count as success: `200-299` (default) or e.g. `200,204` |
| Test path | optional: a **read-only** path (GET) that "Test connection" uses to check the login. Without a test path the test only checks reachability and the certificate. |
| Certificate fingerprint | as for [Redfish](redfish): empty = verify via CA, otherwise pinned certificate |

Tokens and passwords are stored encrypted and never returned by the user interface. In logs and in
test mode they only appear as `***`.

**Important:** "Test connection" **never** sends the configured call, at most a GET to the test path.
So never enter a path that triggers something as the test path.

## Examples

### Proxmox VE (shut down node)

1. In Proxmox: *Datacenter → Permissions → API Tokens*, create a token for a user; grant the token a
   role with the privilege `Sys.PowerMgmt` on `/nodes/<node>` (e.g. a custom role).
2. In Evolved PowerNexus:

| Field | Value |
|---|---|
| Address / port | Proxmox host / 8006 |
| Method / path | `POST` / `/api2/json/nodes/<node>/status` |
| Body / Content-Type | `command=shutdown` / `application/x-www-form-urlencoded` |
| Authentication | Token in custom header, header name `Authorization` |
| Password/token | `PVEAPIToken=<user>@<realm>!<token-id>=<secret>` |
| Test path | `/api2/json/nodes/<node>/status` |
| Certificate | Proxmox uses a self-signed certificate by default → accept the fingerprint |

Proxmox shuts down the guests according to their settings.

### TrueNAS (REST API v2.0)

| Field | Value |
|---|---|
| Method / path | `POST` / `/api/v2.0/system/shutdown` |
| Body | `{}` (depending on the version also `{"reason": "UPS"}`) |
| Authentication | Bearer token (API key from *Settings → API Keys*) |
| Test path | `/api/v2.0/system/info` |

Newer TrueNAS versions are phasing out the REST API; use [SSH](ssh) then.

### Home Assistant (trigger a script or service)

| Field | Value |
|---|---|
| Port | 8123 |
| Method / path | `POST` / `/api/services/script/turn_on` |
| Body | `{"entity_id": "script.shutdown_servers"}` |
| Authentication | Bearer token (long-lived access token from the user profile) |
| Test path | `/api/` |

### Node-RED

Create a flow with an *http in* node (e.g. `POST /ups/shutdown`) and *http response*; secure it with a
custom header (authentication *Token in custom header*) that the flow checks.

## Troubleshooting

| Message | Cause | Action |
|---|---|---|
| Certificate is not confirmed by a known CA | self-signed certificate | verify and accept the fingerprint or install the CA |
| Certificate does NOT match the pinned one | certificate renewed or a different device | check the fingerprint on the device, then accept |
| Response HTTP 401 / 403 | token wrong, expired or without permission | check token and permissions |
| Response HTTP 404 | wrong path | check the path in the device's API documentation |
| Action failed although it worked | device responds with a different status | adjust "Expected status" |
| Timeout | wrong address/port, firewall | check reachability from the UPS server (`curl -v https://<address>:<port>/`) |
