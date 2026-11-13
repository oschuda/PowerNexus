# Test mode

In test mode everything runs as in a real event – trigger, conditions, wait time, cancellation,
steps and events – but **no action is executed** and **no connection** to a target is opened. The
command that *would* be executed is recorded. Events carry the prefix **[TEST]**.

## Kinds

| Kind | Effect | Setting |
|---|---|---|
| Global | applies to all rules | **TEST MODE** switch in the header (admin only) |
| Per rule | this rule only | option *Test mode* in the rule dialog |
| Forced | global, cannot be switched off | environment variable `UPS_DRY_RUN=true` in `/etc/usv-monitor/env` |

While global test mode is active, the header shows a clear banner.

**Test rule** and **Test connection** never trigger anything – regardless of test mode.

## Recommended procedure

1. Create targets and **test the connection**.
2. Create the rule with option *Test mode* and **test the rule**.
3. Wait for or trigger a real sequence in test mode and check it under **Events**.
4. Only then switch off test mode for the rule.
