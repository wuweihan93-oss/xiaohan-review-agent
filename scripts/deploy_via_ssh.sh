#!/usr/bin/env bash
# GitHub Runner 只传输部署配置；服务器从镜像仓库拉取镜像，任务结束后删除临时私钥副本。
set -Eeuo pipefail
umask 077

die() { printf '部署失败：%s\n' "$*" >&2; exit 1; }
for name in SSH_HOST SSH_USER SSH_PRIVATE_KEY SSH_KNOWN_HOSTS REVISION; do
  [[ -n "${!name:-}" ]] || die "缺少配置 $name"
done

SSH_PORT=${SSH_PORT:-22}
DEPLOY_PATH=${DEPLOY_PATH:-/home/ubuntu/apps/xiaohan-review-agent}
[[ "$SSH_HOST" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]] || die 'SSH_HOST 格式不正确'
[[ "$SSH_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] || die 'SSH_USER 格式不正确'
[[ "$SSH_PORT" =~ ^[0-9]{1,5}$ ]] || die 'SSH_PORT 必须是端口号'
(( 10#$SSH_PORT >= 1 && 10#$SSH_PORT <= 65535 )) || die 'SSH_PORT 超出范围'
[[ "$DEPLOY_PATH" == /* ]] || die 'DEPLOY_PATH 必须是服务器绝对路径'
[[ "$REVISION" =~ ^[0-9a-f]{40}$ ]] || die '提交版本格式不正确'

archive=$(realpath "${1:?请指定部署包}")
[[ -f "$archive" && -f "$archive.sha256" ]] || die '部署包或校验文件不存在'
archive_name=$(basename "$archive")
[[ "$archive_name" == xiaohan-release.tar.gz ]] || die '部署包名称不正确'

ssh_directory=$(mktemp -d)
trap 'rm -rf -- "$ssh_directory"' EXIT
printf '%s\n' "$SSH_PRIVATE_KEY" | tr -d '\r' > "$ssh_directory/key"
printf '%s\n' "$SSH_KNOWN_HOSTS" | tr -d '\r' > "$ssh_directory/known_hosts"
ssh-keygen -y -P '' -f "$ssh_directory/key" > /dev/null || die '部署私钥格式不正确或设置了口令'
host_lookup=$SSH_HOST
if [[ "$SSH_PORT" != 22 ]]; then host_lookup="[$SSH_HOST]:$SSH_PORT"; fi
ssh-keygen -F "$host_lookup" -f "$ssh_directory/known_hosts" > /dev/null || die 'SSH_KNOWN_HOSTS 不包含当前服务器和端口'

options=(-i "$ssh_directory/key" -o BatchMode=yes -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$ssh_directory/known_hosts"
  -o ConnectTimeout=15 -o ServerAliveInterval=20 -o ServerAliveCountMax=6)
target="$SSH_USER@$SSH_HOST"
remote_directory=$(ssh "${options[@]}" -p "$SSH_PORT" "$target" "mktemp -d /tmp/xiaohan-release.$REVISION.XXXXXX")
[[ "$remote_directory" =~ ^/tmp/xiaohan-release\.[0-9a-f]{40}\.[a-zA-Z0-9]+$ ]] || die '服务器返回的临时目录不正确'

printf '正在传输部署配置包（不含 Docker 镜像），大小：%s 字节\n' "$(wc -c < "$archive")"
scp "${options[@]}" -P "$SSH_PORT" "$archive" "$archive.sha256" "$target:$remote_directory/"
printf '配置已传输，开始在服务器拉取镜像并部署……\n'
printf -v remote_command 'cd %q && sha256sum -c %q && tar -xzf %q && test "$(cat REVISION)" = %q && bash ./apply-release.sh %q %q' \
  "$remote_directory" "$archive_name.sha256" "$archive_name" "$REVISION" "$DEPLOY_PATH" "$remote_directory"
ssh "${options[@]}" -p "$SSH_PORT" "$target" "$remote_command"

printf -v cleanup_command 'rm -rf -- %q' "$remote_directory"
ssh "${options[@]}" -p "$SSH_PORT" "$target" "$cleanup_command" || printf '部署已完成，服务器临时包未能自动清理：%s\n' "$remote_directory" >&2
