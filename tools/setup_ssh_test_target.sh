#!/usr/bin/env bash
# 用 Docker 在本地起一台「真实 Linux 目标机」，用来验证 AI 堡垒机的 SSH 纳管能力。
#
#   ./tools/setup_ssh_test_target.sh          # 创建并启动（幂等，重复执行会先清理）
#   ./tools/setup_ssh_test_target.sh --down   # 删除测试目标机
#
# 创建完成后，在「资产管理」里这样填：
#   执行器：ssh        主机：127.0.0.1     端口：2222
#   SSH 用户：root     认证方式：私钥       私钥路径：/tmp/aib_ssh_key（脚本会生成）
# 或者用口令方式验证：SSH 用户 opsadm，口令 ops123456（需要 pip install paramiko）
set -e

NAME=aib-ssh-target
KEY=/tmp/aib_ssh_key

if [ "$1" = "--down" ]; then
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  echo "已删除测试目标机 $NAME"
  exit 0
fi

docker rm -f "$NAME" >/dev/null 2>&1 || true

if [ ! -f "$KEY" ]; then
  ssh-keygen -t ed25519 -f "$KEY" -N "" -q
  echo "已生成测试私钥：$KEY"
fi
PUB="$(cat "$KEY.pub")"

docker run -d --name "$NAME" -p 2222:22 ubuntu:22.04 sleep infinity >/dev/null
echo "容器已启动，正在安装 sshd / procps / iproute2 …"
docker exec "$NAME" bash -lc "
  apt-get update -qq &&
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openssh-server procps iproute2 curl >/dev/null 2>&1
  mkdir -p /run/sshd /root/.ssh
  echo '$PUB' > /root/.ssh/authorized_keys
  chmod 700 /root/.ssh && chmod 600 /root/.ssh/authorized_keys
  useradd -m -s /bin/bash opsadm 2>/dev/null || true
  echo 'opsadm:ops123456' | chpasswd
  mkdir -p /home/opsadm/.ssh && echo '$PUB' > /home/opsadm/.ssh/authorized_keys
  chown -R opsadm:opsadm /home/opsadm/.ssh && chmod 700 /home/opsadm/.ssh && chmod 600 /home/opsadm/.ssh/authorized_keys
  sed -i 's/^#*PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
  sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
  /usr/sbin/sshd
"

echo -n "等待 SSH 就绪"
for _ in $(seq 1 20); do
  if ssh -i "$KEY" -p 2222 -o StrictHostKeyChecking=no -o BatchMode=yes -o ConnectTimeout=3 root@127.0.0.1 uptime >/dev/null 2>&1; then
    echo " —— 就绪"
    ssh -i "$KEY" -p 2222 -o StrictHostKeyChecking=no -o BatchMode=yes root@127.0.0.1 'uptime && nproc && free -m | head -2'
    exit 0
  fi
  echo -n "."
  sleep 2
done

echo " SSH 未在预期时间内就绪，请检查容器日志：docker logs $NAME"
exit 1

