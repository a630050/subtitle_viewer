# Fly.io Backup - subtitle-viewer-flyio v35

Backup date: 2026-05-22

Current deployed app:

- App: `subtitle-viewer-flyio`
- Hostname: `subtitle-viewer-flyio.fly.dev`
- Current machine: `784ed2eced30d8`
- Current machine version: `35`
- Region: `sin`
- State: `started`
- Health checks: `1 total, 1 passing`
- Last updated: `2026-05-21T02:06:38Z`

Current image:

- Registry: `registry.fly.io`
- Repository: `subtitle-viewer-flyio`
- Tag: `deployment-01KP85WDSFJTSV36HC5PX1D55M`
- Digest: `sha256:ff50aa74d8d6783073e4edfa0162ad60542dec7ee029ffbc9fbea9093f3b1e2c`
- Full image reference: `registry.fly.io/subtitle-viewer-flyio:deployment-01KP85WDSFJTSV36HC5PX1D55M`

Rollback command:

```powershell
flyctl releases rollback v35 -a subtitle-viewer-flyio
```

Notes:

- This backup records the deployed release and image identity for rollback.
- Fly.io keeps release history, so version `v35` can be restored with the rollback command above as long as the release/image remains available in Fly.io.
- If `subtitle-viewer-flyio-v35-image.tar` exists in this folder, it is an offline Docker image backup of the same deployed image.
