# Security policy and boundaries

## Intended use

Trusted **private** repositories on a developer Mac, with explicit manual jobs and a dedicated
non-admin account. Public/untrusted contributions belong on disposable GitHub-hosted runners
or a separately designed isolated environment, not this personal-machine profile.

GitHub explicitly warns that self-hosted runners can be persistently compromised by untrusted
workflow code, including in private repositories. [Official security guidance](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions#hardening-for-self-hosted-runners).

## What the kit does

- Separates privileged provisioning from non-root registration and job execution.
- Does not grant admin, passwordless sudo, Full Disk Access or a background runner service.
- Verifies the initial official runner archive checksum and uses filtered extraction.
- Refuses foreign registrations, reserved usernames, unsafe settings and known symlink/ownership conflicts.
- Keeps registration tokens out of command arguments and kit files; no echoed-input fallback.
- Installs root-owned wrapper code and uses a separate SDK/account.
- Offers offline regression tests; privileged setup is not exercised by ordinary tests.

## What it does not do

- It is **not a VM, container, malware detector, network firewall or security audit**.
- Jobs can access the network, CI-account files, shared readable files and workload secrets.
- The runner's binaries, credentials and workspace remain writable by the CI account; root-owned wrapper
  code does not make previously executed untrusted jobs harmless.
- Labels and workflow conditions are not a substitute for repository access control.
- Stopping a listener does not erase prior compromise, caches or credentials.
- Standard-account separation does not make third-party build dependencies trustworthy.
- It does not create/restore signing keychains, guarantee cleanup after power loss, or recover every project's state.
- Initial checksum pinning does not disable the official runner's automatic updates.

Never attach personal SSH keys, a personal Apple ID, broad cloud credentials or company-wide PATs
to the CI account. Use separate accounts per repository and minimum-scoped, revocable workload secrets.

## Reporting

Use [GitHub private vulnerability reporting](https://github.com/Poliklot/macos-actions-runner-kit/security/advisories/new)
or **Security → Advisories → Report a vulnerability** in this repository. Private reporting is enabled.
Include affected revision, impact and a minimal reproduction with synthetic data.
Do not put real secrets, private logs or exploit details in a public issue or report attachment.

No supported stable releases or remediation SLA are declared yet. Security fixes and supported
versions must be documented when the first version is released.
