# Tabrion Studio

<div class="tabrion-hero" markdown>
![Tabrion Studio](assets/branding/tabrion-studio-logo.png#only-light)
![Tabrion Studio](assets/branding/tabrion-studio-logo-on-dark.png#only-dark)
</div>

Tabrion Studio is a local developer tool for SQLite files and PostgreSQL databases, built on
[Tabrion](https://github.com/peterkarlhuber/tabrion). It runs on the developer's machine, binds to a loopback address
(`127.x.y.z`) only and opens in the browser.

!!! warning "Pre-alpha"
    Studio does not connect to databases yet. It has the command-line process model, the access protection, Problem
    JSON errors and redacted logs, the configuration files, profiles and project trust, saved passwords, the
    connection manager and the settings, the app shell in German and English, and the command palette. Nothing is
    released or published; distribution is by Git SHA pin.

- [User guide](guide/index.md): installing, starting, connections, settings and troubleshooting.
- [Process model](architecture/process-model.md): how `tabrion-studio` starts, reuses and stops its server.
- [Access protection](architecture/access-protection.md) and the
  [app shell and design system](architecture/frontend.md).
- [Building and testing](development/building.md) and [CI and supply chain](development/ci.md).
- [Architecture decisions](adr/README.md) and [open work](TODO.md).

The names "Tabrion" and "Tabrion Studio" and the logos are © Peter Karlhuber and not covered by the MIT license
(`TRADEMARKS.md` in the repository).
