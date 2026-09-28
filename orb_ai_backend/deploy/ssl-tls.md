# SSL / TLS Configuration Guide

## Overview

ORB AI is designed to run behind a reverse proxy that terminates TLS.
This document lists the recommended options and step-by-step commands.

## Recommended: Let's Encrypt (free, automated)

### 1. Install certbot

```bash
sudo apt update
sudo apt install -y certbot python3-certbot-nginx
```

### 2. Obtain a certificate

Prerequisites:
- DNS `A` record for `orb.example.com` points at your server.
- Port 80 is reachable for the ACME HTTP-01 challenge.

```bash
sudo certbot --nginx -d orb.example.com \
    --agree-tos --redirect --hsts \
    -m ops@example.com
```

Certbot auto-detects the site block in
`orb_ai_backend/deploy/nginx.conf.example`, installs the cert, and
enables HTTPS + HSTS.

### 3. Renewal

Certbot ships a systemd timer / cron job by default. Verify:

```bash
sudo systemctl list-timers | grep certbot
sudo certbot renew --dry-run
```

## Alternative: bring your own certificate

Copy your PEM-encoded key + fullchain to:

```
/etc/letsencrypt/live/orb.example.com/privkey.pem
/etc/letsencrypt/live/orb.example.com/fullchain.pem
```

then reload nginx:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

## Verifying the setup

```bash
# Cert chain OK?
openssl s_client -connect orb.example.com:443 -servername orb.example.com -showcerts < /dev/null

# HSTS + security headers?
curl -sI https://orb.example.com/health | head -30

# SSL Labs
open https://www.ssllabs.com/ssltest/analyze.html?d=orb.example.com
```

Target: **A+** on SSL Labs.

## Hardening checklist

| Setting                          | Value                                              |
|----------------------------------|----------------------------------------------------|
| TLS versions                     | TLS 1.2 and TLS 1.3 only                           |
| Ciphers                          | `HIGH:!aNULL:!MD5:!3DES`                           |
| HSTS                             | `max-age=31536000; includeSubDomains; preload`     |
| Session tickets                  | Disabled or rotated hourly                         |
| OCSP stapling                    | Enabled (`ssl_stapling on;`)                       |
| Diffie-Hellman params            | 2048+ bits (Nginx uses ffdhe2048 by default in TLS 1.3) |

## Behind Cloudflare / CDN

If a CDN terminates TLS in front of Nginx:

1. Use **Full (strict)** origin mode so the CDN validates the origin cert.
2. Set `real_ip_from` + `real_ip_header CF-Connecting-IP` in Nginx so
   client IPs propagate to the API and the rate-limiter.
3. Keep server-level HSTS enabled — the CDN honours the header.
