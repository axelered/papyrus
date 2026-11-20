# Helm Chart

A Helm chart that deploys Papyrus on Kubernetes. It replaces the hand-written manifest in `chart/deploy.nogit.yml`
and is meant for customers and for our own clusters alike. Stage-specific values stay gitignored.

## Goals

- One `helm install` gives a production-safe deployment: non-root, probes, graceful drain, writable `/tmp`, pinned
  worker count, secrets kept out of ConfigMaps.
- The chart never needs a change when the server gains a config key.
- Defaults follow `helm create` conventions so the values file looks familiar.

## Decisions

| Concern        | Choice                                                                                                  |
|----------------|---------------------------------------------------------------------------------------------------------|
| Location       | `Chart.yaml` directly in `chart/`, chart name `papyrus`                                                 |
| Audience       | Public product chart, no cluster-specific defaults (no ingress class, issuer or hosts)                  |
| Config         | Free-form `config:` map rendered verbatim with `toYaml` into the ConfigMap; the server validates keys  |
| API key        | `existingSecret` → `value` → generated with `randAlphaNum` and reused through `lookup`                  |
| Other secrets  | Plain Kubernetes `env` / `envFrom` passthrough, referenced from `config` as `${VAR}`                    |
| Versioning     | `Chart.yaml` commits `version: 0.0.0` / `appVersion: "0.0.0"`, stamped from the git tag like `server/version.py` |
| Image tag      | `image.tag: ""` falls back to `.Chart.AppVersion`                                                       |
| Install        | From the checkout until publishing lands: `helm install papyrus ./chart --set image.tag=X.Y.Z`          |

## Layout

```
chart/
  Chart.yaml
  values.yaml              human-readable template, commented like config.example.yml
  .helmignore              must contain *.nogit*
  templates/
    _helpers.tpl           fullname, labels, selector labels, image
    configmap.yaml
    secret.yaml
    deployment.yaml
    service.yaml
    ingress.yaml
    pdb.yaml
    networkpolicy.yaml
    NOTES.txt
  values.stage.nogit.yml   our titan stage values (gitignored)
```

- `chart/` also holds gitignored `*.nogit*` files with live credentials. `helm package` ignores `.gitignore`, so
  `.helmignore` must exclude `*.nogit*` or they ship inside the package.

## Resources

| Resource            | Rendered when                     | Notes                                                       |
|---------------------|-----------------------------------|-------------------------------------------------------------|
| ConfigMap           | always                            | `config.yml` key, mounted read-only at `/etc/papyrus`       |
| Secret              | `apiKey.existingSecret` is empty  | key `api-key`, see API key                                  |
| Deployment          | always                            | see Deployment                                              |
| Service             | always                            | ClusterIP, port 80 → named port `http` (8000)               |
| Ingress             | `ingress.enabled` (default false) | `helm create` shape: `className`, `annotations`, `hosts`, `tls` |
| PodDisruptionBudget | `replicaCount > 1`                | `maxUnavailable: 1`                                         |
| NetworkPolicy       | `networkPolicy.enabled` (default false) | see NetworkPolicy                                     |

No ServiceAccount is created. Papyrus never calls the Kubernetes API.

## Values

```yaml
replicaCount: 2

image:
  repository: ghcr.io/axelered/papyrus
  tag: ""                      # defaults to .Chart.AppVersion
  pullPolicy: IfNotPresent
imagePullSecrets: []

apiKey:
  existingSecret: ""           # Secret you manage, read from key `api-key`
  value: ""                    # comma-separated keys; empty and no existingSecret = generated

config:                        # same shape as config.example.yml, rendered verbatim
  workers:
    processes: 2               # keep equal to resources.limits.cpu, never "auto" (counts node CPUs)

env: []                        # Kubernetes env list, passed through
envFrom: []                    # Kubernetes envFrom list, passed through

resources:
  requests: { cpu: '1', memory: 1Gi }
  limits: { cpu: '2', memory: 2Gi }

tmpSizeLimit: 2Gi              # emptyDir at /tmp: size for max_queue × max_file_size worst case

ingress:
  enabled: false
  className: ""
  annotations: {}              # body size >= max_file_size, read timeout > deadline (controller-specific)
  hosts: []
  tls: []

networkPolicy:
  enabled: false
  extraEgress: []              # NetworkPolicy egress rules, e.g. in-cluster storage used by connectors

podAnnotations: {}
podLabels: {}
nodeSelector: {}
tolerations: []
affinity: {}
```

- `values.yaml` comments carry the deployment gotchas: `processes` vs CPU limit, Ingress body size and timeout,
  `existingSecret` under GitOps, NetworkPolicy and in-cluster connectors, `image.tag` for installs from source.
- Logging, timing and auth switches (`PAPYRUS_LOG_FORMAT`, `PAPYRUS_LOG_LEVEL`, …) go through `env`. There are no
  dedicated values for them, and none for `PAPYRUS_AUTH_DISABLED` (development only).

## Deployment

Hardcoded, not values, because the image depends on them:

- Pod: `runAsNonRoot`, uid/gid `10001`, `automountServiceAccountToken: false`, `terminationGracePeriodSeconds: 40`
  (the server drains for 30).
- Container: `allowPrivilegeEscalation: false`, drop `ALL` capabilities, port `http` 8000.
- `PAPYRUS_API_KEY` from the chart Secret or `existingSecret`, key `api-key`; then `env` and `envFrom`.
- Probes: readiness `GET /ready` every 5s; liveness `GET /health`, initial delay 15s, period 30s, timeout 3s.
- Volumes: ConfigMap at `/etc/papyrus` (read-only), `emptyDir` at `/tmp` with `tmpSizeLimit`.
- `checksum/config` annotation on the pod template, so a config change rolls the pods.
- todo: `readOnlyRootFilesystem: true` once verified that nothing writes outside `/tmp`.

## API Key

Precedence: `apiKey.existingSecret` → `apiKey.value` → generated.

- The chart Secret stores `api-key`. When `value` is empty, it reuses the key of an existing release Secret found
  with `lookup`, otherwise it generates `key_<randAlphaNum 32>`.
- The Secret carries `helm.sh/resource-policy: keep`, so uninstall keeps it and a reinstall reuses the key.
- `lookup` returns nothing under `helm template`, which Argo CD uses: the key would rotate on every sync. GitOps
  users must set `existingSecret`. Flux and `helm install/upgrade` are fine.
- `NOTES.txt` prints the `kubectl get secret … | base64 -d` command to read the key, plus a port-forward example.

## NetworkPolicy

Opt-in defense in depth for the open issues in `AGENTS.md`: DNS rebinding in the SSRF guard, LibreOffice egress,
Chromium without its sandbox.

- Ingress: allow TCP 8000 only.
- Egress: DNS (UDP/TCP 53) to `kube-system` pods labelled `k8s-app: kube-dns`; everything else to `0.0.0.0/0` and
  `::/0` except private, loopback, link-local (cloud metadata `169.254.169.254`) and CGNAT ranges
  (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.0/8`, `169.254.0.0/16`, `100.64.0.0/10`,
  `fc00::/7`, `fe80::/10`, `::1/128`).
- `extraEgress` rules are appended for connectors that reach in-cluster or private storage.
- Works only on CNIs that enforce policies. Off by default because it breaks private connectors on first install.

## Stage Values

`chart/deploy.nogit.yml` becomes `chart/values.stage.nogit.yml`: titan host with Traefik and the cert-manager
`letsencrypt` issuer, `existingSecret` for the API key, `envFrom` a Secret with the rustfs credentials referenced
as `${S3_KEY}` / `${S3_SECRET}` in the `demo-s3` connector. The old manifest is deleted once stage runs on the chart.

## Out of Scope

- todo: `helm lint` and `helm template` (defaults plus a fixture with ingress, `existingSecret`, networkPolicy) in
  the check job.
- todo: package and push to `oci://ghcr.io/axelered/charts` on release after the image push, version stamped with
  `--version` / `--app-version`, install line in the release notes. Deferred while the GHCR packages are private.
- todo: `docs/kubernetes.mdx` guide on docs.axelered.com, together with the OCI publish.
- HPA (load is shed by `max_queue` 503s), ServiceMonitor (no metrics yet), separate chart versioning (lockstep with
  the app; splitting later is non-breaking), classic Helm repo (`index.yaml`).
- `workers.processes: auto` reading cgroup CPU limits is a server fix, tracked in Future Issues.
