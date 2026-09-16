freebuff --continue 2026-08-24T00-22-08.244Z

Tool Usage Summary
Based on our session, here's how to use the key tools properly:
Jenkins on vanadium (.209:8080)
- Authentication: Use API token JENKINS_API_TOKEN as password with user admin
- Web UI: Password JENKINS_ADMIN_PASS also works (verified after initial probe issue)
- Key jobs: sonar-smoke pipeline (runs SonarScanner against .209 Sonar)
- Important: Keep Jenkins local to vanadium — don't migrate yet
SonarQube on vanadium (.209:9000)
- Authentication: Use SONAR_ADMIN_PASS for admin login; use sonar.login property (not sonar.token) for scanner auth with SQ 9.9
- Scanner: Use SonarScanner CLI 6.2.1.4610 (aarch64) with -Dsonar.login=$TOKEN
- Project: vd-sonar-smoke analyzed successfully, 30s runtime
- Tokens: vd-ci-jenkins-smoke (scanner) and ci-gateway-titanium (gateway wire)
Gateway (:9095)
- ** ci-gateway** runs on titanium (.209) with sonarAuthBasic configured
- Verified: /gateway/sonar/projects returns envelope with vd-sonar-smoke
- Health: Upstream upstreams at jenkins:8080 + sonar:9000
Agent Records & API
- Key records created during session:
- 1b34ef93 — Intent: repurpose vanadium
- 0e3ca767 — Assessment: vanadium capacity
- 549ab80a — Report: vanadium repurpose complete
- 1637bb06 — ACK: vanadium validated
- 21758044 — Loop closed: all three lanes green
- Tags system: use to:engineer-ii, to:architect, type:status-update, ref:b53e14cf etc.
Key Takeaways for Proper Use
1. Sonar 9.9 + Scanner 6.2.1: Use -Dsonar.login, NOT -Dsonar.token (Bearer rejected with 401)
2. Pi hardware: Scanner docker image is amd64-only; use aarch64 zip bundle instead
3. Card swap planning: If moving vanadium to 8GB Pi, reserve DHCP lease for new MAC/IP to avoid b53e14cf-class incidents
4. Backup: .env lives on the SD card only — not replicated; ~/vd-ci-backups/ exists
5. No named users currently — single admin accounts for both services
What you need to do properly
1. For Jenkins: Use API token auth (-u admin:$JT), keep vanadium-local per rehome ledger
2. For SonarQube: Use -Dsonar.login property with the token value
3. For gateway: Maintain sonarAuthBasic in Config.toml; verify /gateway/sonar/projects returns expected envelope
4. For records: Reference records by their IDs when discussing in threads (5c171da8, b53e14cf, etc.)
Would you like me to elaborate on any of these points or help with specific tool configurations?
▣  Engineer · Nemotron 3.5 Lightning Free · 1h 14m

--------

One hiccup on the way: the pre-push hook blocked because the Tools & Scripts catalog had pre-existing drift — an orphaned "Recluster Intents" feature whose file ( recluster_intents.py ) was deleted back in commit  eae39526  but never unregistered. That wasn't from my change, but it gates any push touching  bin/ , so I ran the hook's prescribed fix ( sync-tools-and-scripts.py  → deleted the stale feature, DB-only). Catalog check now passes clean (121 scripts + 29 packages in sync).

Records written:
- Agent record → sysadmin:  0dbc6cee  ( to:sysadmin, type:status-update )
- Change-log forum post:  f51e8276 

Housekeeping notes:
-  hourly_maintenance.py  still lists  angular-assembly.service  in  RETIRED_UNITS  — intentional safety net, harmless now that the unit is gone.
- Nothing else in the devops inbox needs action.

Want me to take on any of the pending plans next (0003 event emitter, 0006 semantics-DB wiring, 0007 service-registry), or is there other ops work?
