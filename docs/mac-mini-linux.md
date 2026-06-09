# Running on a bare-metal Intel Mac mini

An Intel Mac mini makes an excellent host for this gateway: standard x86_64 UEFI,
built-in Gigabit Ethernet (the 2018 model can be ordered with 10GbE), and quiet/
low-power. Linux runs natively, so `macvlan` and the full unique-IP+MAC design
work exactly as on any other PC — **no code or config changes**.

> Apple **Silicon** (M1/M2) minis are *not* covered here — Linux support there is
> experimental. This is for **Intel** Mac minis (2012 / 2014 / 2018).

## Capacity by model

| Model | Model ID | CPU / RAM | Ethernet | Good for |
|---|---|---|---|---|
| Mac mini 2010/2011 | A1347 | C2D / Sandy Bridge i5–i7, ≤16 GB | 1 GbE | 16–32 ch |
| Mac mini 2012/2014 | A1347 | Ivy/Haswell i5–i7, ≤16 GB | 1 GbE | 16–32 ch |
| Mac mini 2018 | A1993 | 4/6-core i3–i7, ≤64 GB | 1 GbE (10 GbE option) | 16–64 ch |

> **A1347 = no T2 chip** — skip step 2 below. RAM on 2011/2012 is user-replaceable
> (twist off the bottom cover); 2014 is soldered. Either way it's plenty here.

All are massively over-spec'd for copy-mode relay; the only thing that matters at
64 channels is NIC speed (the 2018's optional 10GbE is ideal there).

## 1. Make a bootable Ubuntu Server USB

On any machine, download **Ubuntu Server 24.04 LTS** (`.iso`) and write it to a
USB stick (≥4 GB):

- macOS: use [balenaEtcher](https://etcher.balena.io/) (simplest), or
  `sudo dd if=ubuntu-24.04-live-server-amd64.iso of=/dev/diskN bs=4m` (find the
  disk with `diskutil list`, unmount first).

## 2. (Mac mini 2018 only) Allow booting Linux

The 2018 mini has a **T2 security chip** that blocks external/non-Apple boot by
default. The 2012/2014 models have no T2 — skip this step.

1. Power on holding **⌘ + R** to enter macOS Recovery.
2. Utilities → **Startup Security Utility**.
3. Set **Secure Boot → No Security**, and **Allowed Boot Media → Allow booting
   from external or removable media**.
4. Reboot.

## 3. Boot the installer

1. Insert the USB. Power on holding **⌥ (Option/Alt)** to reach the boot picker.
2. Choose the **EFI Boot** USB entry.
3. Install Ubuntu Server normally. You can erase the whole disk — you don't need
   macOS on this box. Enable OpenSSH during install for headless management.

Notes:
- Use the **wired Ethernet** during/after install. Apple's Broadcom **Wi-Fi**
  needs proprietary firmware and, more importantly, Wi-Fi can't carry the
  per-camera MACs — this gateway requires wired Ethernet regardless.
- The 2018's T2-managed SSD and built-in Ethernet are supported by the 24.04
  kernel out of the box.

## 4. Install the gateway

Once booted into Ubuntu and SSH'd in:

```bash
curl -fsSL https://raw.githubusercontent.com/preppdev/onvif-unifi/main/install.sh | sudo bash
```

Find your wired interface name for the config's `parent_interface`:

```bash
ip -br link        # e.g. enp1s0, eno1, or similar (NOT lo / wlan)
```

Then edit `/etc/onvif-gateway/gateway.yaml` (set `parent_interface`, encoder
IP/creds, your static IPs), provision, and start:

```bash
/opt/onvif-gateway/.venv/bin/onvif-gateway -c /etc/onvif-gateway/gateway.yaml provision
/opt/onvif-gateway/.venv/bin/onvif-gateway -c /etc/onvif-gateway/gateway.yaml validate
sudo systemctl enable --now onvif-gateway
journalctl -u onvif-gateway -f
```

## Booting unattended

Set Ubuntu as the default boot so a power cycle comes straight back up:

- 2012/2014: nothing to do — with macOS erased, the Mac boots Ubuntu directly.
- 2018: after erasing macOS the Mac boots Ubuntu's EFI automatically; if it ever
  shows the boot picker, the GRUB/EFI entry is remembered as default.

That's it — from here it's identical to any other Linux host.
