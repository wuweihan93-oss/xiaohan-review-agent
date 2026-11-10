# 小晗 GitHub Actions CI/CD 配置

适用于你已部署的 Ubuntu x86_64 服务器：`43.139.216.149`，SSH 用户 `ubuntu`，项目目录 `/home/ubuntu/apps/xiaohan-review-agent`，页面端口 `8888`。

每次向 GitHub 的 `main` 推送代码，Actions 先启动临时 PostgreSQL 运行后端测试，再测试并编译 Vue，之后构建前后端 Linux 镜像。启用部署开关后，Actions 通过 SSH 上传镜像包，服务器使用 `docker load` 导入镜像，再更新 Compose 容器。

不需要注册镜像仓库，也不需要在服务器重新安装 Python 或 Node 依赖。首次传输镜像可能较慢；当前方案每次会传完整镜像包，适合这个单人学习项目。

## 1. 在 Mac 创建 GitHub Actions 专用 SSH 密钥

在 Mac 终端执行，文件已存在时请使用其他文件名，保留原来的 SSH 密钥：

```bash
ssh-keygen -t ed25519 -C "xiaohan-github-actions" -f ~/.ssh/xiaohan_actions -N ""
cat ~/.ssh/xiaohan_actions.pub | ssh ubuntu@43.139.216.149 'umask 077; mkdir -p ~/.ssh; cat >> ~/.ssh/authorized_keys'
```

确认这把密钥能登录服务器，且服务器账号可以直接执行 Docker：

```bash
ssh -i ~/.ssh/xiaohan_actions -o IdentitiesOnly=yes ubuntu@43.139.216.149 \
  'docker compose version && docker info --format "{{.Architecture}}"'
```

架构应为 `x86_64`。当前流水线按之前构建日志中的 `amd64` 配置；更换 ARM 服务器时需要一起修改构建平台和部署脚本的架构检查。

## 2. 在 GitHub 填写四个 Secrets

打开仓库：<https://github.com/wuweihan93-oss/xiaohan-review-agent/settings/secrets/actions>

依次进入 **Settings → Secrets and variables → Actions → Secrets → New repository secret**。

| 名称 | 填写内容 |
| --- | --- |
| `SSH_HOST` | `43.139.216.149` |
| `SSH_USER` | `ubuntu` |
| `SSH_PRIVATE_KEY` | Mac 的 `~/.ssh/xiaohan_actions` 文件完整内容，含开头和结尾 |
| `SSH_KNOWN_HOSTS` | 按下面命令得到的服务器公钥记录 |

在 Mac 执行下面命令可复制私钥，然后粘贴到 `SSH_PRIVATE_KEY` 的输入框：

```bash
pbcopy < ~/.ssh/xiaohan_actions
```

服务器公钥应从你已经登录的服务器获取。在服务器终端执行：

```bash
awk '{print "43.139.216.149 " $1 " " $2}' /etc/ssh/ssh_host_ed25519_key.pub
```

把输出的整行填到 `SSH_KNOWN_HOSTS`。流水线会校验服务器身份，不会关闭主机密钥校验。SSH 端口不是 22 时，行首使用 `[43.139.216.149]:你的端口`。

模型 API Key、Embedding 配置和 `DB_URL` 继续放在服务器 `.env`，无需复制到 GitHub。CI 使用临时数据库和模拟模型，不会调用付费模型，也不会连接你的线上 PostgreSQL。

## 3. 配置三个 Repository Variables

同一页面切换 **Variables → New repository variable**：

| 名称 | 值 |
| --- | --- |
| `DEPLOY_ENABLED` | `true` |
| `DEPLOY_PATH` | `/home/ubuntu/apps/xiaohan-review-agent` |
| `SSH_PORT` | `22` |

前两步尚未完成时，可以先不设置 `DEPLOY_ENABLED`，测试和镜像构建会运行，自动部署会显示跳过。开关启用后，`main` 的新提交和手动触发都会更新线上服务。

## 4. 确认服务器的持久化配置

在服务器运行：

```bash
cd ~/apps/xiaohan-review-agent
docker compose ps
test -f .env && test -d runtime/data && echo "配置和资料目录存在"
```

`.env` 中保留你已经配置好的模型和数据库信息，以及：

```env
XIAOHAN_BIND_HOST=0.0.0.0
XIAOHAN_HTTP_PORT=8888
```

保持当前已能工作的访问方式即可。SSH 需要能从 GitHub Runner 连接服务器；如果安全组只允许你电脑的 IP 登录 SSH，Actions 会超时，需要为 Runner 配置合适的 SSH 访问规则。

## 5. 提交本次代码并触发流水线

在 Mac 执行：

```bash
cd /Users/williams/Conda/xiaohan-review-agent
git add .github/workflows/ci-cd.yml deploy scripts \
  frontend/src frontend/tests frontend/package.json \
  .dockerignore .env.example .gitignore .python-version README.md
git diff --cached --stat
git commit -m "配置 GitHub Actions 测试与自动部署"
git push origin main
```

这次也提交前面修复的 HTTP 白屏问题和后端依赖源配置，避免下一次自动部署又恢复旧代码。不要提交 `.env`、`runtime/` 或 `.idea/`，项目已提供相应忽略规则。

打开 <https://github.com/wuweihan93-oss/xiaohan-review-agent/actions>，选择“小晗 CI/CD”，观察三个任务：

1. “测试后端、数据库和前端”：包含公网 HTTP 的 ID 生成回归测试，以及部署失败回退测试。
2. “构建 Linux 镜像”：前后端镜像都标记为本次完整 Git commit SHA。
3. “更新 Ubuntu 服务”：传输镜像、启动容器、检查 Nginx 到 API 的请求是否正常。

需要重试时，在 Actions 的“小晗 CI/CD”页面点击 **Run workflow**，选择 `main`。只修改 Secrets 或 Variables 不会自动开始任务，需要手动触发或再推送一次提交。

## 6. 部署后验证

```bash
cd ~/apps/xiaohan-review-agent
docker compose ps
curl -f http://127.0.0.1:8888/api/health
cat .deploy/current-revision
```

浏览器打开 <http://43.139.216.149:8888/>，修改了前端时可以强制刷新。

部署脚本会生成 `compose.override.yaml`，只用于指定本次镜像版本。以后执行普通 `docker compose ps`、`logs` 或 `up` 时，Compose 会自动读取这个文件。流水线不依赖服务器 Git 工作区是否已更新；它上传的是已经构建好的镜像和最新 Compose 配置。

## 7. 更新与失败处理

日常修改只需要 `git add`、`git commit`、`git push origin main`。提交到 PR 会测试和构建，合并到 `main` 后才自动部署。

镜像导入失败时不会停止现有容器。新容器无法健康启动或 API 转发检查失败时，脚本会恢复上一版 Compose 配置并尝试启动上一版镜像；Actions 仍显示本次失败，便于检查原因。回退恢复的是容器和 Compose 配置，数据库内容不会回退。

`.env`、`runtime/data/`、`runtime/uploads/`、`runtime/tutorial/` 和原 PostgreSQL 会保留。更换后端会有短暂中断，建议在没有进行复习问答时推送部署。

排查命令：

```bash
cd ~/apps/xiaohan-review-agent
docker compose ps
docker compose logs --tail=100 backend frontend
```

部署脚本的配置副本保存在 `.deploy/rollback.*`。流水线使用项目锁，避免并发更新；已有自定义 `compose.override.yaml` 时会停止并提示先合并配置。

镜像包只用于更新代码和运行环境，不备份 PostgreSQL 或学习资料。服务器的 Git 源码文件也不会随镜像自动同步；手动在服务器构建前，请先确认使用的是需要的代码版本。

## 本次本地验证

12 项后端测试、2 项前端测试和 11 项部署/SSH 模拟测试通过，前端构建、Compose 配置与 YAML/Bash 语法检查通过。4 项 PostgreSQL 集成测试本次在本地跳过，工作流会在 GitHub 的临时 PostgreSQL 上运行它们。

工作流尚未推送到 GitHub，未触发实际镜像构建、SSH 传输或服务器更新；完成 Secrets、Variables 并推送后，需以 Actions 的实际运行结果确认首次部署。
