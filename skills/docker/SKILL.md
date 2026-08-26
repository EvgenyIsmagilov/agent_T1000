---
name: docker
description: docker and docker compose standards. use for any work on dockerfiles, compose files (compose.yaml, docker-compose.yml), and deploying or troubleshooting containerized services.
---

# Docker

Apply these rules when working with Docker or Docker Compose. Prefer the project's existing conventions and pinned versions over generic defaults.

Host-level concerns — storage hardware (SSD vs HDD), OS services, firewall, backups, and the server monitoring stack — belong to the `infra` skill, not here.

## Workflow

1. Inspect the existing Dockerfile, Compose files, environment files, and deployment notes.
2. Identify service dependencies, persistent data, expected write frequency, and failure modes.
3. Design health checks, volumes, resources, networking, and logging before changing configuration.
4. Make the smallest focused change. Do not refactor unrelated services.
5. Validate with project checks or, at minimum, `docker compose config`.

Do not invent ports, credentials, paths, or requirements. Ask when a missing fact materially affects safety or data.

## Health checks

Add a health check whenever the service exposes a reliable, inexpensive readiness or health signal.

Prefer, in order:

1. an application health endpoint;
2. a native client command such as a database ping;
3. a minimal protocol-level check;
4. a process check only when no meaningful service check exists.

A useful health check verifies the service can perform its core function, not merely that its process exists.

Configure reasonable `interval`, `timeout`, `retries`, and `start_period` for slow-starting services.

Avoid health checks that:

- perform writes or mutate data;
- require external internet access;
- create significant load;
- expose secrets in command arguments;
- fail during normal maintenance or startup;
- depend on tools not present in the image.

Do not add a fake health check to satisfy a checklist; explain when a reliable check is not available.

Do not assume an `unhealthy` status automatically restarts a Compose container. Treat health status as a signal for orchestration and alerts unless restart behavior is explicitly implemented.

## Volumes

This section covers container storage. For choosing the underlying disk (SSD vs HDD) and for host backups, see the `infra` skill.

For every persistent path, know what data it holds, whether it can be recreated, and its backup requirements. Set the required owner, group, and permissions.

- Use explicit, stable named volumes or bind-mount paths. Avoid anonymous volumes for important data.
- Keep configuration, persistent data, temporary data, and backups on distinct paths.
- Never treat a volume on the same physical disk as a backup.
- Preserve existing data locations during edits unless migration is explicitly requested and a rollback plan exists.

## Reliability and safety

- Pin image versions. Avoid mutable `latest` tags for production.
- Use restart policies deliberately; do not use restart loops to hide crashes.
- Set resource limits or reservations when supported and meaningful.
- Run as a non-root user when the image and workload allow it.
- Do not bake secrets into images or commit them to Compose files.
- Mount filesystems read-only where writes are unnecessary.
- Expose only required ports; prefer internal Docker networks for service-to-service traffic.
- Keep logs bounded with rotation or an external logging system.
- Avoid privileged mode, host networking, host PID, and broad device mounts unless required and justified.
- Preserve signal handling and use an appropriate init process when the application does not reap child processes correctly.

## Building images

Do not build images on the Raspberry Pi home server (`rpi`). A build pins all four cores and hammers the USB-attached SSD at the same time; the combined current draw sags the 5V rail, and undervoltage is exactly the condition that precedes USB disk drops on that box.

Build elsewhere and ship the result:

- build for `linux/arm64` on a workstation (`docker buildx build --platform linux/arm64`), then push to a registry or move the image with `docker save` piped to `docker load` over ssh;
- prefer prebuilt upstream images — pulling is cheap on the Pi, building is not;
- if a build on the Pi is genuinely unavoidable, say so first, run it while nothing else is loaded, and check `vcgencmd get_throttled` afterwards.

This covers `docker build`, `docker compose build`, and `docker compose up --build`.

## Validation

Use the project's existing validation commands. Otherwise:

1. run `docker compose config`;
2. build affected images without hiding errors, on a host that may build them (see Building images);
3. start only in a safe environment when permitted;
4. verify health status and dependency readiness;
5. verify volume mappings, permissions, and restart behavior.

Never claim a deployment, health check, or volume mapping works unless it was actually verified. Report what remains untested.
