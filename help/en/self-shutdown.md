# Self-shutdown of the UPS server (optional)

A rule can shut down the computer Evolved PowerNexus runs on as its last step. This is off by default.

**Keep in mind:** after that there is no monitoring, no e-mail and no rule until it is powered on again. A Raspberry Pi
only starts again once its power supply has been interrupted and returns. If it shuts down and the UPS never switches
off (mains returns before the battery is empty), it stays off until someone restarts it.

## Setup

Two independent releases are required.

**1. Permission in the operating system.** The service deliberately does not run as root. To allow the service account
`usv-monitor` to power off, install the supplied polkit rule once as root:

```bash
cp /opt/usv-monitor/deploy/49-usv-monitor-poweroff.rules /etc/polkit-1/rules.d/
```

Check:

```bash
sudo -u usv-monitor busctl call org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager CanPowerOff
```

The answer must be `s "yes"`.

**2. Release in Evolved PowerNexus.** Allow self-shutdown on the tab "Services".

**3. Target and rule.** On the tab "Targets" create an entry of type **UPS server itself**. "Test connection" checks both
releases and triggers nothing. In a rule it may only be **alone in the last step**.

If the release on the tab "Services" is withdrawn later, the step fails instead of shutting down.

## Recommendation

- Shut down all other targets first and give the last step a suitable delay so that results and e-mails still go out.
- Set a low battery level as a condition, e.g. "Battery charge below 20 %".
- In test mode nothing is shut down, only logged. Run the rule through that way first.

## Undoing

```bash
rm /etc/polkit-1/rules.d/49-usv-monitor-poweroff.rules
```

## Troubleshooting

| Message | Cause | Action |
|---|---|---|
| Self-shutdown is not allowed | release on tab "Services" missing | allow it |
| The service account may not power off (`challenge`, `no`) | polkit rule missing | step 1 |
| "must be alone in the last step" | rule has further steps or other targets in the same step | rearrange the rule |
