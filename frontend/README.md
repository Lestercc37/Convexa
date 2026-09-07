# Convexa Frontend

Dashboard web de Convexa construido con Next.js App Router y TypeScript.

## Desarrollo local

Con el backend ejecutándose en `http://localhost:8000`:

```powershell
Copy-Item .env.example .env.local
pnpm install
pnpm dev
```

Abre `http://localhost:3000`.

Para usar otra instancia del backend, cambia `CONVEXA_API_URL` en `.env.local`.

`pnpm dev` corre con `--webpack` (no Turbopack, el default de Next.js 16.2.x) porque Turbopack se cae con un error de memoria tras ~30min activo — bug conocido, sin resolver: [vercel/next.js#94915](https://github.com/vercel/next.js/issues/94915). Revertir una vez que se resuelva upstream.

## Validación

```powershell
pnpm test
pnpm lint
pnpm build
```
