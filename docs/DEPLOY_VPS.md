# Deploying on a small VPS (AWS EC2 or any Linux server)

One server runs the app behind [Caddy](https://caddyserver.com), which
gets and renews the HTTPS certificate itself. Ingestion runs every three
hours and the database is backed up every night, both from systemd
timers. Everything is in `deploy/`.

For a free option without a server, see [DEPLOY.md](DEPLOY.md)
(Hugging Face Spaces). A VPS is the better choice once real people use
it: the disk is persistent, nothing sleeps, and you control backups.

## 1. The server

- **EC2:** Ubuntu 24.04, `t4g.small` (2 vCPU, 2 GB, ARM) or `t3.small`.
  2 GB is enough for the `e5-small` embedder; the default `hashing`
  embedder runs on 1 GB (`t3.micro`, free tier for 12 months).
  20 GB gp3 disk.
- **Security group:** inbound 22 from your IP only, 80 and 443 from
  anywhere.
- **Address:** attach an Elastic IP, then either point a domain's A record
  at it, or use the free `<ip-with-dashes>.sslip.io` name (for
  203.0.113.7: `203-0-113-7.sslip.io`), which resolves to your IP and
  works with Caddy's automatic HTTPS.
- **Billing:** set an AWS budget alert at a few dollars.

## 2. Install

```bash
ssh ubuntu@YOUR_IP
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu && exit       # log in again for the group to apply
ssh ubuntu@YOUR_IP
sudo mkdir -p /opt/spashta && sudo chown ubuntu /opt/spashta
git clone https://github.com/arnabmanandhar/spashta.git /opt/spashta   # private repo: use a fine-grained read-only token
cd /opt/spashta/deploy
cp ../.env.example .env && chmod 600 .env
nano .env        # set SPASHTA_DOMAIN, SPASHTA_ADMIN_TOKEN, SPASHTA_PUBLIC_URL=https://<domain>, optional model key
mkdir -p data && sudo chown 1000:1000 data   # the app runs as uid 1000
docker compose up -d --build
docker compose logs -f app                   # Ctrl-C to stop watching
```

Open `https://<domain>`. The first request may take a minute while Caddy
gets the certificate.

Add to `.env` for this setup:

```
SPASHTA_DOMAIN=203-0-113-7.sslip.io
SPASHTA_PUBLIC_URL=https://203-0-113-7.sslip.io
SPASHTA_EMBEDDER=hashing
```

## 3. Seed sources, schedule ingestion and backups

```bash
docker compose exec app spashta --db /data/spashta.db sources seed
docker compose exec app spashta --db /data/spashta.db ingest --since 2026-09-15   # optional backfill
sudo cp /opt/spashta/deploy/systemd/spashta-* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now spashta-ingest.timer spashta-backup.timer
systemctl list-timers 'spashta-*'
journalctl -u spashta-ingest -n 50          # what the last ingest did
```

Backups land in `deploy/data/backups/` as gzipped SQLite files, made with
SQLite's online backup API (safe while the app runs) and checked with
`PRAGMA integrity_check`; 14 days are kept. To keep copies off the
server, create an S3 bucket, give the instance an IAM role allowed to
`s3:PutObject` on it, install the AWS CLI, and set
`SPASHTA_BACKUP_S3=s3://your-bucket/spashta` in `.env`.

**Restore:**

```bash
cd /opt/spashta/deploy
docker compose stop app
gunzip -c data/backups/spashta-YYYYMMDD-HHMM.db.gz > data/spashta.db
rm -f data/spashta.db-wal data/spashta.db-shm
docker compose start app
```

Test a restore once, on a copy, before you need it.

## 4. Update

```bash
cd /opt/spashta && git pull && cd deploy && docker compose up -d --build
```

## What the setup does for safety

- Only Caddy is reachable from outside; the app port is internal.
- Caddy caps request bodies at 256 KB, adds HSTS, and keeps no access log.
  The app logs each request with a daily-rotating hash instead of the
  visitor's address (see `spashta/api.py`), and Docker's log files are
  capped at 50 MB.
- Caddy replaces any `X-Forwarded-For` a visitor sends, and the app reads
  the entry Caddy added (`SPASHTA_TRUSTED_HOPS=1`), so the per-visitor
  rate limit can't be dodged with a forged header.
- Secrets live only in `deploy/.env` (mode 600, git-ignored).
- The fetcher refuses non-public addresses and pins each connection to
  the address it checked (no SSRF into the instance metadata service at
  169.254.169.254). On EC2, also require IMDSv2 (the default for new
  instances) as a second layer.

## When to move from SQLite to Postgres

Not until it hurts. SQLite in WAL mode serves many concurrent readers and
one writer, and Spashta's writes are the ingest job and stored reports.
Move when the logs show `database is locked` errors under real traffic,
or when you need to run more than one app server. The storage layer is
one class (`spashta/store.py`), so the change is contained.
