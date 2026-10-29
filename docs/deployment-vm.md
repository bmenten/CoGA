# Deploying CoGA on a virtual machine

How to run the production stack on one Linux virtual machine, such as a VM in a university
or hospital data centre: Docker Compose runs Postgres, ClickHouse, the backend and the
frontend, and a reverse proxy on the VM terminates TLS. This is the setup for a **test or
performance environment**. The Google Cloud deployment is in
[deployment-gcp.md](deployment-gcp.md); local development in [development.md](development.md).

## Before you start

- **Which data.** A test VM holds synthetic data only. Real patient data needs a DPIA and a
  data-processing agreement covering that VM first, wherever it runs.
- **Not the device.** The VM is outside the validated environment of the in-house IVD
  ([regulatory/README.md](regulatory/README.md)): nothing signed out there is a clinical
  record. It does run the backend's production checks, so a test there exercises the same
  start-up refusals, audit and sign-out paths as a real deployment.

## 1. What to ask for

| Item | What to request |
| --- | --- |
| Operating system | Ubuntu 24.04 LTS or Debian 12 |
| CPU and memory | 8 vCPU and 32 GB at least; 16 vCPU and 64 GB to test at scale. ClickHouse caps each query at 4 GB ([`clickhouse/users.d/memory-guards.xml`](../clickhouse/users.d/memory-guards.xml)), so more memory runs more queries at once, not larger ones |
| Disks | a system disk of 50 GB, and a separate SSD data disk (500 GB to 1 TB) for the Docker volumes and the family packages; snapshots of the data disk, if the provider offers them |
| Host name | a DNS name, such as `coga-test.example.org` |
| Inbound traffic | 443 (and 80 for an ACME certificate) from the institution's network or VPN only; 22 from the administrators' addresses only |
| Outbound traffic | HTTPS to the internet: the first start downloads the reference data (GENCODE, UCSC, HGNC, NCBI, ClinGen, GenCC, HPO), and Docker pulls the images from Docker Hub |
| TLS certificate | from the institution's certificate service when the VM is reachable only from inside, since an ACME HTTP challenge cannot reach it; Let's Encrypt when port 80 is open to the internet |
| Mail relay (optional) | an SMTP relay that accepts mail on port 25 without authentication, for the signup notice to `ADMIN_EMAIL` |

### At Ghent University

A staff member requests the VM through the ICT self-service portal (the virtual-server form,
<https://helpdesk.ugent.be/webhosting/virtueel.php>), with the default Linux profile or a later
PXE install of a supported, non-EOL distribution. Submitting the request accepts ICT's terms
of use. ICT manages only the platform; the VM's administrator carries the rest, and ICT may
switch off a VM that breaks the terms. What each term asks of a CoGA VM:

| ICT term | What it means here |
| --- | --- |
| **Self-care**: the administrator is responsible for the OS, the licences and access control (authentication and authorization) inside the VM; ICT gives only the management console (vSphere Web Client, through Athena) | SSH keys, ufw, nginx and CoGA's own accounts and project access are yours (steps 2, 4 and 6). The dbNSFP licence (step 4) is yours too |
| **VMware tools** are mandatory; without them ICT may switch the VM off | install `open-vm-tools` (step 2) |
| **Keep it up to date**; a VM with an outdated OS or missing security patches may be switched off | `unattended-upgrades` (step 2), a reboot when a kernel update needs one (stop the stack first, [step 9](#9-run-it)), and regular rebuilds so the images pick up their base images' fixes |
| **Back-ups** are the administrator's; ICT's disaster-recovery back-ups restore the whole platform, not files inside a VM | back up to a network share from ICT's storage offering, not to the VM's own disk ([step 9](#9-run-it)) |
| **Fair use**; a VM that uses resources unreasonably may be switched off | request the size you need (step 1), and raise `FAMILY_IMPORT_WORKER_COUNT` and the ClickHouse limits only as far as the VM's size allows ([step 10](#10-tuning-at-scale)) |
| **Yearly confirmation**: an unused VM, or one outside the university's normal work, gets an e-mail; without an answer through ICT self-service within 30 days it is switched off for good | the request names a mailbox someone reads; answer the e-mail |
| **A successor administrator** must be reported through ICT self-service or HelpMe; a VM without an administrator is shut down | name the new administrator before you hand over |
| **Security incidents**: ICT isolates or switches off the VM, and the department can be held co-liable for negligence | synthetic data only, loopback-only ports, and the firewall of step 2 |

Since ICT may switch the VM off, make sure a shutdown stops ClickHouse cleanly (step 2) and
that the back-ups are current.

## 2. Prepare the VM

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y unattended-upgrades open-vm-tools git ufw nginx
```

`open-vm-tools` lets the VMware platform see and shut down the VM properly; on a VMware host
such as Ghent University's it is mandatory.

1. Install Docker Engine and the Compose plugin from Docker's own apt repository
   (<https://docs.docker.com/engine/install/>), not the distribution's `docker.io` package. The
   override file below needs Compose 2.24.4 or later (`docker compose version`).
2. Mount the data disk, for instance on `/srv`, and put Docker's data there, in
   `/etc/docker/daemon.json`:

   ```json
   { "data-root": "/srv/docker" }
   ```

   Then `sudo systemctl restart docker`.
3. Open only SSH and the web ports: `sudo ufw allow 22,80,443/tcp && sudo ufw enable`. Docker
   publishes container ports past ufw, which is why the override in step 5 binds the frontend to
   loopback.
4. Let a shutdown of the VM wait for ClickHouse. The stack gives ClickHouse five minutes to
   stop; give the Docker service more, so the operating system does not cut it off, with
   `sudo systemctl edit docker` and:

   ```ini
   [Service]
   TimeoutStopSec=6min
   ```

## 3. Get the code

The repository is private: give the VM a read-only deploy key on GitHub. Check out a known
commit or release tag, so you always know what runs.

```bash
sudo mkdir -p /srv/coga && sudo chown "$USER" /srv/coga
git clone git@github.com:bmenten/coga.git /srv/coga
git -C /srv/coga checkout <tag-or-commit>
```

## 4. Configure

```bash
cd /srv/coga
cp .env.example .env
chmod 600 .env
```

`.env.example` lists every setting. Change at least these:

| Setting | Value |
| --- | --- |
| `APP_ENV` | `staging` (any value other than `development`, `dev`, `local` or `test` turns on the production checks) |
| `SECRET_KEY`, `INTEGRITY_ANCHOR_SIGNING_KEY` | generated as `.env.example` shows; different from each other |
| `POSTGRES_PASSWORD`, `CLICKHOUSE_PASSWORD`, `ADMIN_PASSWORD` | strong, distinct values |
| `CORS_ORIGINS` | the app's own origin only: `["https://coga-test.example.org"]` |
| `CORS_ORIGIN_REGEX` | empty (`CORS_ORIGIN_REGEX=`): the default admits local origins, which the backend refuses outside development |
| `ENABLE_HSTS` | `true`: TLS terminates on this machine |
| `FRONTEND_TRUSTED_PROXY_HOPS` | `1`: the reverse proxy sits in front of the frontend |
| `ADMIN_EMAIL`, `SMTP_HOST` | the administrator's address and the relay, or leave `SMTP_HOST` unset |
| `VITE_PROBLEM_REPORT_URL` | where testers report a problem, an `https://` or `mailto:` address |
| `FAMILY_IMPORT_WORKER_COUNT` | 1 to 8; more workers import families in parallel |
| `METRICS_TOKEN` | optional, 32+ characters, to collect `/metrics` ([monitoring.md](monitoring.md)) |

Leave `AZURE_TENANT_ID` and `AZURE_CLIENT_ID` empty unless the VM's users sign in through
an Entra ID tenant that has an app registration for this host; accounts then sign in with
their passwords.

**The dbNSFP gene file** is not in the repository (its download needs a registration,
[data-import.md](data-import.md)). Copy `dbNSFP5.4_gene.gz` to `/srv/coga/data/ref-data/`; without
it the first gene reference sync does not run.

## 5. Build and start

```bash
cd /srv/coga
export GIT_SHA="$(git rev-parse --short=12 HEAD)"
docker compose -f docker-compose.yml -f docker-compose.vm.yml up --build -d
docker compose logs -f backend
```

- Without `GIT_SHA` the backend refuses to start outside development: every report it signs
  names the commit.
- [`docker-compose.vm.yml`](../docker-compose.vm.yml) binds the frontend to `127.0.0.1:3000`
  and rotates the containers' logs. Without it, port 3000 is open on every interface.
- The first start downloads and loads the reference data, which takes several minutes. The
  backend turns healthy when it is done, and the frontend starts after it.

To spare yourself the two `-f` options, set them once in `.env`:
`COMPOSE_FILE=docker-compose.yml:docker-compose.vm.yml`.

## 6. Reverse proxy and TLS

nginx forwards everything to the frontend server, which proxies `/api` to the backend. Put
the certificate and key where only root reads them, and the site in
`/etc/nginx/sites-available/coga`:

```nginx
server {
    listen 80;
    server_name coga-test.example.org;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    http2 on;
    server_name coga-test.example.org;

    ssl_certificate     /etc/ssl/coga/fullchain.pem;
    ssl_certificate_key /etc/ssl/coga/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;

    # Above MAX_UPLOAD_BYTES (1 GiB by default).
    client_max_body_size 1100m;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        # One hop, as FRONTEND_TRUSTED_PROXY_HOPS=1 expects: set, never appended.
        proxy_set_header X-Forwarded-For $remote_addr;
        # The frontend waits up to API_PROXY_TIMEOUT_MS (600 s) for the backend.
        proxy_read_timeout 900s;
        proxy_send_timeout 900s;
        proxy_request_buffering off;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/coga /etc/nginx/sites-enabled/coga
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

With Let's Encrypt, `sudo apt install certbot python3-certbot-nginx` and
`sudo certbot --nginx -d coga-test.example.org` fill in the certificate lines and renew them.

## 7. Check it

```bash
curl -fsS https://coga-test.example.org/api/health
curl -fsS https://coga-test.example.org/api/version   # the version and the GIT_SHA you built
docker compose ps                                      # every service healthy
```

Then sign in with `ADMIN_EMAIL` and `ADMIN_PASSWORD`, and as administrator:

- **Administration → ClickHouse Tables & Operations**: the tables exist and the integrity check
  passes.
- **Admin → Species & Assemblies**: rebuild the clinical CNV knowledgebase, which the startup
  does not load.

## 8. Load test data

- **Demo families** ([demo/README.md](../demo/README.md)): run the loader on the VM, in a
  virtual environment with `backend/requirements.txt`. It reads `.env` and reaches the databases
  on their loopback ports; with `APP_ENV=staging` it runs the backend's production checks, so
  export `GIT_SHA` first.
- **Family packages** ([data-import.md](data-import.md)): copy them with `rsync` to
  `/srv/coga/data/families/`, which the backend sees as `/data/families` (`FAMILY_IMPORT_ROOTS`),
  and import them with Package Import. A long-read small-variant dataset takes hours to import.

## 9. Run it

**Update.** Pull the new commit, stamp it and rebuild; Compose replaces only what changed,
and the backend brings the Postgres schema to the new revision as it starts
([database.md](database.md#schema-migrations)):

```bash
cd /srv/coga
git fetch && git checkout <tag-or-commit>
export GIT_SHA="$(git rev-parse --short=12 HEAD)"
docker compose -f docker-compose.yml -f docker-compose.vm.yml up --build -d
```

**Stop** with `docker compose stop`, never by killing the containers or the VM: ClickHouse needs
up to five minutes to finish its merges, and a cut-off flush corrupts parts that the next
start then refuses to attach. A shutdown of the VM waits for it as well, once step 2 gave the
Docker service the time.

**Back up** Postgres every night, to a share off the VM (at Ghent University, a network share
from ICT's storage offering, mounted here on `/mnt/backup`):

```bash
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > /mnt/backup/coga-$(date +%F).dump
```

Copy `.env` there too, kept as secret as on the VM: a restored database needs the keys it was
written with, such as `INTEGRITY_ANCHOR_SIGNING_KEY`.

ClickHouse holds the imported variants, which can be imported again from the packages. To keep
them, take a snapshot of the data disk while ClickHouse is stopped, or copy the
`clickhouse_data` volume. Restore a backup at least once, into a throwaway stack.

**Watch** with `docker compose ps` and `docker compose logs <service>`. With `METRICS_TOKEN`
set, a Prometheus on the VM scrapes `http://127.0.0.1:8000/metrics`
([monitoring.md](monitoring.md)); nginx does not serve that path, since the frontend proxies only
`/api`.

**Restrict the database role** to test the go-live configuration: the API then runs as
`coga_app` and the migration runs apart ([db-runtime-role-runbook.md](db-runtime-role-runbook.md)).

## 10. Tuning at scale

| Setting | What it changes |
| --- | --- |
| `CLICKHOUSE_MAX_MEMORY_USAGE` | the per-query memory cap for the app's queries, over the memory guards' 4 GB |
| `CLICKHOUSE_MAX_EXECUTION_TIME` | how long a query may run (110 s); large gene panels may need more |
| `FAMILY_IMPORT_WORKER_COUNT` | parallel imports; watch the backend's memory as you raise it |
| [`clickhouse/users.d/memory-guards.xml`](../clickhouse/users.d/memory-guards.xml) | when GROUP BY and ORDER BY spill to disk; a change applies after `docker compose restart clickhouse` |

`docker stats` shows each container's memory and CPU while you test. When the backend is
killed for lack of memory, `docker compose ps` shows it restarting and `dmesg` names the OOM
kill.

## Troubleshooting

The start-up refusals and the datastore errors are the same as in development; see
[development.md](development.md#troubleshooting). Specific to the VM:

- **502 from nginx** — the frontend is not up yet; it starts once the backend is healthy.
- **The backend refuses to start on `CORS_ORIGINS` or `CORS_ORIGIN_REGEX`** — set the app's own
  origin and an empty regex (step 4).
- **Sign-in works but rate limiting or the audit log show the proxy's address** — check
  `FRONTEND_TRUSTED_PROXY_HOPS=1` and that nginx sets, not appends, `X-Forwarded-For`.
- **Uploads fail at about 1 MB** — `client_max_body_size` is missing from the nginx site.
