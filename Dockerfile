FROM node:22-slim AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
ARG BACKEND_URL=http://api:8000
ARG NEXT_PUBLIC_VOICE_WS_ORIGIN=
ENV BACKEND_URL=$BACKEND_URL NEXT_PUBLIC_VOICE_WS_ORIGIN=$NEXT_PUBLIC_VOICE_WS_ORIGIN
RUN npm run build

FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production BACKEND_URL=http://api:8000
COPY --from=build /app ./
EXPOSE 3000
CMD ["npm", "start"]
