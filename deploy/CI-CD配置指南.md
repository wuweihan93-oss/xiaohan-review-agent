# 小晗 GitHub Actions CI/CD 配置：国内镜像仓库

适用于已经部署的 Ubuntu x86_64 服务器：`43.139.216.149`，SSH 用户 `ubuntu`，项目目录 `/home/ubuntu/apps/xiaohan-review-agent`，页面端口 `8888`。

更新流程为：

```text
推送 main → 自动测试 → GitHub 构建镜像 → 推送国内镜像仓库
                                            ↓
Ubuntu 收到小型部署配置包 → 从仓库拉取镜像 → 更新容器 → 健康检查
```

GitHub 不再通过 SSH 传输包含完整镜像的数百 MB 压缩包。SSH 只传 Compose 配置、部署脚本、提交版本和两个镜像的内容摘要。Docker 拉取镜像时会复用服务器已有的镜像层；首次仍需下载缺少的镜像层，具体耗时取决于到仓库的网络速度。

以下以阿里云容器镜像服务 ACR 为例；腾讯云 TCR 等兼容 Docker 的仓库也可以使用，配置项含义相同。

## 1. 创建命名空间和两个镜像仓库

1. 打开 [阿里云容器镜像服务控制台](https://cr.console.aliyun.com/)。这里与百炼模型 API 是两个服务，百炼 API Key 不能作为镜像仓库密码。
2. 在实例列表选择可用的个人版实例和中国内地地域。已有企业版实例也可以使用。
3. 进入 **仓库管理 → 命名空间**，创建一个命名空间，例如 `xiaohan-study`；若名称已被占用，换一个，并在后面的 GitHub Variable 中使用实际名称。
4. 进入 **仓库管理 → 镜像仓库**，在这个命名空间下创建两个 **私有** 仓库：
   - `xiaohan-review-backend`
   - `xiaohan-review-frontend`
5. 创建时，代码源选择 **本地仓库**。镜像由 GitHub Actions 构建并推送，这里不需要再绑定 GitHub 或设置 ACR 自动构建规则。
6. 进入 **访问凭证**，设置镜像仓库的固定登录密码，记下控制台给出的登录用户名。
7. 在仓库详情的 **基本信息 / 操作指南** 中复制公网 `docker login` 命令使用的域名。

域名可能类似：

```text
crpi-xxxx.cn-hangzhou.personal.cr.aliyuncs.com
```

这个地址只是格式示例。不同实例和地域的地址不同，必须使用你自己的控制台显示的 **公网地址**。GitHub Runner 和目前的腾讯云服务器都不能使用阿里云 VPC 专用地址。`REGISTRY_HOST` 只填域名，不包含 `https://`、命名空间、仓库名或镜像标签。

对应的镜像地址会是：

```text
你的公网仓库域名/xiaohan-study/xiaohan-review-backend:完整提交SHA
你的公网仓库域名/xiaohan-study/xiaohan-review-frontend:完整提交SHA
```

参考阿里云官方说明：[创建镜像仓库](https://help.aliyun.com/zh/acr/user-guide/create-a-repository-and-build-images)、[个人版镜像推送与拉取](https://help.aliyun.com/zh/acr/user-guide/use-a-container-registry-personal-edition-instance-to-push-and-pull-images)。

## 2. 在 GitHub 增加两个 Secrets 和两个 Variables

打开仓库的 [Actions 配置页面](https://github.com/wuweihan93-oss/xiaohan-review-agent/settings/secrets/actions)，进入 **Settings → Secrets and variables → Actions**。

在 **Secrets → New repository secret** 中添加：

| 名称 | 填写内容 |
| --- | --- |
| `REGISTRY_USERNAME` | 镜像仓库控制台 `docker login` 命令中的用户名 |
| `REGISTRY_PASSWORD` | 镜像仓库的固定登录密码或具有推送权限的访问令牌 |

在 **Variables → New repository variable** 中添加：

| 名称 | 填写内容 |
| --- | --- |
| `REGISTRY_HOST` | 从控制台复制的公网仓库域名，不含 `https://` 和路径 |
| `REGISTRY_NAMESPACE` | 实际创建的命名空间，例如 `xiaohan-study` |

之前配置的 SSH 信息继续使用：

| 类型 | 名称 | 值或含义 |
| --- | --- | --- |
| Secret | `SSH_HOST` | `43.139.216.149` |
| Secret | `SSH_USER` | `ubuntu` |
| Secret | `SSH_PRIVATE_KEY` | Mac 的 `~/.ssh/xiaohan_actions` 私钥完整内容 |
| Secret | `SSH_KNOWN_HOSTS` | 已核实的服务器 SSH 主机公钥记录 |
| Variable | `DEPLOY_ENABLED` | `true` |
| Variable | `DEPLOY_PATH` | `/home/ubuntu/apps/xiaohan-review-agent` |
| Variable | `SSH_PORT` | `22` |

GitHub 使用仓库用户名和密码推送镜像，使用 SSH 密钥登录服务器。它们是两套独立凭证。模型 Key 和数据库 `DB_URL` 继续放在服务器 `.env` 中。

如果尚未创建 SSH 密钥，在 Mac 执行；文件已存在时保留原文件：

```bash
ssh-keygen -t ed25519 -C "xiaohan-github-actions" -f ~/.ssh/xiaohan_actions -N ""
cat ~/.ssh/xiaohan_actions.pub | ssh ubuntu@43.139.216.149 'umask 077; mkdir -p ~/.ssh; cat >> ~/.ssh/authorized_keys'
```

在 Mac 复制私钥到剪贴板后，粘贴进 GitHub 的 `SSH_PRIVATE_KEY`：

```bash
pbcopy < ~/.ssh/xiaohan_actions
```

服务器公钥记录从已登录的服务器获取：

```bash
awk '{print "43.139.216.149 " $1 " " $2}' /etc/ssh/ssh_host_ed25519_key.pub
```

整行填入 `SSH_KNOWN_HOSTS`。非 22 端口时，行首改为 `[43.139.216.149]:实际端口`。

## 3. 在 Ubuntu 登录镜像仓库一次

登录 Ubuntu，使用现在运行项目的 **ubuntu 用户** 执行以下命令，按提示填入域名和用户名：

```bash
read -r -p '镜像仓库公网域名：' XIAOHAN_REGISTRY_HOST
read -r -p '镜像仓库登录用户名：' XIAOHAN_REGISTRY_USERNAME
docker login "$XIAOHAN_REGISTRY_HOST" --username "$XIAOHAN_REGISTRY_USERNAME"
```

看到 `Password:` 时输入仓库密码；终端不会显示输入内容。看到 `Login Succeeded` 即完成。这里输入的是镜像仓库密码，不是 SSH 登录密码。

请使用普通 `docker login`，与部署脚本运行用户保持一致。`sudo docker login` 会把凭证存到 root 的目录，后续以 ubuntu 用户运行的部署脚本将无法使用该凭证。

服务器账号需要有两个仓库的拉取权限。学习项目可以先使用与 GitHub 相同的仓库账号；仓库登录密码更换后，更新 GitHub Secret，并在 Ubuntu 重新登录。

确认现有项目和 Docker 正常：

```bash
cd ~/apps/xiaohan-review-agent
docker compose version
docker compose ps
test -f .env && test -d runtime/data && echo '配置和资料目录存在'
```

项目 `.env` 继续保留已能使用的模型、Embedding 和 PostgreSQL 配置，以及：

```env
XIAOHAN_BIND_HOST=0.0.0.0
XIAOHAN_HTTP_PORT=8888
```

## 4. 在 Mac 提交并推送新流程

本次代码已经把镜像构建、推送、配置下发、镜像拉取和失败回退连接起来。完成上面的控制台配置后，在 Mac 执行：

```bash
cd /Users/williams/Conda/xiaohan-review-agent
git add .github/workflows/ci-cd.yml deploy scripts README.md \
  frontend/index.html frontend/src
git diff --cached --stat
git commit -m "改用国内镜像仓库自动部署"
git push origin main
```

这些路径也会包含当前已经修改的前端页面。真实 `.env`、`runtime/`、虚拟环境和 SSH 私钥均不在上述提交路径内。

打开 [仓库 Actions](https://github.com/wuweihan93-oss/xiaohan-review-agent/actions)，检查新提交对应的“小晗 CI/CD”。

1. **测试后端、数据库和前端**：运行临时 PostgreSQL 集成测试、部署回退测试与前端测试。
2. **构建并推送 Linux 镜像**：登录国内仓库，构建并推送两个 Linux amd64 镜像。
3. **更新 Ubuntu 服务**：SSH 下发小型配置包，服务器输出两个镜像的拉取进度，再更新容器并检查健康状态。

首次会推送、下载完整的缺失镜像层。以后只改前端时，后端构建可以命中缓存，服务器也会复用已经拉过的后端镜像层。

创建 PR 时只测试和构建，不登录仓库、不推送镜像、不更新服务器。合并到 `main` 或手动运行 `main` 工作流时才推送镜像；`DEPLOY_ENABLED=true` 时才部署。

只修改 GitHub Secrets 或 Variables 不会自动触发任务。需要手动执行 **Actions → 小晗 CI/CD → Run workflow → main**。修改工作流后需先推送代码；在旧任务页面点击 **Re-run jobs** 会继续使用旧提交的上传镜像包流程。

## 5. 部署后验证

在 Ubuntu 执行：

```bash
cd ~/apps/xiaohan-review-agent
docker compose ps
curl -f http://127.0.0.1:8888/api/health
cat .deploy/current-revision
```

浏览器打开 <http://43.139.216.149:8888/>。修改前端后可以强制刷新。

脚本会生成 `compose.override.yaml`，把两个服务固定到本次实际构建的 `仓库地址@sha256:内容摘要`。即使镜像标签之后发生变化，也不会影响这个版本的部署。

## 6. 常见问题与回退

| 现象 | 检查位置 |
| --- | --- |
| 构建步骤提示仓库配置错误 | GitHub Variables 中的域名、命名空间是否正确；域名不要包含 `https://` |
| GitHub 登录或推送时报 `unauthorized` / `denied` | `REGISTRY_USERNAME`、`REGISTRY_PASSWORD`，以及两个私有仓库是否已创建、账号是否有推送权限 |
| Ubuntu 拉取时报 `unauthorized` / `denied` | 使用 ubuntu 用户重新执行 `docker login`，确认账号有拉取权限 |
| 拉取时报 `manifest unknown` | 对应仓库中是否已有该次构建的镜像；不要删除正在使用的版本 |
| 拉取超时 | 使用控制台公网地址，检查 Ubuntu 到该地域仓库的网络；VPC 地址不能跨云直接访问 |
| 更新服务显示跳过 | 确认 `DEPLOY_ENABLED=true`，且触发的是 `main` 的提交或手动运行 |
| 日志仍显示“正在传输镜像部署包” | 运行的是旧提交，需要推送新流程后触发新任务 |

如果慢在 GitHub 向国内仓库推送镜像，仍需检查这一段跨境网络；镜像仓库主要解决重复传输和服务器拉取路径问题，不能保证所有跨境连接都变快。

两个镜像全部拉取成功后才修改容器配置。拉取失败会让流水线失败，当前容器继续运行。新容器启动失败或 Nginx 到 API 的检查不通过时，脚本恢复上一版 Compose 配置，并尝试使用保留的本地镜像恢复服务。

回退只恢复容器与配置，不回退数据库内容。项目 `.env`、`runtime/data/`、`runtime/uploads/`、`runtime/tutorial/` 和 PostgreSQL 数据会保留。服务器源代码工作区不会随镜像自动更新，应用代码运行在镜像内部。

排查命令：

```bash
cd ~/apps/xiaohan-review-agent
docker compose ps
docker compose logs --tail=100 backend frontend
```

流水线保留现有部署锁，避免并发更新容器；配置副本保存在 `.deploy/rollback.*`。已有自定义 `compose.override.yaml` 时，会停止并提示先合并。

## 验证范围

14 项模拟 Docker / SSH 测试通过：覆盖镜像拉取失败、摘要固定、Compose 更新失败回退、API 代理检查失败回退、原有配置和资料保留、SSH 主机校验及临时私钥清理。工作流通过 actionlint、YAML 和 Bash 语法检查；已执行实际工作流中的打包步骤，示例配置包约 3.2 KB，并使用真实 Docker Compose 验证合并配置。

本地检查不登录真实镜像仓库，不推送镜像，也不更新线上容器。完成仓库配置并推送代码后，以 Actions 的实际运行结果验证首次仓库部署。
