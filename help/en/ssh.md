# SSH (Ubuntu Server, Arch Linux)

Evolved PowerNexus logs in via SSH and runs:

| Action | Command on the target |
|---|---|
| Shut down | `sudo -n /usr/bin/systemctl --no-block poweroff` |
| Reboot | `sudo -n /usr/bin/systemctl --no-block reboot` |

## Setup on the target

### 1. Create a user

Ubuntu Server:

```bash
sudo adduser --disabled-password --gecos "Evolved PowerNexus" usvmon
```

Arch Linux (`sudo` is not installed by default):

```bash
pacman -S sudo
useradd -m -c "Evolved PowerNexus" usvmon
```

### 2. Login with key (recommended) or password

Key: add Evolved PowerNexus's public key to `/home/usvmon/.ssh/authorized_keys` (permissions: directory
700, file 600) and enter the path of the private key on the UPS server in the target dialog.

Password: `sudo passwd usvmon` and enter the password in the target dialog.

### 3. Permission to shut down – for these two commands only

```bash
sudo visudo -f /etc/sudoers.d/usv-monitor
```

Content (exactly like this, `sudo` compares the arguments exactly):

```
usvmon ALL=(root) NOPASSWD: /usr/bin/systemctl --no-block poweroff, /usr/bin/systemctl --no-block reboot
```

Check:

```bash
sudo -l -U usvmon
```

### 4. Determine the host fingerprint

On the target:

```bash
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

**Test connection** shows the fingerprint reported by the computer. If it matches this value,
**accept** it. Actions are only executed on targets with a confirmed fingerprint.

## Setup in Evolved PowerNexus

| Field | Value |
|---|---|
| Interface | SSH |
| Address / port | IP or name, port 22 |
| User | `usvmon` |
| Password or key path | see step 2 |
| Use sudo | on (off when logging in as root) |

## Troubleshooting

| Message | Cause | Action |
|---|---|---|
| sudo permission for shutdown is missing | sudoers entry missing or different | step 3, copy the line exactly |
| Host fingerprint does NOT match | computer reinstalled – or another computer at this address | verify the fingerprint on the computer before accepting it again |
| Timeout | computer off, firewall, wrong address | test `ssh usvmon@<address>` from the UPS server |
| Permission denied | wrong password or key | test the login manually |
