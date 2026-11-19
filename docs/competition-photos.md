# 比赛照片

入口为 `/photos`，导航中的 Photos。管理员登录 `/admin` 后选择“比赛照片”，新建相册、填写比赛日期，多选上传照片。新相册默认隐藏；整理后勾选“向认证成员开放”保存。可重新隐藏相册或逐张删除照片，隐藏后直接图片链接也失效。管理员可以预览隐藏相册。

访客通过 OJ Wall 正式账号登录。认证使用 DLUT CPC 管理员已审核通过的 `cpc_claims` 关系；填写真实姓名、注册账号或验证邮箱不等于成员认证。未认证用户从 OJ Wall `/regionals` 申请，在 DLUT `/admin` 的“成员认证”审核。

## 配置

DLUT Compose 新增 `PHOTO_PUBLIC_URL`（本站 HTTPS origin）、`OJ_WALL_URL`（OJ Wall HTTPS origin）、`OJ_WALL_AUTHORITY_ID`（OJ Wall 的原有稳定 authority UUID），沿用 `CPC_SYNC_TOKEN`。OJ Wall 新增 `CPC_PHOTO_PUBLIC_URL`，设为 DLUT 的公开 HTTPS origin，作为唯一允许回跳地址；沿用原来的名单同步配置。

OJ Wall 用 60 秒、单次、PKCE S256 绑定的随机授权码，经服务端凭据交换返回本站已有主体 UUID。DLUT 的随机 state 由 HttpOnly / Secure / SameSite=Lax Cookie 与数据库绑定，10 分钟到期。授权失败不创建照片会话。照片会话仅存随机 token 摘要，8 小时到期；每次列表、缩略图、完整图及 HEAD 都直接检查当前批准状态与成员存在性，审核撤销即时阻止后续访问，不依赖 OJ Wall 的五分钟名单缓存。照片会话独立退出；OJ Wall 退出不会自动退出已建立的照片会话。

照片请求不重定向到公共对象 URL，响应为 `private, no-store`，图片设 `Cross-Origin-Resource-Policy: same-origin`。图片和目录都不通过 `/api/site` 发布。应用访问日志去除授权查询参数。新管理写接口要求现有管理员会话、CSRF token 和严格同源 Origin。授权码交换只允许服务器共享凭据；不向浏览器暴露该凭据。

## 存储与备份

生产配置 `PHOTO_STORAGE=github-lfs`，`PHOTO_LFS_REPOSITORY=Code92007/dlut-cpc-photos`。仓库必须为私有；与公开资料库分开。照片和缩略图均直接上传私有 GitHub LFS，保存时使用内存缓冲，不落服务器磁盘；读取时在核验成员认证后从 LFS 取回并校验大小和 SHA-256，再返回图片字节。浏览器无法取得仓库凭据、临时下载地址或签名。服务端仍会使用转发流量。远端故障时返回 503，不回退到本地保存或公开 URL。

仓库的专用写入 deploy key 位于持久卷 `runtime/photo_lfs_key`，0600；公钥只授权此照片仓库，不部署个人账号 Token。GitHub 主机公钥从官方 `/meta` API 获取，存为 `runtime/photo_lfs_known_hosts`，SSH 严格校验。镜像需有 Git / OpenSSH。SSH `git-lfs-authenticate` 换取短期 LFS 凭据，经标准 Batch / Basic Transfer API 上传、校验及读取。上传成功后提交标准 Git LFS pointer 和 `.gitattributes`，GitHub LFS 对象可随正常 Git LFS 克隆恢复。匿名可见的仓库会停止读写；私有状态最多缓存五分钟。

服务器只保存原 SQLite 中的相册、照片索引、LFS OID/大小/提交 SHA、会话，以及 `runtime/photo_lfs_metadata.git` 中的 Git pointer 元数据。此裸库无 checkout、无 LFS 图片缓存；临时索引使用后删除。Git 提交只包含约 130 字节的 pointer，不包含图片。备份和迁移需保留 SQLite、专用 deploy key、主机公钥、原有身份 UUID 与配置；裸 Git 元数据可从远端恢复。

删除照片会从站内索引及远端最新目录中移除文件；Git 历史及 LFS 的历史对象仍保留，LFS 配额回收须按 GitHub 文档处理。GitHub LFS 的存储和下载有账号额度，不能视为无限空间；不更改账号付费预算。官方说明：https://docs.github.com/en/billing/concepts/product-billing/git-lfs 。

未配置生产远端时，仅本地开发可用 `PHOTO_STORAGE=local`，文件位于 `runtime/competition_photos/`；`COMPETITION_PHOTOS_PATH` 可覆盖目录且必须在静态 web 目录之外。切换已有本地照片时须先上传并核对 LFS OID 后再删除原文件。2026-11-19 首次启用远端时生产库无照片，故没有本地照片需要迁移。

照片接受 JPEG / PNG / WebP，单张上限 12 MB / 4000 万像素，纠正 EXIF 方向后生成最长边 6000 像素的 JPEG 和 720 像素缩略图，移除源文件 EXIF 等元数据。源文件不单独存档，需要保留摄影原片时另行备份。

验证：`python -m unittest discover -s tests -p 'test_competition_photos.py'`；现有认证、管理员、静态资源与 Docker 包装回归仍需通过。OJ Wall 对应 `tests/test_photo_sso.py`。
