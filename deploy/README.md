# Hosted enrollment (digitaltidewater.com)

Provision an appliance in two steps — install Ubuntu, then:

```bash
curl -fsSL https://provision:PASSWORD@digitaltidewater.com/onvif | sudo bash
```

A clean URL, gated by HTTP basic auth. The file behind it bakes in the Tailscale
key + fleet server URL + enroll key and chains to the installer.

## 1. The file

Take `enroll.sh.template`, fill in the reusable Tailscale auth key and the fleet
server's `FLEET_ENROLL_KEY`, and host its contents at the path `/onvif`.

## 2. Password-gate the path

**Apache (shared hosting / cPanel — most common):**

```bash
# once, somewhere outside the web root:
htpasswd -c /home/USER/.htpasswd-onvif provision      # prompts for the password
```

`.htaccess` in the directory serving `/onvif`:

```apache
<Files "onvif">
  AuthType Basic
  AuthName "ONVIF Provisioning"
  AuthUserFile /home/USER/.htpasswd-onvif
  Require valid-user
</Files>
```

**nginx:**

```nginx
location = /onvif {
    auth_basic "ONVIF Provisioning";
    auth_basic_user_file /etc/nginx/.htpasswd-onvif;   # htpasswd-created
    default_type text/plain;
    alias /var/www/onvif/enroll.sh;
}
```

## 3. Provision

```bash
curl -fsSL https://provision:PASSWORD@digitaltidewater.com/onvif | sudo bash
```

(`provision` is the basic-auth username; use whatever you set with `htpasswd`.)

## Security notes

- **Always HTTPS** — the file and the basic-auth credentials cross the wire.
- The file still contains a reusable Tailscale key + the enroll key; the password
  is what protects them. Pick a strong one.
- If a credential leaks: change the basic-auth password, and rotate the Tailscale
  key (admin console) + `FLEET_ENROLL_KEY` (fleet `.env`) and re-host.
- Recommended regardless: **tag** the Tailscale key (`tag:onvif`) with an ACL so
  even a leaked key can only enroll locked-down nodes.
