# edricd.com (FastAPI + Nginx + Frontend)

个人网站项目：前端静态页 + FastAPI 后端 + Nginx 反代，Docker Compose 一键部署，支持 Let's Encrypt HTTPS 自动续期。

## 项目结构

```
repo/
├─ admin/                   # 后台管理 - 提供各种个人api
├─ backend/                 # FastAPI 后端
│  ├─ app/
│  │  ├─ core/
│  │  │  └─ config.py       # 配置读取
│  │  ├─ templates/         # 邮件模板
│  │  └─ main.py            # API 入口（含 /api/contact）
│  └─ Dockerfile
├─ frontend/                # 前端静态页面
│  ├─ index.html
│  └─ static/
│     ├─ css/
│     └─ js/
├─ nginx/
│  └─ conf.d/
│     └─ site.conf          # Nginx 配置（HTTP->HTTPS + 静态页 + API 反代）
├─ docker-compose.yml       # 一键启动服务
└─ .env                     # 后端环境变量（服务器上配置）
```

## 服务说明

- `nginx`：80/443 端口；静态站点发布 + `/api/` 反向代理到后端
- `backend`：FastAPI；`/api/contact` 提交表单并发送邮件
- `certbot`：证书签发（一次性运行）
- `certbot-renew`：Let's Encrypt 自动续期

## 环境变量（后端）

在服务器 `.env` 中配置：

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_email@gmail.com
SMTP_PASS=your_email_password

RECAPTCHA_SITE_KEY=your_recaptcha_site_key
RECAPTCHA_SECRET_KEY=your_recaptcha_secret_key
LOGIN_RECAPTCHA_REQUIRED=0
```

## 本地或服务器启动（HTTP）

```
docker compose up -d --build
```

## 启用 HTTPS（Let's Encrypt）

1) 先启动 nginx（让 webroot 生效）
```
docker compose up -d nginx
```

2) 申请证书（一次性）
```
docker compose run --rm certbot certonly \
  --webroot -w /var/www/certbot \
  -d edricd.com -d www.edricd.com \
  --email edricding0108@gmail.com \
  --agree-tos --no-eff-email
```

3) 启动/重启所有服务
```
docker compose up -d
```

## 访问

- HTTP 会自动跳转到 HTTPS
- 主页：`https://www.edricd.com`
- API：`https://www.edricd.com/api/health`
