# Third-party components

This is an independent helper, not an official GitHub or Apple product. It downloads the official
[GitHub Actions runner](https://github.com/actions/runner) at runtime; it does not redistribute runner binaries.
The runner has its own [MIT license and notices](https://github.com/actions/runner/blob/v2.337.0/LICENSE).
SDKs, Xcode, Ruby, Python and build tools remain subject to their respective terms; the kit does not accept them for you.

The source distribution includes three platform-independent Python wheels solely for the terminal UI:

- Questionary 2.1.1 — MIT;
- prompt_toolkit 3.0.53 — BSD 3-Clause;
- wcwidth 0.8.3 — MIT.

Their complete upstream license texts and package metadata remain inside each wheel under
`tool/runner/vendor/*.whl`. Exact SHA-256 values are enforced before provisioning and again before
Python adds them to its import path. They are installed root-owned with the wrapper; no runtime
PyPI request or mutable `latest` resolution occurs. An invalid/missing bundle is not imported and
the CLI retains a standard-library plain-text fallback.

This helper is distributed under the [MIT license](LICENSE), copyright 2026 Poliklot.
Its license does not replace third-party tool licenses or license your workload's project-specific integrations.
