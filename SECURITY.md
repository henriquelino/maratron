# Security Policy

Maratron is a personal, open-source project. It runs locally on a Windows PC: it reads a USB serial device, serves a dashboard on `127.0.0.1` (localhost only), and writes to local shared memory for the SteamVR driver. It has no server, cloud, or account system, so the attack surface is small.

## Supported versions

The latest commit on `main` is the only supported version. There are no released version branches.

## Reporting a vulnerability

Please do not open a public issue for a security problem.

Use GitHub private vulnerability reporting instead:

1. Go to the repository's **Security** tab.
2. Click **Report a vulnerability**.
3. Describe the issue and how to reproduce it.

This sends the report privately to the maintainer. Expect a best-effort response, since this is a hobby project maintained in spare time.

If private reporting is not enabled, contact the maintainer [@henriquelino](https://github.com/henriquelino).
