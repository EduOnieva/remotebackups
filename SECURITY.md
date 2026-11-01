# Security

remotebackups holds an SSH private key and starts rsync with it, and its web UI has no login.
The README says where to keep the UI (your tailnet, your LAN or behind your proxy's
authentication); a report that assumes the UI is on the open internet is still welcome.

## Reporting a vulnerability

Report it privately through GitHub: the *Security* tab of the repository, *Report a
vulnerability*, or https://github.com/EduOnieva/remotebackups/security/advisories/new.
Please do not describe it in a public pull request. Expect an answer within a week.

## Supported versions

The newest release only: `latest`, which is also the newest `X.Y` tag. A fix ships as a new
release, and the weekly rebuild of the release tags picks up fixes in the base image and
Alpine packages on its own.
