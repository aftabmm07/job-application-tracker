FROM node:24.15.0-bookworm-slim AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html vite.config.js ./
COPY src/ ./src/
COPY public/ ./public/
RUN npm run build

FROM nginx:1.28.0-alpine
COPY nginx.conf /etc/nginx/nginx.conf
COPY --from=build /app/dist/ /usr/share/nginx/html/

# The static server needs no root privileges or writable application files.
USER nginx
EXPOSE 8080
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
