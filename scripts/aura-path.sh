# Resolve an Aura v0.2.0 binary. The only stdout is the executable path.
# Progress and checksum lines go to stderr so command substitution stays clean.
# Same pattern as cybrid-systems/aura-runner scripts/aura-path.sh.

install_aura() {
  local root="$1"
  local version="${2:-0.2.0}"
  local force="${AURA_FORCE_TOOLS:-}"

  if [[ -z "$force" ]] && command -v aura >/dev/null 2>&1; then
    local v
    v="$(aura --version 2>/dev/null || true)"
    if [[ "$v" == *"0.2.0"* ]]; then
      command -v aura
      return 0
    fi
  fi

  local dest="$root/.tools/aura/bin/aura"
  if [[ -x "$dest" ]]; then
    printf '%s\n' "$dest"
    return 0
  fi

  local arch tarball url tmp
  arch="$(uname -m)"
  case "$arch" in
    x86_64) arch="x86_64" ;;
    aarch64|arm64) arch="arm64" ;;
    *) echo "unsupported architecture: $(uname -m)" >&2; return 1 ;;
  esac
  tarball="aura-${version}-linux-${arch}.tar.gz"
  url="https://github.com/cybrid-systems/aura/releases/download/v${version}/${tarball}"
  tmp="${TMPDIR:-/tmp}"
  echo "installing Aura v${version} (${arch}) from the official release" >&2

  if ! curl -fsSL -o "${tmp}/${tarball}" "$url"; then
    echo "download failed: $url" >&2
    return 1
  fi
  if ! curl -fsSL -o "${tmp}/${tarball}.sha256" "${url}.sha256"; then
    echo "checksum download failed: ${url}.sha256" >&2
    return 1
  fi
  if ! (cd "$tmp" && sha256sum -c "${tarball}.sha256") >&2; then
    echo "checksum failed for $tarball" >&2
    return 1
  fi

  rm -rf "$root/.tools/aura-src"
  mkdir -p "$root/.tools/aura-src"
  if ! tar -xzf "${tmp}/${tarball}" -C "$root/.tools/aura-src" --strip-components=1; then
    echo "extract failed: $tarball" >&2
    return 1
  fi
  rm -f "$root/.tools/aura"
  ln -s "$root/.tools/aura-src" "$root/.tools/aura"
  if [[ ! -x "$dest" ]]; then
    echo "install did not produce an executable at $dest" >&2
    return 1
  fi
  echo "Aura ready: $dest" >&2
  printf '%s\n' "$dest"
}
