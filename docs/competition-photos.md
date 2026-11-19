# 比赛照片

入口为 `/photos`，导航中的 Photos。管理员登录 `/admin` 后选择“比赛照片”，新建相册、填写比赛日期，多选上传照片。新相册默认隐藏；整理后勾选“向认证成员开放”保存。可重新隐藏相册或逐张删除照片，隐藏后直接图片链接也失效。管理员可以预览隐藏相册。

访客通过 OJ Wall 正式账号登录。认证使用 DLUT CPC 管理员已审核通过的 `cpc_claims` 关系；填写真实姓名、注册账号或验证邮箱不等于成员认证。未认证用户从 OJ Wall `/regionals` 申请，在 DLUT `/admin` 的“成员认证”审核。

## 配置

DLUT Compose 新增 `PHOTO_PUBLIC_URL`（本站 HTTPS origin）、`OJ_WALL_URL`（OJ Wall HTTPS origin）、`OJ_WALL_AUTHORITY_ID`（OJ Wall 的原有稳定 authority UUID），沿用 `CPC_SYNC_TOKEN`。OJ Wall 新增 `CPC_PHOTO_PUBLIC_URL`，设为 DLUT 的公开 HTTPS origin，作为唯一允许回跳地址；沿用原来的名单同步配置。

OJ Wall 用 60 秒、单次、PKCE S256 绑定的随机授权码，经服务端凭据交换返回本站已有主体 UUID。DLUT 的随机 state 由 HttpOnly / Secure / SameSite=Lax Cookie 与数据库绑定，10 分钟到期。授权失败不创建照片会话。照片会话仅存随机 token 摘要，8 小时到期；每次列表、缩略图、完整图及 HEAD 都直接检查当前批准状态与成员存在性，审核撤销即时阻止后续访问，不依赖 OJ Wall 的五分钟名单缓存。照片会话独立退出；OJ Wall 退出不会自动退出已建立的照片会话。

照片请求不重定向到公共对象 URL，响应为 `private, no-store`，图片设 `Cross-Origin-Resource-Policy: same-origin`。图片和目录都不通过 `/api/site` 发布。应用访问日志去除授权查询参数。新管理写接口要求现有管理员会话、CSRF token 和严格同源 Origin。授权码交换只允许服务器共享凭据；不向浏览器暴露该凭据。

## 存储与备份

相册、照片索引、会话与登录 state 保存在原运行 SQLite 库；照片默认在同一个持久卷的 `runtime/competition_photos/` 中。Docker 镜像不包含照片。备份和迁移须同时保留数据库、照片目录、原有身份 UUID 和环境配置。`COMPETITION_PHOTOS_PATH` 可覆盖图片目录，必须在静态 web 目录之外。

照片接受 JPEG / PNG / WebP，单张上限 12 MB / 4000 万像素，纠正 EXIF 方向后生成最长边 6000 像素的 JPEG 和 720 像素缩略图，移除源文件 EXIF 等元数据。源文件不单独存档，需要保留摄影原片时另行备份。

验证：`python -m unittest discover -s tests -p 'test_competition_photos.py'`；现有认证、管理员、静态资源与 Docker 包装回归仍需通过。OJ Wall 对应 `tests/test_photo_sso.py`。
