# Zero-touch deployment (golden image)

Goal: build one disk image, clone it to every Mac mini, ship it, and **program
each box remotely** from the fleet dashboard — no on-site configuration.

## How it works

A fresh box boots, gets DHCP for its own address, auto-joins your Tailscale
network, and the `onvif-agent` self-registers with the fleet server using a
shared enroll key. It derives a **stable unique `box_id` from its NIC MAC**, so
every clone is distinct with nothing edited per box. It appears in the dashboard
as **unprovisioned**; you then push its camera config and the agent applies it
and starts the gateway.

```
clone image → ship → plug in → DHCP + Tailscale auto-join → self-register
   → shows "unprovisioned" in dashboard → you push config → cameras live
```

## Building the golden image (once)

On a reference Mac mini with Ubuntu installed:

1. **Install** (gateway + agent + firstboot + Tailscale, joined with a REUSABLE key):
   ```bash
   curl -fsSL https://raw.githubusercontent.com/preppdev/onvif-unifi/main/install.sh \
     | sudo TAILSCALE_AUTHKEY='tskey-auth-REUSABLE-...' bash
   ```
   Use a **reusable** key (Tailscale admin → Settings → Keys → Generate →
   *Reusable*; tag e.g. `tag:onvif`). The installer saves it to
   `/etc/onvif-gateway/tailscale.authkey` so each clone auto-joins on first boot.

2. **Write the agent bootstrap** at `/etc/onvif-gateway/fleet.yaml`
   (from `fleet.yaml.example`): `server_url: http://fleet:8080` (the fleet
   server's Tailscale name) and `enroll_key` = your server's `FLEET_ENROLL_KEY`.
   Leave `box_id`/`api_key` out — they're auto-derived per box.

3. **Prepare the master** (one script — disables the gateway, strips identity,
   logs out Tailscale, resets machine-id, clears logs):
   ```bash
   sudo /opt/onvif-gateway/scripts/prepare-image.sh
   ```

4. **Power off and image the disk** (clone the SSD, or `dd`/Clonezilla to a file).
   Each clone gets a unique `box_id` and Tailscale hostname from its NIC MAC, so
   the identical disk contents don't collide.

## Per box (no technical skill needed on site)

1. Clone the image to the mini's disk.
2. Plug it into the client's network (wired) and power on.
3. It joins Tailscale and appears in the dashboard as **unprovisioned** within a
   minute or two.

## Programming a box remotely

1. Open the box in the dashboard (`http://fleet:8080`).
2. Paste its `gateway.yaml` into the **Configuration** box and Save. Set the
   site's encoder IP/creds and a free static IP range for the cameras. (Tip: SSH
   to the box over Tailscale and run `onvif-gateway provision` first to read the
   encoder's real RTSP URLs.)
3. On its next heartbeat the agent writes the config, enables and starts the
   gateway, and the row flips to **provisioned · in sync**. Cameras go live for
   UniFi to adopt.

To change a site later, edit the config in the dashboard — the box re-applies it
and restarts the gateway automatically.
