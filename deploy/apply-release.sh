#!/usr/bin/env bash
# 在已有项目目录应用镜像版本；保留 .env、runtime 和 PostgreSQL 数据。
set -Eeuo pipefail
umask 077

die() { printf '部署失败：%s\n' "$*" >&2; exit 1; }
[[ $# == 2 ]] || die '需要提供项目目录和已解压的部署包目录'
project_directory=$(realpath "$1")
release_directory=$(realpath "$2")
[[ -f "$project_directory/.env" && -f "$project_directory/compose.yaml" ]] || die '请先完成首次部署，项目目录需要已有 .env 和 compose.yaml'
for name in compose.yaml REVISION BACKEND_IMAGE FRONTEND_IMAGE; do
  [[ -f "$release_directory/$name" ]] || die "部署包缺少 $name"
done
revision=$(cat "$release_directory/REVISION")
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || die '部署包中的提交版本格式不正确'
backend_image=$(cat "$release_directory/BACKEND_IMAGE")
frontend_image=$(cat "$release_directory/FRONTEND_IMAGE")
# 只接受固定摘要的仓库镜像，避免标签变化后部署到未经本次构建验证的内容。
image_pattern='^[a-z0-9][a-z0-9.-]*(:[0-9]{1,5})?/[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$'
[[ "$backend_image" =~ $image_pattern ]] || die '后端镜像地址格式不正确，需要完整仓库地址和 sha256 摘要'
[[ "$frontend_image" =~ $image_pattern ]] || die '前端镜像地址格式不正确，需要完整仓库地址和 sha256 摘要'
[[ $(uname -m) == x86_64 ]] || die '当前流水线构建的是 amd64 镜像，服务器需要 x86_64 架构'

state_directory="$project_directory/.deploy"
mkdir -p "$state_directory"
exec 9> "$state_directory/deploy.lock"
flock -w 300 9 || die '另一次部署仍在运行，请稍后重试'

override="$project_directory/compose.override.yaml"
marker='# 此文件由小晗 CI/CD 自动生成。'
if [[ -f "$override" ]] && ! head -n 1 "$override" | grep -Fxq "$marker"; then
  die '已有自定义 compose.override.yaml，请先合并配置后再启用自动部署'
fi

compose() {
  local arguments=(--project-name xiaohan-review --project-directory "$project_directory"
    --env-file "$project_directory/.env" -f "$project_directory/compose.yaml")
  if [[ -f "$override" ]]; then arguments+=(-f "$override"); fi
  docker compose "${arguments[@]}" "$@"
}

# 首次配置时在服务器执行 docker login；这里复用当前用户的仓库登录凭证。
# 两个镜像全部拉取成功后才更新容器，已存在的镜像层会由 Docker 自动复用。
printf '正在拉取后端镜像：%s\n' "$backend_image"
docker pull "$backend_image"
printf '正在拉取前端镜像：%s\n' "$frontend_image"
docker pull "$frontend_image"
docker image inspect "$backend_image" > /dev/null
docker image inspect "$frontend_image" > /dev/null
backup_directory=$(mktemp -d "$state_directory/rollback.XXXXXX")
cp "$project_directory/compose.yaml" "$backup_directory/compose.yaml"
if [[ -f "$override" ]]; then cp "$override" "$backup_directory/compose.override.yaml"; fi

rollback() {
  local status=$1
  trap - ERR INT TERM
  set +e
  printf '新版本部署失败，正在恢复上一版容器……\n' >&2
  local restored=true
  cp "$backup_directory/compose.yaml" "$project_directory/compose.yaml" || restored=false
  if [[ -f "$backup_directory/compose.override.yaml" ]]; then
    cp "$backup_directory/compose.override.yaml" "$override" || restored=false
  else
    rm -f -- "$override" || restored=false
  fi
  if [[ "$restored" == true ]] && compose up -d --no-build --pull never --wait --wait-timeout 180; then
    printf '上一版容器已恢复，本次流水线仍标记失败。\n' >&2
  else
    printf '自动恢复未完成，请在服务器检查 docker compose ps 和 logs；配置副本位于 %s\n' "$backup_directory" >&2
  fi
  exit "$status"
}

trap 'rollback $?' ERR
trap 'rollback 130' INT
trap 'rollback 143' TERM
cp "$release_directory/compose.yaml" "$project_directory/compose.yaml"
printf '%s\nservices:\n  backend:\n    image: %s\n  frontend:\n    image: %s\n' \
  "$marker" "$backend_image" "$frontend_image" > "$override"
printf '镜像已准备好，正在更新容器并等待健康检查……\n'
compose config --quiet
compose up -d --no-build --pull never --wait --wait-timeout 180

# 同时检查 Nginx 和 API 转发，不依赖服务器当前设置的对外端口。
healthy=false
for attempt in {1..10}; do
  if compose exec -T frontend wget -q -T 10 -O /dev/null http://127.0.0.1/api/health; then
    healthy=true
    break
  fi
  sleep 2
done
if [[ "$healthy" != true ]]; then
  printf 'Nginx 到后端 API 的检查未通过。\n' >&2
  rollback 1
fi
printf '%s\n' "$revision" > "$state_directory/current-revision"
trap - ERR INT TERM
printf '部署成功，当前版本：%s\n' "$revision"
