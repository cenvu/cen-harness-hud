# Security Policy

## Scope

CEN Harness HUD is a local developer tool that reads harness-provided status
information and renders it locally. Out of scope: vulnerabilities in the
harnesses themselves (AGY, Codex, Pi, Herdr), in macOS, or in your accounts.

To report a security issue, please open a private security advisory via the
project's repository once public contact channels exist. (No email reporting
channel is published at this time.)

## Threat model

CEN HUD runs entirely as your local user, with no elevated privileges and no
`sudo`.

**Installer boundary:**

- never opens credential stores (`auth.json`, keychains, token caches,
  cookies)
- never requires or performs credential migration
- patches only the exact config keys it owns; foreign customizations abort
  the install before any mutation

**Runtime boundary:**

- the AGY/Herdr integrations read harness-provided status payloads and CEN's
  own state files only; they implement no network client
- the Codex integration invokes the external Codex app-server over stdio; it
  implements no HTTP client of its own (whether the app-server itself uses
  the network is Codex's behavior, not CEN HUD's)
- the optional Pi balance extension is an explicit network client: it obtains
  your existing DeepSeek API key through the supported Pi/environment
  credential surface, uses it solely as Bearer auth for DeepSeek's official
  balance request, and does not persist, log or display the raw secret
- external harnesses may perform their own network activity according to
  their own behavior and configuration — that activity belongs to those
  tools, not to CEN HUD

There is no CEN-operated network service and no analytics anywhere in CEN
HUD.

## Configuration mutation safety

Every foreign config change follows:
`read → validate → conflict check → private backup → mutate → verify`.

- Backups are written mode `0600` under `~/.config/cen-harness-hud/backups/`.
- Foreign customizations abort the install before any mutation (no `--force`).
- Uninstall restores backups only when the file still matches the recorded
  post-install hash; user edits are warned about, never clobbered.

## Privacy at rest

Personal-but-non-secret runtime state is stored with restrictive modes:

| Location | Mode |
|---|---|
| runtime state root | `0700` |
| files containing personal state | `0600` |

The installed `cen-hud doctor` verifies these permissions. See
[PRIVACY.md](PRIVACY.md) for exactly what is stored.
