#!/usr/bin/env bash
# One-time setup of a fresh Oracle Cloud Ubuntu VM for ReviewPilot (docs/deploy.md, step 5).
# Safe to re-run: every step checks before it changes anything.
#
#   scp deploy/oracle/bootstrap.sh ubuntu@<vm-ip>:      # from your laptop (the repo is private)
#   ssh ubuntu@<vm-ip> 'bash bootstrap.sh'
#
# It installs Docker from Docker's own repository, opens ports 80/443 in the VM's
# firewall, adds swap on small VMs, turns on automatic security updates, and creates a
# read-only deploy key so the VM can clone the private repository.
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this as your normal user (ubuntu); it uses sudo where needed." >&2
  exit 1
fi
USER_NAME="$(id -un)"
step() { printf '\n==> %s\n' "$*"; }

step "System packages and automatic security updates"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates curl git unattended-upgrades iptables-persistent
echo "unattended-upgrades unattended-upgrades/enable_auto_updates boolean true" \
  | sudo debconf-set-selections
sudo dpkg-reconfigure -f noninteractive unattended-upgrades

step "Docker Engine + Compose plugin (from download.docker.com)"
if ! command -v docker >/dev/null 2>&1; then
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc
  # shellcheck disable=SC1091
  CODENAME="$(. /etc/os-release && echo "$VERSION_CODENAME")"
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $CODENAME stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update -y
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER_NAME"

step "Firewall: allow HTTP and HTTPS (Oracle's Ubuntu image rejects everything but SSH)"
# The VM has its own iptables rules on top of the cloud Security List: open both.
for rule in "tcp 80" "tcp 443" "udp 443"; do
  # shellcheck disable=SC2086  # split "tcp 80" into protocol and port on purpose
  set -- $rule
  if ! sudo iptables -C INPUT -p "$1" --dport "$2" -m state --state NEW -j ACCEPT 2>/dev/null; then
    # Insert before the catch-all REJECT, or append if the image has none.
    reject="$(sudo iptables -L INPUT --line-numbers | awk '/REJECT/ {print $1; exit}')"
    if [ -n "$reject" ]; then
      sudo iptables -I INPUT "$reject" -p "$1" --dport "$2" -m state --state NEW -j ACCEPT
    else
      sudo iptables -A INPUT -p "$1" --dport "$2" -m state --state NEW -j ACCEPT
    fi
  fi
done
sudo netfilter-persistent save

step "Swap (only on VMs with under 2 GB of RAM, e.g. the AMD micro shape)"
mem_kb="$(awk '/MemTotal/ {print $2}' /proc/meminfo)"
if [ "$mem_kb" -lt 2000000 ] && ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

step "Read-only deploy key for the private repository"
KEY="$HOME/.ssh/reviewpilot_deploy"
if [ ! -f "$KEY" ]; then
  mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
  ssh-keygen -q -t ed25519 -N "" -C "reviewpilot-vm-deploy" -f "$KEY"
fi
if ! grep -q "Host github-reviewpilot" "$HOME/.ssh/config" 2>/dev/null; then
  cat >>"$HOME/.ssh/config" <<EOF

Host github-reviewpilot
  HostName github.com
  User git
  IdentityFile $KEY
  IdentitiesOnly yes
EOF
  chmod 600 "$HOME/.ssh/config"
fi
ssh-keyscan -t ed25519 github.com 2>/dev/null >>"$HOME/.ssh/known_hosts"
sort -u -o "$HOME/.ssh/known_hosts" "$HOME/.ssh/known_hosts"

cat <<EOF

Done. Next (docs/deploy.md, step 5):
  1. Add this public key on GitHub: repo Settings → Deploy keys → Add deploy key
     (title "oracle-vm", leave "Allow write access" UNCHECKED):

     $(cat "$KEY.pub")

  2. Log out and back in (so the docker group applies), then:
     git clone github-reviewpilot:Ommadure/ai-pr-reviewer.git ~/ai-pr-reviewer
EOF
