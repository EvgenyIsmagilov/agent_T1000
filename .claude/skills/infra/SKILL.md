---
name: infra
description: standards for administering and hardening linux servers (home server and vps). use for systemd units, nginx/caddy reverse proxy, ssh and firewall config, storage placement (ssd/hdd), backups, cron/timers, server monitoring, and idempotent shell or deployment scripts.
---

# Infra

Apply these standards when administering a Linux server (home server or VPS). Container-internal concerns — Dockerfiles, Compose, container health checks — belong to the `docker` skill; this skill covers the host and OS around them.

## Workflow

1. Inspect the target first: distro, init system, what services already run, existing configs. Never assume the environment.
2. Follow the machine's existing conventions and configs — the server always wins over defaults here.
3. Prefer the smallest reversible change. Back up any config before editing it (keep a timestamped `.bak`).
4. Make changes idempotent and safe to re-run.
5. State what changed, how to verify it, and how to roll it back.

## Safety

- Judge every command by its blast radius: what breaks if it is wrong, and whether it can be undone. Confirm destructive or hard-to-reverse actions before running them.
- Test config before applying: `nginx -t`, `sshd -t`, `systemd-analyze verify`, `visudo -c`. Reload rather than restart when the service supports it.
- Do not lock yourself out. When changing SSH, firewall, or networking, keep a second session open and verify the new state before closing it.
- Never hardcode passwords, tokens, or keys in scripts, units, or configs. Use an env file with `chmod 600` (or a secret store), and keep secrets out of version control and logs.
- Never widen the firewall or expose a service beyond what is needed. Bind internal services to `localhost` and reach them through the reverse proxy.

## Services (systemd)

- Prefer a systemd unit over an ad-hoc background process. Enable and start explicitly.
- Run as a dedicated non-root user. Set `Restart=`, correct `After=`/`Wants=` ordering, and hardening directives (`NoNewPrivileges`, `ProtectSystem`, `PrivateTmp`) where practical.
- Prefer systemd timers over cron for new jobs — they log to the journal. Inspect logs with `journalctl -u <unit>`.

## Networking and reverse proxy

- Put HTTP services behind a reverse proxy (nginx or caddy); terminate TLS there and automate certificate renewal.
- Validate the proxy config before reload; reload, do not restart, when possible.

## SSH and firewall

- Default-deny inbound; open only the ports actually needed.
- SSH: key-only auth, disable root login and password auth, and add fail2ban or an equivalent for brute-force protection.

## Storage placement

Choose the disk by workload, not by free space. For any service — containerized or not — decide where its data lives before deployment.

Prefer SSD for:

- databases and their transaction logs;
- search indexes;
- queues and frequently updated state;
- metadata with frequent random reads or writes;
- services that repeatedly scan many small files;
- latency-sensitive caches that must persist.

Prefer HDD for:

- media libraries, backups, and archives;
- large, infrequently accessed files;
- replaceable bulk storage where latency does not matter.

Do not put a write-heavy service on HDD merely because it has more free space. If HDD is proposed for such a service, warn about latency and IOPS cost and require explicit acceptance. A service may split its data — database on SSD, large media on HDD.

## Backups and recovery

- Automate backups and follow 3-2-1 (three copies, two media, one offsite).
- Back up data, config, and the restore procedure. A backup you have never restored is not a backup — test restores.

## Monitoring

Reuse the existing stack. When the server already runs Grafana, add service and container stability metrics to it — do not silently deploy a second monitoring stack. First find which data source already feeds Grafana (Prometheus, VictoriaMetrics, InfluxDB, ...) and reuse it. For a Prometheus-compatible stack, reuse node exporter for host metrics and cAdvisor or runtime-native metrics for containers before adding new collectors.

Monitor at least:

- container and service availability, health status, and restart count/frequency;
- CPU usage and throttling;
- memory usage, limits, and OOM events;
- disk capacity, inode usage, and I/O latency;
- network traffic and errors;
- application request rate, latency, and error rate when exposed;
- database connections, query latency, locks, or queue depth when relevant.

Alert on user-impacting conditions, not pretty dashboards: repeated restarts, unhealthy service, sustained high memory or OOM, low disk or inode exhaustion, abnormal error rate or latency, and failed or stale backups.

## Validation

- Dry-run or config-test before applying.
- After a change, confirm the service is actually up and reachable, and check the journal for errors.
- After storage or monitoring changes, confirm data landed on the intended disk and metrics appear in the existing backend.
- Never claim a service works unless it was actually checked. State what was verified and what was not.
