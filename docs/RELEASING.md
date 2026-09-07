# Releasing Patcha

Patcha ships as a signed, notarized `.dmg` from GitHub Releases. It is not
distributed through the Mac App Store: the app needs Accessibility and Screen
Recording, which are not available to sandboxed apps.

Distributing outside the App Store still requires an Apple Developer Program
membership and notarization. An unnotarized download is quarantined by
Gatekeeper, and since macOS 15 the Control-click "Open" bypass no longer works
— users must approve the app in System Settings > Privacy & Security.

## One-time setup

### 1. Apple Developer Program

Enroll at <https://developer.apple.com/programs/> ($99/year). The team ID
already configured in the Xcode project is `Z7JCWW3N99`.

### 2. Developer ID Application certificate

In Xcode: Settings > Accounts > Manage Certificates > + > Developer ID
Application. Confirm it landed in the keychain:

```sh
security find-identity -v -p codesigning
```

You want a line reading `Developer ID Application: NAME (TEAMID)`. An
`Apple Development` certificate is not sufficient for distribution.

### 3. notarytool credentials

Create an App Store Connect API key (Users and Access > Integrations > App Store
Connect API) and store it as a keychain profile:

```sh
xcrun notarytool store-credentials "patcha-notary" \
    --key ~/private_keys/AuthKey_XXXXXXXX.p8 \
    --key-id XXXXXXXX \
    --issuer XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX
```

## Cutting a release

```sh
export PATCHA_SIGN_IDENTITY="Developer ID Application: NAME (TEAMID)"
export PATCHA_NOTARY_PROFILE="patcha-notary"
./build.sh
```

`build.sh` builds the app unsigned, stages the daemon, helper binaries and
models into `Contents/Resources`, then signs inside-out (nested executables
first, app bundle last), notarizes, staples, and builds a signed and stapled
`.dmg` at `dist/patcha-<version>.dmg`.

Without `PATCHA_SIGN_IDENTITY` the build falls back to an ad-hoc signature.
That is fine for local testing and useless for distribution.

## Models

The MobileCLIP visual pre-filter (~68 MB) is bundled in the app. The FastVLM
captioner (~810 MB) is not — the daemon downloads it to
`~/.patcha/models/fastvlm` on first run, because the signed app bundle is
read-only and a bundled copy would mean two multi-gigabyte notarization uploads
per release.

Gist captioning stays off until the fetch completes; everything else works
immediately. Progress is published to `~/.patcha/model_download.json`. To
pre-seed a machine or recover from a failed fetch:

```sh
patcha fetch-models          # --force re-downloads
```

Set `ENABLE_MODEL_AUTO_DOWNLOAD=false` to opt out of the automatic fetch.

## Verifying before you publish

```sh
spctl -a -vvv -t install dist/patcha-<version>.dmg
xcrun stapler validate dist/patcha-<version>.dmg
```

`spctl` should report `source=Notarized Developer ID`. The real test is
downloading the `.dmg` on a machine that has never built Patcha — quarantine is
applied on download, so a locally built file will not reproduce a user's first
run.

## Gotchas

- **Sign after staging, never before.** Copying anything into the bundle after
  signing invalidates the seal. `build_app.sh` deliberately builds unsigned.
- **`ENABLE_APP_SANDBOX` must stay `NO`.** The sandbox blocks spawning the
  daemon, the Accessibility API, and writes to `~/.patcha`.
- **Usage description strings are mandatory under hardened runtime.** A missing
  `NSAppleEventsUsageDescription` or `NSScreenCaptureUsageDescription` crashes
  the process that triggers the prompt rather than showing a denial.
- **TCC grants are keyed to the signing identity.** Switching identities resets
  a user's Accessibility and Screen Recording approvals.
