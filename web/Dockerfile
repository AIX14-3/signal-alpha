# 멀티스테이지 빌드.
#   - dev     : 로컬 docker-compose 용(next dev, 소스/노드모듈은 compose 가 바인드마운트).
#   - builder : 프로덕션 빌드(`next build` → output:'standalone').
#   - runner  : 최소 산출물만 담아 `node server.js` 로 구동(Cloud Run/GCE).
# 기본 타깃은 마지막 스테이지(runner). compose 의 dev 서비스는 `target: dev` 로 가리킨다.

FROM node:22-alpine AS base
WORKDIR /app

# --- deps: package*.json 만으로 node_modules 설치(레이어 캐시) ---
FROM base AS deps
COPY package*.json ./
RUN npm ci

# --- dev: 로컬 개발 서버 ---
FROM base AS dev
ENV NODE_ENV=development
COPY --from=deps /app/node_modules ./node_modules
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev", "--", "--hostname", "0.0.0.0", "--port", "3000"]

# --- builder: 프로덕션 빌드 ---
# NEXT_PUBLIC_* 는 빌드타임에 클라이언트 번들로 인라인된다 → 반드시 build-arg 로 주입.
# (런타임 env 로는 브라우저에 안 박힘.) BE 주소/포트원/소셜 client_id 모두 여기서 확정.
FROM base AS builder
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ARG NEXT_PUBLIC_MAIN_API_BASE_URL
ARG NEXT_PUBLIC_PORTONE_STORE_ID
ARG NEXT_PUBLIC_PORTONE_CHANNEL_KEY_IDENTITY
ARG NEXT_PUBLIC_PORTONE_CHANNEL_KEY_PAYMENT
ARG NEXT_PUBLIC_NAVER_CLIENT_ID
ARG NEXT_PUBLIC_GOOGLE_CLIENT_ID
ARG NEXT_PUBLIC_KAKAO_CLIENT_ID
ENV NEXT_PUBLIC_MAIN_API_BASE_URL=${NEXT_PUBLIC_MAIN_API_BASE_URL} \
    NEXT_PUBLIC_PORTONE_STORE_ID=${NEXT_PUBLIC_PORTONE_STORE_ID} \
    NEXT_PUBLIC_PORTONE_CHANNEL_KEY_IDENTITY=${NEXT_PUBLIC_PORTONE_CHANNEL_KEY_IDENTITY} \
    NEXT_PUBLIC_PORTONE_CHANNEL_KEY_PAYMENT=${NEXT_PUBLIC_PORTONE_CHANNEL_KEY_PAYMENT} \
    NEXT_PUBLIC_NAVER_CLIENT_ID=${NEXT_PUBLIC_NAVER_CLIENT_ID} \
    NEXT_PUBLIC_GOOGLE_CLIENT_ID=${NEXT_PUBLIC_GOOGLE_CLIENT_ID} \
    NEXT_PUBLIC_KAKAO_CLIENT_ID=${NEXT_PUBLIC_KAKAO_CLIENT_ID}
RUN npm run build

# --- runner: 최소 프로덕션 이미지 ---
FROM base AS runner
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
# 비루트 유저로 구동.
RUN addgroup --system --gid 1001 nodejs && adduser --system --uid 1001 nextjs
# standalone 산출물(서버 + 추려진 node_modules) 과 정적 자산만 복사.
# (web/public 디렉터리는 현재 없음 → 추가 시 `COPY --from=builder /app/public ./public` 한 줄 추가.)
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
