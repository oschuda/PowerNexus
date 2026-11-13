# E-mail and Microsoft 365

Evolved PowerNexus reports by e-mail

- **events** a recipient list has subscribed to (e.g. power failure), independent of rules,
- **rules** with a recipient list: the plan when the wait time starts, the result per target at the end,
- the **expiry** of the Microsoft 365 login (from 30 days before: weekly, daily in the last week).

Delivery runs independently of the rule: an undeliverable e-mail never holds up a shutdown. Failed e-mails are retried
after 15 s, 1, 5 and 15 minutes and then appear as an event. The queue and retries are kept in memory and are lost when
the service restarts.

**During a power failure:** e-mails only arrive if switch, firewall and internet access are also on the UPS.

| Delivery method | For | Authentication |
|---|---|---|
| Microsoft 365 – Graph API (recommended) | Exchange Online | app registration, permission `Mail.Send` |
| Microsoft 365 – SMTP with OAuth 2.0 | Exchange Online, if Graph is not allowed | app registration, permission `SMTP.SendAsApp` |
| Generic SMTP | other providers, internal relay | optional user and password |

For Microsoft 365 the login uses an app registration because Microsoft is retiring user/password login for SMTP in
Exchange Online. Please check the current state in your own tenant.

## App registration in Microsoft Entra ID

1. [Entra admin center](https://entra.microsoft.com) → *App registrations* → *New registration*.
   Name e.g. `Evolved PowerNexus`, account type "this directory only", no redirect URI.
2. Note from the overview page: **Application (client) ID** and **Directory (tenant) ID**.
3. *Certificates & secrets*, one of two variants:
   - **Client secret:** *New client secret*. Enter the **Value**, not the "Secret ID". The value is shown only once.
     Note the expiry date.
   - **Certificate:** upload the public part (`.cer`). Evolved PowerNexus stores the PFX file with the private key and
     reads the expiry date from it.

```bash
openssl req -x509 -newkey rsa:2048 -nodes -days 730 -subj "/CN=Evolved PowerNexus" -keyout ups.key -out ups.cer
openssl pkcs12 -export -inkey ups.key -in ups.cer -out ups.pfx
```

4. Assign the permission for the delivery method (see below) and **grant admin consent**.

### Graph API

*API permissions* → *Add a permission* → *Microsoft Graph* → *Application permissions* → `Mail.Send` →
*Grant admin consent*.

**Important:** without a restriction the application may send on behalf of **every** mailbox. So restrict it to the
sender mailbox. Microsoft now recommends RBAC for Applications in Exchange Online. In that case remove the `Mail.Send`
permission from the app registration again, because Entra and Exchange permissions add up:

```powershell
Connect-ExchangeOnline
# Object ID: Entra → Enterprise applications → Evolved PowerNexus
New-ServicePrincipal -AppId <client-id> -ObjectId <object-id> -DisplayName "Evolved PowerNexus"
New-ManagementScope -Name "Evolved PowerNexus" -RecipientRestrictionFilter "PrimarySmtpAddress -eq 'powernexus@company.com'"
New-ManagementRoleAssignment -App <client-id> -Role "Application Mail.Send" -CustomResourceScope "Evolved PowerNexus"
Test-ServicePrincipalAuthorization -Identity <client-id> -Resource powernexus@company.com
```

Alternatively, with `Mail.Send` in the app registration, an application access policy on a mail-enabled security group
that contains the sender mailbox:

```powershell
New-ApplicationAccessPolicy -AppId <client-id> -PolicyScopeGroupId ups-senders@company.com -AccessRight RestrictAccess -Description "Evolved PowerNexus"
```

Changes to these permissions can take up to about an hour to take effect.

### SMTP with OAuth 2.0

*API permissions* → *Add a permission* → *APIs my organization uses* → *Office 365 Exchange Online* →
*Application permissions* → `SMTP.SendAsApp` → *Grant admin consent*. Then release the mailbox:

```powershell
Connect-ExchangeOnline
New-ServicePrincipal -AppId <client-id> -ObjectId <object-id>
Add-MailboxPermission -Identity powernexus@company.com -User <object-id> -AccessRights FullAccess
Set-CASMailbox -Identity powernexus@company.com -SmtpClientAuthenticationDisabled $false
```

## Setup in Evolved PowerNexus (tab "E-mail")

| Field | Value |
|---|---|
| Delivery method | Graph API, SMTP with OAuth or generic SMTP |
| Sender mailbox | e.g. `powernexus@company.com`; must match the release above |
| Dashboard link | appears in every e-mail, optional |
| Directory ID, application ID | from step 2 |
| Authentication | client secret with expiry date or PFX file with password |
| Server, port, encryption | generic SMTP only, usually port 587 with STARTTLS |
| Recipient lists | name, addresses, e-mail language and events that are always reported |

A rule uses lists via "E-mail to" in the rule dialog. A list used by a rule cannot be deleted.

Secrets are stored encrypted in `notifications.yaml` and never returned by the user interface. Empty fields leave the
stored value unchanged.

The certificate of an SMTP server is always verified against the UPS server's CA list and the server name. For an
internal CA install the CA certificate (`update-ca-certificates`, see [Redfish](redfish)). Encryption "none" is only
possible without credentials, i.e. for an internal relay.

**"Send test e-mail"** uses the values in the form, even unsaved, and for Microsoft 365 checks login and delivery
separately. The UPS server needs outbound access: `login.microsoftonline.com` and `graph.microsoft.com` on port 443 or
`smtp.office365.com` on port 587.

## Test mode

In test mode e-mails for rules really are sent so that the flow can be checked. They carry `[TEST]` in the subject and
a note that nothing was executed. "Test rule" sends no e-mail and only names the lists.

## Troubleshooting

| Message | Cause | Action |
|---|---|---|
| wrong client secret | "Secret ID" entered instead of "Value" | enter the value, create a new secret if needed |
| client secret has expired | expiry date reached | create a new secret, enter value and date |
| application ID not found | wrong ID or wrong directory | copy both IDs from the overview |
| admin consent is missing | consent not granted | step 4 |
| certificate not accepted | `.cer` not uploaded, expired | check the certificate in the app registration |
| blocked by conditional access | policy includes the application | adjust the policy |
| sending denied | permission or mailbox release missing, change not yet effective | check the release, wait up to an hour |
| sender mailbox not found | wrong address or no Exchange license | check address and mailbox |
| SMTP login rejected | SMTP AUTH off for the mailbox, release missing | `Set-CASMailbox`, `Add-MailboxPermission` |
| SMTP server certificate not confirmed | server name does not match, internal CA missing | enter the name from the certificate, install the CA |
| no connection | firewall, proxy, DNS | check reachability from the UPS server |
