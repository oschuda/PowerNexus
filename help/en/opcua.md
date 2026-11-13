# OPC-UA (controller)

For controllers (e.g. Bosch Rexroth XM42) and industrial PCs. "Shut down" here means: Evolved PowerNexus **writes a value
to a variable**. What happens next is decided by the program on the controller, so a variable that triggers the clean
shutdown must exist there.

In test mode the value that would be written is only logged. "Test connection" and "Test rule" read the variable and
check that it is writable and that the data type matches, but never write.

## Fields

| Field | Meaning |
|---|---|
| Address, port | controller, usually port 4840 |
| Security | *Encrypted (SignAndEncrypt)* (default) or *Unencrypted* |
| Authentication | *Anonymous* or *User and password* (fields at the top of the dialog) |
| Variable (NodeId) | e.g. `ns=2;s=Power.ShutdownRequest` or `ns=4;i=1001` |
| Data type | Boolean (default), Int16, Int32, UInt16, UInt32, Float, String; must match the variable |
| Value to write | default `true` |
| Read value back | checks the value after writing (default: on) |

**Read value back:** if the program resets the variable itself immediately (pulse), the read-back reports an error even
though everything worked. Switch the option off in that case. If the controller shuts down immediately and the read-back
is no longer possible, the step counts as successful and the log notes it.

With *Unencrypted* the data travels the network in clear text. This only makes sense in a separated plant network.

## Setting up an encrypted connection

Both sides must trust each other. Evolved PowerNexus prefers `Aes256_Sha256_RsaPss`, otherwise `Basic256Sha256`, depending
on what the controller offers.

**Step 1: the controller trusts us.** Select *Encrypted* in the target dialog. Evolved PowerNexus creates its client
certificate the first time (RSA 2048, SHA-256, 5 years). It meets the requirements of the XM42 (among others `keyCertSign`,
`AuthorityKeyIdentifier`, ApplicationUri as the first SAN entry). Download the certificate (DER) and import it on the
controller; for the XM42 in IndraWorks under OPC-UA server, certificate management. The **SHA-1 thumbprint** is shown
because the engineering tools display this value.

**Step 2: we trust the controller.** Click "Test connection". The controller's certificate is shown with subject, SHA-1
thumbprint, validity, signature and key length. Compare the thumbprint with the one on the controller, only then click
"Trust this certificate" and test again.

Without a trusted server certificate no session is opened and nothing is written; there is no automatic acceptance. If
the controller's certificate is renewed, it must be confirmed again. Certificates of generic controllers often do not
contain their address (e.g. `CN=IndraControl`). The host name is therefore not checked; what counts is the exact trusted
certificate.

**Host name changed?** The ApplicationUri `urn:<hostname>:USVMonitor:OPCUA` is part of the certificate. If the host name
changes, Evolved PowerNexus creates a new certificate and points this out in the dialog. Repeat step 1 then.

Storage on the UPS server: `/etc/usv-monitor/pki/` with `own/` (own certificate and key), `trusted/` (trusted server
certificates) and `rejected/` (recently seen, not trusted).

**Gentle handling:** Evolved PowerNexus connects only for an action or test and disconnects immediately afterwards. There is
no permanent connection and only one session per controller at a time.

## Troubleshooting

The message names the side where the cause lies.

| Message | Cause | Action |
|---|---|---|
| The controller rejects our client certificate (`BadSecurityChecksFailed`) | certificate not trusted on the controller | step 1 |
| We do not trust the server certificate yet (`BadCertificateUntrusted`) | the controller already accepted us, step 2 is missing | step 2 |
| Certificate fails the policy (`BadCertificatePolicyCheckFailed`) | SHA-1 signature or key below 2048 bits | renew the certificate on the side with the defect |
| Login rejected | wrong user/password, login type not allowed | check the controller's user and allowed login types |
| Variable not found | wrong NodeId, different namespace index | check the NodeId with an OPC-UA client (e.g. UaExpert) |
| Variable not writable | access rights of the variable or account | grant the rights on the controller |
| Data type does not match | type in the dialog differs from the variable | use the "Variable data type" shown |
| No connection, but ping works | controller's connection table exhausted | restart the controller's OPC-UA service |
| Offers no unencrypted access / no suitable encryption | controller's security settings | the offered variants are in the message; set accordingly |
