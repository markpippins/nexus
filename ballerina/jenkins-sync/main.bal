// jenkins-sync — Ballerina moat service that keeps Jenkins build data in the
// nexus `jenkins` schema (canonical for CI status) and serves it to Barbie UI.
//
//   PULL (scheduled): Jenkins REST -> nexus Postgres `jenkins` schema.
//   SERVE: REST API for Barbie (builds, stages, test results).
//
// Config from Config.toml (gitignored — see Config.toml.example).

import ballerina/http;
import ballerina/io;
import ballerina/lang.runtime;
import ballerina/sql;
import ballerinax/postgresql;
import ballerinax/postgresql.driver as _;

configurable int port = 9097;
configurable string bindHost = "127.0.0.1";
configurable string jenkinsBase = ?;
// Credential: EITHER pre-encoded jenkinsAuthBasic (base64("user:token"),
// legacy key — takes precedence) OR the jenkinsUser + jenkinsToken pair,
// which matches ci-gateway's keys so both components can share one
// credential block. (Incident 2026-09-19: the Sep 3 token rotation updated
// ci-gateway but not jenkins-sync's base64 blob; sync failed silently for
// 16 days. Matching keys + startup validation prevent a repeat.)
configurable string jenkinsAuthBasic = "";
configurable string jenkinsUser = "";
configurable string jenkinsToken = "";
configurable string dbHost = ?;
configurable int dbPort = 5432;
configurable string dbDatabase = ?;
configurable string dbUser = ?;
configurable string dbPass = ?;
configurable int syncIntervalSeconds = 120;

// Resolved at module init: validates the credential config and dies loudly
// on a bad combination instead of failing every sync cycle silently.
final string jenkinsAuthValue = resolveJenkinsAuth();

function resolveJenkinsAuth() returns string {
    if jenkinsAuthBasic.length() > 0 {
        if jenkinsUser.length() > 0 || jenkinsToken.length() > 0 {
            panic error("jenkins-sync config: set EITHER jenkinsAuthBasic OR " +
                "jenkinsUser+jenkinsToken, not both");
        }
        return jenkinsAuthBasic;
    }
    if jenkinsUser.length() > 0 && jenkinsToken.length() > 0 {
        // Same wire format ci-gateway produces via http:Client auth config:
        // Basic base64("user:token").
        return (jenkinsUser + ":" + jenkinsToken).toBytes().toBase64();
    }
    panic error("jenkins-sync config: no Jenkins credential — set either " +
        "jenkinsAuthBasic (base64 user:token) or jenkinsUser + jenkinsToken");
}

final http:Client jenkins = check new (jenkinsBase);
final postgresql:Client db = check new (dbHost, dbUser, dbPass, dbDatabase, dbPort);

// ── Jenkins REST helpers ────────────────────────────────────────────

function jenkinsGet(string path) returns json|http:ClientError {
    return jenkins->get(path,
        headers = {"Authorization": "Basic " + jenkinsAuthValue},
        targetType = json);
}

function jval(json j, string key) returns json {
    if j is map<json> && j.hasKey(key) {
        return j[key];
    }
    return ();
}

function jstr(json j, string key) returns string {
    json v = jval(j, key);
    if v is string { return v; }
    if v is () { return ""; }
    return v.toString();
}

function jint(json j, string key) returns int {
    json v = jval(j, key);
    if v is int { return v; }
    if v is float { return <int>v; }
    return 0;
}

function upstreamFail(string endpointName, string detail) returns http:Response {
    http:Response res = new;
    res.statusCode = 502;
    res.setJsonPayload({"error": "upstream_fail", "endpoint": endpointName, "detail": detail});
    return res;
}

// ── Pull: jobs ──────────────────────────────────────────────────────

type PullResult record {|
    string kind;
    int total;
    int upserted;
    int pages;
    int failures;
    string? lastError;
|};

function pullJobs() returns PullResult {
    json|http:ClientError payloadResult = jenkinsGet("/api/json?tree=jobs[name,url,description,buildable,color,healthReport[description,score],lastBuild[number,result,timestamp,duration,url]]");
    if payloadResult is http:ClientError {
        return {kind: "jobs", total: 0, upserted: 0, pages: 1, failures: 1, lastError: payloadResult.message()};
    }
    json[] jobs = <json[]>jval(payloadResult, "jobs");
    int upserted = 0;
    int failures = 0;
    string? lastErr = ();
    foreach json j in jobs {
        string name = jstr(j, "name");
        string url = jstr(j, "url");
        string desc = jstr(j, "description");
        boolean buildable = jval(j, "buildable") == true;
        string color = jstr(j, "color");
        json lb = jval(j, "lastBuild");
        int lbNum = jint(lb, "number");
        string lbResult = jstr(lb, "result");
        int lbTs = jint(lb, "timestamp");
        int lbDur = jint(lb, "duration");
        json[] hr = <json[]>jval(j, "healthReport");
        int healthScore = 0;
        string healthDesc = "";
        if hr.length() > 0 {
            healthScore = jint(hr[0], "score");
            healthDesc = jstr(hr[0], "description");
        }
        sql:ParameterizedQuery q = `INSERT INTO jenkins.jobs
            (name, url, description, buildable, color, last_build_num, last_build_result,
             last_build_ts, last_build_dur, health_score, health_desc, raw_json, synced_at)
            VALUES (${name}, ${url}, ${desc}, ${buildable}, ${color},
                    ${lbNum > 0 ? lbNum : null}, ${lbResult != "" ? lbResult : null},
                    ${lbTs > 0 ? lbTs : null}, ${lbDur}, ${healthScore}, ${healthDesc},
                    ${j.toJsonString()}::jsonb, now())
            ON CONFLICT (name) DO UPDATE SET
                url = EXCLUDED.url, description = EXCLUDED.description,
                buildable = EXCLUDED.buildable, color = EXCLUDED.color,
                last_build_num = EXCLUDED.last_build_num,
                last_build_result = EXCLUDED.last_build_result,
                last_build_ts = EXCLUDED.last_build_ts,
                last_build_dur = EXCLUDED.last_build_dur,
                health_score = EXCLUDED.health_score,
                health_desc = EXCLUDED.health_desc,
                raw_json = EXCLUDED.raw_json, synced_at = now()`;
        sql:ExecutionResult|sql:Error r = db->execute(q);
        if r is sql:Error {
            failures += 1;
            lastErr = r.message();
        } else {
            upserted += 1;
        }
    }
    return {kind: "jobs", total: jobs.length(), upserted, pages: 1, failures, lastError: lastErr};
}

// ── Pull: builds (for each job, fetch recent builds) ────────────────

// Row access helper (same convention as sonar-sync): with stream<record {}, ...>
// the row's columns arrive nested under the "value" key, not as direct fields.
function rowMap(record {} row) returns map<anydata> {
    anydata v = row["value"];
    if v is map<anydata> {
        return v;
    }
    return {};
}

// Column map -> json (flatten one row for API output).
function colsToJson(map<anydata> cols) returns map<json> {
    map<json> j = {};
    foreach string k in cols.keys() {
        anydata v = cols[k];
        if v is () {
            j[k] = ();
        } else if v is string|int|float|decimal|boolean {
            j[k] = v;
        } else {
            j[k] = v.toJsonString();
        }
    }
    return j;
}

// Run a query and return flattened json rows (error field set on db failure).
type QueryRows record {|
    json[] items;
    string? dbError;
|};

function queryJson(sql:ParameterizedQuery qry) returns QueryRows {
    stream<record {}, sql:Error?> rs = db->query(qry);
    json[] out = [];
    while true {
        record {}|sql:Error? row = rs.next();
        if row is () { break; }
        if row is sql:Error {
            return {items: [], dbError: row.message()};
        }
        out.push(colsToJson(rowMap(row)));
    }
    return {items: out, dbError: ()};
}

// Read a single int (e.g. COUNT(*)) out of a one-row query result.
function scalarInt(sql:ParameterizedQuery qry) returns int {
    QueryRows r = queryJson(qry);
    if r.dbError != () || r.items.length() == 0 {
        return 0;
    }
    json first = r.items[0];
    if first is map<json> {
        json n = first.get("n");
        if n is int { return n; }
        if n is decimal { return <int>n; }
        if n is float { return <int>n; }
    }
    return 0;
}

function pullBuilds() returns PullResult {
    // Get all tracked jobs
    stream<record {}, sql:Error?> rs = db->query(
        `SELECT name FROM jenkins.jobs ORDER BY name`);
    int total = 0;
    int upserted = 0;
    int failures = 0;
    string? lastErr = ();
    while true {
        record{}|sql:Error? row = rs.next();
        if row is () { break; }
        if row is sql:Error { lastErr = row.message(); break; }
        map<anydata> cols = rowMap(row);
        string jobName = "";
        anydata nameVal = cols["name"];
        if nameVal is string {
            jobName = nameVal;
        }
        if jobName == "" {
            continue;
        }
        string buildsPath = string `/job/${jobName}/api/json?tree=builds[number,result,timestamp,duration,displayName,url]`;
        json|http:ClientError payload = jenkinsGet(buildsPath);
        if payload is http:ClientError {
            failures += 1;
            lastErr = jobName + " => " + buildsPath + " => " + payload.message();
            io:println("[jenkins-sync] builds fetch FAIL: ", lastErr);
            continue;
        }
        json[] builds = <json[]>jval(payload, "builds");
        // Process up to 20 most recent builds per job
        int idx = 0;
        foreach json b in builds {
            if idx >= 20 { break; }
            idx += 1;
            int num = jint(b, "number");
            string result = jstr(b, "result");
            int ts = jint(b, "timestamp");
            int dur = jint(b, "duration");
            string displayName = jstr(b, "displayName");
            string url = jstr(b, "url");
            string commitMsg = "";
            string author = "";
            sql:ParameterizedQuery q = `INSERT INTO jenkins.builds
                (job_name, number, result, timestamp, duration, display_name, url,
                 commit_msg, author, raw_json, synced_at)
                VALUES (${jobName}, ${num}, ${result != "" ? result : null},
                        ${ts}, ${dur}, ${displayName}, ${url},
                        ${commitMsg}, ${author},
                        ${b.toJsonString()}::jsonb, now())
                ON CONFLICT (job_name, number) DO UPDATE SET
                    result = EXCLUDED.result, timestamp = EXCLUDED.timestamp,
                    duration = EXCLUDED.duration, commit_msg = EXCLUDED.commit_msg,
                    author = EXCLUDED.author, raw_json = EXCLUDED.raw_json,
                    synced_at = now()`;
            sql:ExecutionResult|sql:Error r = db->execute(q);
            if r is sql:Error {
                failures += 1;
                lastErr = r.message();
            } else {
                upserted += 1;
            }
            total += 1;
        }
    }
    
    return {kind: "builds", total, upserted, pages: 1, failures, lastError: lastErr};
}

// ── Pull: test results for recent builds ────────────────────────────

function pullTestResults() returns PullResult {
    // Get recent builds (last 24h) that don't have test data yet
    stream<record {}, sql:Error?> rs = db->query(
        `SELECT job_name, number FROM jenkins.builds
         WHERE timestamp > (extract(epoch from now()) - 86400) * 1000
           AND test_total IS NULL
         ORDER BY timestamp DESC LIMIT 20`);
    int total = 0;
    int upserted = 0;
    int failures = 0;
    string? lastErr = ();
    while true {
        record{}|sql:Error? row = rs.next();
        if row is () { break; }
        if row is sql:Error { lastErr = row.message(); break; }
        map<anydata> cols = rowMap(row);
        string jobName = "";
        int buildNum = 0;
        anydata jnVal = cols["job_name"];
        if jnVal is string {
            jobName = jnVal;
        }
        anydata bnVal = cols["number"];
        if bnVal is int {
            buildNum = bnVal;
        } else if bnVal is decimal {
            buildNum = <int>bnVal;
        } else if bnVal is float {
            buildNum = <int>bnVal;
        }
        if jobName == "" || buildNum == 0 {
            continue;
        }
        json|http:ClientError payload = jenkinsGet(
            string `/job/${jobName}/${buildNum}/testReport/api/json`);
        if payload is http:ClientError {
            // No test report available — set zeros
            sql:ExecutionResult|sql:Error r = db->execute(
                `UPDATE jenkins.builds SET test_total = 0, test_pass = 0,
                 test_fail = 0, test_skip = 0
                 WHERE job_name = ${jobName} AND number = ${buildNum}`);
            total += 1;
            continue;
        }
        int totalTests = jint(payload, "totalCount");
        int failCount = jint(payload, "failCount");
        int skipCount = jint(payload, "skipCount");
        int passCount = totalTests - failCount - skipCount;
        sql:ExecutionResult|sql:Error r = db->execute(
            `UPDATE jenkins.builds SET test_total = ${totalTests}, test_pass = ${passCount},
             test_fail = ${failCount}, test_skip = ${skipCount}
             WHERE job_name = ${jobName} AND number = ${buildNum}`);
        if r is sql:Error {
            failures += 1;
            lastErr = r.message();
        } else {
            upserted += 1;
        }
        total += 1;
    }
    
    return {kind: "test_results", total, upserted, pages: 1, failures, lastError: lastErr};
}

// ── Sync orchestrator ───────────────────────────────────────────────

function runSync() returns json {
    PullResult jobs = pullJobs();
    PullResult builds = pullBuilds();
    PullResult tests = pullTestResults();
    int totalFailures = jobs.failures + builds.failures + tests.failures;
    string status = totalFailures == 0 ? "ok" : "partial";

    sql:ExecutionResult|sql:Error r = db->execute(
        `UPDATE jenkins.sync_state SET
            last_jobs_sync = now(), last_builds_sync = now(),
            jobs_total = ${jobs.upserted}, builds_total = ${builds.upserted},
            last_sync_status = ${status}, last_sync_count = ${jobs.upserted + builds.upserted + tests.upserted},
            updated_at = now()
         WHERE id = 1`);

    return {
        jobs,
        builds,
        tests,
        status,
        totalFailures
    };
}

// ── Background sync loop ────────────────────────────────────────────
// NOTE: the sync loop runs on its own strand so that main() returns
// immediately — Ballerina does not start service listeners until the
// entry function returns.

function syncLoop() {
    runtime:sleep(5.0); // initial delay
    while true {
        json result = runSync();
        io:println("[jenkins-sync] sync: ", result.toJsonString());
        runtime:sleep(<decimal>syncIntervalSeconds);
    }
}

// ── HTTP Service ────────────────────────────────────────────────────

service /jenkins\-sync on new http:Listener(port) {

    // Health check.
    resource function get health() returns json {
        return {"status": "ok", "service": "jenkins-sync", "port": port};
    }

    // All jobs with last build info.
    resource function get jobs() returns json|http:Response {
        QueryRows r = queryJson(`SELECT name, url, description, buildable, color,
                    last_build_num, last_build_result, last_build_ts,
                    last_build_dur, health_score, health_desc, synced_at
             FROM jenkins.jobs ORDER BY name`);
        string? dbErr = r.dbError;
        if dbErr is string {
            return upstreamFail("jobs", dbErr);
        }
        return {items: r.items, count: r.items.length()};
    }

    // Builds for a job (newest first).
    resource function get builds(string jobName, int maxResults = 25) returns json|http:Response {
        QueryRows r = queryJson(`SELECT job_name, number, result, timestamp, duration, display_name, url,
                    commit_msg, author, test_total, test_pass, test_fail, test_skip, synced_at
             FROM jenkins.builds
             WHERE job_name = ${jobName}
             ORDER BY number DESC LIMIT ${maxResults}`);
        string? dbErr = r.dbError;
        if dbErr is string {
            return upstreamFail("builds", dbErr);
        }
        int total = scalarInt(`SELECT COUNT(*) AS n FROM jenkins.builds WHERE job_name = ${jobName}`);
        return {items: r.items, count: total};
    }

    // All builds across all jobs (for the overview view).
    resource function get allBuilds(int 'limit = 50) returns json|http:Response {
        QueryRows r = queryJson(`SELECT job_name, number, result, timestamp, duration, display_name,
                    commit_msg, author, test_total, test_pass, test_fail, test_skip
             FROM jenkins.builds
             ORDER BY timestamp DESC LIMIT ${'limit}`);
        string? dbErr = r.dbError;
        if dbErr is string {
            return upstreamFail("allBuilds", dbErr);
        }
        return {items: r.items, count: r.items.length()};
    }

    // CI summary: pass/fail rates, recent trends.
    resource function get summary() returns json|http:Response {
        // Last 30 builds per job
        QueryRows r = queryJson(`SELECT job_name,
                    COUNT(*) AS total_builds,
                    COUNT(*) FILTER (WHERE result = 'SUCCESS') AS pass_count,
                    COUNT(*) FILTER (WHERE result = 'FAILURE') AS fail_count,
                    COUNT(*) FILTER (WHERE result = 'UNSTABLE') AS unstable_count,
                    MIN(timestamp) AS first_build_ts,
                    MAX(timestamp) AS last_build_ts
             FROM jenkins.builds
             GROUP BY job_name
             ORDER BY job_name`);
        string? dbErr = r.dbError;
        if dbErr is string {
            return upstreamFail("summary", dbErr);
        }
        QueryRows o = queryJson(`SELECT COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE result = 'SUCCESS') AS pass_count,
                    COUNT(*) FILTER (WHERE result = 'FAILURE') AS fail_count
             FROM jenkins.builds`);
        json overallJson = o.items.length() > 0 ? o.items[0] : {};

        return {jobs: r.items, overall: overallJson};
    }

    // Sync bookkeeping.
    resource function get state() returns json {
        QueryRows r = queryJson(`SELECT last_jobs_sync, last_builds_sync, jobs_total, builds_total,
                    last_sync_status, last_sync_count, updated_at
             FROM jenkins.sync_state WHERE id = 1`);
        if r.dbError != () {
            return {status: "db-error"};
        }
        return r.items.length() > 0 ? r.items[0] : {};
    }

    // Manual sync trigger.
    resource function post sync() returns json {
        return runSync();
    }
}

// ── Startup ─────────────────────────────────────────────────────────

public function main() {
    io:println("[jenkins-sync] starting on port ", port);
    _ = start syncLoop();
}
