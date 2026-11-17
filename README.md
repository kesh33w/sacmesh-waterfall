## November 17 deployment sync

Includes the deployed embedded view for sacmesh.org, adjusted band viewport, and silent embedded-mode startup. Server root routing accepts embed query parameters; framing is limited to sacmesh.org. Host-specific bind and SSH addresses remain environment-configured rather than published.

# SacMesh Waterfall

Live receive-only spectrum for **909.875 and 910.525 MHz**, with dual-channel audio, packet labels and host statistics.

Live site: https://waterfall.sacmesh.org

## Architecture

An RTL-SDR on a Raspberry Pi sends fixed-frequency IQ samples over authenticated SSH to a NAS or Linux server. The server runs FFT/audio processing and the web application. A separate Pi helper reads OpenHop observations in read-only mode and exports sanitized metadata. The web server never opens the bridge database.

At 2.4 MS/s, IQ transfer is about **38.4 Mbit/s**, or 415 GB/day of transient LAN traffic. IQ and spectrum history stay in bounded RAM; they are not recorded to disk. Use a wired link. Only one capture process can own the dongle.

## Install

1. On the Pi, install the official RTL-SDR driver appropriate to the dongle. V4 requires a compatible driver. Verify receiver access for an unprivileged account.
2. Install `pi/sdr-remote` as an executable at a fixed location. Create a dedicated SSH key on the server. Restrict its Pi `authorized_keys` entry using `from="SERVER_ADDRESS",restrict,command="/absolute/path/to/sdr-remote"`. This wrapper accepts only `iq` and `metadata`, not a shell or arbitrary tuning.
3. Pin the Pi's SSH host key using a separately verified fingerprint. Place the private key and pinned host key file in local `ssh/id_ed25519` and `ssh/known_hosts`, readable only by the service user. Never commit them.
4. For packet labels, install `pi/packet_metadata.py` and adapt the supplied systemd unit to the installed OpenHop Python environment and database location. The helper needs the compatible `openhop_core` package already used by OpenHop. The database is opened read-only. Spectrum/audio work without metadata.
5. Copy `.env.example` to `.env`, enter your Pi SSH target and service UID/GID, and create private `ssh` and `telemetry` directories owned by that UID. Start with `docker compose up -d --build waterfall`.
6. Open http://127.0.0.1:8767 on the server, or configure the bind address for your private network/reverse proxy. Do not expose the telemetry socket publicly.

The optional `public` Compose profile runs Cloudflare Tunnel using a separately provisioned `ssh/tunnel-token` file. Configure its origin before enabling it with `docker compose --profile public up -d`. No tunnel, credentials, firewall rules or DNS records are created by this repository.

## Configuration

`WATERFALL_SDR_HOST` is required, in `user@host` form. `WATERFALL_BIND` defaults to localhost and `WATERFALL_PORT` to 8767. `WATERFALL_TRUSTED_PROXIES` is a comma-separated list of exact forwarding-peer addresses allowed to supply `CF-Connecting-IP`; leave it empty unless you control the forwarding path. `WATERFALL_DOMAIN` labels telemetry.

The private Unix socket `/telemetry/telemetry.sock` serves `/v1/snapshot` for an incoming-connections monitor. It contains viewer addresses and request metadata and is mode 0600. Viewer records expire after 15 seconds without polling. The host-resource strip reports the **web/DSP server**, not the remote Pi. Customize its display label with the `data-host-label` attribute on `<body>` in `index.html` (the live deployment uses `Synology RS1221+`); an empty value uses the generic label.

The Pi metadata helper supports `WATERFALL_OPENHOP_DB` to override its database path. Its radio IDs and frequencies must match your OpenHop installation. The helper includes a narrow allowlist of 40 public hashtag channels plus the firmware-default Public channel. It uses the standard derived keys, validates candidate decryptions, and exports a channel name only for a unique valid match. A short channel-hash match alone is insufficient. Unknown or ambiguous channels stay unnamed. **Chat bodies and claimed chat sender names are never exported**, and no private-channel keys are included. Operators can review the `CHANNELS` list before enabling the helper.

## Interpret the display

- Shaded columns mark the two 62.5 kHz channels. The receiver uses 2.4 MS/s centered at 910.200 MHz and 9.9 dB gain. Power is uncalibrated dBFS.
- Audio is the received signal envelope, **not decoded speech or LoRa data**. Both channels are initially selected; autoplay may require a browser click.
- A `~` label marks conservative visual alignment to a single nearby same-channel energy burst. Ambiguous overlaps, RX reports near local TX and reports with no distinct energy stay at recorded time. This is an approximate association, not packet decoding.
- Labels come from separate radio observations, not SDR decoding. Timestamps are approximate. A nearby label does not prove that a specific burst belongs to that packet.
- Packet text and markers use CoreScope's default packet-type colors. Labels use free space on either side of the channel bands without changing event timing; overlapping rectangles are rejected and each channel has a 20-label cap. Compact labels prioritize the inferred last hop, with `?` for uncertainty and hashes for ambiguous candidates. TX uses an optional configured local bridge name. Direction remains in details, not special red triangles.
- The SDR is co-located with the bridge; overlay records come from the bridge, not SDR decoding. Burst matches and timing are estimates. Settled alignment uses a 1,024-event cache; pause preserves the displayed snapshot through resize.
- The primary label can show a last-hop **candidate**, inferred from the final flood-path hash. A question mark marks a unique match among known keys; short-hash collisions and incomplete node knowledge prevent treating it as verified RF identity. Click for evidence, ambiguity and original-source details.
- Advert names are taken from verified signed adverts. They identify the original advert source, **not necessarily the last repeater that transmitted the RF burst**. Relayed traffic cannot safely be labeled as a known last-hop transmitter without additional evidence.
- A channel label identifies a matching public-channel key, not an authenticated sender identity.

## Checks

```sh
python3 -m py_compile server.py water_telemetry.py pi/packet_metadata.py
python3 -m unittest discover -s tests -v
node tests/test_alignment.js
node tests/test_alignment_cache.js
```

Production validation included fixed-channel FFT/audio processing, remote metadata retrieval, and rejection of unsupported SSH commands. The packaged configuration substitutes environment values for deployment-specific addresses; configure and test it for your host before replacing a running installation. CPU shares are relative scheduling weights, not a hard CPU cap.

## Provenance and licensing

FFT settings/windowing were adapted from the earlier WATER project's `smf_utility_bot/sdr_assist.py`. This is the standalone SacMesh receiver, not the full WATER bot. No license grant is asserted: the source snapshot had no license, so clarify licensing before redistributing derivatives. NumPy, RTL-SDR and OpenHop retain their respective upstream licenses.

The display defaults to a −82 dBFS floor and 30 dB color range. The reversible 20–60 dB range slider changes contrast, not receiver gain. Gain changes require updating the fixed Pi capture wrapper and matching API metadata. Compare clipping and noise on your own installation; stronger displayed bursts alone do not prove improved reception sensitivity.

Optional Pi `/opt/waterfall-metadata/transmitter.json` contains only a public display name, for example `{"name":"My bridge"}`. It labels local transmissions; absent configuration shows an unknown node. Do not copy a private configuration file here. No site-specific transmitter file is included.
