# HADS Updater

Minimal Python service for authenticated upload and publishing of signed HADS releases.

## Endpoints

- `GET /health` returns basic service status.

## Quick start

```bash
cd /home/jpuchky/.openclaw/workspace/hads-updater
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
mkdir -p keys releases
# configure HADS_RELEASE_* values in .env and place the private key at HADS_RELEASE_PRIVATE_KEY
./run.sh --reload
```

If your shell prompt still shows `(.venv)` but commands use `/usr/bin/python`, the shell kept a stale virtualenv state. In that case either run `deactivate` and `source .venv/bin/activate` again, or skip shell activation entirely and use `./run.sh`.

## HADS release admin

Open `http://127.0.0.1:8000/` and sign in with `HADS_RELEASE_ADMIN_USERNAME` and `HADS_RELEASE_ADMIN_PASSWORD`.

The admin UI lets you:

- upload a `.zip` package
- fill `version`, `minimum_version`, `release_date`, `title`, `summary`
- paste release notes JSON
- mark the release as mandatory

On submit, the backend creates:

- `HADS_RELEASE_OUTPUT_ROOT/<version>/<package>.zip`
- `HADS_RELEASE_OUTPUT_ROOT/<version>/release.json`
- `HADS_RELEASE_OUTPUT_ROOT/<version>/release.json.sig`
- `HADS_RELEASE_OUTPUT_ROOT/releases.json`

Public update endpoints:

- `GET /release.json` returns the signed metadata of the current release used by HADS
- `GET /release.json.sig` returns its detached RSA/SHA-256 signature
- `GET /releases.json` returns the release index with all releases, links to each release, and the latest one marked as `current: true`
- `GET /release-files/<version>/release.json` returns metadata for a specific release
- `GET /release-files/<version>/release.json.sig` returns the signature
- `GET /release-files/<version>/<package>.zip` returns the uploaded package

If signing or file writing fails, the target release folder is deleted so a partial release does not remain on disk.

## Notes

- For production, change the default admin credentials and session secret before exposing the app anywhere.
