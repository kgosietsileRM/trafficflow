# FlowTraffic OPS camera wall

A stand-alone traffic operations console built around the camera links in
`examples/assets/link.json`. The wall lists all configured feeds and features
one selected camera at a time; selecting a camera and starting monitoring opens
that feed for live lane analytics. Other YouTube feeds remain still previews
until selected.

The operations workspace retains per-lane occupancy, queue and relative-speed
telemetry, incident detection, response advisories, configurable policy
thresholds, replay, session history, and JSON report export. Live sessions run
until stopped and retain a rolling replay window. Recorded sources finish after
the configured sample count.

The example uses a separate virtual environment and installs
`spe-engine==0.2.1` from PyPI together with Flask and `yt-dlp` (used to resolve
YouTube camera links). It does not import from `../../src`.

## Run

```powershell
.\run.ps1
```

Open <http://127.0.0.1:5002>. Camera sources are read from `link.json`; the
first configured live source is selected by default. Choose a lane preset and
press **Start monitoring** to start analysis. Use **Stop** to end a live
session and save its report.

To recreate or migrate an existing environment to the PyPI SDK install, run
`.\bootstrap.ps1 -Reinstall`.

Outputs are stored under `outputs/sessions/<id>/`:

- `frame-*.jpg` — annotated frames for live display and replay
- `session.json` — persisted session telemetry
- `report.json` — incident, lane, advisory, and source report

## API

```powershell
$body = '{"source":"cam1","preset":"boulevard","frames":120,"fps":5,"mode":"local"}'
$id = (Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5002/api/sessions -ContentType application/json -Body $body).id
Invoke-RestMethod http://127.0.0.1:5002/api/sessions/$id
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5002/api/sessions/$id/cancel
```

Server mode requires the SPE engine at `http://127.0.0.1:5000` (configurable in
the form).
