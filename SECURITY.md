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
`read → validate → conflict check → private byte backup → mutate → verify`.

- Private backups are written mode `0600` under
  `~/.config/cen-harness-hud/backups/` (`0700` dirs); retired drift archives
  keep the same `0700`/`0600` permissions.
- Foreign customizations abort the install before any mutation (no `--force`).

**Failed install:** the exact pre-attempt bytes and file mode are restored
from the private backup.

**Schema-2 clean uninstall (v0.3.1 and later):** each CEN-owned semantic
surface is independently checked against its recorded after-state. If
unchanged, only that surface is inversed; if user-modified, it is NOT
overwritten and drift evidence is retained. Unrelated changes elsewhere in
the same file are preserved and do not block a safe inverse.

**Schema-1 legacy installs** keep the conservative behavior: backups are
restored only when the whole file still matches the recorded post-install
hash; on mismatch the manifest is retired to a private `0600` archive (with
the retained backups) rather than left claiming an active install.

## Privacy at rest

Personal-but-non-secret runtime state is stored with restrictive modes:

| Location | Mode |
|---|---|
| runtime state root | `0700` |
| files containing personal state | `0600` |

The installed `cen-hud doctor` verifies these permissions. See
[PRIVACY.md](PRIVACY.md) for exactly what is stored.
