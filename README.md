# Kripto-Bot — Polymarket Paper Trading Research Platform

[![DEMO ONLY](https://img.shields.io/badge/DEMO-ONLY%20%7C%20NO%20REAL%20ORDERS-red)](.)

**DEMO-ONLY / PAPER-TRADING** araştırma platformu: Polymarket tarzı prediction market’lerde copy-trading ve arbitraj gözlemini simüle eder. Sistem **gerçek emir kesinlikle göndermez**; yalnızca simülasyon modunda çalışır.

- **Repo:** [github.com/mockupsoft/kripto-bot](https://github.com/mockupsoft/kripto-bot)
- **Kurulum:** Aşağıdaki [Kurulum](#kurulum) bölümü ve [SETUP.md](./SETUP.md) (adım adım).
- **Cursor / AI kuralları:** [.cursor/rules](#cursor-rules-kural-dosyalari) altında; farklı bir AI veya geliştirici projeyi bu kurallara göre rahatça kurabilir ve geliştirebilir.

---

## Ne Yapar?

$900 sanal bankroll ile:

1. Seçili cüzdanları izler; kârlılık vs **kopyalanabilirlik** değerlendirir.
2. Yeni cüzdan işlemlerini tespit eder; gecikme/slippage altında kopyalamayı simüle eder.
3. İlişkili prediction market’lerde spread dislokasyonlarını izler.
4. Order-book yürümesi, komisyon ve kısmi dolum ile kağıt girişleri simüle eder.
5. Stratejilerin kârlı olup olmayacağını gösteren analitik üretir.

---

## Mimari

```
Polymarket API/WS
       ↓
  Ingestion (raw_events → normalizer)
       ↓
  Wallet Intelligence (scoring, alpha, influence graph, leader impact)
       ↓
  Signal Engine (Bayesian, Edge, Spread, 3-layer edge model)
       ↓
  Strategies (direct_copy, high_conviction, leader_copy, dislocation, shadow)
       ↓
  Paper Execution (book walking, fees, slippage)
       ↓
  Risk Engine (Kelly, exposure caps, kill switch)
       ↓
  Analytics + Dashboard
```

---

## Stack

| Bileşen   | Teknoloji                          |
|----------|-------------------------------------|
| Backend  | Python 3.11+, FastAPI, SQLAlchemy 2, structlog |
| Frontend | Next.js 14, TypeScript, Tailwind   |
| Veritabanı | PostgreSQL 16                   |
| Kuyruk   | Redis 7, arq                       |
| Grafik  | TradingView Lightweight Charts, Recharts |
| Ortam   | Docker Compose                      |

---

## Kurulum

### Gereksinimler

- **Docker** ve **Docker Compose**
- (Opsiyonel) Yerel geliştirme için: Python 3.11+, Node.js 20+

### 1. Repoyu klonla

```bash
git clone https://github.com/mockupsoft/kripto-bot.git
cd kripto-bot
```

### 2. Ortam dosyası

```bash
cp .env.example .env
```

`.env` içinde varsayılan değerler Docker için uygundur (`postgres`, `redis` servis adları). Yerel çalıştırıyorsanız `DATABASE_URL` ve `REDIS_URL` için `localhost` ve portları (5433, 6380) kullanın; detay için [SETUP.md](./SETUP.md).

### 3. Servisleri çalıştır

```bash
docker compose up --build -d
```

İlk açılışta migration ve seed otomatik çalışır.

### 4. Erişim adresleri

| Servis    | URL / Bağlantı        |
|-----------|------------------------|
| Frontend  | http://localhost:3002 |
| Backend API | http://localhost:8002 |
| PostgreSQL | localhost:5433 (polybot / polybot_dev) |
| Redis     | localhost:6380         |

Tarayıcıda **http://localhost:3002** açarak dashboard’a girebilirsiniz.

### Doğrulama

- Frontend: http://localhost:3002
- API sağlık: `curl http://localhost:8002/api/overview`
- Worker (Redis): Container `kripto-bot-worker-1` “Up” olmalı; log: `docker logs kripto-bot-worker-1`

Detaylı adımlar, sorun giderme ve “farklı bir AI nasıl kurar?” senaryosu için **[SETUP.md](./SETUP.md)** dosyasına bakın.

---

## Cursor Rules (Kural Dosyaları)

Proje, **Cursor IDE** (ve benzeri AI destekli editörler) için `.cursor/rules` altında kural dosyaları içerir. Başka bir AI veya geliştirici bu kurallara göre davranışı ve mimariyi anlayabilir.

| Dosya | İçerik |
|-------|--------|
| `01-project-architecture.mdc` | Genel mimari, DEMO_MODE_ONLY, katman yapısı |
| `02-backend-python-standards.mdc` | Python/FastAPI standartları |
| `03-docker-deployment.mdc` | Docker Compose, portlar, servisler |
| `04-database-schema.mdc` | Veritabanı şeması, tablolar |
| `05-strategy-system.mdc` | Strateji motoru, runner, allocation |
| `06-data-ingestion.mdc` | Ingestion, raw_events, normalizer |
| `07-frontend-standards.mdc` | Next.js, sayfalar, API kullanımı |
| `08-wallet-intelligence.mdc` | Wallet scoring, alpha, influence graph |
| `09-risk-management.mdc` | Risk, Kelly, kill switch |
| `10-analytics-system.mdc` | Analytics endpoint’leri, edge calibration, 3-layer edge |
| `00-known-issues.mdc` | Bilinen hatalar ve çözümleri |

- **Kullanım:** Cursor’da projeyi açtığınızda bu kurallar otomatik uygulanabilir (alwaysApply / description’a göre).
- **Amaç:** Yeni özellik veya bug fix yaparken mimari ve güvenlik kurallarının korunması; farklı bir AI’ın projeyi eksiksiz kurup çalıştırması ve geliştirmesi.

---

## Stratejiler

| Strateji | Açıklama |
|----------|----------|
| direct_copy | İzlenen cüzdan işlemlerini gecikme + edge doğrulamasıyla kopyalar |
| high_conviction | Ek filtreler: wallet skoru, copyability decay |
| leader_copy | Influence graph’ta “leader” cüzdanları kopyalar; leader impact + prop_signal |
| dislocation | İlişkili market’lerde spread anomali (z-score) ile kağıt yapı |
| shadow | Sadece “girseydi” logu; gerçek pozisyon açmaz |

---

## Önemli Endpoint’ler

| Method | Path | Açıklama |
|--------|------|----------|
| GET | `/api/overview` | Özet, bakiye, pozisyon sayıları |
| GET | `/api/wallets` | Cüzdan listesi, copyable alpha |
| GET | `/api/wallets/intelligence/leader-impact` | Leader impact leaderboard |
| GET | `/api/markets` | Market listesi, actionability |
| GET | `/api/trades` | Kağıt işlem logu |
| GET | `/api/signals` | Sinyal listesi |
| GET | `/api/analytics/daily-digest` | Günlük özet, PF, expectancy |
| GET | `/api/analytics/edge-calibration` | Edge kalibrasyonu |
| GET | `/api/analytics/strategy-health` | Strateji sağlığı, kill switch |
| WS | `/ws/live` | Canlı güncellemeler |

---

## Güvenlik

- **DEMO_MODE_ONLY:** Uygulama başlangıcında kontrol edilir; gerçek emir yolu yoktur.
- Gerçek borsa/Polymarket trading API anahtarı **gerekmez**; sadece public market/wallet verisi kullanılır.
- Tüm “işlemler” veritabanında sanal kayıt olarak tutulur.

---

## Lisans ve Katkı

Bu proje araştırma ve eğitim amaçlıdır. Katkı için issue veya pull request açabilirsiniz; değişikliklerde DEMO_MODE_ONLY ve mevcut Cursor kurallarına uyulması önerilir.
