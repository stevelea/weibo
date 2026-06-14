# EV Conduit — Weibo → News Pipeline

Automated XPeng news pipeline that scrapes Weibo community content about XPeng (小鹏汽车) vehicles, VLA autonomous driving, robots, and flying cars. Publishes translated and summarized news to [evconduit.com](https://www.evconduit.com).

## Architecture

```
Weibo Community ──→ RSSHub (RSS feeds) ──→ Pipeline (Python) ──→ DeepSeek AI ──→ Hugo ──→ evconduit.com
超话 super topics      │                     │                  (translate,     (static   
Monitored accounts     │                     │                   summarize,      site)    
                       │                     │                   categorize)            
                       │              SQLite (dedup + state)
                       │                    
                       └── OCR + Image pipeline (Tesseract + DeepSeek)
```

## Features

- **Multi-source ingestion**: RSSHub feeds from 8 monitored Weibo accounts + XPeng super topic (超话)
- **AI-powered processing**: DeepSeek translates, categorizes, scores relevance (0–100), and summarizes every post
- **OCR + image captioning**: Tesseract extracts Chinese text from images, DeepSeek translates to English captions
- **Local image hosting**: Images downloaded and served locally to avoid Weibo CDN hotlinking
- **Video cards**: Clickable poster thumbnails linking to Weibo video pages (stable URLs, no CDN expiry)
- **Nginx reverse proxy**: Routes traffic, proxies Weibo resources with proper headers
- **Dark-themed Hugo site**: Responsive, modern design with category/tag taxonomies

## Quick Start

### Prerequisites

- Docker + Docker Compose
- [DeepSeek API key](https://platform.deepseek.com/api_keys)

### Install

```bash
git clone https://github.com/yourusername/weibo.git
cd weibo
cp .env.example .env
# Edit .env → set DEEPSEEK_API_KEY=sk-...
docker compose up -d
```

The site will be available at **http://localhost**.

### Verify

```bash
# Watch the pipeline processing
docker compose logs -f pipeline

# Check RSSHub feeds
curl http://localhost:1200/weibo/user/5710264970

# Check site
curl http://localhost/
```

## Production Deployment

### 1. Get a server

Any Linux VPS with ≥2GB RAM and ≥20GB disk. Recommended: Ubuntu 22.04 LTS.

### 2. Install Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out and back in
```

### 3. Clone and configure

```bash
git clone https://github.com/yourusername/weibo.git /opt/evconduit
cd /opt/evconduit
cp .env.example .env
# Set your DeepSeek API key
nano .env
```

### 4. SSL certificates (Let's Encrypt)

```bash
sudo apt install certbot
sudo certbot certonly --standalone -d evconduit.com -d www.evconduit.com
sudo cp /etc/letsencrypt/live/evconduit.com/fullchain.pem nginx/ssl/cert.pem
sudo cp /etc/letsencrypt/live/evconduit.com/privkey.pem nginx/ssl/key.pem
```

### 5. Switch to production nginx config

Edit `docker-compose.yml` and uncomment the SSL lines in the nginx service,
or use the provided `nginx/nginx.ssl.conf`.

### 6. Start

```bash
docker compose up -d
```

### 7. Auto-renew SSL

```bash
# Add to crontab (runs daily at 3am)
echo "0 3 * * * certbot renew --quiet && docker compose -f /opt/evconduit/docker-compose.yml restart nginx" | sudo crontab -
```

## Configuration

### Adding Weibo accounts

Edit `config/accounts.yaml`:

```yaml
accounts:
  - uid: "1234567890"        # Weibo user ID (from weibo.com/u/{uid})
    name: "Account Name"     # Display name
    category: official       # official | executive | media | reviewer | community | ev_media | tech_media
    priority: high           # high | medium | low
```

### Adding super topics (超话)

Edit `config/supertopics.yaml`:

```yaml
supertopics:
  - id: "1008086b2a91552bca604612ce68abe0ec03ff"
    name: "小鹏汽车"
    priority: high
```

### Keyword search (requires crawl4weibo + Playwright)

Edit `config/keywords.yaml` — keyword groups for deep scraping. Only active when `crawl4weibo` and Playwright are installed in the pipeline container.

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DEEPSEEK_API_KEY` | Yes | — | DeepSeek API key |
| `DEEPSEEK_MODEL` | No | `deepseek-chat` | Model to use |
| `CYCLE_INTERVAL_MINUTES` | No | `10` | Pipeline cycle interval |
| `LOG_LEVEL` | No | `info` | Logging level |

## Project Structure

```
weibo/
├── config/
│   ├── accounts.yaml         # Weibo accounts to monitor
│   ├── keywords.yaml         # Search keywords (crawl4weibo)
│   └── supertopics.yaml      # Super topics (超话)
├── src/
│   ├── main.py               # Entry point + scheduler
│   ├── config.py             # YAML config loader
│   ├── ingest/
│   │   ├── rsshub.py         # RSSHub feed consumer (accounts + super topics)
│   │   └── crawl.py          # crawl4weibo keyword scraper
│   ├── store/
│   │   └── models.py         # SQLAlchemy models + DB wrapper
│   ├── process/
│   │   ├── ai.py             # DeepSeek API processor
│   │   └── ocr.py            # Tesseract OCR + image downloader
│   └── publish/
│       └── hugo.py           # Hugo markdown generator
├── hugo/themes/evconduit/    # Custom Hugo theme
├── nginx/
│   └── nginx.conf            # Reverse proxy with Weibo resource proxy
├── docker-compose.yml        # Full stack orchestration
├── Dockerfile                # Pipeline container
├── pyproject.toml            # Python project config
└── README.md
```

## How It Avoids Weibo Blocks

1. **RSSHub uses Weibo's mobile API** (m.weibo.cn) — less aggressively guarded
2. **Reader-like traffic pattern** — RSS polling looks like a feed reader, not a scraper
3. **Conservative rate limiting** — delays between requests
4. **Chromium-bundled RSSHub** — handles Sina Visitor System with headless browser
5. **Local image hosting** — images downloaded once, served from our CDN

## Monitoring

```bash
# All containers
docker compose ps

# Pipeline logs
docker compose logs -f pipeline

# Database stats
docker exec weibo-pipeline sqlite3 /app/data/weibo.db \
  "SELECT COUNT(*), AVG(relevance_score) FROM posts WHERE ai_processed=1;"

# Nginx access
docker compose logs nginx | tail -20
```

## License

MIT
