# RSSHub Dockerfile — self-hosted RSSHub instance for Weibo feed generation
FROM node:22-alpine

WORKDIR /app

# Install RSSHub
RUN npm install -g rsshub

# Create data directory for cookies / cache
RUN mkdir -p /app/data

# Copy Weibo cookie configuration (if you have one)
# Place your Weibo SUB cookie in ./rsshub/data/weibo_cookie.txt
# Format: SUB=_2A25... (one line, just the cookie value)

EXPOSE 1200

ENV NODE_ENV=production
ENV CACHE_TYPE=memory
ENV REQUEST_TIMEOUT=30

CMD ["npx", "rsshub"]
