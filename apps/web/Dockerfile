FROM public.ecr.aws/docker/library/node:20-slim AS builder

WORKDIR /app

COPY package.json /app/package.json
COPY apps/web/package.json /app/apps/web/package.json
COPY packages/contracts/package.json /app/packages/contracts/package.json
COPY packages/contracts/tsconfig.json /app/packages/contracts/tsconfig.json
COPY packages/contracts/src /app/packages/contracts/src
COPY apps/web /app/apps/web

RUN npm install
RUN npm run build --workspace apps/web

FROM public.ecr.aws/docker/library/node:20-slim AS runner

ENV NODE_ENV=production
WORKDIR /app

COPY --from=builder /app/package.json /app/package.json
COPY --from=builder /app/node_modules /app/node_modules
COPY --from=builder /app/apps/web /app/apps/web
COPY --from=builder /app/packages /app/packages

EXPOSE 3000
CMD ["npm", "run", "start", "--workspace", "apps/web", "--", "--hostname", "0.0.0.0", "--port", "3000"]
