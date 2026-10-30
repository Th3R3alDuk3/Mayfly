<img src="logo.png" alt="Mayfly" width="120" align="right"/>

# Mayfly

[![App image](https://github.com/Th3R3alDuk3/Mayfly/actions/workflows/app.yml/badge.svg)](https://github.com/Th3R3alDuk3/Mayfly/actions/workflows/app.yml)
[![Python](https://img.shields.io/badge/python-3.14%2B-blue)](pyproject.toml)
[![License](https://img.shields.io/github/license/Th3R3alDuk3/Mayfly)](LICENSE)

> Disposable OpenChamber coding sessions in microVMs.

- **Open to anyone:** while a slot is free, everyone gets a link and a password.
- **Private:** a session belongs to the first browser that opens the link.
  Nobody else gets in, not even with the link and the password.
- **Isolated:** each session is a microVM with its own kernel running
  [OpenChamber](https://github.com/openchamber/openchamber) on
  [OpenCode](https://github.com/anomalyco/opencode), built on
  [microsandbox](https://github.com/superradcompany/microsandbox).
- **Short-lived:** it ends once the browser is gone or its lifetime is over.
- **Closed network:** a microVM reaches only what you list, and everything works
  with no internet at all.

## 🚀 Setup

Requires a Linux host with **KVM** and Docker.

1. Build the images. [Dockerfile.sandbox](docker/Dockerfile.sandbox) pins
   OpenCode and OpenChamber; `OPENCODE_VERSION` and `OPENCHAMBER_VERSION` are
   build arguments. It also lists the tools and Python libraries every session
   starts with.

   ```bash
   docker build -f docker/Dockerfile.sandbox -t mayfly-sandbox .
   docker build -f docker/Dockerfile.app -t mayfly .
   ```

2. Create the configuration, then set `PUBLIC_URL` to exactly what the browser
   shows and put the model provider into `sandbox/opencode.json`.

   ```bash
   cp .env.example .env
   cp sandbox/opencode.example.json sandbox/opencode.json
   ```

3. Start the server and load the sandbox image into it. The volume keeps loaded
   images across restarts.

   ```bash
   docker run -d -p 127.0.0.1:8123:8123 \
     --restart unless-stopped \
     --device /dev/kvm \
     -v mayfly-data:/root/.microsandbox \
     -v "$PWD/sandbox:/app/sandbox:ro" \
     --env-file .env \
     --name mayfly \
     mayfly
   docker save mayfly-sandbox \
     | docker exec -i mayfly uv run --no-sync msb load
   ```

4. Open `PUBLIC_URL` for a session, or connect an MCP client to
   `<PUBLIC_URL>/mcp/` (with the trailing slash).

### Prebuilt images

CI publishes both images to ghcr.io. The sandbox image is rebuilt monthly.

| Image | Use |
|---|---|
| `ghcr.io/th3r3alduk3/mayfly:latest` | Replaces the local server build |
| `ghcr.io/th3r3alduk3/mayfly-sandbox:latest` | Goes into `SANDBOX_IMAGE` |

Pull the sandbox image beforehand, as a pull on the first session can outlast
the client's timeout. `--force` fetches updates.

```bash
docker exec mayfly uv run --no-sync msb pull \
  ghcr.io/th3r3alduk3/mayfly-sandbox:latest
```

`SANDBOX_IMAGE` accepts any OCI reference. Registry credentials, plain-HTTP
registries and custom CAs go into `~/.microsandbox/config.json` in the
`mayfly-data` volume.

### Network access

Besides its package indexes, a microVM reaches what `SANDBOX_ALLOW` lists. Keep
the model endpoint in it.

| Entry | Means |
|---|---|
| `openrouter.ai` | HTTPS on port 443 |
| `*.github.com` | The same for all subdomains |
| `https://git.lan:8443` | HTTPS on another port |
| `http://llm.lan:8080` | Plain HTTP |
| `http://host.microsandbox.internal:8080` | A service on the server itself |
| `http://10.0.0.5:5432` | Any TCP protocol, by IP address |

Names carry HTTP and HTTPS only. `github.com` needs `*.github.com` and
`*.githubusercontent.com` too, or clones and raw downloads fail.

### Own package index

Sessions install from public indexes by default. For an index in the LAN such
as Nexus, or to send everything through a proxy:

| Setting | Value |
|---|---|
| `SANDBOX_INDEX_URL` | Index for `pip` and `uv` |
| `SANDBOX_NPM_REGISTRY` | Registry for `npm` |
| `SANDBOX_INSECURE_HOST` | Host name, if an index uses an own certificate or plain HTTP |
| `SANDBOX_PROXY` | `IP:port` of an HTTP proxy for all traffic |

> [!WARNING]
> Code in the sandbox can reach the indexes too, so they must not accept
> anonymous uploads.

A proxy receives all `SANDBOX_ALLOW` targets and the indexes as CONNECT by IP
address and has to allow those addresses and ports; without it nothing leaves.
Names must still resolve on the server, `host.microsandbox.internal` stays
direct, and image pulls bypass the proxy. Sandboxes then use IPv4 only. The
address must be reachable from the server, which in Docker is not `127.0.0.1`.

Build the sandbox image against the same indexes; these arguments affect the
build only. Behind a proxy, add `--build-arg HTTPS_PROXY=http://<ip>:<port>`.

```bash
docker build -f docker/Dockerfile.sandbox \
  --build-arg PIP_INDEX_URL=https://nexus.example.com/repository/pypi/simple \
  --build-arg PIP_TRUSTED_HOST=nexus.example.com \
  --build-arg NPM_CONFIG_REGISTRY=https://nexus.example.com/repository/npm/ \
  --build-arg NPM_CONFIG_STRICT_SSL=false \
  -t mayfly-sandbox .
```

For the server image, set the index in the `[tool.uv]` block of
[pyproject.toml](pyproject.toml) and run `uv lock`. The `FROM` images of both
Dockerfiles must be reachable as well, and the Debian mirror has to be as new
as the sandbox's `FROM` image, including `trixie-updates` and `trixie-security`.

## 🛠️ Sessions

| Entry | Description |
|---|---|
| `GET /` | Start a session with a one-time link; once a browser owns a session, `/` is its workspace |
| `POST /sessions` | The same for scripts |
| `create_mayfly_session` | The same as an MCP tool |

All return `url` and `password`. When all `MAX_CONCURRENT_SANDBOXES` slots are
taken, a new session gets a 503.

A link works once. Its first visitor gets a cookie that is the only way into the
session, so the link and the password are worthless to anyone else. The owner
can reopen the link from the same browser. Open it soon: a session nobody
connects to ends after `SANDBOX_IDLE_TIMEOUT`, and something that fetches links
ahead of the user, such as a chat preview, would claim it.

A browser holds one session at a time: opening another link replaces its cookie,
and the earlier session idles out. Without that cookie, every `GET /` starts a
new session, so a reload takes another slot until the unused one idles out.

A session ends `SANDBOX_IDLE_TIMEOUT` seconds after its last browser connection,
after `SANDBOX_MAX_DURATION` at the latest, or with the server.

## 🔒 Security & limits

[.env.example](.env.example) lists all settings.

- **Access:** the link is unguessable and works once, the cookie it hands out is
  HttpOnly and never reaches the sandbox, and OpenChamber's password guards the
  session on top. Nothing authenticates who starts a session, so one client can
  take all slots until they idle out. Use a rate limiting TLS reverse proxy
  outside a trusted network.
- **Isolation:** every session boots its own microVM with its own Linux kernel
  (KVM via libkrun), the restricted in-guest security profile and fixed RAM/vCPU
  caps. Plan for up to `MAX_CONCURRENT_SANDBOXES` × `SANDBOX_MEMORY` of RAM.
- **Network:** a microVM's policy allows the indexes, `files.pythonhosted.org`
  and `SANDBOX_ALLOW`, and denies everything else, name resolution included.
  HTTPS is intercepted, so an allowed host cannot be asked for another site.
- **Disk:** a microVM can write 4 GiB to its own disk (microsandbox default),
  workspace and packages included; it is removed when the session ends.
- **Files:** the browser uploads only `SANDBOX_UPLOAD_EXTENSIONS`, up to
  `SANDBOX_UPLOAD_MAX_BYTES` each.
- **OpenChamber:** runs in enterprise mode, set in the image. Providers come only
  from `sandbox/opencode.json`; tunnels, relay, speech and everything else that
  would need the internet are off. A content security policy keeps the page on
  this server, so its hardcoded calls to `models.dev` stay in the browser.
- **Agent:** [sandbox/opencode.example.json](sandbox/opencode.example.json) denies
  web and image tools and paths outside the workspace; its `policies` lock the
  provider. `sandbox/` is OpenCode's system config, so a repository cannot
  override it.
- **Telemetry:** off everywhere. OpenChamber's update check points at a dead
  address and reports nothing, OpenCode's auto-update and model fetch are off,
  and `DO_NOT_TRACK` and `OTEL_SDK_DISABLED` are set in the image.
- **Errors:** responses and tool errors never carry exception text, and logs
  carry no session secrets.

microsandbox keeps `/dev/kvm` out of the microVM. As a second layer, turn nested
virtualization off on the host (`kvm_amd` on AMD), so its code stays out of
reach if that ever changes:

```bash
echo "options kvm_intel nested=0" | sudo tee /etc/modprobe.d/kvm-nested.conf
sudo modprobe -r kvm_intel && sudo modprobe kvm_intel
```
