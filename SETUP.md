# Kripto-Bot — Adım Adım Kurulum

Bu dosya, projeyi **sıfırdan** kuran bir geliştirici veya **farklı bir AI** için tekrarlanabilir adımları içerir. Tüm komutlar proje kök dizininde (`kripto-bot/`) çalıştırılmalıdır.

---

## 1. Ön koşullar

- **Docker** ve **Docker Compose** yüklü olmalı.
- Git ile repoya erişim.

Kontrol:

```bash
docker --version
docker compose version
git --version
```

---

## 2. Projeyi klonla

```bash
git clone https://github.com/mockupsoft/kripto-bot.git
cd kripto-bot
```

---

## 3. Ortam dosyası oluştur

```bash
cp .env.example .env
```

`.env` dosyasını düzenlemeniz **gerekmez**; Docker Compose ile çalışırken varsayılan değerler kullanılır:

- `DATABASE_URL`: `postgres` (servis adı) — container içi
- `REDIS_URL`: `redis://redis:6379/0` — container içi (backend/worker için)
- Frontend tarafında API: tarayıcı `localhost:8002` kullanır (`NEXT_PUBLIC_*`)

**Yerel geliştirme** (Docker’sız backend/frontend) yapacaksanız `.env` içinde:

- `DATABASE_URL=postgresql+asyncpg://polybot:polybot_dev@127.0.0.1:5433/polybot`
- `DATABASE_URL_SYNC=postgresql://polybot:polybot_dev@127.0.0.1:5433/polybot`
- `REDIS_URL=redis://127.0.0.1:6380/0`

yazın ve önce sadece Postgres + Redis’i Docker ile çalıştırın:

```bash
docker compose up -d postgres redis
```

---

## 4. Servisleri build ve başlat

```bash
docker compose up --build -d
```

Bu komut:

- `postgres`, `redis`, `backend`, `worker`, `frontend` servislerini build eder ve arka planda başlatır.
- Backend ilk çalıştığında `alembic upgrade head` ve `python -m app.seed.run_seed` otomatik çalışır.
- Worker, Redis’e `REDIS_URL=redis://redis:6379/0` ile bağlanır (compose’ta tanımlı).

Beklenen süre: ilk build 2–5 dakika sürebilir.

---

## 5. Servislerin çalıştığını doğrula

```bash
docker compose ps
```

Tüm servisler `Up` veya `running` olmalı. Worker birkaç saniye içinde Redis’e bağlanır; `Restarting` ise [Sorun giderme](#sorun-giderme) bölümüne bakın.

**Manuel kontroller:**

- Frontend: tarayıcıda **http://localhost:3002**
- Backend özet: `curl -s http://localhost:8002/api/overview | head -c 500`
- Worker log: `docker compose logs worker --tail 20`

---

## 6. İlk kullanım

1. **http://localhost:3002** — Dashboard (Overview, Wallets, Markets, Trades, Signals, Analytics, Research Lab).
2. Canlı veri için backend’in Polymarket API’ye erişebilmesi yeterli; ek kimlik doğrulama gerekmez (public API).
3. Paper balance $900 ile başlar; stratejiler sinyal üretip kağıt pozisyon açar/kapatır.

---

## Sorun giderme

### Worker sürekli yeniden başlıyor (Redis bağlantı hatası)

- **Sebep:** Worker container’da Redis adresi `localhost` olmamalı; servis adı `redis` kullanılmalı.
- **Çözüm:** `docker-compose.yml` içinde `worker` servisinde şu satırlar olmalı:

  ```yaml
  environment:
    - REDIS_URL=redis://redis:6379/0
  ```

- Sonra: `docker compose up -d worker --force-recreate`

### Frontend “API’ye ulaşamıyor”

- Tarayıcı `localhost:8002` kullanıyor mu kontrol edin. `.env` içinde:
  - `NEXT_PUBLIC_API_URL=http://localhost:8002`
  - `NEXT_PUBLIC_WS_URL=ws://localhost:8002/ws/live`
- Backend gerçekten 8002’de dinliyor mu: `curl -s -o /dev/null -w "%{http_code}" http://localhost:8002/api/overview` → 200 beklenir.

### Veritabanı migration hatası

```bash
docker compose exec backend alembic upgrade head
docker compose exec backend python -m app.seed.run_seed
```

### Port çakışması

Makinede 3002, 8002, 5433 veya 6380 kullanımdaysa `docker-compose.yml` içinde `ports` bölümlerini değiştirin (örn. `"3003:3000"`).

---

## Cursor / AI için notlar

- Proje kökünde **`.cursor/rules`** altında kural dosyaları (`.mdc`) vardır. Cursor açıldığında mimari, backend/frontend standartları, bilinen hatalar ve analytics/edge modeli bu kurallardan okunur.
- Yeni bir AI bu repoyu klonlayıp `SETUP.md` adımlarını uygulayarak projeyi çalıştırabilir; geliştirme yaparken `.cursor/rules` dosyalarına uyum önerilir.
- Detaylı mimari ve güvenlik kuralları için **README.md** ve **.cursor/rules/01-project-architecture.mdc** kullanılabilir.

---

## Özet komut listesi (kopyala-yapıştır)

```bash
git clone https://github.com/mockupsoft/kripto-bot.git
cd kripto-bot
cp .env.example .env
docker compose up --build -d
# Birkaç dakika sonra:
open http://localhost:3002
curl -s http://localhost:8002/api/overview | head -c 300
```

Bu adımlarla proje eksiksiz kurulmuş ve çalışır durumda olmalıdır.
