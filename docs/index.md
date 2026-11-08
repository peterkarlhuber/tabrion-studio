# Tabrion Studio

<div class="tabrion-hero" markdown>
![Tabrion Studio](assets/branding/tabrion-studio-logo.png#only-light)
![Tabrion Studio](assets/branding/tabrion-studio-logo-on-dark.png#only-dark)
</div>

Tabrion Studio is a local developer tool for SQLite files and PostgreSQL databases, built on
[Tabrion](https://github.com/peterkarlhuber/tabrion). It runs on the developer's machine, binds to `127.0.0.1` only
and opens in the browser.

!!! warning "Pre-alpha"
    This is the project skeleton (work packages STU-001, STU-002, STU-003, STU-005, STU-018, STU-031, STU-051): the
    command-line process model, CI, the license and supply-chain checks, the access protection and the empty app
    shell. Nothing is released or published.

- [Process model](architecture/process-model.md): how `tabrion-studio` starts, reuses and stops its server.
- [Access protection](architecture/access-protection.md) and the
  [app shell and design system](architecture/frontend.md).
- [Building and testing](development/building.md) and [CI and supply chain](development/ci.md).
- [Architecture decisions](adr/README.md) and [open work](TODO.md).

The names "Tabrion" and "Tabrion Studio" and the logos are © Peter Karlhuber and not covered by the MIT license
(`TRADEMARKS.md` in the repository).
