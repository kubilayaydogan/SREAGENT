#!/usr/bin/env bash
# APP_VM (Rocky Linux 9) — Docker Engine + Compose plugin + chrony + firewalld kuralları.
# Kullanım: sudo ./install-docker.sh <yönetim-CIDR>   ör. sudo ./install-docker.sh 10.0.0.0/24
# SELinux ve firewalld KAPATILMAZ. Uygulama portları yalnız yönetim ağına açılır.
set -euo pipefail
ADMIN_CIDR="${1:?yönetim ağı CIDR'ı verin (ör. 10.0.0.0/24)}"
[[ $EUID -eq 0 ]] || { echo "root olarak çalıştırın"; exit 1; }

dnf install -y dnf-plugins-core chrony
systemctl enable --now chronyd
dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo
dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
# Docker log rotasyonu ve canlı geri yükleme
install -d /etc/docker
cat > /etc/docker/daemon.json <<'JSON'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "20m", "max-file": "3" },
  "live-restore": true
}
JSON
systemctl enable --now docker

systemctl enable --now firewalld
ZONE="$(firewall-cmd --get-default-zone)"
for p in 8080 8081 8082 8083 8084; do
  rule="rule family=ipv4 source address=${ADMIN_CIDR} port port=${p} protocol=tcp accept"
  firewall-cmd --permanent --zone="$ZONE" --query-rich-rule="$rule" >/dev/null 2>&1 || firewall-cmd --permanent --zone="$ZONE" --add-rich-rule="$rule"
done
firewall-cmd --reload
docker version --format 'docker {{.Server.Version}}'; docker compose version
getenforce
echo "Not: Docker yayınlanan portlar için iptables kuralı ekler; firewalld ile birlikte portları"
echo "yalnız APP_BIND_IP'ye bağlayın (.env) ve 0.0.0.0'a yayınlamayın."
