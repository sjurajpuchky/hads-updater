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
- preview and validate `version`, `minimum_version` (or `minimal_version`) and `release_notes` loaded from the package's single `manifest.json`
- fill only the metadata that is not part of the package manifest: `release_date`, `title`, `summary`
- mark the release as mandatory

The preview is informational. On publication the backend reads and validates the uploaded ZIP again and uses the manifest values as the only source for the version, minimum version and release notes. A text `release_notes` value is normalized to the `important` category; a structured object may use `new`, `improved`, `fixed`, `security` and `important`.

### Nginx upload limit

The preview and publication endpoints upload the complete ZIP. Nginx defaults to a
1 MB request limit, which rejects normal HADS packages with `413 Request Entity Too
Large` before FastAPI receives them. The virtual host serving this application must
therefore contain, inside its `server` block:

```nginx
client_max_body_size 512m;
```

After changing the virtual host, validate and reload nginx. The browser UI detects
non-JSON proxy responses and reports this limit explicitly instead of displaying a
JSON parser error.

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
