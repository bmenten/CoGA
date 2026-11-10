# Deploying CoGA on a virtual machine

How to run the production stack on one Linux virtual machine, such as a VM in a university
or hospital data centre. Docker Compose runs Postgres, ClickHouse, the backend, the import
worker and the frontend; a reverse proxy on the VM terminates TLS. This is the setup for a
**test or performance environment**. The Google Cloud deployment is in
[deployment-gcp.md](deployment-gcp.md); local development in [development.md](development.md).

## Before you start

- **Which data.** A test VM holds synthetic or public test data only. Real patient data first
  needs a DPIA and a data-processing agreement covering that VM, wherever it runs.
- **Not the device.** The VM is outside the validated environment of the in-house IVD
  ([regulatory/README.md](regulatory/README.md)): nothing signed out there is a clinical
  record. It does run the backend's production checks, so a test there exercises the same
  start-up refusals, audit and sign-out paths as a real deployment.

## 1. What to ask for

| Item | What to request |
| --- | --- |
| Operating system | Ubuntu 26.04 or 24.04 LTS, or Debian 12; Docker's own apt repository serves each |
| CPU and memory | 8 vCPU and 32 GB at least; 16 vCPU and 64 GB to test at scale. ClickHouse caps each query at 4 GB ([`clickhouse/users.d/memory-guards.xml`](../clickhouse/users.d/memory-guards.xml)), so more memory runs more queries at once, not larger ones |
| Disks | a system disk of 50 GB, and a separate SSD data disk (500 GB to 1 TB) for the Docker volumes and the family packages; snapshots of the data disk, if the provider offers them |
| Host name | a DNS name, such as `coga-test.example.org` |
| Inbound traffic | 443 (and 80 for an ACME certificate) from the institution's network or VPN only; 22 from the administrators' addresses only. Nothing else: Postgres, ClickHouse and the backend listen on loopback |
| Outbound traffic | HTTPS to the internet: Docker pulls the images from Docker Hub, the image builds fetch packages from PyPI and npm, and the first start downloads the reference data (GENCODE, UCSC, HGNC, NCBI, ClinGen, GenCC, HPO, Monarch) |
| TLS certificate | from the institution's certificate service when the VM is reachable only from inside, since an ACME HTTP challenge cannot reach it; Let's Encrypt when port 80 is open to the internet |
| Mail relay (optional) | an SMTP relay CoGA may send through (port 25 without sign-in, or 587 with STARTTLS and an account), for the mail to the administrators about an account that waits for approval |

### At Ghent University

A staff member requests the VM through the ICT self-service portal (the virtual-server form,
<https://helpdesk.ugent.be/webhosting/virtueel.php>), with the default Linux profile or a later
PXE install of a supported, non-EOL distribution. Submitting the request accepts ICT's terms
of use. ICT manages only the platform; the VM's administrator is responsible for the rest, and
ICT may switch off a VM that breaks the terms. What each term asks of a CoGA VM:

| ICT term | What it means here |
| --- | --- |
| **Self-care**: the administrator is responsible for the OS, the licences and access control (authentication and authorization) inside the VM; ICT gives only the management console (vSphere Web Client, through Athena) | SSH keys, ufw, nginx, CoGA's own accounts and project access, and the dbNSFP licence are yours (steps 2, 4 and 6) |
| **VMware tools** are mandatory; without them ICT may switch the VM off | install `open-vm-tools` right after the OS (step 2) |
| **Keep it up to date**; a VM with an outdated OS or missing security patches may be switched off | `unattended-upgrades` (step 2), a reboot when a kernel update needs one ([step 9](#9-run-it)), and regular rebuilds so the images pick up their base images' fixes |
| **Back-ups** are the administrator's; ICT's disaster-recovery back-ups restore the whole platform, not files inside a VM | back up to a network share from ICT's storage offering, not to the VM's own disk ([step 9](#9-run-it)) |
| **Fair use**; a VM that uses resources unreasonably may be switched off | request the size you need (step 1), and raise `IMPORT_WORKER_REPLICAS` and the ClickHouse limits only as far as the VM's size allows ([step 10](#10-tuning-at-scale)) |
| **Yearly confirmation**: an unused VM, or one outside the university's normal work, gets an e-mail; without an answer through ICT self-service within 30 days it is switched off for good | name a mailbox someone reads in the request; answer the e-mail |
| **A successor administrator** must be reported through ICT self-service or HelpMe; a VM without an administrator is shut down | name the new administrator before you hand over |
| **Security incidents**: ICT isolates or switches off the VM, and the department can be held co-liable for negligence | test data only, loopback-only ports, and the firewall of step 2 |

What else to know there:

- **Installing.** With the netboot option, open the VM's remote console in the vSphere Web
  Client, hold Esc while it boots, and pick the distribution from ICT's menu. The Ubuntu
  installer seems to hang twice, during the install and at cloud-init: wait. DHCP hands out the
  address ICT registered for the VM's MAC address.
- **SSH keys.** The installer imports SSH keys only from a GitHub account that has public
  keys. Otherwise keep password sign-in for the first login, copy your key with
  `ssh-copy-id <user>@<host name>`, and switch passwords off (step 2).
- **The network.** ICT's firewall profile admits the ports it lists, from UGent's network and
  VPN. It can list more than CoGA needs, such as 5432: Postgres listens on loopback and ufw
  keeps the port closed, but ask ICT to drop it. Check the outbound traffic in step 2.
- **Host name and certificate.** Give the VM a host name under `ugent.be`. Nothing outside
  UGent reaches it, so its certificate comes from HARICA ([step 6](#6-reverse-proxy-and-tls)),
  not from an ACME HTTP challenge. ICT's single sign-on is open only to existing UGent DNS
  names served over HTTPS.
- **Network shares** are SMB 3.1.1 on `files.ugent.be`; there is no NFS. Mount them with a
  system account from ICT ([step 8](#a-network-share)), not with a personal UGent account,
  whose password would open that person's own drives too.

ICT may switch the VM off, so make sure a shutdown stops ClickHouse cleanly (step 2) and the
back-ups are current.

## 2. Prepare the VM

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y unattended-upgrades open-vm-tools git ufw nginx
```

`open-vm-tools` lets the VMware platform see and shut down the VM properly; on a VMware host
such as Ghent University's it is mandatory.

1. **Check the outbound traffic.** Each address must answer with an HTTP code (200, 301, 401
   and the like); `000` means it is blocked, and the network's administrators must open it:

   ```bash
   for u in https://github.com https://download.docker.com https://registry-1.docker.io/v2/ \
       https://pypi.org https://registry.npmjs.org https://ftp.ebi.ac.uk \
       https://hgdownload.soe.ucsc.edu https://ftp.ncbi.nlm.nih.gov https://storage.googleapis.com; do
     printf '%-40s ' "$u"; curl -sS -o /dev/null -m 10 -w '%{http_code}\n' "$u" 2>&1
   done
   ```

2. **Give `/srv` a volume of its own** for Docker, the code and the packages. Mount the data
   disk there. With a single disk and an LVM install, Ubuntu's installer can leave part of the
   volume group free (`sudo vgs` shows it): make that the volume for `/srv`, and a data disk
   added later extends it in place.

   ```bash
   sudo lvcreate -n srv -l 100%FREE ubuntu-vg
   sudo mkfs.ext4 -L srv /dev/ubuntu-vg/srv
   echo '/dev/ubuntu-vg/srv /srv ext4 defaults 0 2' | sudo tee -a /etc/fstab
   sudo systemctl daemon-reload && sudo mount /srv
   ```

   A data disk added later, say `/dev/sdb`:
   `sudo pvcreate /dev/sdb && sudo vgextend ubuntu-vg /dev/sdb && sudo lvextend -r -l +100%FREE ubuntu-vg/srv`.

3. **Install Docker** from Docker's own apt repository
   (<https://docs.docker.com/engine/install/>), not the distribution's `docker.io` package. Put
   its data on `/srv` first, so nothing lands on the system disk:

   ```bash
   sudo mkdir -p /etc/docker /srv/docker
   echo '{ "data-root": "/srv/docker" }' | sudo tee /etc/docker/daemon.json
   sudo install -m 0755 -d /etc/apt/keyrings
   sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
   sudo chmod a+r /etc/apt/keyrings/docker.asc
   printf 'Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: %s\nComponents: stable\nSigned-By: /etc/apt/keyrings/docker.asc\n' \
     "$(. /etc/os-release && echo "$VERSION_CODENAME")" | sudo tee /etc/apt/sources.list.d/docker.sources
   sudo apt update
   sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
   sudo usermod -aG docker "$USER"
   ```

   On Debian, write `debian` for `ubuntu` in both URLs. The `docker` group is root in all but
   name: put administrators in it only, and sign out and in again. Then check:
   `docker info --format '{{.DockerRootDir}}'` prints `/srv/docker`, `docker compose version`
   is 2.24.4 or later (the override file of step 5 needs it), and `docker run --rm hello-world`
   pulls from Docker Hub.

4. **Let a shutdown of the VM wait for ClickHouse.** The stack gives ClickHouse five minutes
   to stop. Give the Docker service more, so the operating system does not cut it off:

   ```bash
   sudo mkdir -p /etc/systemd/system/docker.service.d
   printf '[Service]\nTimeoutStopSec=6min\n' | sudo tee /etc/systemd/system/docker.service.d/coga.conf
   sudo systemctl daemon-reload && sudo systemctl restart docker
   ```

5. **Open only SSH and the web ports:** `sudo ufw allow 22,80,443/tcp && sudo ufw enable`,
   and nothing else, even a port the institution's firewall admits. Docker publishes container
   ports past ufw, so Compose binds Postgres, ClickHouse and the backend to loopback, and the
   override in step 5 the frontend.

6. **Allow SSH keys only**, once your key signs you in:

   ```bash
   printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin no\n' \
     | sudo tee /etc/ssh/sshd_config.d/00-coga.conf
   sudo sshd -t && sudo systemctl restart ssh
   ```

   sshd keeps the first value it reads, and the `00-` puts this file before
   `50-cloud-init.conf`, which can switch passwords back on. Keep your session open, and check
   from a new terminal that `ssh -o PubkeyAuthentication=no <user>@<host name>` is refused.

## 3. Get the code

The repository is public: clone it over HTTPS. Check out a known commit or release tag, so you
always know what runs.

```bash
sudo mkdir -p /srv/coga && sudo chown "$USER": /srv/coga
git clone https://github.com/bmenten/CoGA.git /srv/coga
git -C /srv/coga checkout <tag-or-commit>
```

## 4. Configure

```bash
cd /srv/coga
cp .env.example .env
chmod 600 .env
```

**Secrets.** Make them on the VM itself, so they never pass through another machine, a
clipboard, a chat or a ticket. This fills the five secrets in `.env` and shows the
administrator's password once, for your password manager:

```bash
python3 - <<'EOF'
import base64, os, re, secrets, pathlib
p = pathlib.Path('/srv/coga/.env')
s = p.read_text()
assert re.search(r'(?m)^SECRET_KEY=change-me$', s), '.env already holds secrets'
vals = {
    'SECRET_KEY': secrets.token_urlsafe(48),
    'INTEGRITY_ANCHOR_SIGNING_KEY': base64.b64encode(os.urandom(32)).decode(),
    'POSTGRES_PASSWORD': secrets.token_urlsafe(32),
    'CLICKHOUSE_PASSWORD': secrets.token_urlsafe(32),
    'ADMIN_PASSWORD': secrets.token_urlsafe(18),
}
for k, v in vals.items():
    s, n = re.subn(rf'(?m)^{k}=.*$', lambda m: f'{k}={v}', s)
    assert n == 1, k
p.write_text(s)
print('ADMIN_PASSWORD =', vals['ADMIN_PASSWORD'])
EOF
```

- Set them once, before the first start. Postgres takes `POSTGRES_PASSWORD` only when its
  volume is created. `INTEGRITY_ANCHOR_SIGNING_KEY` checks the signed integrity anchors, so it
  never changes once anything is signed: keep it with the back-ups ([step 9](#9-run-it)).
- `ADMIN_PASSWORD` can change: at its next start the backend gives the emergency
  administrator the new password. The backend refuses only the placeholders (`admin`,
  `change-me`), so its strength is up to you.
- `.env` is in `.gitignore`. Never paste it into a ticket, an issue or a chat.

`.env.example` lists every setting. Change at least these as well:

| Setting | Value |
| --- | --- |
| `APP_ENV` | `staging` (any value other than `development`, `dev`, `local` or `test` turns on the production checks) |
| `ADMIN_EMAIL`, `ADMIN_USERNAME` | the address the emergency administrator signs in with, and its account name. With Microsoft or CAS sign-in, not the address of anyone who signs in that way, yours included: CoGA refuses the first sign-in of an organisation account whose address another account holds |
| `CORS_ORIGINS` | the app's own origin only: `["https://coga-test.example.org"]` |
| `CORS_ORIGIN_REGEX` | empty (`CORS_ORIGIN_REGEX=`): the default admits local origins, which the backend refuses outside development |
| `ENABLE_HSTS` | `true`: TLS terminates on this machine |
| `FRONTEND_TRUSTED_PROXY_HOPS` | `1`: the reverse proxy sits in front of the frontend |
| `COMPOSE_FILE` | `docker-compose.yml:docker-compose.vm.yml`, so every `docker compose` command uses the override of step 5 |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM` | the mail relay, its port (25 by default) and the sender it accepts; leave `SMTP_HOST` unset to send no mail. With a relay that needs an account, also `SMTP_STARTTLS=true`, `SMTP_USERNAME` and `SMTP_PASSWORD`. *Users* shows who gets the mail and sends a test |
| `VITE_PROBLEM_REPORT_URL` | where testers report a problem, an `https://` or `mailto:` address |
| `FAMILY_IMPORT_ROOTS` | `/data/families`, and `/data/families,/data/share` once a network share is mounted ([step 8](#a-network-share)) |
| `IMPORT_WORKER_REPLICAS` | the import-worker processes (1 by default); each imports one family at a time on a core of its own, so more of them import families in parallel ([step 10](#10-tuning-at-scale)) |
| `METRICS_TOKEN` | optional, 32+ characters, to collect `/metrics` ([monitoring.md](monitoring.md)) |

**Sign-in.** Left empty, `AZURE_TENANT_ID` and `AZURE_CLIENT_ID` give every account a CoGA
password. With an app registration in the organisation's Entra ID, users sign in with their
Microsoft account instead, MFA included, and nobody can sign up. IT creates the registration as
[deployment-gcp.md §12.11](deployment-gcp.md#1211-microsoft-sign-in-azure-ad--microsoft-entra-id)
describes, with the redirect URI `https://<host name>/login`. The go-live steps there apply
here too, with `AZURE_ADMIN_OVERRIDE` in `.env` in place of the Terraform variable.

- The redirect URI needs HTTPS: switch Microsoft sign-in on once the certificate of step 6 is
  in place.
- Ask for *Assignment required* on the enterprise application, with only the testers
  assigned. Without it every account in the tenant, students and guests included, can sign in
  and gets a CoGA account (a viewer in no project).
- The backend reads these settings when it starts. After a change, `docker compose up -d`
  recreates the backend and the import worker; nothing needs rebuilding.

**At Ghent University** ICT registers web applications on its CAS server, `login.ugent.be`
(the self-service form for a URL/OAuth application), not in Entra ID. CoGA signs in there
instead of with Microsoft ([security-posture.md](security-posture.md)):

```env
CAS_SERVER_URL=https://login.ugent.be
CAS_SERVICE_URL=https://coga-test.example.org/login
CAS_DISPLAY_NAME=UGent
```

- In ICT's form, register a **URL** (a CAS service), not an OAuth application: CoGA speaks
  CAS only. The endpoint URL is `CAS_SERVICE_URL`, and the attributes are `mail`, `surname`
  and `azureObjectId` (public) and `givenName` (protected: the motivation is the user's name in
  the app and in the audit log). Ask for nothing else, and no guest accounts. Without
  `givenName` CoGA shows only the surname; once ICT releases it, each user's next sign-in fills
  in the first name.
- An OAuth registration does not register the service: after the UGent sign-in,
  `login.ugent.be` answers "Ongeldige service-URL" (your service is not registered). Register
  the URL as well; the OAuth credentials are not needed. The same page appears when the
  registered URL differs from `CAS_SERVICE_URL` in any character: give ICT the tracking ID the
  page shows.
- A sign-in CoGA refuses after the CAS server let it through says why in
  `docker compose logs backend | grep "cas sign-in"`: an attribute the answer lacks, the code
  the CAS server named (such as `INVALID_SERVICE`), that the server could not be reached, or
  that another account has the user's e-mail address or user name. Neither the ticket nor the
  answer is logged.
- A sign-in whose answer lacks `mail`, `givenName` or `surname` logs a warning under the same
  `cas sign-in` prefix that names the attributes the answer does carry, never their values.
  Ask ICT to release the missing one, or, when it arrives under another name, set
  `CAS_EMAIL_ATTRIBUTE`, `CAS_GIVEN_NAME_ATTRIBUTE` or `CAS_FAMILY_NAME_ATTRIBUTE` to that
  name.
- The service needs HTTPS: set these once the certificate of step 6 is in place, then
  `docker compose up -d`.
- CAS admits every UGent account, students included. A first sign-in makes an account that
  waits for approval on *Users*, where an administrator activates it, or deletes or archives
  it when it is not relevant. Every active administrator is told by e-mail when `SMTP_HOST` is
  set.
- The first administrator: sign in once through CAS, then sign in at `/login/emergency` with
  `ADMIN_EMAIL` and `ADMIN_PASSWORD` as the emergency administrator, with
  `AZURE_ADMIN_OVERRIDE=true`. Activate your account and make it an administrator, and switch
  the override off again. `ADMIN_EMAIL` must not be your own UGent address: CoGA would refuse
  your CAS sign-in, since the emergency administrator already has that address.

**The dbNSFP gene file** is not in the repository (its download needs a registration,
[data-import.md](data-import.md)). Copy `dbNSFP5.4_gene.gz` to `/srv/coga/data/ref-data/`
(from your own machine:
`scp dbNSFP5.4_gene.gz <user>@<host name>:/srv/coga/data/ref-data/`). Without it the first
gene reference sync still runs, from the online sources, but it takes hours (one NCBI request per
gene) and leaves dbNSFP's fields empty. The first start after you copy the file in syncs again
with it.

## 5. Build and start

```bash
cd /srv/coga
export GIT_SHA="$(git rev-parse --short=12 HEAD)"
docker compose up --build -d
docker compose logs -f backend
```

- Without `GIT_SHA` the backend refuses to start outside development: every report it signs
  names the commit.
- [`docker-compose.vm.yml`](../docker-compose.vm.yml) binds the frontend to `127.0.0.1:3000`
  and rotates the containers' logs. `COMPOSE_FILE` (step 4) applies it; without that setting,
  pass `-f docker-compose.yml -f docker-compose.vm.yml` to every command. Check that
  `docker compose ps` shows `127.0.0.1:3000->3000/tcp` for the frontend: without the override,
  port 3000 is open on every interface.
- The first start downloads and loads the reference data, which takes several minutes. The
  backend turns healthy when it is done; the frontend and the import worker start after it.
  The import worker (`import-worker`) runs the package imports; the backend runs none
  ([application-scheme.md](application-scheme.md#import-worker-processes)).

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

Enable it once the certificate and key are in place; before that, `nginx -t` fails:

```bash
sudo ln -sf /etc/nginx/sites-available/coga /etc/nginx/sites-enabled/coga
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

With Let's Encrypt, `sudo apt install certbot python3-certbot-nginx` and
`sudo certbot --nginx -d coga-test.example.org` fill in the certificate lines and renew them.

**Until the certificate is in place**, reach the app through an SSH tunnel from your own
machine and open `http://localhost:3300`:

```bash
ssh -N -L 3300:127.0.0.1:3000 <user>@coga-test.example.org
```

This works with `ENABLE_HSTS=true`: the frontend does not redirect to HTTPS, and browsers
ignore HSTS over plain HTTP. Port 3300 keeps it clear of a development stack on 3000.

### At Ghent University: a HARICA certificate

1. Request a server certificate for the VM's host name on HARICA's portal, signed in with your
   UGent account. If HARICA makes the key pair in your browser, save the private key then: it
   is not part of the certificate you download later.
2. The certificate follows once the request is approved, after hours or days. Download it as
   PEM with its chain (HARICA's "PEM bundle"): the certificate and HARICA's intermediate
   certificates.
3. On the VM, check that the bundle holds at least two certificates and the host name, and
   that the key belongs to it. A key made in the browser can be encrypted
   (`BEGIN ENCRYPTED PRIVATE KEY`); `openssl pkey -in privateKey.pem -out key.pem` decrypts it
   for nginx.

   ```bash
   grep -c 'BEGIN CERTIFICATE' bundle.pem
   openssl x509 -in bundle.pem -noout -subject -enddate -ext subjectAltName
   [ "$(openssl x509 -in bundle.pem -noout -pubkey | openssl sha256)" = \
     "$(openssl pkey -in privateKey.pem -pubout | openssl sha256)" ] && echo "key matches"
   ```

4. Install both, then delete every other copy of the key, the one in your downloads included:

   ```bash
   sudo install -d -m 700 /etc/ssl/coga
   sudo install -m 644 bundle.pem /etc/ssl/coga/fullchain.pem
   sudo install -m 600 privateKey.pem /etc/ssl/coga/privkey.pem
   ```

5. Renew it before its end date. From March 2027 a certificate lives 100 days at most, and 47
   days from March 2029. Where the institution offers it, HARICA's ACME service (with external
   account binding) lets `certbot` renew without an inbound challenge.

## 7. Check it

```bash
curl -fsS https://coga-test.example.org/api/health
curl -fsS https://coga-test.example.org/api/version   # the version and the GIT_SHA you built
docker compose ps                                      # every service up, the databases healthy
```

Before TLS, run the first two on the VM against `http://127.0.0.1:3000`.

Then sign in with `ADMIN_EMAIL` and `ADMIN_PASSWORD` (with Microsoft or CAS sign-in, as the
first administrator of [step 4](#4-configure)), and as administrator:

- **Administration → ClickHouse Tables & Operations**: the tables exist and the integrity check
  passes.
- **Admin → Species & Assemblies**: the clinical CNVs and the DGV variants of GRCh38 fill in by
  themselves. The first start builds the clinical CNV knowledgebase and loads DGV in the
  background, which can take up to two hours
  ([data-import.md](data-import.md#loading-or-replacing-reference-data)). A count that stays
  empty, or an error on that page, needs its ↻.

## 8. Load test data

**Demo families** ([demo/README.md](../demo/README.md)). The backend image holds neither the
loader nor the demo bundle, and the VM need not have the backend's Python: run the loader in
a one-off backend container, with the checkout mounted. It reaches the databases on Compose's
network.

```bash
cd /srv/coga
docker compose run --rm --no-deps -e PYTHONDONTWRITEBYTECODE=1 -v /srv/coga:/repo -w /repo \
  backend python scripts/load_demo_quartet.py --overwrite
```

The NIPT demo is a package folder with its package file, for the project `Demo` on GRCh38: create
that project, copy the folder into the project's folder of a watched import folder
(`FAMILY_IMPORT_WATCH_ROOTS`, [below](#a-network-share)), such as
`mkdir -p data/families/Demo && cp -r demo/nipt_family data/families/Demo/` with
`FAMILY_IMPORT_WATCH_ROOTS=/data/families`, and approve its delivery under **Deliveries**
([demo/nipt_family/README.md](../demo/nipt_family/README.md)).

**Family packages** ([data-import.md](data-import.md)). Copy each folder from your own machine
to `/srv/coga/data/families/`, which the backend sees as `/data/families`
(`FAMILY_IMPORT_ROOTS`), and import it with Package Import. Leave the trailing slash off the
source, so the folder itself is copied; run the same command again to finish an interrupted
copy.

```bash
rsync -av --progress <package folder> <user>@coga-test.example.org:/srv/coga/data/families/
```

A folder without a package file of its own is added under **Deliveries** with **Add a folder by
hand**: CoGA makes its package from the folder, and writes nothing into it. A long-read
small-variant dataset takes hours to import.

**Aligned reads** stay in their package. The manifest's `alignments` dataset names each
sample's CRAM and its index, whatever they are called, and the genome browser (IGV) reads them
from the package folder its import read. A sample with none recorded is looked for as
`<sample>.cram` with `<sample>.cram.crai`, named by the sample and family IDs in CoGA, in
`data/families/<family>/` (or its `bams/` or `alignments/` folder) and `data/<family>/`.

### A network share

A share holding packages and their CRAM files is read where it is: imports read the packages
in place, and the genome browser reads a CRAM, the CNV caller's signal files and the QC report
from the path its import recorded, in the package folder on the share. Mount it
read-only on `/srv/coga/data/share` (which git ignores), as a second import root. At Ghent
University (`files.ugent.be`, SMB 3.1.1, with ICT's options `vers=3.11`, `sec=ntlmv2i` and
`echo_interval=5`):

```bash
sudo apt install -y cifs-utils keyutils smbclient
sudo install -m 600 /dev/null /root/.share-credentials
sudo nano /root/.share-credentials      # user=<system account>, pass=<its password>, dom=UGENT
sudo mkdir -p /srv/coga/data/share
echo '//files.ugent.be/<system account>/shares/<share>/<folder> /srv/coga/data/share cifs credentials=/root/.share-credentials,ro,vers=3.11,sec=ntlmv2i,echo_interval=5,noperm,file_mode=0444,dir_mode=0555,_netdev,nofail 0 0' \
  | sudo tee -a /etc/fstab
sudo systemctl daemon-reload && sudo mount /srv/coga/data/share
```

- `smbclient -U 'UGENT/<account>' //files.ugent.be/<account> -m smb3` tests the account and
  the path first, and `nc -vz files.ugent.be 445` the network.
- A container sees only what was mounted when it started. Have Docker wait for the share at
  boot, and after mounting it by hand run `docker compose up -d --force-recreate backend import-worker`:

  ```bash
  printf '[Unit]\nWants=srv-coga-data-share.mount\nAfter=srv-coga-data-share.mount\n' \
    | sudo tee /etc/systemd/system/docker.service.d/share.conf
  sudo systemctl daemon-reload
  ```

- Set `FAMILY_IMPORT_ROOTS=/data/families,/data/share` in `.env` and run `docker compose up -d`.
- The mount is read-only, which suits CoGA: it writes nothing into a package folder. Every import
  reads the manifest CoGA keeps, made from the delivery's package
  ([data-import.md](data-import.md#a-manifest-kept-in-coga)).
- To have CoGA watch the share for packages the pipelines deliver with their package file
  (`coga-package.json`, [coga-package.md](coga-package.md)), lay it out with a folder per project, named as the
  project in CoGA (`/data/share/<project>/<family>`), add `FAMILY_IMPORT_WATCH_ROOTS=/data/share` to `.env` and run
  `docker compose up -d`: the import-worker service checks each new delivery every five minutes
  (`FAMILY_IMPORT_WATCH_INTERVAL_SECONDS`) and lists it under `GET /api/family-imports/deliveries`.
  It imports nothing by itself ([data-import.md](data-import.md#watched-import-folders)).

## 9. Run it

**Update.** Pull the new commit, stamp it and rebuild; Compose replaces only what changed,
and the backend brings the Postgres schema to the new revision as it starts
([database.md](database.md#schema-migrations)):

```bash
cd /srv/coga
git fetch && git checkout <tag-or-commit>
export GIT_SHA="$(git rev-parse --short=12 HEAD)"
docker compose up --build -d
```

A browser tab open during the update reloads itself the first time it opens another page,
to load the new version.

**Stop** with `docker compose stop`, never by killing the containers or the VM. ClickHouse
needs up to five minutes to finish its merges, and a cut-off flush corrupts parts that the
next start then refuses to attach. A shutdown of the VM also waits for it, once step 2 has
given the Docker service the time.

**Reboot** when the login banner says `*** System restart required ***`: `unattended-upgrades`
has installed an update, usually a kernel, that takes effect only after a reboot, and
`cat /var/run/reboot-required.pkgs` names it. Wait until no import is running, since a reboot
stops one where it is and it ends interrupted
([data-import.md](data-import.md#the-import-job)). Then stop the stack, reboot, and start it
again once you are signed in:

```bash
cd /srv/coga && docker compose stop
sudo reboot
```

```bash
cd /srv/coga && docker compose up -d && docker compose ps
```

After `docker compose stop` the backend, the import worker and the frontend stay down through
the reboot until `docker compose up -d` starts them; it rebuilds nothing. Then check the site as
in [step 7](#7-check-it). A reboot the stack was not stopped for, such as maintenance of the
host, starts every service again by itself.

**Back up** Postgres every night, to a share off the VM, mounted here on `/mnt/backup`:

```bash
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > /mnt/backup/coga-$(date +%F).dump
```

Copy `.env` there too: a restored database needs the keys it was written with, such as
`INTEGRITY_ANCHOR_SIGNING_KEY`. Everyone with access to the share can read what is on it, so
give the back-up folder to the back-up account alone, or encrypt the copy of `.env`. At Ghent
University, mount a folder of the share as in [step 8](#a-network-share), writable for root
only: `rw,uid=0,gid=0,file_mode=0600,dir_mode=0700` in place of
`ro,…,file_mode=0444,dir_mode=0555`.

ClickHouse holds the imported variants, which can be imported again from the packages. To
keep them, take a snapshot of the data disk while ClickHouse is stopped, or copy the
`clickhouse_data` volume. Restore a backup at least once, into a throwaway stack.

**Watch** with `docker compose ps` and `docker compose logs <service>`. With `METRICS_TOKEN`
set, a Prometheus on the VM scrapes `http://127.0.0.1:8000/metrics`
([monitoring.md](monitoring.md)); nginx does not serve that path, since the frontend proxies only
`/api`.

**Restrict the database role** to test the go-live configuration: the API then runs as
`coga_app`, and the migration runs separately
([db-runtime-role-runbook.md](db-runtime-role-runbook.md)).

## 10. Tuning at scale

| Setting | What it changes |
| --- | --- |
| `CLICKHOUSE_MAX_MEMORY_USAGE` | the per-query memory cap for the app's queries, over the memory guards' 4 GB |
| `CLICKHOUSE_MAX_EXECUTION_TIME` | how long a query may run (110 s); large gene panels may need more |
| `IMPORT_WORKER_REPLICAS` | parallel imports: the import-worker processes, each on a core of its own and holding one import's memory; leave cores for the API and ClickHouse, and watch the memory as you raise it |
| `FAMILY_IMPORT_WORKER_COUNT` | the imports each import-worker process runs at once (1 by default, up to 8); they share the process's one core, so raise the replicas instead |
| [`clickhouse/users.d/memory-guards.xml`](../clickhouse/users.d/memory-guards.xml) | when GROUP BY and ORDER BY spill to disk; a change applies after `docker compose restart clickhouse` |

`docker stats` shows each container's memory and CPU while you test. When the backend or an
import worker is killed for lack of memory, `docker compose ps` shows it restarting, and
`dmesg` names the OOM kill. An import stopped by a worker's restart is ended as interrupted once
its heartbeat is ten minutes old ([data-import.md](data-import.md#the-import-job)).

## Troubleshooting

The start-up refusals and the datastore errors are the same as in development; see
[development.md](development.md#troubleshooting). Specific to the VM:

- **502 from nginx** — the frontend is not up yet; it starts once the backend is healthy.
- **The backend refuses to start on `CORS_ORIGINS` or `CORS_ORIGIN_REGEX`** — set the app's own
  origin and an empty regex (step 4).
- **The backend log warns that the gene annotation GTF could not be fetched** — GENCODE
  (`ftp.ebi.ac.uk`) was out of reach, and the genes came from UCSC's table. Once it answers
  again, import the genes again with overwrite
  ([data-import.md](data-import.md#what-startup-loads)).
- **Package Import does not list the share's folders** — Docker started before the share was
  mounted; run `docker compose up -d --force-recreate backend import-worker` (step 8).
- **Sign-in works but rate limiting or the audit log show the proxy's address** — check
  `FRONTEND_TRUSTED_PROXY_HOPS=1` and that nginx sets, not appends, `X-Forwarded-For`.
- **Uploads fail at about 1 MB** — `client_max_body_size` is missing from the nginx site.
- **A page shows "This view could not be loaded" after an update** — the tab was opened before
  it, and reloading itself once did not load the new version; press **Reload page**.
