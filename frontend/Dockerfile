# 多阶段：构建 React 静态包 → nginx 服务 + 反代后端。
# 无 CDN：所有依赖经 npm 打包进静态产物（§3 镜像层守卫）。
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts
COPY . .
RUN npm run build

FROM nginx:stable-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
