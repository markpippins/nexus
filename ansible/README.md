# nexus/ansible — failover-tier management

Ansible entry point for mandate #2 (failover-tier maintenance on the
standby machine). Replaces the manual steps in
`docs/VANADIUM_BRING_UP_PROMPT.md` — note that document predates the
W1–W3 rulings: registry and broker run as **containers** now, not nohup
JVM processes.

## Layout

```
ansible.cfg                 connection defaults (slow-Pi tolerant)
inventory/hosts.ini         vanadium, helium
group_vars/all.yml          fleet-wide: authority host, checkout paths, candidate stack
group_vars/vanadium.yml     vanadium-specific: branch, legacy tier env, endpoint map
group_vars/helium.yml       helium-specific: main branch, compose override, mongo source
playbooks/deploy-tier.yml   git sync → render .env → compose up (+W2 override) → prune
playbooks/health-check.yml  probe every tier endpoint, fail on non-200
playbooks/reboot-drill.yml  reboot → wait → verify auto-recovery → health-check
playbooks/deploy-candidate.yml   candidate stack (adonisjs/moleculer) — target-parameterized
playbooks/health-candidate.yml   probe candidate endpoints (14080/api/health, 18082/health)
```

## Targets

`deploy-candidate.yml` and `health-candidate.yml` are target-parameterized
via `-e candidate_targets=<inventory-group>`; they default to `vanadium`.

**helium is the preferred first deployment target.** vanadium is now a
fixture: it hosts the CI/CD pipeline (Jenkins, SonarQube, ballerina tier)
and the backup database that is live for read-only clients, so candidate
container churn there competes with CI and runs beside the replica. helium
hosts the data-infra tier (`~/srv/infra` mongo/redis/nats plus ollama) and
nothing else.

Address both hosts by mDNS name. Both take DHCP leases and have moved
(helium went `.202` → `.229`); never pin an IP in inventory or scripts.

## Usage

```bash
cd nexus/ansible

# ship latest code + rebuild changed images + restart tier
ansible-playbook playbooks/deploy-tier.yml

# verify every health endpoint
ansible-playbook playbooks/health-check.yml

# full failover drill (does NOT touch titanium)
ansible-playbook playbooks/reboot-drill.yml

# candidate stack — preferred first target is helium
ansible-playbook -i inventory/hosts.ini playbooks/deploy-candidate.yml \
  -e candidate_targets=helium
ansible-playbook -i inventory/hosts.ini playbooks/health-candidate.yml \
  -e candidate_targets=helium
```

## Ground rules encoded from the bring-up doctrine

- Standby services share titanium's PostgreSQL over LAN — the tier is a
  warm standby, never a second authority. No destructive tests.
- The candidate group runs its own in-compose redis on every target, so
  `docker compose down` in that project cannot disturb another project's
  backing stores on the same host.
- terrain-ts is expected amber on arm64 (prebuilt x86 dist): the health
  check reports it as WARN, not FAIL, until its source is recovered (W2 backlog).
- The W2 override (`docker-compose.vanadium.yml`) runs terrain-ts
  host-networked; deploy always passes both compose files in the
  `-f` order base→override.
