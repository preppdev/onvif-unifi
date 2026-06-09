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

1. **Install the gateway** (this also installs the agent + Tailscale):
   ```bash
   curl -fsSL https://raw.githubusercontent.com/preppdev/onvif-unifi/main/install.sh \
     | sudo TAILSCALE_AUTHKEY='tskey-auth-REUSABLE-...' bash
   ```
   Use a **reusable** Tailscale auth key (Tailscale admin → Settings → Keys →
   Generate → enable *Reusable*; *Ephemeral* optional; tag e.g. `tag:onvif`) so
   every clone can join with the same key.

2. **Write the agent bootstrap** at `/etc/onvif-gateway/fleet.yaml`
   (from `fleet.yaml.example`): set `server_url` to the fleet server's Tailscale
   name and `enroll_key` to your server's `FLEET_ENROLL_KEY`. Leave `box_id`
   and `api_key` blank (auto-derived/auto-generated).

3. **Enable the agent, leave the gateway disabled** (no cameras until provisioned):
   ```bash
   sudo systemctl enable onvif-agent
   sudo systemctl disable onvif-gateway
   sudo rm -f /etc/onvif-gateway/gateway.yaml          # no leftover site config
   ```

4. **Strip per-machine identity** so clones regenerate cleanly:
   ```bash
   sudo rm -f /var/lib/onvif-gateway/api_key /var/lib/onvif-gateway/applied_version
   sudo truncate -s 0 /etc/machine-id
   sudo tailscale logout                                # each clone re-auths via the reusable key
   sudo cloud-init clean 2>/dev/null || true
   ```

5. **Image the disk** (clone the SSD, or `dd`/Clonezilla to an image file).
   `box_id` comes from the NIC MAC, so each physical box is unique regardless of
   the cloned contents.

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
